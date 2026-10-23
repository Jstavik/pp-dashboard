"""Denní a týdenní report — sestava grafů a tabulek k zvolenému datu.

Sekce = (id, titulek, fn(asof) → plotly Figure | None). Stejné figury
používá web (stránka Reporty, graf přes /api/chart/reporty/<druh>/<id>),
export XLSX (všechna čísla: klíčová čísla, data za každým grafem, události)
i export PDF (kaleido → PNG, stránky A4 složí Pillow).

PDF: kaleido 1.x potřebuje Chrome/Chromium (bez roota: `plotly_get_chrome`,
nebo BROWSER_PATH na existující chrome). Je to nejlehčí cesta: Chrome běží
jako samostatný proces jen během exportu (~150–300 MB), WeasyPrint by
potřeboval systémové knihovny Pango/Cairo (root) a stejně by grafy musel
dostat jako obrázky. Bez Chrome export PDF vrátí srozumitelnou chybu a
stránka nabízí tisk z prohlížeče (Tisk → Uložit jako PDF, print CSS A4).
"""
import io
import logging
import threading

import pandas as pd
import plotly.graph_objects as go

from core import overview as ov
from core.palette import NEUTRAL, country_color

log = logging.getLogger("ppdash.report")
TZ = "Europe/Prague"

KINDS = {"denni": "Denní report", "tydenni": "Týdenní report"}
FONT = "system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif"


def parse_date(v) -> pd.Timestamp:
    today = pd.Timestamp.now(tz=TZ).tz_localize(None).normalize()
    try:
        d = pd.Timestamp(v).normalize() if v else today
    except (ValueError, TypeError):
        d = today
    return min(d, today)


def _layout(fig, title, ytitle, height=340, xtitle=None):
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", font=dict(size=14)), height=height,
        margin=dict(l=60, r=20, t=48, b=40), font=dict(family=FONT, size=12),
        hovermode="x unified", plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, bgcolor="rgba(255,255,255,0)"),
        separators=",\u00a0",
    )
    fig.update_yaxes(title_text=ytitle, gridcolor="#e6ebf1", zerolinecolor="#cfd8e3")
    # české formáty dat i bez plotly.js locale (PDF přes kaleido)
    fig.update_xaxes(title_text=xtitle, gridcolor="#e6ebf1",
                     tickformat="%H:%M" if xtitle == "čas (Praha)" else "%-d. %-m.",
                     hoverformat="%d.%m.%Y %H:%M" if xtitle == "čas (Praha)" else "%d.%m.%Y")
    return fig


def _empty(fig_title):
    return None  # web ukáže „data se dotahují“, PDF/XLSX sekci přeskočí


def _line(fig, s, name, color, dash=None, width=2, mode="lines"):
    fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name, mode=mode,
                             line=dict(color=color, width=width, dash=dash), marker=dict(size=8)))


# ── grafy ───────────────────────────────────────────────────────────────

def fig_key_numbers(asof, weekly=False):
    rows = ov.key_numbers(asof)
    from web.util import fmt_value
    lab, val, dd, ww, dt = [], [], [], [], []
    for r in rows:
        lab.append(r["label"])
        val.append(f"{fmt_value(r['value'], r['fmt'])} {r['unit']}" if r["value"] is not None else "data se dotahují")
        dd.append(f"{fmt_value(r['dd'], '+,.2f')} {r['dunit']}" if r["dd"] is not None else "—")
        ww.append(f"{fmt_value(r['ww'], '+,.2f')} {r['dunit']}" if r["ww"] is not None else "—")
        dt.append(pd.Timestamp(r["date"]).strftime("%d.%m.%Y") if r["date"] is not None else "—")
    cols = ["Ukazatel", "Hodnota", "Změna d/d", "Změna w/w", "Datum dat"]
    vals = [lab, val, dd, ww, dt]
    if weekly:
        cols, vals = [cols[0], cols[1], cols[3], cols[4]], [lab, val, ww, dt]
    fig = go.Figure(go.Table(
        header=dict(values=cols, fill_color="#eef2f7", align="left", font=dict(size=12, color="#1d2733")),
        cells=dict(values=vals, align=["left"] + ["right"] * (len(cols) - 1), height=24,
                   fill_color="#ffffff", font=dict(size=12, color="#1d2733"))))
    fig.update_layout(title=dict(text="Klíčová čísla", x=0, font=dict(size=14)), height=60 + 26 * (len(rows) + 1),
                      margin=dict(l=10, r=10, t=40, b=10), font=dict(family=FONT), paper_bgcolor="#ffffff")
    return fig


