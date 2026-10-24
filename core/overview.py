"""Přehled dne (úvodní stránka) — PŘEDPOČÍTANÝ při aktualizaci dat.

scripts/update_all.py po každém běhu zavolá build_overview(); výsledek je
history/overview.json (dlaždice + feed událostí) a history/overview_events.json
(trvalý feed: událost si drží čas, kdy byla poprvé zjištěna). Web jen čte JSON
(web/tabs/overview.py) — žádný výpočet při requestu.

Minutový live běh (ČEPS, GASSCO) přepočítá jen „živé“ dlaždice a události
(LIVE=True), ostatní zůstanou z posledního plného běhu.

Každá dlaždice = dict: key, group, label, value, unit, fmt, dd/ww (změna
proti včerejšku / týdnu; u % a °C v bodech), dd_unit, spark (≈30 hodnot),
href (detailní stránka), asof (datum/čas dat), sub (poznámka), state
(ok/warn/bad/None). Chybějící data → value None, sub „data se dotahují“.
Každý builder běží zvlášť (chyba jednoho nic neshodí).
"""
import fcntl
import json
import logging
import math
import os
import time

import numpy as np
import pandas as pd

from core.settings import history_path, history_root

log = logging.getLogger("ppdash.overview")
TZ = "Europe/Prague"

OVERVIEW_FILE = "overview.json"
EVENTS_FILE = "overview_events.json"
EVENTS_KEEP_DAYS = 21
EVENTS_MAX = 400

# sousední země pro odstávky ENTSO-E (kódy jako v outages.parquet)
OUTAGE_COUNTRIES = {"CZ": "ČR", "DE_LU": "DE", "AT": "AT", "SK": "SK", "PL": "PL"}
OUTAGE_MIN_MW = 100

GROUPS = [("power", "Elektřina"), ("gas", "Plyn"), ("weather", "Počasí"), ("nordic", "Nordic"), ("market", "Trh")]

_BUILDERS = []


def builder(live=False):
    def deco(fn):
        _BUILDERS.append((fn, live))
        return fn
    return deco


def _now():
    return pd.Timestamp.now(tz=TZ)


