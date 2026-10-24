"""Nový vzhled (prototyp, PRESENTATION_PROPOSAL.md) — sdílené stavební kameny.

Pravidla, která drží všechny grafy nového vzhledu stejně:
- **sezónnost** = aktuální rok (silná čára, slot 1 palety) proti pásmu
  min–max předchozích 5 let (světle šedá plocha) a jejich průměru (šedá čára);
  loňský rok tenkou tmavší šedou. Osa x = data aktuálního roku (měsíce),
  ne „den v roce“.
- **small multiples** = stejné měřítko os ve všech panelech, panel nese
  název, legenda jen jednou.
- **anotace událostí** = svislá tenká čára + krátký popisek nahoře; cíle
  (např. 90 % zásobníků) vodorovně.
- jedna osa y (nikdy dvě), jednotky v názvu osy, popisek hodnoty jen na
  konci aktuální řady.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.palette import NEUTRAL, SLOTS

CUR = SLOTS[0]
BAND = "rgba(140, 138, 132, 0.26)"  # čitelné ve světlém i tmavém režimu
MEAN = "#7d7b76"
LAST = "#aeaca6"
EVENT = "#8a8780"
TZ = "Europe/Prague"


def daily(s: pd.Series, interpolate: bool = False) -> pd.Series:
    """Řada s DatetimeIndex → denní hodnoty s naivním indexem (místní den).
    interpolate=True dopočítá dny mezi body (týdenní data → sezónní pásmo)."""
    if s.empty:
        return s
    idx = pd.DatetimeIndex(s.index)
    s = pd.Series(s.values, index=idx)
    if idx.tz is not None:
        s.index = idx.tz_convert(TZ).tz_localize(None)
    s = s.groupby(s.index.normalize()).mean().sort_index()
    if interpolate and len(s) > 1:
        s = s.resample("D").mean().interpolate(limit=13, limit_area="inside")
    return s


def seasonal(s: pd.Series, year: int = None, n_years: int = 5, smooth: int = 1, interpolate: bool = False) -> dict:
    """Denní řada → {cur, last, lo, hi, mean, years} indexované daty roku `year`.
    Přestupný den se zahodí; smooth = klouzavý průměr dnů (ceny jsou hlučné)."""
    s = daily(s, interpolate).dropna()
    if s.empty:
        return {}
    year = year or int(s.index.max().year)
    if smooth > 1:
        s = s.rolling(smooth, min_periods=1).mean()
    s = s[~((s.index.month == 2) & (s.index.day == 29))]
    frame = pd.DataFrame({"v": s.values, "y": s.index.year, "md": s.index.strftime("%m-%d")})
    wide = frame.pivot_table(index="md", columns="y", values="v", aggfunc="mean")
    prev = [y for y in wide.columns if year - n_years <= y < year]
    idx = pd.to_datetime([f"{year}-{md}" for md in wide.index])
    out = {"year": year, "years": prev, "x": idx}
    out["cur"] = pd.Series(wide[year].values, index=idx) if year in wide else pd.Series(dtype=float)
    out["last"] = pd.Series(wide[year - 1].values, index=idx) if year - 1 in wide else pd.Series(dtype=float)
    if prev:
        p = wide[prev]
        out["lo"] = pd.Series(p.min(axis=1).values, index=idx)
        out["hi"] = pd.Series(p.max(axis=1).values, index=idx)
        out["mean"] = pd.Series(p.mean(axis=1).values, index=idx)
    return out


def _thin(sz: dict, step: int = 1) -> dict:
    """Menší JSON: x jako 'YYYY-MM-DD', hodnoty na 3 platná desetinná místa,
    v mřížce jen každý `step`-tý den (poslední bod aktuálního roku vždy)."""
    def f(s):
        if not isinstance(s, pd.Series) or s.empty:
            return s
        s = s.round(3)
        if step > 1:
            keep = np.zeros(len(s), dtype=bool)
            keep[::step] = True
            last = s.dropna()
            if not last.empty:
                keep[s.index.get_loc(last.index[-1])] = True
            s = s[keep]
        return s
    out = {k: f(v) for k, v in sz.items()}
    if step > 1 and "x" in sz:
        out["x"] = out["mean"].index if "mean" in out else sz["x"][::step]
    return out


def _x(idx):
    return pd.DatetimeIndex(idx).strftime("%Y-%m-%d")


def _band_traces(sz: dict, row=None, col=None, show_legend=True, unit="", fmt=".1f", step=1):
    sz = _thin(sz, step)
    tr = []
    if "lo" in sz:
        yrs = f"{min(sz['years'])}–{max(sz['years'])}" if sz["years"] else ""
        tr.append(go.Scatter(x=_x(sz["hi"].index), y=sz["hi"], mode="lines", line=dict(width=0), hoverinfo="skip",
                             showlegend=False, legendgroup="band"))
        tr.append(go.Scatter(x=_x(sz["lo"].index), y=sz["lo"], mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor=BAND, name=f"min–max {yrs}", legendgroup="band", showlegend=show_legend,
                             hovertemplate=f"min %{{y:{fmt}}} {unit}<extra></extra>"))
        tr.append(go.Scatter(x=_x(sz["mean"].index), y=sz["mean"], mode="lines", line=dict(color=MEAN, width=1.2),
                             name=f"průměr {yrs}", legendgroup="mean", showlegend=show_legend,
                             hovertemplate=f"průměr %{{y:{fmt}}} {unit}<extra></extra>"))
    if not sz.get("last", pd.Series(dtype=float)).dropna().empty:
        tr.append(go.Scatter(x=_x(sz["last"].index), y=sz["last"], mode="lines", line=dict(color=LAST, width=1.2),
                             name=str(sz["year"] - 1), legendgroup="last", showlegend=show_legend,
                             hovertemplate=f"{sz['year'] - 1}: %{{y:{fmt}}} {unit}<extra></extra>"))
    cur = sz.get("cur", pd.Series(dtype=float)).dropna()
    if not cur.empty:
        tr.append(go.Scatter(x=_x(cur.index), y=cur.values, mode="lines", line=dict(color=CUR, width=2.5),
                             name=str(sz["year"]), legendgroup="cur", showlegend=show_legend,
                             hovertemplate=f"<b>{sz['year']}: %{{y:{fmt}}} {unit}</b><extra></extra>"))
    return tr


def _end_label(fig, sz, unit, fmt, row=None, col=None):
    cur = sz.get("cur", pd.Series(dtype=float)).dropna()
    if cur.empty:
        return
    x, y = cur.index[-1].strftime("%Y-%m-%d"), cur.iloc[-1]
    txt = f"<b>{format(y, fmt).replace('.', ',')}</b> {unit}".strip()
    kw = dict(row=row, col=col) if row else {}
    fig.add_annotation(x=x, y=y, text=txt, showarrow=False, xanchor="left", xshift=6,
                       font=dict(size=12, color=CUR), **kw)


def _style(fig, title=None, unit="", height=420, legend_bottom=False):
    """Titulek vlevo nahoře, legenda pod ním (nebo pod grafem) — nikdy přes titulek."""
    legend = (dict(orientation="h", y=-0.08, yanchor="top", x=0, xanchor="left", font=dict(size=11))
              if legend_bottom else
              dict(orientation="h", y=1.0, yanchor="bottom", x=0, xanchor="left", font=dict(size=11)))
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", y=0.98, yanchor="top", font=dict(size=15)) if title else None,
        template="plotly_white", height=height, hovermode="x unified",
        margin=dict(l=56, r=64, t=(78 if title else 30) if not legend_bottom else (48 if title else 20),
                    b=40 if not legend_bottom else 70),
        legend=legend,
    )
    fig.update_xaxes(showgrid=False, tickformat="%b", dtick="M1", ticklabelmode="period")
    fig.update_yaxes(gridcolor="#ececea", zeroline=False, title_text=unit or None)
    return fig


def add_events(fig, events, y_top=1.0, row=None, col=None, x_range=None):
    """events = [(datum, popisek)]. Svislá tenká čára + popisek nahoře; mimo rozsah osy se vynechá."""
    lo, hi = x_range if x_range else (None, None)
    for i, (d, label) in enumerate(events):
        d = pd.Timestamp(d)
        if (lo is not None and d < lo) or (hi is not None and d > hi):
            continue
        kw = dict(row=row, col=col) if row else {}
        fig.add_vline(x=d, line=dict(color=EVENT, width=1), opacity=0.7, **kw)
        fig.add_annotation(x=d, y=y_top, yref="paper" if not row else None, text=label, showarrow=False,
                           xanchor="left", yanchor="top", xshift=3, yshift=-12 * (i % 2),
                           font=dict(size=10, color=EVENT), **kw)


def fig_seasonal(sz: dict, title: str, unit: str, fmt=".1f", targets=(), events=(), height=420, y_range=None,
                 legend_bottom=False):
    """Hlavní sezónní graf: letos vs. pásmo 5 let + průměr + loni, cíle, události."""
    fig = go.Figure()
    if not sz:
        return fig
    for t in _band_traces(sz, unit=unit, fmt=fmt):
        fig.add_trace(t)
    for val, label in targets:
        fig.add_hline(y=val, line=dict(color=EVENT, width=1), opacity=0.8,
                      annotation_text=label, annotation_position="top left",
                      annotation_font=dict(size=10, color=EVENT))
    _end_label(fig, sz, unit, fmt)
    _style(fig, title, unit, height, legend_bottom)
    add_events(fig, events, x_range=(sz["x"].min(), sz["x"].max()))
    if y_range:
        fig.update_yaxes(range=y_range)
    return fig


def fig_small_multiples(panels: list, unit: str, cols=3, fmt=".0f", height_per_row=170, shared_y=True,
                        title=None, y_range=None):
    """panels = [(název, sezónní dict)] — mřížka stejných sezónních mini-grafů."""
    panels = [(n, sz) for n, sz in panels if sz]
    if not panels:
        return go.Figure()
    rows = int(np.ceil(len(panels) / cols))
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=[n for n, _ in panels], shared_xaxes=True,
                        shared_yaxes=shared_y, vertical_spacing=0.32 / rows + 0.04, horizontal_spacing=0.04)
    for i, (name, sz) in enumerate(panels):
        r, c = i // cols + 1, i % cols + 1
        for t in _band_traces(sz, show_legend=(i == 0), unit=unit, fmt=fmt, step=3):
            fig.add_trace(t, row=r, col=c)
        _end_label(fig, sz, "", fmt, row=r, col=c)
    _style(fig, title, "", height=max(260, rows * height_per_row + 110), legend_bottom=True)
    fig.update_layout(hovermode="x unified", margin=dict(l=48, r=40, t=70 if title else 50, b=60),
                      legend=dict(y=-0.04 if rows > 2 else -0.1))
    fig.update_xaxes(dtick="M3", tickangle=0)
    fig.update_annotations(font_size=12)
    fig.update_yaxes(title_text=None)
    fig.update_yaxes(title_text=unit, row=1, col=1)
    if y_range:
        fig.update_yaxes(range=y_range)
    return fig


def fig_lines(series: dict, title: str, unit: str, fmt=".1f", height=360, events=(), colors=None, zero=False):
    """Prosté časové řady (dict název → Series); barvy podle entity (consistency)
    nebo `colors`; popisek hodnoty na konci každé řady (≤ 4 řady)."""
    fig = go.Figure()
    for i, (name, s) in enumerate(series.items()):
        s = s.dropna()
        if s.empty:
            continue
        line = dict(width=2)
        if colors and name in colors:
            line["color"] = colors[name]
        fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines", name=name, line=line,
                                 hovertemplate=f"{name}: %{{y:{fmt}}} {unit}<extra></extra>"))
        if len(series) <= 4:
            fig.add_annotation(x=s.index[-1], y=s.iloc[-1], showarrow=False, xanchor="left", xshift=6,
                               text=f"{format(s.iloc[-1], fmt).replace('.', ',')}", font=dict(size=11))
    _style(fig, title, unit, height)
    fig.update_xaxes(tickformat="%d.%m.", dtick=None, ticklabelmode="instant")
    if zero:
        fig.add_hline(y=0, line=dict(color="#b5b3ad", width=1))
    if fig.data:
        xs = [pd.Timestamp(t.x[0]) for t in fig.data] + [pd.Timestamp(t.x[-1]) for t in fig.data]
        add_events(fig, events, x_range=(min(xs), max(xs)))
    return fig


def fig_bars_signed(s: pd.Series, title: str, unit: str, fmt=".1f", height=300):
    """Spread / saldo: sloupce kladné modře, záporné oranžově (diverging), nula zvýrazněná."""
    s = s.dropna()
    fig = go.Figure(go.Bar(x=s.index, y=s.values, marker=dict(color=np.where(s.values >= 0, SLOTS[0], SLOTS[1])),
                           hovertemplate=f"%{{x|%d.%m.}}: %{{y:{fmt}}} {unit}<extra></extra>", name=title))
    _style(fig, title, unit, height)
    fig.update_xaxes(tickformat="%d.%m.", dtick=None, ticklabelmode="instant")
    fig.update_layout(showlegend=False, bargap=0.25)
    fig.add_hline(y=0, line=dict(color="#8a8780", width=1))
    return fig


def fig_multiples_ts(panels: list, unit: str, cols=4, fmt=".1f", height_per_row=150, title=None, zero=True,
                     color=None):
    """panels = [(název, Series)] — mřížka časových řad se stejným měřítkem (spready, saldo)."""
    panels = [(n, s.dropna()) for n, s in panels if s is not None and not s.dropna().empty]
    if not panels:
        return go.Figure()
    rows = int(np.ceil(len(panels) / cols))
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=[n for n, _ in panels], shared_xaxes=True,
                        shared_yaxes=True, vertical_spacing=0.3 / rows + 0.04, horizontal_spacing=0.03)
    for i, (name, s) in enumerate(panels):
        r, c = i // cols + 1, i % cols + 1
        fig.add_trace(go.Scatter(x=_x(s.index), y=s.round(2).values, mode="lines", name=name, showlegend=False,
                                 line=dict(width=1.6, color=color or CUR),
                                 hovertemplate=f"{name}: %{{y:{fmt}}} {unit}<extra></extra>"), row=r, col=c)
        if zero:
            fig.add_hline(y=0, line=dict(color="#b5b3ad", width=1), row=r, col=c)
        _end_label(fig, {"cur": s}, "", fmt, row=r, col=c)
    _style(fig, title, "", height=max(240, rows * height_per_row + 70))
    fig.update_layout(margin=dict(l=48, r=40, t=70 if title else 46, b=30))
    fig.update_xaxes(tickformat="%d.%m.", dtick=None, ticklabelmode="instant")
    fig.update_annotations(font_size=12)
    fig.update_yaxes(title_text=unit, row=1, col=1)
    return fig
