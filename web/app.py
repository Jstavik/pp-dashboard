"""FastAPI aplikace — spuštění: uvicorn web.app:app --host 127.0.0.1 --port 8501

Stránky (/elektrina, /plyn, /report, /odstavky-ceps, /cot) se skládají ze
záložek, které se načítají LÍNĚ jako HTML fragmenty (/frag/<page>/<tab>);
grafy si fragment dotahuje zvlášť jako Plotly JSON (/api/chart/...).
Při startu se nic těžkého nenačítá — data se čtou až při prvním požadavku
(a pak drží v TTL cache, viz core/cache.py).
"""
import hashlib
import io
import json
import logging
import os
import resource
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web import registry
from web.params import Params
from web.util import fmt_value

log = logging.getLogger("ppdash.web")

WEB_DIR = Path(__file__).resolve().parent
STARTED = time.time()

# pyarrow: systémový alokátor místo mimalloc — uvolněná paměť po čtení
# parquet se vrací OS (mimalloc ji držel, na 2 GB RAM to dělalo stovky MB).
# Totéž nastavuje ARROW_DEFAULT_MEMORY_POOL=system v systemd unitě.
try:
    import pyarrow as _pa
    _pa.set_memory_pool(_pa.system_memory_pool())
except Exception:  # pragma: no cover
    pass

# copy-on-write: DataFrame vrácené z cache (mělké kopie) se při zápisu
# do sloupce zkopírují, místo aby přepsaly sdílená data v cache.
pd.set_option("mode.copy_on_write", True)


def _load_tabs() -> None:
    # Import modulů záložek = registrace (pořadí určuje pořadí v navigaci)
    from web.tabs import electricity, gas, report, energy_charts, market, nordic, weather, explorer, ceps_outages, cot, reports  # noqa: F401


_load_tabs()

@asynccontextmanager
async def lifespan(_app):
    # Max N souběžně počítaných požadavků (sync endpointy běží v threadpoolu)
    # — na 2 GB RAM nechceme 40 paralelních výpočtů nad DataFrame.
    import anyio.to_thread
    anyio.to_thread.current_default_thread_limiter().total_tokens = int(os.environ.get("PPDASH_THREADS", "8"))
    yield


app = FastAPI(title="PP Dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")


def _asset_version() -> str:
    h = hashlib.sha1()
    for name in ("app.js", "app.css"):
        p = WEB_DIR / "static" / name
        if p.exists():
            h.update(str(p.stat().st_mtime_ns).encode())
    return h.hexdigest()[:10]


def _num(v, fmt=",.0f"):
    return fmt_value(v, fmt)


from web.nav import NAV, active_group  # noqa: E402

templates.env.globals.update(
    pages=registry.PAGES, tabs=registry.TABS, asset_version=_asset_version(), nav=NAV, nav_group=active_group,
)
templates.env.filters["num"] = _num
templates.env.filters["fmt"] = fmt_value


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request):
    from web.overview import context
    return templates.TemplateResponse(request, "overview.html", {"o": context(), "nav_active": "prehled"})


@app.get("/frag-overview", response_class=HTMLResponse, include_in_schema=False)
def overview_fragment(request: Request):
    from web.overview import context
    return templates.TemplateResponse(request, "overview_body.html", {"o": context()})


