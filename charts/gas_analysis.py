"""Grafy stránky Analýza toků plynu (/plyn/analyza) — nový vzhled
(charts/v2.py): titulek vlevo, legenda nad grafem, jedna osa y s jednotkou,
sezónnost = aktuální rok proti pásmu min–max předchozích 5 let.

Vstupem jsou malé agregované tabulky z core/gas_analysis.py."""
import pandas as pd
import plotly.graph_objects as go

from charts import v2
from core.gas_analysis import IND_LABELS
from core.palette import NEUTRAL, SLOTS, entity_color

KIND_COLORS = {"LNG terminály": "#0d9488", "Zásobníky": "#9c6ade", "Těžba": "#6d4c41",
               "Spotřeba / distribuce": "#7d7b76", "Ostatní": "#aeaca6", "Mimo EU (neurčeno)": "#5f6b73",
               "Norsko": "#1f3a93", "Rusko": "#8b1e3f"}
EXTRA = ["#8c564b", "#17a2b8", "#b8a200", "#9467bd", "#bc5090", "#58508d", "#3d7a3d", "#4aa3df", "#a0522d",
         "#2f9e44", "#c9a227", "#7a5195", "#ef5675", "#00876c"]
IND_STYLE = {
    "Physical Flow": dict(color=SLOTS[0], width=2.4),
    "Allocation": dict(color=SLOTS[2], width=1.4),
    "Nomination": dict(color=SLOTS[1], width=1.4, dash="dot"),
    "Renomination": dict(color=SLOTS[6], width=1.4, dash="dash"),
    "Firm Technical": dict(color=NEUTRAL, width=1.8, shape="hv"),
    "Firm Booked": dict(color=SLOTS[3], width=1.6, shape="hv"),
    "Interruptible Booked": dict(color=SLOTS[4], width=1.2, shape="hv", dash="dot"),
}
INT_COLORS = {"Planned interruption of firm capacity": SLOTS[3],
              "Unplanned interruption of firm capacity": SLOTS[7],
              "Actual interruption of interruptible capacity": SLOTS[4]}
_XFMT = {"day": "%d. %m. %Y", "week": "týden od %d. %m. %Y", "month": "%m/%Y", "year": "%Y"}


def _base(cat: str) -> str:
    return cat.split(" · ")[0]


def cat_color(cat: str, i: int) -> str:
    b = _base(cat)
    return KIND_COLORS.get(b) or entity_color(b) or EXTRA[i % len(EXTRA)]


def _x(idx):
    return pd.DatetimeIndex(idx).strftime("%Y-%m-%d")


def _fmt(unit: str) -> str:
    return ",.0f" if unit == "GWh" else ",.1f"


def _time_axis(fig, grain: str):
    fig.update_xaxes(tickformat=None, dtick=None, ticklabelmode="period" if grain in ("month", "year") else "instant",
                     hoverformat=_XFMT.get(grain, "%d. %m. %Y"))
    return fig


def fig_country(agg: pd.DataFrame, grain: str, unit: str, title: str, net_label: str = None) -> go.Figure:
    """Skládané sloupce podle kategorie (výstupy záporně, šrafované) + čára čisté bilance."""
    fig = go.Figure()
    if agg is None or agg.empty:
        return fig
    x, fmt = _x(agg.index), _fmt(unit)
    bases = list(dict.fromkeys(_base(c) for c in agg.columns))
    for c in agg.columns:
        exit_ = c.endswith(" · výstup")
        col = cat_color(c, bases.index(_base(c)))
        fig.add_trace(go.Bar(x=x, y=agg[c].round(3), name=c, legendgroup=_base(c),
                             marker=dict(color=col, line=dict(width=0),
                                         pattern=dict(shape="/", fgcolor="rgba(255,255,255,0.55)") if exit_ else None),
                             hovertemplate=f"{c}: %{{y:{fmt}}} {unit}<extra></extra>"))
    if net_label:
        net = agg.sum(axis=1, min_count=1)
        fig.add_trace(go.Scatter(x=x, y=net.round(3), name=net_label, mode="lines",
                                 line=dict(color="#1f1f1d", width=2, shape="hv" if grain != "day" else "linear"),
                                 hovertemplate=f"<b>{net_label}: %{{y:{fmt}}} {unit}</b><extra></extra>"))
    v2._style(fig, title, unit, height=460)
    fig.update_layout(barmode="relative", bargap=0.08 if grain == "day" else 0.2)
    fig.update_yaxes(zeroline=True, zerolinecolor="#b9b7b1")
    return _time_axis(fig, grain)


def fig_point(agg: pd.DataFrame, grain: str, unit: str, title: str, flow: str = None,
              util: pd.Series = None) -> go.Figure:
    """Hlavní tok sloupci, ostatní toky a kapacity čarami; využití kapacity v hoveru."""
    fig = go.Figure()
    if agg is None or agg.empty:
        return fig
    x, fmt = _x(agg.index), _fmt(unit)
    for c in agg.columns:
        s = agg[c]
        if s.notna().sum() == 0:
            continue
        name = IND_LABELS.get(c, c)
        if c == flow:
            cd = util.reindex(agg.index).round(1) if util is not None else None
            ht = (f"<b>{name}: %{{y:{fmt}}} {unit}</b>" +
                  (" · využití %{customdata:.0f} %" if cd is not None else "") + "<extra></extra>")
            fig.add_trace(go.Bar(x=x, y=s.round(3), name=name, customdata=cd, hovertemplate=ht,
                                 marker=dict(color=SLOTS[0], opacity=0.85, line=dict(width=0))))
            continue
        st = dict(IND_STYLE.get(c, dict(color=NEUTRAL, width=1.2)))
        if grain != "day":
            st["shape"] = "hv"
        fig.add_trace(go.Scatter(x=x, y=s.round(3), name=name, mode="lines", line=st, connectgaps=False,
                                 hovertemplate=f"{name}: %{{y:{fmt}}} {unit}<extra></extra>"))
    v2._style(fig, title, unit, height=460)
    fig.update_layout(bargap=0.08 if grain == "day" else 0.2)
    fig.update_yaxes(rangemode="tozero")
    return _time_axis(fig, grain)


