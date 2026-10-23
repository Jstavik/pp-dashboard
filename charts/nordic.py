"""Grafy stránky Nordic — hydrobilance."""
import pandas as pd
import plotly.graph_objects as go

from charts.explorer import _LAYOUT, _title
from config import year_color


def fig_reservoir_season(d: pd.DataFrame, stats: pd.DataFrame, title: str) -> go.Figure:
    """NVE plnění [%] po ISO týdnech: pásmo min–max a medián (NVE statistika,
    jinak z historie), posledních 5 let, aktuální rok tučně."""
    fig = go.Figure()
    if stats is None or stats.empty:
        hist = d[d["iso_year"] < d["iso_year"].max()]
        stats = hist.groupby("iso_week")["filling_pct"].agg(min_pct="min", median_pct="median",
                                                             max_pct="max").reset_index()
        label = f"{int(hist['iso_year'].min())}–{int(hist['iso_year'].max())}" if not hist.empty else ""
    else:
        label = "NVE statistika"
    stats = stats.sort_values("iso_week")
    fig.add_trace(go.Scatter(x=stats["iso_week"], y=stats["max_pct"], mode="lines", line=dict(width=0),
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=stats["iso_week"], y=stats["min_pct"], mode="lines", line=dict(width=0),
                             fill="tonexty", fillcolor="rgba(120,144,156,0.2)", name=f"min–max ({label})"))
    fig.add_trace(go.Scatter(x=stats["iso_week"], y=stats["median_pct"], mode="lines", name="medián",
                             line=dict(color="#78909C", dash="dash")))
    years = sorted(d["iso_year"].unique())[-5:]
    for y in years:
        g = d[d["iso_year"] == y].sort_values("iso_week")
        fig.add_trace(go.Scatter(x=g["iso_week"], y=g["filling_pct"], mode="lines", name=str(y),
                                 line=dict(color=year_color(int(y)), width=3 if y == years[-1] else 1.3)))
    fig.update_layout(**_LAYOUT, title=_title(title, "plnění podle ISO týdne"), yaxis_title="%",
                      xaxis_title="týden", yaxis_range=[0, 100])
    return fig


def fig_country_hydro(df: pd.DataFrame) -> go.Figure:
    """df: country, year, x (týden), v [GWh] — ENTSO-E nádrže NO/SE/FI, aktuální
    rok vs. medián a pásmo předchozích let, tři podgrafy vedle sebe."""
    from plotly.subplots import make_subplots
    countries = list(dict.fromkeys(df["country"]))
    names = {"NO": "Norsko", "SE": "Švédsko", "FI": "Finsko"}
    fig = make_subplots(rows=1, cols=len(countries), subplot_titles=[names.get(c, c) for c in countries])
    for i, cc in enumerate(countries, 1):
        d = df[df["country"] == cc]
        cur = d["year"].max()
        st = d[d["year"] < cur].groupby("x")["v"].agg(["min", "median", "max"]).reset_index()
        show = i == 1
        fig.add_trace(go.Scatter(x=st["x"], y=st["max"] / 1000, line=dict(width=0), showlegend=False,
                                 hoverinfo="skip"), row=1, col=i)
        fig.add_trace(go.Scatter(x=st["x"], y=st["min"] / 1000, line=dict(width=0), fill="tonexty",
                                 fillcolor="rgba(120,144,156,0.2)", name="min–max", showlegend=show,
                                 legendgroup="band"), row=1, col=i)
        fig.add_trace(go.Scatter(x=st["x"], y=st["median"] / 1000, name="medián", showlegend=show,
                                 legendgroup="med", line=dict(color="#78909C", dash="dash")), row=1, col=i)
        for y in (cur - 1, cur):
            g = d[d["year"] == y]
            fig.add_trace(go.Scatter(x=g["x"], y=g["v"] / 1000, name=str(y), legendgroup=str(y), showlegend=show,
                                     line=dict(color=year_color(int(y)), width=3 if y == cur else 1.3)), row=1, col=i)
    fig.update_layout(**{**_LAYOUT, "margin": dict(l=60, r=20, t=95, b=80)},
                      title=_title("Nádrže NO / SE / FI (ENTSO-E 16.1.D)", "zásoba energie [TWh] po týdnech"),
                      height=400)
    fig.update_yaxes(title_text="TWh", col=1)
    return fig