def fig_da(asof, days=31, weekly=False):
    fig = go.Figure()
    any_data = False
    for cc in ("CZ", "DE"):
        d = ov.daily_da(cc, days=days if not weekly else 7 * 13, asof=asof)
        if d.empty:
            continue
        any_data = True
        if weekly:
            d = d.resample("W-SUN").mean()
        _line(fig, d["base"], f"{cc} base", country_color(cc))
        if "peak" in d:
            _line(fig, d["peak"].dropna(), f"{cc} peak", country_color(cc), dash="dot", width=1.6)
    if not any_data:
        return None
    t = "Day-ahead ceny CZ a DE — týdenní průměry" if weekly else "Day-ahead ceny CZ a DE — base a peak"
    return _layout(fig, t, "EUR/MWh", xtitle="den dodávky" if not weekly else "týden")


def fig_spread(asof, days=31):
    cz, de = ov.daily_da("CZ", days=days, asof=asof), ov.daily_da("DE", days=days, asof=asof)
    if cz.empty or de.empty:
        return None
    fig = go.Figure()
    for col, dash in (("base", None), ("peak", "dot")):
        s = (cz[col] - de[col]).dropna()
        fig.add_trace(go.Bar(x=s.index, y=s.values, name=f"spread {col}", opacity=1 if col == "base" else .55,
                             marker_color=country_color("CZ") if col == "base" else "#9cc3f0"))
    fig.update_layout(barmode="group", bargap=.25)
    return _layout(fig, "Spread CZ − DE", "EUR/MWh", xtitle="den dodávky")


def fig_hourly(asof):
    """Hodinový profil DA pro dodávku D+1 (když ještě není, tak D)."""
    fig = go.Figure()
    day = asof + pd.Timedelta(days=1)
    if ov.hourly_da("CZ", day).empty:
        day = asof
    for cc, zone in (("CZ", "CZ"), ("DE", "DE_LU")):
        s = ov.hourly_da(zone, day)
        if not s.empty:
            fig.add_trace(go.Scatter(x=s.index, y=s.values, name=cc,
                                     line=dict(color=country_color(cc), width=2, shape="hv")))
    if not fig.data:
        return None
    return _layout(fig, f"DA hodinový profil — dodávka {day:%d.%m.%Y}", "EUR/MWh", xtitle="čas (Praha)")


def fig_storage(asof, days=60):
    fig = go.Figure()
    for cc in ("EU", "CZ", "DE"):
        s = ov.storage_series(cc, days=days + 400, asof=asof)
        if s.empty:
            continue
        cur = s[s.index > asof - pd.Timedelta(days=days)]
        _line(fig, cur, cc, country_color(cc))
        ly = s[(s.index > asof - pd.Timedelta(days=days + 365)) & (s.index <= asof - pd.Timedelta(days=365))]
        if not ly.empty:
            ly.index = ly.index + pd.Timedelta(days=365)
            _line(fig, ly, f"{cc} loni", country_color(cc), dash="dot", width=1.2)
    if not fig.data:
        return None
    return _layout(fig, "Plnění zásobníků plynu (letos vs. loni)", "plnění [%]", xtitle="plynárenský den")