def _f(v):
    """float/None pro JSON (NaN → None)."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, 4)


def _spark(s: pd.Series, n=30) -> list:
    s = s.dropna().tail(n)
    return [_f(v) for v in s.values]


def tile(key, group, label, value=None, unit="", fmt=",.1f", dd=None, ww=None, dd_unit=None, spark=None,
         href="/", asof=None, sub="", state=None, dfmt=None) -> dict:
    return {"key": key, "group": group, "label": label, "value": _f(value), "unit": unit, "fmt": fmt,
            "dd": _f(dd), "ww": _f(ww), "dd_unit": dd_unit if dd_unit is not None else unit,
            "dfmt": dfmt or ("+" + fmt.lstrip("+")), "spark": spark or [], "href": href,
            "asof": str(asof) if asof is not None else None, "sub": sub, "state": state}


def _changes(s: pd.Series, lag_w=7):
    """Poslední hodnota, změna d/d a w/w (řada indexovaná dnem)."""
    s = s.dropna()
    if s.empty:
        return None, None, None, None
    last_i = s.index[-1]
    v = s.iloc[-1]
    prev = s.iloc[-2] if len(s) > 1 else None
    wk = s[s.index <= last_i - pd.Timedelta(days=lag_w)]
    w = wk.iloc[-1] if not wk.empty else None
    return v, (v - prev if prev is not None else None), (v - w if w is not None else None), last_i


def _asof_day(ts) -> str:
    ts = pd.Timestamp(ts)
    return ts.strftime("%d.%m.%Y")


# ── Elektřina ───────────────────────────────────────────────────────────

def _day_index(col) -> pd.DatetimeIndex:
    """Časový sloupec → půlnoc dne v Praze bez tz (index denních řad)."""
    t = pd.to_datetime(col, utc=True).dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize()
    return pd.DatetimeIndex(t)


def _window(days, asof):
    """(date_from, date_to, cutoff) pro čtení partitionovaných dat k datu asof."""
    end = pd.Timestamp(asof) if asof is not None else _now().tz_localize(None).normalize()
    return (end - pd.Timedelta(days=days + 2)).date(), (end + pd.Timedelta(days=2)).date(), end


def _cut(s, cutoff):
    return s[s.index <= cutoff] if len(s) else s


def daily_da(cc: str, days=60, asof=None) -> pd.DataFrame:
    """Denní base/peak (EUR/MWh) pro zemi: dap_europe (base/peak přímo),
    doplněné z entsoe_prices (hodinové/15min → base = průměr, peak =
    8–20 h po–pá místního času). asof = den reportu (bere i dodávku D+1)."""
    from data.partitioned_store import read_partitioned
    since, until, cutoff = _window(days, asof)
    out = pd.DataFrame(columns=["base", "peak"])
    zone = {"DE": "DE_LU"}.get(cc, cc)
    ep = read_partitioned(history_path(f"entsoe_prices/{zone}"), fmt="parquet", date_from=since, date_to=until)
    if not ep.empty:
        t = pd.to_datetime(ep["time"], utc=True).dt.tz_convert(TZ)
        s = pd.Series(ep["price"].values, index=t).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        day = s.index.normalize().tz_localize(None)
        base = s.groupby(day).mean()
        pk = s[(s.index.hour >= 8) & (s.index.hour < 20) & (s.index.dayofweek < 5)]
        peak = pk.groupby(pk.index.normalize().tz_localize(None)).mean()
        out = pd.DataFrame({"base": base, "peak": peak})
    dap = read_partitioned(history_path("dap_europe"), fmt="parquet", date_from=since, date_to=until)
    if not dap.empty:
        d = dap[dap["cc"] == cc].copy()
        if not d.empty:
            d.index = pd.to_datetime(d["date"])
            d = d[~d.index.duplicated(keep="last")][["base", "peak"]]
            out = d.combine_first(out) if not out.empty else d
    if out.empty:
        return pd.DataFrame(columns=["base", "peak"], index=pd.DatetimeIndex([]), dtype=float)
    out = out.sort_index()
    out = out[(out.index >= pd.Timestamp(since)) & (out.index <= cutoff + pd.Timedelta(days=1))]
    return out


def hourly_da(zone: str, day) -> pd.Series:
    """Ceny DA pro den dodávky (místní čas) z entsoe_prices."""
    from data.partitioned_store import read_partitioned
    day = pd.Timestamp(day)
    ep = read_partitioned(history_path(f"entsoe_prices/{zone}"), fmt="parquet", date_from=day.date(),
                          date_to=(day + pd.Timedelta(days=1)).date())
    if ep.empty:
        return pd.Series(dtype=float)
    t = pd.to_datetime(ep["time"], utc=True).dt.tz_convert(TZ)
    s = pd.Series(ep["price"].values, index=t).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s[s.index.normalize().tz_localize(None) == day.normalize()]


def storage_series(cc: str, days=75, asof=None) -> pd.Series:
    from data.partitioned_store import read_partitioned
    since, until, cutoff = _window(days, asof)
    df = read_partitioned(history_path("gie_all_storage"), fmt="csv", date_from=since, date_to=until,
                          columns=["gasDayStart", "country_code", "full"])
    if df.empty:
        return pd.Series(dtype=float)
    d = df[df["country_code"] == cc]
    if d.empty:
        return pd.Series(dtype=float)
    s = pd.Series(pd.to_numeric(d["full"], errors="coerce").values, index=_day_index(d["gasDayStart"])).sort_index()
    return _cut(s[~s.index.duplicated(keep="last")], cutoff)


def no_flow_series(days=60, asof=None) -> pd.Series:
    """GASSCO: součet výstupních nominací NCS (mcm/d) — export plynu z Norska."""
    from data.partitioned_store import read_partitioned
    since, until, cutoff = _window(days, asof)
    g = read_partitioned(history_path("gassco_nominations"), fmt="csv", date_from=since, date_to=until)
    if g.empty:
        return pd.Series(dtype=float)
    g = g[g["point"] == "Sum Exit Nominations NCS"]
    if g.empty:
        return pd.Series(dtype=float)
    s = pd.Series(g["value_MSm3"].values, index=_day_index(g["date"])).sort_index()
    return _cut(s[~s.index.duplicated(keep="last")], cutoff)


def lng_series(days=60, asof=None) -> pd.Series:
    """GIE ALSI: send-out všech terminálů EU (TWh/d)."""
    from data.partitioned_store import read_partitioned
    since, until, cutoff = _window(days, asof)
    lng = read_partitioned(history_path("lng_storage"), fmt="csv", date_from=since, date_to=until,
                           columns=["gasDayStart", "country_code", "sendOut"])
    if lng.empty:
        return pd.Series(dtype=float)
    lng = lng[lng["country_code"] != "EU"]
    v = pd.to_numeric(lng["sendOut"], errors="coerce")
    v = v.where(v.between(0, 3000))  # chybné hodnoty v ALSI (zemi > 3 TWh/d nemá žádný terminál)
    s = pd.Series(v.values, index=_day_index(lng["gasDayStart"]))
    return _cut(s.groupby(level=0).sum(min_count=1).sort_index() / 1000, cutoff)


def ceps_imbalance(hours=30, asof=None) -> pd.Series:
    from data.ceps import load_ceps_store
    end = _now() if asof is None else pd.Timestamp(asof).tz_localize(TZ) + pd.Timedelta(days=1)
    df = load_ceps_store("imbalance", date_from=end - pd.Timedelta(hours=hours), date_to=end)
    if df.empty:
        return pd.Series(dtype=float)
    s = df.groupby("time")["value"].mean().sort_index()
    s.index = s.index.tz_convert(TZ)
    return s


_ceps_imbalance = ceps_imbalance


def temp_anomaly(region: str, asof=None, days=30):
    """(řada denních odchylek teploty od normálu, průměrná odchylka předpovědi
    ECMWF na 7 dní od asof, datum posledního pozorování)."""
    from data import weather as wx
    from data.partitioned_store import read_partitioned
    daily = read_partitioned(os.path.join(wx.DAILY_DIR, region), fmt="parquet")
    if daily.empty:
        return pd.Series(dtype=float), None, None
    daily = daily[daily["var"] == "temp"]
    nrm = wx.normals(daily, "temp")
    if nrm.empty:
        return pd.Series(dtype=float), None, None
    m = nrm.set_index("doy")["mean"]
    loc = pd.to_datetime(daily["date"], utc=True).dt.tz_convert(TZ)
    s = pd.Series(daily["value"].values - loc.dt.dayofyear.map(m).values,
                  index=loc.dt.tz_localize(None).dt.normalize()).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    _, _, cutoff = _window(days, asof)
    s = _cut(s, cutoff).tail(days)
    fc_anom = None
    fcd = read_partitioned(os.path.join(wx.FC_DAILY_DIR, region), fmt="parquet",
                           date_from=(cutoff - pd.Timedelta(days=3)).date(), date_to=(cutoff + pd.Timedelta(days=1)).date())
    if not fcd.empty:
        fcd = fcd[fcd["var"] == "temp"]
        iss = pd.to_datetime(fcd["issued"], utc=True).dt.tz_convert(TZ).dt.tz_localize(None)
        fcd = fcd[iss <= cutoff + pd.Timedelta(days=1)]
        if not fcd.empty:
            fcd = fcd[fcd["issued"] == fcd["issued"].max()]
            mod = fcd[fcd["model"].astype(str).str.startswith("ecmwf_ifs")]
            fcd = mod if not mod.empty else fcd
            dl = pd.to_datetime(fcd["date"], utc=True).dt.tz_convert(TZ).dt.tz_localize(None)
            sel = (dl >= cutoff) & (dl < cutoff + pd.Timedelta(days=7))
            if sel.any():
                fc_anom = float((fcd["value"][sel].values - dl[sel].dt.dayofyear.map(m).values).mean())
    return s, fc_anom, (s.index[-1] if not s.empty else None)


def hydro_no(asof=None):
    """NVE celé Norsko: (odchylka plnění od mediánu v p.b., plnění %) jako týdenní řady."""
    from data.nordic import load_nve, load_nve_stats
    nve, st = load_nve(), load_nve_stats()
    if nve.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    d = nve[nve["area"] == "NO"].sort_values(["iso_year", "iso_week"])
    idx = _day_index(d["date"])
    fill = pd.Series(d["filling_pct"].values, index=idx)
    if st.empty:
        dev = pd.Series(dtype=float)
    else:
        med = st[st["area"] == "NO"].set_index("iso_week")["median_pct"]
        dev = pd.Series(d["filling_pct"].values - d["iso_week"].map(med).values, index=idx)
    _, _, cutoff = _window(0, asof)
    return _cut(dev, cutoff), _cut(fill, cutoff)


def cot_mm_net(asof=None) -> pd.Series:
    """TTF (ICE Endex TFM) — net pozice Investment Funds (≈ managed money), TWh."""
    path = history_path("ice_cot.parquet")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    import pyarrow.parquet as pq
    t = pq.read_table(path, columns=["product_code", "category", "side", "scope", "report_date", "nops", "venue"],
                      filters=[("product_code", "==", "TFM"), ("category", "==", "Investment Funds"),
                               ("scope", "==", "Total")]).to_pandas()
    if t.empty:
        return pd.Series(dtype=float)
    pv = t.pivot_table(index="report_date", columns="side", values="nops", aggfunc="sum")
    if "Long" not in pv or "Short" not in pv:
        return pd.Series(dtype=float)
    s = (pv["Long"] - pv["Short"]) / 1e6  # MWh → TWh
    s.index = pd.to_datetime(s.index)
    _, _, cutoff = _window(0, asof)
    return _cut(s.sort_index(), cutoff)


# ── katalog klíčových čísel (dlaždice i reporty) ────────────────────────
# key → (skupina, popisek, jednotka, formát, jednotka změny, href, fn(asof) → Series)

def _da(cc, col):
    return lambda asof: (lambda d: d[col] if col in d else pd.Series(dtype=float))(daily_da(cc, asof=asof))


def _spread(col):
    def fn(asof):
        cz, de = daily_da("CZ", asof=asof), daily_da("DE", asof=asof)
        return (cz[col] - de[col]).dropna() if col in cz and col in de else pd.Series(dtype=float)
    return fn


KEY_NUMBERS = {
    "da_CZ_base": ("power", "DA CZ base", "EUR/MWh", ",.1f", "EUR/MWh", "/elektrina?tab=dap", _da("CZ", "base")),
    "da_CZ_peak": ("power", "DA CZ peak", "EUR/MWh", ",.1f", "EUR/MWh", "/elektrina?tab=dap", _da("CZ", "peak")),
    "da_DE_base": ("power", "DA DE base", "EUR/MWh", ",.1f", "EUR/MWh", "/trh-eu?tab=ceny", _da("DE", "base")),
    "da_DE_peak": ("power", "DA DE peak", "EUR/MWh", ",.1f", "EUR/MWh", "/trh-eu?tab=ceny", _da("DE", "peak")),
    "spread_czde_base": ("power", "Spread CZ–DE base", "EUR/MWh", "+,.1f", "EUR/MWh", "/trh-eu?tab=ceny",
                         _spread("base")),
    "spread_czde_peak": ("power", "Spread CZ–DE peak", "EUR/MWh", "+,.1f", "EUR/MWh", "/trh-eu?tab=ceny",
                         _spread("peak")),
    "stor_EU": ("gas", "Zásobníky EU", "%", ",.1f", "p.b.", "/plyn?tab=zasobniky", lambda a: storage_series("EU", asof=a)),
    "stor_CZ": ("gas", "Zásobníky CZ", "%", ",.1f", "p.b.", "/plyn?tab=zasobniky", lambda a: storage_series("CZ", asof=a)),
    "stor_DE": ("gas", "Zásobníky DE", "%", ",.1f", "p.b.", "/plyn?tab=zasobniky", lambda a: storage_series("DE", asof=a)),
    "flow_no": ("gas", "Export plynu NO", "mcm/d", ",.1f", "mcm/d", "/plyn?tab=gassco", lambda a: no_flow_series(asof=a)),
    "lng_eu": ("gas", "LNG send-out EU", "TWh/d", ",.2f", "TWh/d", "/plyn?tab=lng", lambda a: lng_series(asof=a)),
    "temp_CZ": ("weather", "Teplota CZ vs. normál", "°C", "+,.1f", "°C", "/pocasi?tab=teplota&region=CZ",
                lambda a: temp_anomaly("CZ", a)[0]),
    "temp_DE": ("weather", "Teplota DE vs. normál", "°C", "+,.1f", "°C", "/pocasi?tab=teplota&region=DE",
                lambda a: temp_anomaly("DE", a)[0]),
    "hydro_no": ("nordic", "Hydro NO vs. medián", "p.b.", "+,.1f", "p.b.", "/nordic?tab=hydro", lambda a: hydro_no(a)[0]),
    "cot_ttf": ("market", "CoT TTF fondy net", "TWh", "+,.1f", "TWh", "/cot?tab=ttf", lambda a: cot_mm_net(a)),
}


def key_numbers(asof=None) -> list:
    """[{key, label, unit, value, dd, ww, date}] k datu asof (pro reporty)."""
    rows = []
    for key, (group, label, unit, fmt, dunit, href, fn) in KEY_NUMBERS.items():
        try:
            s = fn(asof)
        except Exception as e:
            log.warning("klíčové číslo %s: %s", key, e)
            s = pd.Series(dtype=float)
        v, dd, ww, last = _changes(s)
        if key in ("hydro_no", "cot_ttf"):  # týdenní data: d/d nedává smysl
            dd = None
        rows.append({"key": key, "group": group, "label": label, "unit": unit, "fmt": fmt, "dunit": dunit,
                     "value": _f(v), "dd": _f(dd), "ww": _f(ww), "date": last, "series": s})
    return rows


@builder()
def _da_tiles():
    tiles = []
    for key in ("da_CZ_base", "da_CZ_peak", "da_DE_base", "da_DE_peak", "spread_czde_base", "spread_czde_peak"):
        group, label, unit, fmt, dunit, href, fn = KEY_NUMBERS[key]
        s = fn(None)
        v, dd, ww, last = _changes(s)
        tiles.append(tile(key, group, label, v, unit, fmt, dd, ww, spark=_spark(s), href=href,
                          asof=_asof_day(last) if last is not None else None,
                          sub=f"dodávka {_asof_day(last)}" if last is not None else "data se dotahují",
                          dfmt="+,.1f"))
    return tiles


@builder(live=True)
def _ceps_tile():
    s = ceps_imbalance()
    if s.empty:
        return [tile("ceps_imb", "power", "ČEPS odchylka", None, "MW", ",.0f", href="/elektrina",
                     sub="data se dotahují")]
    now = _now()
    last = s.iloc[-1]
    today = s[s.index >= now.normalize()]
    yday = s[(s.index >= now.normalize() - pd.Timedelta(days=1)) & (s.index < now.normalize())]
    dd = (today.mean() - yday.mean()) if not today.empty and not yday.empty else None
    spark = s[s.index >= now - pd.Timedelta(hours=6)].resample("5min").mean()
    state = "bad" if last < -80 else "warn" if last > 80 else "ok"
    sub = f"průměr dnes {today.mean():+,.0f} MW".replace(",", " ") if not today.empty else ""
    return [tile("ceps_imb", "power", "ČEPS odchylka", last, "MW", "+,.0f", dd, None, spark=_spark(spark, 72),
                 href="/elektrina", asof=s.index[-1].strftime("%d.%m.%Y %H:%M"), sub=sub, state=state)]


def _simple_tiles(keys, sub_fn, n=30, dfmt=None):
    out = []
    for key in keys:
        group, label, unit, fmt, dunit, href, fn = KEY_NUMBERS[key]
        s = fn(None)
        v, dd, ww, last = _changes(s)
        out.append(tile(key, group, label, v, unit, fmt, dd, ww, dd_unit=dunit, spark=_spark(s, n), href=href,
                        asof=_asof_day(last) if last is not None else None,
                        sub=sub_fn(last) if last is not None else "data se dotahují", dfmt=dfmt))
    return out


@builder()
def _storage_tiles():
    return _simple_tiles(["stor_EU", "stor_CZ", "stor_DE"], lambda d: f"plynárenský den {_asof_day(d)}", 45, "+,.2f")


@builder()
def _flow_tiles():
    return (_simple_tiles(["flow_no"], lambda d: "GASSCO nominace výstupů NCS")
            + _simple_tiles(["lng_eu"], lambda d: "GIE ALSI, součet terminálů"))


@builder()
def _weather_tiles():
    tiles = []
    for region in ("CZ", "DE"):
        key = f"temp_{region}"
        group, label, unit, fmt, dunit, href, _fn = KEY_NUMBERS[key]
        s, fc, last = temp_anomaly(region)
        v, dd, ww, _ = _changes(s)
        sub = (f"předpověď 7 dní {fc:+.1f} °C".replace(".", ",") if fc is not None else "") if last is not None \
            else "data se dotahují"
        state = None if v is None else ("warn" if abs(v) >= 3 else None)
        tiles.append(tile(key, group, label, v, unit, fmt, dd, ww, spark=_spark(s), href=href,
                          asof=_asof_day(last) if last is not None else None, sub=sub, state=state))
    return tiles


@builder()
def _hydro_tile():
    dev, fill = hydro_no()
    if fill.empty:
        return [tile("hydro_no", "nordic", "Hydro NO vs. medián", None, "p.b.", href="/nordic?tab=hydro",
                     sub="data se dotahují")]
    _, _, ww, last = _changes(fill)
    v = dev.dropna().iloc[-1] if not dev.dropna().empty else None
    wk = last.isocalendar()
    sub = f"plnění {fill.iloc[-1]:.1f} %".replace(".", ",")
    if ww is not None:
        sub += f", w/w {ww:+.1f} p.b.".replace(".", ",")
    return [tile("hydro_no", "nordic", "Hydro NO vs. medián", v, "p.b.", "+,.1f", None, None,
                 spark=_spark(dev, 26), href="/nordic?tab=hydro", asof=f"týden {wk[1]}/{wk[0]}", sub=sub,
                 state=None if v is None else ("bad" if v < -10 else "warn" if v < -5 else None))]


@builder()
def _cot_tile():
    s = cot_mm_net()
    v, _, ww, last = _changes(s, lag_w=7)
    return [tile("cot_ttf", "market", "CoT TTF fondy net", v, "TWh", "+,.1f", None, ww, spark=_spark(s, 26),
                 href="/cot?tab=ttf", asof=_asof_day(last) if last is not None else None,
                 sub=f"report {_asof_day(last)} · Investment Funds" if last is not None else "data se dotahují")]


# ── události (feed) ─────────────────────────────────────────────────────

def _ev(eid, t, cat, title, text="", href="/", sev="info") -> dict:
    return {"id": eid, "t": pd.Timestamp(t).tz_convert("UTC").isoformat() if pd.Timestamp(t).tzinfo
            else pd.Timestamp(t).tz_localize(TZ).tz_convert("UTC").isoformat(),
            "cat": cat, "title": title, "text": text, "href": href, "sev": sev}


def _fmt_d(ts) -> str:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(TZ)
    d = ts.strftime("%d.%m.") if ts.year == _now().year else ts.strftime("%d.%m.%Y")
    return f"{d} {ts:%H:%M}" if (ts.hour or ts.minute) else d


def _mw(v) -> str:
    return f"{v:,.0f} MW".replace(",", " ")


@builder()
def _outage_events():
    from data.outages import OUTAGES_PATH, OUTAGES_SNAPSHOTS_DIR
    if not os.path.exists(OUTAGES_PATH):
        return []
    cur = pd.read_parquet(OUTAGES_PATH)
    snaps = sorted(f for f in os.listdir(OUTAGES_SNAPSHOTS_DIR) if f.endswith(".parquet")) \
        if os.path.isdir(OUTAGES_SNAPSHOTS_DIR) else []
    today = _now().strftime("%Y-%m-%d")
    prev_files = [f for f in snaps if f.removesuffix(".parquet") < today]
    if not prev_files:
        return []
    prev = pd.read_parquet(os.path.join(OUTAGES_SNAPSHOTS_DIR, prev_files[-1]))
    now = pd.Timestamp.now(tz="UTC")
    key = ["country", "production_resource_name", "start"]
    cur = cur[cur["country"].isin(OUTAGE_COUNTRIES) & (cur["end"] >= now) & (cur["unavail_mw"] >= OUTAGE_MIN_MW)]
    if "docstatus" in cur:
        cur = cur[cur["docstatus"].fillna("") != "Cancelled"]
    prev = prev[prev["country"].isin(OUTAGE_COUNTRIES)].drop_duplicates(key, keep="last")
    cur = cur.drop_duplicates(key, keep="last")
    m = cur.merge(prev[key + ["end", "unavail_mw"]], on=key, how="left", suffixes=("", "_prev"), indicator="merge_")
    m["typ"] = np.where(m["businesstype"].astype(str).str.contains("Planned"), "plánovaná", "neplánovaná")
    out = []

    def sev_of(typ, mw):
        return "bad" if typ == "neplánovaná" and mw >= 300 else ("warn" if typ == "neplánovaná" else "info")

    # nové: navazující kusy téže odstávky (ENTSO-E je dělí podle výkonu) → 1 událost
    new = m[m["merge_"] == "left_only"]
    for (country, name, typ), g in new.groupby(["country", "production_resource_name", "typ"]):
        cc = OUTAGE_COUNTRIES[country]
        mw = g["unavail_mw"].max()
        href = "/odstavky-ceps" if country == "CZ" else "/elektrina?tab=vyroba"
        out.append(_ev(f"out:{country}:{name}:{pd.Timestamp(g['start'].min()).isoformat()}", now, "Odstávka",
                       f"{cc}: nová {typ} odstávka {name} −{_mw(mw)}",
                       f"{g['plant_type'].iloc[0]}, {_fmt_d(g['start'].min())} – {_fmt_d(g['end'].max())}",
                       href, sev_of(typ, mw)))
    # změny: jen odstávky do 90 dní (vzdálené revize plánů jsou šum)
    chg = m[(m["merge_"] == "both") & (m["start"] <= now + pd.Timedelta(days=90))]
    for r in chg.itertuples(index=False):
        d_mw = r.unavail_mw - r.unavail_mw_prev
        d_end = (pd.Timestamp(r.end) - pd.Timestamp(r.end_prev)) if pd.notna(r.end_prev) else pd.Timedelta(0)
        parts = []
        if abs(d_mw) >= 50:
            parts.append(f"{_mw(r.unavail_mw_prev)} → {_mw(r.unavail_mw)}")
        if abs(d_end) >= pd.Timedelta(hours=12):
            parts.append(f"konec {_fmt_d(r.end_prev)} → {_fmt_d(r.end)}")
        if not parts:
            continue
        href = "/odstavky-ceps" if r.country == "CZ" else "/elektrina?tab=vyroba"
        out.append(_ev(f"outchg:{r.country}:{r.production_resource_name}:{pd.Timestamp(r.start).isoformat()}:"
                       f"{r.unavail_mw:.0f}:{pd.Timestamp(r.end).isoformat()}", now, "Odstávka",
                       f"{OUTAGE_COUNTRIES[r.country]}: změna odstávky {r.production_resource_name}",
                       f"{', '.join(parts)} ({r.typ})", href, sev_of(r.typ, r.unavail_mw)))
    return out


@builder(live=True)
def _umm_events():
    path = history_path("gassco_umm.parquet")
    if not os.path.exists(path):
        return []
    df = pd.read_parquet(path)
    if df.empty:
        return []
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=EVENTS_KEEP_DAYS)
    df = df[pd.to_datetime(df["publicationDateTime"], utc=True) >= since]
    out = []
    for r in df.itertuples(index=False):
        unplanned = str(r.unavailabilityType).lower().startswith("unplan")
        cap = r.unavailableCapacity
        status = str(r.eventStatus)
        sev = "bad" if unplanned and (cap or 0) >= 10 else ("warn" if unplanned else "info")
        title = (f"GASSCO: {'neplánovaná' if unplanned else 'plánovaná'} nedostupnost {r.affectedAsset}"
                 f" −{cap:,.1f} mcm/d".replace(",", " ").replace(".", ","))
        if status.lower() != "active":
            title += f" ({status})"
        text = f"{_fmt_d(r.eventStart)} – {_fmt_d(r.eventStop)}"
        if r.unavailabilityReason:
            text += f" · {r.unavailabilityReason}"
        rev = int(r.revision) if pd.notna(r.revision) else 0
        out.append(_ev(f"umm:{r.messageId}:{rev}", r.publicationDateTime, "GASSCO UMM", title, text,
                       "/plyn?tab=gassco", sev))
    return out


@builder()
def _price_alerts():
    out = []
    for cc in ("CZ", "DE"):
        d = daily_da(cc, days=45)
        if d.empty or "base" not in d or d["base"].dropna().shape[0] < 15:
            continue
        s = d["base"].dropna()
        hist, last = s.iloc[:-1].tail(30), s.iloc[-1]
        mu, sd = hist.mean(), hist.std()
        if sd and abs(last - mu) > 2 * sd:
            day = s.index[-1]
            out.append(_ev(f"price:{cc}:{day:%Y-%m-%d}", pd.Timestamp(day).tz_localize(TZ) - pd.Timedelta(hours=10),
                           "Cena", f"DA {cc} base {last:,.1f} EUR/MWh mimo pásmo".replace(",", " ").replace(".", ","),
                           f"dodávka {day:%d.%m.}, průměr 30 dní {mu:.1f} ± {2 * sd:.1f} (2σ)".replace(".", ","),
                           "/elektrina?tab=dap" if cc == "CZ" else "/trh-eu?tab=ceny",
                           "warn"))
    return out


@builder()
def _weather_alerts():
    out = []
    for region in ("CZ", "DE"):
        s, fc, last = temp_anomaly(region)
        if fc is not None and abs(fc) >= 3:
            day = _now().normalize()
            out.append(_ev(f"temp:{region}:{day:%Y-%m-%d}", _now(), "Počasí",
                           f"{region}: předpověď na 7 dní {fc:+.1f} °C vs. normál".replace(".", ","),
                           "ECMWF IFS, průměr denních teplot", f"/pocasi?tab=teplota&region={region}",
                           "warn"))
    return out


@builder()
def _gas_entry_exit_events():
    """Nesrovnalosti exit země A ≠ entry země B na hranicích ve střední Evropě
    (core/gasflow.py, poslední 2 dny ENTSO-G, max 5 největších za den)."""
    from core import gasflow as gf
    out = []
    for day in gf.available_days()[-2:]:
        m = gf.mismatches(day)
        m = m[(m["level"] == "hranice") & m["from"].isin(gf.MAIN) & m["to"].isin(gf.MAIN)]
        seen = set()
        for r in m.to_dict("records"):
            pair = (r["from"], r["to"])
            if pair in seen or len(seen) >= 5:
                continue
            seen.add(pair)
            ind = "fyzický tok" if r["indicator"] == "Physical Flow" else "alokace"
            n = lambda v, sign="": f"{v:{sign},.0f}".replace(",", " ").replace("-", "−")  # noqa: E731
            out.append(_ev(f"ee:{day}:{r['from']}-{r['to']}", pd.Timestamp(day).tz_localize(TZ) + pd.Timedelta(hours=34),
                           "Plyn", f"Plyn {r['from']}→{r['to']}: exit ≠ entry o {n(r['diff'], '+')} GWh/d ({r['rel']:.0f} %)",
                           f"{day:%d.%m.}, {ind}: exit {r['from']} {n(r['exit'])} vs. entry {r['to']} {n(r['entry'])} "
                           "GWh/d (ENTSO-G)",
                           f"/plyn?tab=entry-exit&to={day}", "warn"))
    return out


@builder(live=True)
def _imbalance_alerts():
    s = _ceps_imbalance(hours=3)
    if s.empty:
        return []
    h = s[s.index >= s.index[-1] - pd.Timedelta(hours=1)].mean()
    if abs(h) < 150:
        return []
    t = s.index[-1].floor("h")
    return [_ev(f"imb:{t:%Y-%m-%dT%H}", s.index[-1], "ČEPS",
                f"ČEPS: {'deficit' if h < 0 else 'přebytek'} — průměr poslední hodiny {h:+,.0f} MW".replace(",", " "),
                "systémová odchylka ČR", "/elektrina", "bad" if h < 0 else "warn")]


# ── sestavení a uložení ─────────────────────────────────────────────────

def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def merge_events(old: list, new: list, now=None) -> list:
    """Nové události přidat (čas = první zjištění / publikace), staré držet
    EVENTS_KEEP_DAYS dní, max EVENTS_MAX, seřazené od nejnovější."""
    now = now or pd.Timestamp.now(tz="UTC")
    by_id = {e["id"]: e for e in old}
    for e in new:
        if e["id"] not in by_id:
            by_id[e["id"]] = e
    cutoff = (now - pd.Timedelta(days=EVENTS_KEEP_DAYS)).isoformat()
    evs = [e for e in by_id.values() if e["t"] >= cutoff]
    evs.sort(key=lambda e: e["t"], reverse=True)
    return evs[:EVENTS_MAX]


def build_overview(live_only: bool = False) -> dict:
    root = history_root()
    root.mkdir(parents=True, exist_ok=True)
    ov_path, ev_path = str(root / OVERVIEW_FILE), str(root / EVENTS_FILE)
    t0 = time.monotonic()
    tiles, events, errors = [], [], []
    for fn, live in _BUILDERS:
        if live_only and not live:
            continue
        try:
            res = fn() or []
        except Exception as e:
            log.warning("přehled: %s selhal: %s: %s", fn.__name__, type(e).__name__, e)
            errors.append(f"{fn.__name__}: {type(e).__name__}: {e}")
            continue
        for item in res:
            (events if "cat" in item else tiles).append(item)
    with open(str(root / ".overview.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        old = _read_json(ov_path, {})
        if live_only and old.get("tiles"):
            fresh = {t["key"]: t for t in tiles}
            tiles = [fresh.pop(t["key"], t) for t in old["tiles"]] + list(fresh.values())
        evs = merge_events(_read_json(ev_path, []), events)
        _write_json(ev_path, evs)
        data = {"built": pd.Timestamp.now(tz="UTC").isoformat(), "full_built": old.get("full_built"),
                "tiles": tiles, "events": evs[:120], "errors": errors if not live_only else old.get("errors", []),
                "secs": round(time.monotonic() - t0, 2)}
        if not live_only:
            data["full_built"] = data["built"]
        _write_json(ov_path, data)
    log.info("přehled dne: %d dlaždic, %d událostí, %.1f s", len(tiles), len(evs), time.monotonic() - t0)
    return data


def overview_path() -> str:
    return str(history_root() / OVERVIEW_FILE)


def load_overview() -> dict:
    return _read_json(overview_path(), {})
