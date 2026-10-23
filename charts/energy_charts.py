"""Grafy Energy-Charts (Fraunhofer ISE): frekvence sítě, přeshraniční
obchod/toky, výroba podle zdroje + day-ahead prognóza, instalovaný výkon.

Vstupy jsou tidy data z data/energy_charts.py::load_energy_charts
(timestamp, series_id, series_name, value, unit). Znaménko přeshraničních
řad (cbet, cbpf i public_power cross_border_electricity_trading): kladné =
import, záporné = export — ověřeno na datech CZ (výroba + obchod ≈ zatížení).
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import PSR_TYPES

TZ = "Europe/Prague"

# series_id Energy-Charts → PSR kód (sdílená paleta a české názvy s ENTSO-E grafy)
EC_PSR = {
    "biomass": "B01", "fossil_brown_coal_lignite": "B02", "fossil_coal_derived_gas": "B03",
    "fossil_gas": "B04", "fossil_hard_coal": "B05", "fossil_oil": "B06", "geothermal": "B09",
    "hydro_pumped_storage": "B10", "hydro_run_of_river": "B11", "hydro_water_reservoir": "B12",
    "nuclear": "B14", "other_renewables": "B15", "solar": "B16", "solar_ac": "B16",
    "solar_dc": "B16", "waste": "B17", "wind_offshore": "B18", "wind_onshore": "B19",
    "others": "B20",
}
# pořadí ve stacku: základní zatížení dole, proměnlivé OZE nahoře
STACK_ORDER = [
    "nuclear", "fossil_brown_coal_lignite", "fossil_hard_coal", "fossil_coal_derived_gas",
    "fossil_gas", "fossil_oil", "biomass", "waste", "geothermal", "hydro_run_of_river",
    "hydro_water_reservoir", "hydro_pumped_storage", "other_renewables", "others",
    "wind_offshore", "wind_onshore", "solar", "solar_ac", "solar_dc",
]
NEIGHBOR_NAMES = {
    "austria": "Rakousko", "germany": "Německo", "poland": "Polsko", "slovakia": "Slovensko",
    "hungary": "Maďarsko", "france": "Francie", "netherlands": "Nizozemsko", "belgium": "Belgie",
    "switzerland": "Švýcarsko", "denmark": "Dánsko", "sweden": "Švédsko", "norway": "Norsko",
    "czech_republic": "Česko", "italy": "Itálie", "slovenia": "Slovinsko", "luxembourg": "Lucembursko",
    "sum": "Saldo",
}
NEIGHBOR_COLORS = ["#1565C0", "#E53935", "#43A047", "#FB8C00", "#8E24AA", "#00ACC1",
                   "#6D4C41", "#3949AB", "#F4511E", "#00897B", "#7CB342", "#5E35B1"]

RESOLUTIONS = {"15min": "15 min", "1h": "hodina", "1D": "den"}
_LAYOUT = dict(template="plotly_white", hovermode="x unified",
               legend=dict(orientation="h", y=-0.18, xanchor="center", x=0.5),
               margin=dict(l=60, r=20, t=60, b=80))


def psr_name_color(series_id: str, fallback_name: str = None) -> tuple:
    code = EC_PSR.get(series_id)
    if code in PSR_TYPES:
        return PSR_TYPES[code]
    return (fallback_name or series_id, "#90A4AE")


def auto_resolution(d_from, d_to) -> str:
    days = (pd.Timestamp(d_to) - pd.Timestamp(d_from)).days + 1
    return "15min" if days <= 14 else "1h" if days <= 120 else "1D"


def to_wide(df: pd.DataFrame, resolution: str = None) -> pd.DataFrame:
    """Tidy → wide (index čas v Praze, sloupce series_id), volitelně
    zprůměrované na resolution (15min/1h/1D). Už wide vstup (výsledek
    data.energy_charts.load_energy_charts_wide — agregovaný při čtení) se
    vrací beze změny."""
    if df is None or df.empty:
        return pd.DataFrame()
    if "timestamp" not in df.columns and isinstance(df.index, pd.DatetimeIndex):
        return df
    d = df[["timestamp", "series_id", "value"]].copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True).dt.tz_convert(TZ)
    d["series_id"] = d["series_id"].astype(str)
    w = d.pivot_table(index="timestamp", columns="series_id", values="value", aggfunc="mean").sort_index()
    w.columns.name = None
    if resolution and resolution != "15min":
        w = w.resample(resolution).mean().dropna(how="all")
    return w


def _title(text: str, sub: str = "") -> str:
    return text + (f"  <span style='font-size:11px;color:#888'>{sub}</span>" if sub else "")


# ── frekvence ───────────────────────────────────────────────────────────

def fig_frequency(df: pd.DataFrame, resolution: str = None, height: int = 420) -> go.Figure:
    """5min průměry frekvence (DE-Freiburg = synchronní oblast kontinentální
    Evropy). Pro delší rozsahy hodinový/denní průměr + pásmo min–max."""
    w = to_wide(df)
    if w.empty:
        return None
    s = w.iloc[:, 0].dropna()
    fig = go.Figure()
    if resolution and resolution not in ("15min", "5min"):
        g = s.resample(resolution)
        mean, lo, hi = g.mean().dropna(), g.min().dropna(), g.max().dropna()
        fig.add_trace(go.Scatter(x=hi.index, y=hi.values, mode="lines", line=dict(width=0),
                                 name="max (5min)", showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=lo.index, y=lo.values, mode="lines", line=dict(width=0),
                                 fill="tonexty", fillcolor="rgba(21,101,192,.18)", name="min–max (5min)",
                                 hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=mean.index, y=mean.values, mode="lines", name=f"průměr ({RESOLUTIONS[resolution]})",
                                 line=dict(color="#1565C0", width=1.6),
                                 hovertemplate="%{y:.3f} Hz<extra></extra>"))
    else:
        fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines", name="frekvence (5min průměr)",
                                 line=dict(color="#1565C0", width=1.2),
                                 hovertemplate="%{y:.3f} Hz<extra></extra>"))
    fig.add_hline(y=50.0, line=dict(color="#78909C", width=1, dash="dot"))
    last = s.index.max()
    fig.update_layout(title=_title("Frekvence sítě [Hz] — kontinentální Evropa (DE-Freiburg)",
                                   f"poslední data: {last:%d.%m.%Y %H:%M}"),
                      height=height, yaxis=dict(title="Hz", tickformat=".2f"), **_LAYOUT)
    return fig


def frequency_stats(df: pd.DataFrame) -> dict:
    w = to_wide(df)
    if w.empty:
        return {}
    s = w.iloc[:, 0].dropna()
    dev = (s - 50.0) * 1000
    return {"mean": s.mean(), "min": s.min(), "max": s.max(), "std_mhz": dev.std(),
            "share_out_50": float((dev.abs() > 50).mean() * 100), "n": len(s),
            "last": s.index.max()}


def fig_frequency_hist(df: pd.DataFrame, height: int = 340) -> go.Figure:
    w = to_wide(df)
    if w.empty:
        return None
    dev = ((w.iloc[:, 0].dropna() - 50.0) * 1000).round(1)
    fig = go.Figure(go.Histogram(x=dev.values, nbinsx=80, marker_color="#1565C0", opacity=.85,
                                 name="odchylka", hovertemplate="%{x} mHz: %{y}×<extra></extra>"))
    fig.add_vline(x=0, line=dict(color="#78909C", dash="dot"))
    fig.update_layout(title="Rozdělení odchylky od 50 Hz [mHz] (5min průměry)", height=height,
                      xaxis=dict(title="mHz"), yaxis=dict(title="počet 5min intervalů"),
                      bargap=0.02, **{k: v for k, v in _LAYOUT.items() if k != "hovermode"})
    return fig


# ── přeshraniční obchod a toky ──────────────────────────────────────────

def fig_crossborder(df: pd.DataFrame, kind: str, country_name: str, resolution: str,
                    height: int = 440) -> go.Figure:
    """cbet (obchodní výměny) / cbpf (fyzické toky) po sousedech — skládané
    plochy + saldo. Hodnoty v MW (API vrací GW)."""
    w = to_wide(df, resolution)
    if w.empty:
        return None
    w = w * 1000.0
    label = "Obchodní výměny (cbet)" if kind == "cbet" else "Fyzické toky (cbpf)"
    fig = go.Figure()
    neighbors = [c for c in w.columns if c != "sum"]
    for i, c in enumerate(neighbors):
        fig.add_trace(go.Bar(x=w.index, y=w[c], name=NEIGHBOR_NAMES.get(c, c),
                             marker_color=NEIGHBOR_COLORS[i % len(NEIGHBOR_COLORS)], marker_line_width=0,
                             hovertemplate="%{y:,.0f} MW"))
    net = w["sum"] if "sum" in w.columns else w[neighbors].sum(axis=1)
    fig.add_trace(go.Scatter(x=w.index, y=net, name="Saldo", mode="lines",
                             line=dict(color="#263238", width=2), hovertemplate="<b>%{y:,.0f} MW</b>"))
    fig.update_layout(title=_title(f"{country_name} — {label} [MW]",
                                   f"kladné = import, záporné = export · {RESOLUTIONS.get(resolution, resolution)}"),
                      barmode="relative", bargap=0, height=height, yaxis=dict(title="MW"), **_LAYOUT)
    return fig


def crossborder_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Energie za období po sousedech [GWh]: import, export, saldo
    (vstup v nativním 15min rozlišení)."""
    w = to_wide(df)
    if w.empty:
        return pd.DataFrame()
    # 15min hodnoty v GW → GWh = GW × 0.25 h
    rows = []
    for c in w.columns:
        s = w[c].dropna()
        rows.append({"soused": NEIGHBOR_NAMES.get(c, c), "import_GWh": s.clip(lower=0).sum() * 0.25,
                     "export_GWh": -s.clip(upper=0).sum() * 0.25, "saldo_GWh": s.sum() * 0.25})
    out = pd.DataFrame(rows)
    return out.sort_values("soused", key=lambda x: x == "Saldo").reset_index(drop=True)