def fig_season(df: pd.DataFrame, mode: str, ref_year: int, unit: str, title: str) -> go.Figure:
    """df year, x, v (core/gas_analysis.seasonal) → aktuální rok proti pásmu 5 let."""
    fig = go.Figure()
    if df is None or df.empty:
        return fig
    d = df.copy()
    if mode == "month":
        d["t"] = pd.to_datetime({"year": ref_year, "month": d["x"], "day": 1})
    else:
        d = d[~((d["x"].dt.month == 2) & (d["x"].dt.day == 29))]
        d["t"] = pd.to_datetime({"year": ref_year, "month": d["x"].dt.month, "day": d["x"].dt.day})
    wide = d.pivot_table(index="t", columns="year", values="v", aggfunc="mean").sort_index()
    prev = [y for y in wide.columns if ref_year - 5 <= y < ref_year]
    sz = {"year": ref_year, "years": prev, "x": wide.index,
          "cur": wide[ref_year] if ref_year in wide else pd.Series(dtype=float),
          "last": wide[ref_year - 1] if ref_year - 1 in wide else pd.Series(dtype=float)}
    if len(prev) >= 2:
        sz.update(lo=wide[prev].min(axis=1), hi=wide[prev].max(axis=1), mean=wide[prev].mean(axis=1))
    fmt = ".0f" if unit == "GWh" else ".1f"
    for tr in v2._band_traces(sz, unit=unit, fmt=fmt):
        if mode == "month" and tr.name in (str(ref_year), str(ref_year - 1)):
            tr.update(mode="lines+markers", marker=dict(size=5))
        fig.add_trace(tr)
    v2._end_label(fig, sz, unit, fmt)
    v2._style(fig, title, unit, height=400)
    if mode == "month":
        fig.update_xaxes(hoverformat="%B")
    else:
        fig.update_xaxes(hoverformat="%d. %m.")
    return fig


def fig_compare(cmp: pd.DataFrame, unit: str, label_a: str, label_b: str, title: str) -> go.Figure:
    """Vodorovné sloupce A vs B po kategoriích (celkem/bilance dole)."""
    fig = go.Figure()
    if cmp is None or cmp.empty:
        return fig
    c = cmp.iloc[::-1]
    fmt = _fmt(unit)
    y = [str(i) for i in c.index]
    fig.add_trace(go.Bar(y=y, x=c["B"].round(3), name=f"B: {label_b}", orientation="h",
                         marker=dict(color="#aeaca6"), hovertemplate=f"B: %{{x:{fmt}}} {unit}<extra></extra>"))
    fig.add_trace(go.Bar(y=y, x=c["A"].round(3), name=f"A: {label_a}", orientation="h",
                         marker=dict(color=SLOTS[0]), hovertemplate=f"A: %{{x:{fmt}}} {unit}<extra></extra>"))
    v2._style(fig, title, None, height=max(260, 70 + 34 * len(c)))
    fig.update_layout(barmode="group", hovermode="y unified", bargap=0.25)
    fig.update_xaxes(tickformat=None, dtick=None, ticklabelmode="instant", showgrid=True, gridcolor="#ececea",
                     title_text=unit, zeroline=True, zerolinecolor="#b9b7b1")
    fig.update_yaxes(title_text=None, gridcolor=None, automargin=True)
    return fig


def fig_interruptions(wide: pd.DataFrame, title: str, today=None) -> go.Figure:
    """Přerušení kapacity po dnech (GWh/d), skládané podle druhu; čára = dnešek."""
    fig = go.Figure()
    if wide is None or wide.empty:
        return fig
    x = _x(wide.index)
    for c in wide.columns:
        s = wide[c]
        if s.fillna(0).abs().sum() == 0:
            continue
        name = IND_LABELS.get(c, c)
        fig.add_trace(go.Bar(x=x, y=s.round(3), name=name, marker=dict(color=INT_COLORS.get(c, NEUTRAL), line=dict(width=0)),
                             hovertemplate=f"{name}: %{{y:,.1f}} GWh/d<extra></extra>"))
    if not fig.data:
        return fig
    v2._style(fig, title, "GWh/d", height=300)
    fig.update_layout(barmode="stack", bargap=0.05)
    if today is not None:
        t = pd.Timestamp(today).strftime("%Y-%m-%d")
        fig.add_vline(x=t, line=dict(color=v2.EVENT, width=1, dash="dot"))
        fig.add_annotation(x=t, y=1, yref="paper", text="dnes", showarrow=False, yanchor="bottom",
                           font=dict(size=11, color=v2.EVENT))
    return _time_axis(fig, "day")