def fig_flows(asof, days=60, weekly=False):
    no, lng = ov.no_flow_series(days=days, asof=asof), ov.lng_series(days=days, asof=asof)
    if no.empty and lng.empty:
        return None
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Export plynu NO (GASSCO) [mcm/d]",
                                                        "LNG send-out EU (GIE ALSI) [TWh/d]"))
    if weekly:
        no, lng = no.resample("W-SUN").mean(), lng.resample("W-SUN").mean()
    fig.add_trace(go.Scatter(x=no.index, y=no.values, name="NO export", line=dict(color=country_color("NO"), width=2)),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=lng.index, y=lng.values, name="LNG EU", line=dict(color=NEUTRAL, width=2)), row=1, col=2)
    _layout(fig, "Toky plynu", None)
    fig.update_yaxes(title_text="mcm/d", row=1, col=1)
    fig.update_yaxes(title_text="TWh/d", row=1, col=2)
    fig.update_layout(showlegend=False)
    return fig


def fig_temp(asof, days=30):
    fig = go.Figure()
    for cc in ("CZ", "DE"):
        s, fc, _last = ov.temp_anomaly(cc, asof, days=days)
        if s.empty:
            continue
        fig.add_trace(go.Bar(x=s.index, y=s.values, name=f"{cc} odchylka", marker_color=country_color(cc),
                             opacity=.85))
    if not fig.data:
        return None
    fig.update_layout(barmode="group")
    fig.add_hline(y=0, line=dict(color="#78909c", width=1))
    return _layout(fig, "Teplota — odchylka denního průměru od normálu 1991–2020", "°C", xtitle="den")


def fig_hydro(asof):
    dev, fill = ov.hydro_no(asof)
    if dev.empty:
        return None
    s = dev.tail(26)
    fig = go.Figure(go.Bar(x=s.index, y=s.values, name="NO vs. medián", marker_color=country_color("NO")))
    fig.add_hline(y=0, line=dict(color="#78909c", width=1))
    return _layout(fig, "Hydro Norsko — plnění nádrží vs. medián (NVE)", "p.b.", xtitle="týden")


def fig_cot(asof):
    s = ov.cot_mm_net(asof).tail(26)
    if s.empty:
        return None
    fig = go.Figure(go.Bar(x=s.index, y=s.values, name="Investment Funds net", marker_color="#6250d6"))
    fig.add_hline(y=0, line=dict(color="#78909c", width=1))
    return _layout(fig, "CoT TTF (ICE Endex) — net pozice Investment Funds", "TWh", xtitle="report (pátek)")


def events_for(asof, weekly=False) -> pd.DataFrame:
    import json
    import os
    from core.settings import history_root
    path = history_root() / ov.EVENTS_FILE
    try:
        evs = json.loads(path.read_text(encoding="utf-8")) if os.path.exists(path) else []
    except (OSError, ValueError):
        evs = []
    if not evs:
        return pd.DataFrame(columns=["čas", "kategorie", "událost", "detail"])
    df = pd.DataFrame(evs)
    t = pd.to_datetime(df["t"], utc=True, format="ISO8601").dt.tz_convert(TZ).dt.tz_localize(None)
    start = asof - pd.Timedelta(days=6 if weekly else 0)
    sel = (t >= start) & (t < asof + pd.Timedelta(days=1))
    df = df[sel].assign(t=t[sel]).sort_values("t", ascending=False)
    return pd.DataFrame({"čas": df["t"].dt.strftime("%d.%m. %H:%M"), "kategorie": df["cat"],
                         "událost": df["title"], "detail": df["text"]})


def fig_events(asof, weekly=False):
    df = events_for(asof, weekly).head(40)
    if df.empty:
        return None
    fig = go.Figure(go.Table(
        columnwidth=[70, 80, 330, 260],
        header=dict(values=list(df.columns), fill_color="#eef2f7", align="left", font=dict(size=11, color="#1d2733")),
        cells=dict(values=[df[c] for c in df.columns], align="left", fill_color="#ffffff",
                   font=dict(size=10, color="#1d2733"), height=22)))
    fig.update_layout(title=dict(text="Události" + (" za týden" if weekly else " dne"), x=0, font=dict(size=14)),
                      height=min(1100, 70 + 24 * (len(df) + 1)), margin=dict(l=10, r=10, t=40, b=10),
                      font=dict(family=FONT), paper_bgcolor="#ffffff")
    return fig


