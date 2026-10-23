"""Grafy stránky Trh EU (ENTSO-E z historie)."""
import pandas as pd
import plotly.graph_objects as go

from charts.explorer import _LAYOUT, _title


def fig_zone_heatmap(df: pd.DataFrame, names: dict, title: str, unit: str) -> go.Figure:
    """df: b (den), cat (zóna), v → zóny × dny, seřazené podle průměru."""
    z = df.pivot_table(index="cat", columns=df["b"].dt.tz_localize(None).dt.normalize(), values="v")
    z = z.loc[z.mean(axis=1).sort_values(ascending=False).index]
    fig = go.Figure(go.Heatmap(z=z.values, x=z.columns, y=[names.get(c, c) for c in z.index],
                               colorscale="RdYlBu_r", colorbar=dict(title=unit),
                               hovertemplate="%{y} %{x|%d.%m.%Y}: %{z:,.1f} " + unit + "<extra></extra>"))
    fig.update_layout(template="plotly_white", title=_title(title), margin=dict(l=110, r=20, t=60, b=40),
                      height=max(360, 80 + 18 * len(z.index)))
    return fig


def fig_actual_vs_forecast(df: pd.DataFrame, title: str, unit: str, sub: str = "") -> go.Figure:
    """df: b, cat ∈ {actual, forecast}, v + odchylka prognózy (forecast − actual)."""
    w = df.pivot_table(index="b", columns="cat", values="v").sort_index()
    x = w.index.tz_localize(None)
    fig = go.Figure()
    if "actual" in w:
        fig.add_trace(go.Scatter(x=x, y=w["actual"], name="skutečnost", line=dict(color="#1d2733", width=1.8)))
    if "forecast" in w:
        fig.add_trace(go.Scatter(x=x, y=w["forecast"], name="prognóza D-1", line=dict(color="#1565C0", dash="dot")))
    if {"actual", "forecast"} <= set(w.columns):
        fig.add_trace(go.Bar(x=x, y=w["forecast"] - w["actual"], name="odchylka prognózy", yaxis="y2",
                             marker_color="rgba(229,57,53,.45)"))
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", title="odchylka", showgrid=False))
    fig.update_layout(**_LAYOUT, title=_title(title, sub), yaxis_title=unit)
    return fig