@app.get("/healthz")
def healthz():
    """Levná kontrola bez čtení dat — pro systemd/monitoring/Cloudflare."""
    from core.settings import history_root
    hist = history_root()
    ok = hist.is_dir()
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    try:
        cur_mb = int(open("/proc/self/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20
    except (OSError, ValueError):
        cur_mb = None
    body = {
        "status": "ok" if ok else "degraded",
        "history_dir": str(hist),
        "history_exists": ok,
        "uptime_s": round(time.time() - STARTED),
        "rss_mb": round(cur_mb) if cur_mb is not None else None,
        "max_rss_mb": round(rss_mb),
    }
    return JSONResponse(body, status_code=200 if ok else 503)


@app.get("/vendor/plotly.min.js", include_in_schema=False)
def plotly_js():
    # plotly.js přímo z nainstalovaného balíku plotly — stejná verze jako
    # Python strana, žádná závislost na CDN.
    import plotly
    path = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    return FileResponse(path, media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.post("/api/cache/clear")
def cache_clear():
    from core.cache import clear_all
    clear_all()
    return {"cleared": True}


@app.get("/api/cache/stats")
def cache_stats():
    from core.cache import cache_stats as stats
    return [s for s in stats() if s["entries"] or s["hits"] or s["misses"]]


def _page_or_404(page_key: str):
    page = registry.PAGES.get(page_key)
    if page is None or not page.tabs:
        raise HTTPException(404, "Stránka neexistuje")
    return page


@app.get("/frag/{page_key}/{tab_key}", response_class=HTMLResponse)
def fragment(request: Request, page_key: str, tab_key: str):
    spec = registry.TABS.get((page_key, tab_key))
    if spec is None:
        raise HTTPException(404, "Záložka neexistuje")
    p = Params.from_request(request)
    try:
        ctx = spec.render(p) or {}
        error = None
    except Exception as e:
        log.exception("fragment %s/%s selhal", page_key, tab_key)
        ctx, error = {}, f"{type(e).__name__}: {e}"
    ctx.setdefault("query", str(p))
    return templates.TemplateResponse(request, "fragment.html", {
        "spec": spec, "p": p, "ctx": ctx, "error": error,
        "template": f"tabs/{page_key}/{tab_key}.html",
    })


@app.get("/frag-header/elektrina", response_class=HTMLResponse)
def electricity_header(request: Request):
    from web.services import electricity_header as build
    try:
        h, error = build(), None
    except Exception as e:
        log.exception("hlavička Elektřiny selhala")
        h, error = None, str(e)
    return templates.TemplateResponse(request, "header_electricity.html", {"h": h, "error": error})


def _figure_json(fig) -> str:
    if isinstance(fig, dict):
        return json.dumps(fig)
    return fig.to_json()


@app.get("/api/chart/{page_key}/{tab_key}/{chart_id}")
def chart(request: Request, page_key: str, tab_key: str, chart_id: str):
    spec = registry.CHARTS.get((page_key, tab_key, chart_id))
    if spec is None:
        raise HTTPException(404, "Graf neexistuje")
    p = Params.from_request(request)
    t0 = time.monotonic()
    try:
        fig = spec.fn(p)
    except Exception as e:
        log.warning("graf %s/%s/%s selhal: %s\n%s", page_key, tab_key, chart_id, e, traceback.format_exc(limit=4))
        return JSONResponse({"error": f"Graf se nepodařilo vytvořit ({type(e).__name__}: {e})"})
    if fig is None:
        return JSONResponse({"empty": True})
    from web.consistency import apply_entity_colors, is_empty
    if is_empty(fig):
        return JSONResponse({"empty": True})
    apply_entity_colors(fig)
    payload = '{"config": %s, "figure": %s}' % (json.dumps(spec.config), _figure_json(fig))
    log.debug("graf %s/%s/%s %.2fs", page_key, tab_key, chart_id, time.monotonic() - t0)
    return Response(payload, media_type="application/json")


@app.get("/api/chart-data/{page_key}/{tab_key}/{chart_id}")
def chart_data(request: Request, page_key: str, tab_key: str, chart_id: str, fmt: str = "csv"):
    """Data za grafem jako CSV/XLSX (stejné parametry jako /api/chart/...)."""
    from web import export
    spec = registry.CHARTS.get((page_key, tab_key, chart_id))
    if spec is None or fmt not in ("csv", "xlsx"):
        raise HTTPException(404, "Graf neexistuje")
    p = Params.from_request(request)
    p._items = [(k, v) for k, v in p._items if k != "fmt"]
    try:
        fig = spec.fn(p)
    except Exception as e:
        log.warning("export %s/%s/%s selhal: %s", page_key, tab_key, chart_id, e)
        raise HTTPException(500, f"Graf se nepodařilo vytvořit ({type(e).__name__}: {e})")
    if fig is None:
        raise HTTPException(404, "Graf nemá data")
    stamp = pd.Timestamp.now(tz="Europe/Prague").strftime("%Y%m%d-%H%M")
    filename = f"{page_key}_{tab_key}_{chart_id}_{stamp}.{fmt}"
    if fmt == "csv":
        body, mt = export.to_csv_bytes(fig), "text/csv; charset=utf-8"
    else:
        body = export.to_xlsx_bytes(fig, {"graf": f"{page_key}/{tab_key}/{chart_id}", "parametry": str(p)})
        mt = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(body, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/explorer/pivot")
def explorer_pivot(request: Request, fmt: str = "csv"):
    """Kontingenční tabulka Průzkumníku jako CSV/XLSX (stejné parametry jako záložka)."""
    from web import export
    from web.tabs.explorer import pivot_export
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(404, "Neznámý formát")
    p = Params.from_request(request)
    p._items = [(k, v) for k, v in p._items if k != "fmt"]
    df, base = pivot_export(p)
    if df.empty:
        raise HTTPException(404, "Pivot nemá data")
    stamp = pd.Timestamp.now(tz="Europe/Prague").strftime("%Y%m%d-%H%M")
    filename = f"{base}_{stamp}.{fmt}"
    if fmt == "csv":
        body, mt = ("\ufeff" + df.to_csv()).encode("utf-8"), "text/csv; charset=utf-8"
    else:
        body = export.dataframe_xlsx_bytes(df.reset_index(), {"pohled": str(p)})
        mt = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(body, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/explorer/views")
async def explorer_view_save(request: Request):
    from web.tabs.explorer import save_view
    body = await request.json()
    try:
        return {"views": save_view(str(body.get("name", "")), str(body.get("query", "")))}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/explorer/views")
def explorer_view_delete(name: str):
    from web.tabs.explorer import delete_view
    return {"views": delete_view(name)}


@app.get("/api/report/{kind}.{fmt}")
def report_export(request: Request, kind: str, fmt: str):
    """Denní/týdenní report jako PDF (kaleido + Pillow) nebo XLSX (všechna čísla)."""
    from core import report
    from web.tabs.explorer import load_views
    if kind not in report.KINDS or fmt not in ("pdf", "xlsx"):
        raise HTTPException(404, "Report neexistuje")
    p = Params.from_request(request)
    asof = report.parse_date(p.str("date"))
    names = set(p.list("views", []))
    views = [(v["name"], v["query"]) for v in load_views() if v["name"] in names]
    filename = f"{kind}_report_{asof:%Y%m%d}.{fmt}"
    if fmt == "xlsx":
        body = report.to_xlsx(kind, asof, views)
        mt = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        try:
            body = report.to_pdf(kind, asof, views)
        except report.PdfUnavailable as e:
            return HTMLResponse(f"<!doctype html><meta charset=utf-8><p style='font:16px system-ui;padding:24px'>"
                                f"{e}</p><p style='font:16px system-ui;padding:0 24px'><a href='javascript:history.back()'>"
                                f"← zpět</a></p>", status_code=503)
        mt = "application/pdf"
    return Response(body, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/csv/{name}")
def csv_export(request: Request, name: str):
    fn = registry.CSVS.get(name)
    if fn is None:
        raise HTTPException(404, "Export neexistuje")
    df, filename = fn(Params.from_request(request))
    buf = io.StringIO()
    df.to_csv(buf)
    return Response(buf.getvalue().encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/{page_key}", response_class=HTMLResponse)
def page_view(request: Request, page_key: str):
    page = _page_or_404(page_key)
    tab_key = request.query_params.get("tab")
    if tab_key not in page.tabs:
        tab_key = page.tabs[0]
    query = str(Params.from_request(request))
    return templates.TemplateResponse(request, "page.html", {
        "page": page, "active_tab": tab_key, "initial_query": query,
    })