SECTIONS = {
    "denni": [
        ("klicova", "Klíčová čísla", lambda a: fig_key_numbers(a)),
        ("da", "Day-ahead ceny", lambda a: fig_da(a)),
        ("hodiny", "Hodinový profil DA", lambda a: fig_hourly(a)),
        ("spread", "Spread CZ–DE", lambda a: fig_spread(a)),
        ("zasobniky", "Zásobníky plynu", lambda a: fig_storage(a)),
        ("toky", "Toky plynu", lambda a: fig_flows(a)),
        ("teplota", "Teplota vs. normál", lambda a: fig_temp(a)),
        ("udalosti", "Události", lambda a: fig_events(a)),
    ],
    "tydenni": [
        ("klicova", "Klíčová čísla", lambda a: fig_key_numbers(a, weekly=True)),
        ("da", "Day-ahead ceny (týdny)", lambda a: fig_da(a, weekly=True)),
        ("spread", "Spread CZ–DE (8 týdnů)", lambda a: fig_spread(a, days=56)),
        ("zasobniky", "Zásobníky plynu", lambda a: fig_storage(a, days=90)),
        ("toky", "Toky plynu (týdny)", lambda a: fig_flows(a, days=7 * 13, weekly=True)),
        ("teplota", "Teplota vs. normál", lambda a: fig_temp(a, days=42)),
        ("hydro", "Hydro Norsko", lambda a: fig_hydro(a)),
        ("cot", "CoT TTF", lambda a: fig_cot(a)),
        ("udalosti", "Události za týden", lambda a: fig_events(a, weekly=True)),
    ],
}


def section_figure(kind: str, sid: str, asof):
    for k, _t, fn in SECTIONS.get(kind, []):
        if k == sid:
            return fn(asof)
    raise KeyError(sid)


def view_figure(query: str):
    """Graf uloženého pohledu Průzkumníku (stejný jako /api/chart/pruzkumnik/pohled/main)."""
    from starlette.datastructures import QueryParams
    from web.params import Params
    from web import registry
    spec = registry.CHARTS[("pruzkumnik", "pohled", "main")]
    return spec.fn(Params(list(QueryParams(query).multi_items())))


def build_figures(kind: str, asof, views: list = None) -> list:
    """[(titulek, figure)] — prázdné sekce vynechá."""
    out = []
    for sid, title, fn in SECTIONS[kind]:
        try:
            fig = fn(asof)
        except Exception as e:
            log.warning("report %s/%s: %s", kind, sid, e)
            fig = None
        if fig is not None:
            out.append((title, fig))
    for name, query in views or []:
        try:
            fig = view_figure(query)
            if fig is not None:
                out.append((f"Pohled: {name}", fig))
        except Exception as e:
            log.warning("report pohled %s: %s", name, e)
    return out


# ── exporty ─────────────────────────────────────────────────────────────

def to_xlsx(kind: str, asof, views: list = None) -> bytes:
    from web import export
    buf = io.BytesIO()
    used = set()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        rows = ov.key_numbers(asof)
        kn = pd.DataFrame([{"ukazatel": r["label"], "hodnota": r["value"], "jednotka": r["unit"],
                            "změna d/d": r["dd"], "změna w/w": r["ww"], "jednotka změny": r["dunit"],
                            "datum dat": r["date"]} for r in rows])
        kn.to_excel(xw, sheet_name="klíčová čísla", index=False)
        used.add("klíčová čísla")
        for title, fig in build_figures(kind, asof, views):
            if title == "Klíčová čísla":
                continue
            if title.startswith("Události"):
                df = events_for(asof, kind == "tydenni")
            else:
                wide = export.figure_wide(fig)
                df = export._clean(wide, excel=True) if wide is not None else export._clean(export.figure_long(fig), True)
                if wide is not None:
                    df = df.reset_index()
            name = "".join(ch for ch in title if ch not in "[]:*?/\\")[:31]
            base, i = name, 2
            while name in used:
                name = f"{base[:28]} {i}"
                i += 1
            used.add(name)
            df.to_excel(xw, sheet_name=name, index=False)
        info = {"report": KINDS[kind], "k datu": asof.strftime("%d.%m.%Y"),
                "exportováno": pd.Timestamp.now(tz=TZ).strftime("%d.%m.%Y %H:%M"),
                "časy": "Europe/Prague", "zdroje": "ENTSO-E, ČEPS, GIE, GASSCO, ICE, NVE, Open-Meteo (CC BY 4.0)"}
        pd.DataFrame(list(info.items()), columns=["položka", "hodnota"]).to_excel(xw, sheet_name="info", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col[:200] if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 70)
    return buf.getvalue()