# ── výroba ──────────────────────────────────────────────────────────────

LINE_SERIES = {"load": ("Zatížení", "#263238", "solid"),
               "residual_load": ("Reziduální zatížení", "#78909C", "dash"),
               "cross_border_electricity_trading": ("Přeshraniční obchod (+import)", "#E53935", "dot")}


def fig_public_power(df: pd.DataFrame, country_name: str, resolution: str, height: int = 480) -> go.Figure:
    w = to_wide(df, resolution)
    if w.empty:
        return None
    fig = go.Figure()
    for sid in [c for c in STACK_ORDER if c in w.columns] + \
               sorted(c for c in w.columns if c in EC_PSR and c not in STACK_ORDER):
        s = w[sid]
        if s.fillna(0).abs().sum() == 0:
            continue
        name, color = psr_name_color(sid)
        fig.add_trace(go.Scatter(x=w.index, y=s.clip(lower=0), name=name, mode="lines", stackgroup="gen",
                                 line=dict(width=0.5, color=color), fillcolor=color,
                                 hovertemplate="%{y:,.0f} MW"))
    if "hydro_pumped_storage_consumption" in w.columns and w["hydro_pumped_storage_consumption"].abs().sum() > 0:
        s = -w["hydro_pumped_storage_consumption"].abs()
        fig.add_trace(go.Scatter(x=w.index, y=s, name="Čerpání PVE", mode="lines", stackgroup="cons",
                                 line=dict(width=0.5, color="#4DD0E1"), fillcolor="#4DD0E1",
                                 hovertemplate="%{y:,.0f} MW"))
    for sid, (name, color, dash) in LINE_SERIES.items():
        if sid in w.columns:
            fig.add_trace(go.Scatter(x=w.index, y=w[sid], name=name, mode="lines",
                                     line=dict(color=color, width=2 if sid == "load" else 1.5, dash=dash),
                                     hovertemplate="<b>%{y:,.0f} MW</b>"))
    fig.update_layout(title=_title(f"{country_name} — výroba podle zdroje [MW] (Energy-Charts)",
                                   RESOLUTIONS.get(resolution, resolution)),
                      height=height, yaxis=dict(title="MW"), **_LAYOUT)
    return fig