class PdfUnavailable(RuntimeError):
    pass


_pdf_lock = threading.Lock()


def _render_pngs(figs: list, width=1150) -> list:
    """Figury → PNG přes kaleido (jeden Chrome pro celou dávku, když to verze umí)."""
    try:
        import kaleido  # noqa: F401
        import plotly.io as pio
    except ImportError:
        raise PdfUnavailable("Export PDF potřebuje balíček kaleido (pip install kaleido) a Chrome "
                             "(plotly_get_chrome). Zatím použijte Tisk → Uložit jako PDF.")
    out, started = [], False
    try:
        try:  # jeden Chrome pro celou dávku (kaleido ≥ 1.0); jinak se spouští na každý obrázek
            kaleido.start_sync_server(silence_warnings=True)
            started = True
        except Exception:
            pass
        for fig in figs:
            h = int(fig.layout.height or 400)
            out.append(pio.to_image(fig, format="png", width=width, height=h, scale=2))
    except Exception as e:
        raise PdfUnavailable(f"Export PDF selhal ({type(e).__name__}: {e}). Chybí Chrome? Spusťte "
                             "`plotly_get_chrome` nebo nastavte BROWSER_PATH. Zatím použijte Tisk → Uložit jako PDF.")
    finally:
        if started:
            try:
                kaleido.stop_sync_server(silence_warnings=True)
            except Exception:
                pass
    return out


def to_pdf(kind: str, asof, views: list = None) -> bytes:
    """A4 na výšku: titulek + grafy pod sebou (zalomení stránek podle výšky)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise PdfUnavailable("Export PDF potřebuje balíček pillow.")
    figs = build_figures(kind, asof, views)
    head = go.Figure()
    head.update_layout(
        height=150, paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False), yaxis=dict(visible=False), font=dict(family=FONT),
        annotations=[dict(text=f"<b>{KINDS[kind]}</b> — {asof:%d.%m.%Y}", x=0, y=0.75, xref="paper", yref="paper",
                          showarrow=False, xanchor="left", font=dict(size=30, color="#1d2733")),
                     dict(text=f"PP Dashboard · vytvořeno {pd.Timestamp.now(tz=TZ):%d.%m.%Y %H:%M} (Praha) · "
                               "data: ENTSO-E, ČEPS, GIE, GASSCO, ICE, NVE, Open-Meteo (CC BY 4.0)",
                          x=0, y=0.15, xref="paper", yref="paper", showarrow=False, xanchor="left",
                          font=dict(size=14, color="#66788a"))])
    with _pdf_lock:  # jeden export najednou (Chrome ~200 MB)
        pngs = _render_pngs([head] + [f for _t, f in figs])
    page_w, page_h, margin = 2480, 3508, 120  # A4 při 300 dpi
    inner_w = page_w - 2 * margin
    pages, page, y = [], Image.new("RGB", (page_w, page_h), "white"), margin
    for png in pngs:
        im = Image.open(io.BytesIO(png)).convert("RGB")
        im = im.resize((inner_w, int(im.height * inner_w / im.width)))
        if im.height > page_h - 2 * margin:
            im = im.resize((int(im.width * (page_h - 2 * margin) / im.height), page_h - 2 * margin))
        if y + im.height > page_h - margin and y > margin:
            pages.append(page)
            page, y = Image.new("RGB", (page_w, page_h), "white"), margin
        page.paste(im, (margin, y))
        y += im.height + 40
    pages.append(page)
    for i, pg in enumerate(pages, 1):
        ImageDraw.Draw(pg).text((page_w - margin - 120, page_h - 80), f"{i}/{len(pages)}", fill=(102, 120, 138))
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=300.0)
    return buf.getvalue()