def fig_forecast_vs_actual(actual: pd.DataFrame, forecast: pd.DataFrame, country_name: str,
                           resolution: str, height: int = 420) -> go.Figure:
    """Day-ahead prognóza (solar, wind, load) vs. skutečnost z public_power."""
    fw = to_wide(forecast, resolution)
    if fw.empty:
        return None
    aw = to_wide(actual, resolution)
    pairs = [c for c in ("load", "solar", "wind_onshore", "wind_offshore") if c in fw.columns]
    if not pairs:
        return None
    fig = make_subplots(rows=len(pairs), cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=[{"load": "Zatížení", "solar": "Solární", "wind_onshore": "Vítr onshore",
                                         "wind_offshore": "Vítr offshore"}[c] + " [MW]" for c in pairs])
    for i, c in enumerate(pairs, start=1):
        color = "#263238" if c == "load" else psr_name_color(c)[1]
        if c in aw.columns:
            fig.add_trace(go.Scatter(x=aw.index, y=aw[c], name="skutečnost", mode="lines",
                                     line=dict(color=color, width=1.8), legendgroup="a", showlegend=i == 1,
                                     hovertemplate="%{y:,.0f} MW"), row=i, col=1)
        fig.add_trace(go.Scatter(x=fw.index, y=fw[c], name="prognóza day-ahead", mode="lines",
                                 line=dict(color=color, width=1.5, dash="dash"), legendgroup="f",
                                 showlegend=i == 1, hovertemplate="%{y:,.0f} MW"), row=i, col=1)
    layout = dict(_LAYOUT)
    layout["legend"] = dict(orientation="h", y=-0.08, xanchor="center", x=0.5)
    fig.update_layout(title=_title(f"{country_name} — prognóza vs. skutečnost",
                                   RESOLUTIONS.get(resolution, resolution)),
                      height=max(height, 260 * len(pairs)), **layout)
    return fig


def forecast_errors(actual: pd.DataFrame, forecast: pd.DataFrame) -> pd.DataFrame:
    """MAE / bias / MAPE prognózy po řadách (na společných 15min časech)."""
    fw, aw = to_wide(forecast), to_wide(actual)
    rows = []
    for c in fw.columns:
        if c not in aw.columns:
            continue
        j = pd.concat([aw[c], fw[c]], axis=1, keys=["a", "f"]).dropna()
        if j.empty:
            continue
        err = j["f"] - j["a"]
        denom = j["a"].abs().where(j["a"].abs() > 1)
        rows.append({"řada": {"load": "Zatížení", "solar": "Solární", "wind_onshore": "Vítr onshore",
                               "wind_offshore": "Vítr offshore"}.get(c, c),
                     "MAE_MW": err.abs().mean(), "bias_MW": err.mean(),
                     "MAPE_pct": (err.abs() / denom).mean() * 100, "n": len(j)})
    return pd.DataFrame(rows)


# ── instalovaný výkon ───────────────────────────────────────────────────

def installed_wide(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    d["year"] = pd.to_datetime(d["timestamp"], utc=True).dt.tz_convert(TZ).dt.year
    w = d.pivot_table(index="year", columns="series_id", values="value", aggfunc="last").sort_index()
    w.columns.name = None
    return w


def fig_installed(df: pd.DataFrame, country_name: str, height: int = 460) -> go.Figure:
    w = installed_wide(df)
    if w.empty:
        return None
    fig = go.Figure()
    for sid in [c for c in STACK_ORDER if c in w.columns] + \
               sorted(c for c in w.columns if c not in STACK_ORDER):
        if w[sid].fillna(0).sum() == 0:
            continue
        name, color = psr_name_color(sid)
        fig.add_trace(go.Bar(x=w.index.astype(str), y=w[sid], name=name, marker_color=color,
                             hovertemplate="%{y:,.2f} GW"))
    fig.update_layout(title=_title(f"{country_name} — instalovaný výkon [GW] (Energy-Charts, roční)"),
                      barmode="stack", height=height, yaxis=dict(title="GW"),
                      **{k: v for k, v in _LAYOUT.items() if k != "hovermode"}, hovermode="x unified")
    return fig
