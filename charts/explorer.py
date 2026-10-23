"""Grafy Průzkumníku — z malého agregovaného výsledku core/explore.py
(b, cat, v): čára, skládané sloupce, skládaná plocha, souhrn po kategoriích
(cross-filter), sezónnost (porovnání let), heatmapy rok×den a den×hodina."""
import pandas as pd
import plotly.graph_objects as go

from config import PSR_CODE_BY_SOURCE_TYPE, PSR_TYPES, year_color

PALETTE = ["#1565C0", "#E53935", "#43A047", "#FB8C00", "#8E24AA", "#00ACC1", "#6D4C41",
           "#3949AB", "#F4511E", "#00897B", "#7CB342", "#5E35B1", "#C0CA33", "#D81B60",
           "#546E7A", "#FFB300", "#26A69A", "#AB47BC", "#8D6E63", "#5C6BC0"]
CHART_TYPES = ["line", "bar", "area", "season", "heat_year", "heat_hour"]
CHART_LABELS = {"line": "čára", "bar": "sloupce (skládané)", "area": "plocha (skládaná)",
                "season": "porovnání let", "heat_year": "heatmapa rok×den", "heat_hour": "heatmapa den×hodina"}
_LAYOUT = dict(template="plotly_white", hovermode="x unified",
               legend=dict(orientation="h", y=-0.2, xanchor="center", x=0.5),
               margin=dict(l=60, r=20, t=60, b=80))
MONTHS = ["led", "úno", "bře", "dub", "kvě", "čvn", "čvc", "srp", "zář", "říj", "lis", "pro"]


def series_color(cat: str, i: int) -> str:
    from charts.energy_charts import EC_PSR
    code = PSR_CODE_BY_SOURCE_TYPE.get(cat) or EC_PSR.get(cat)
    if code in PSR_TYPES:
        return PSR_TYPES[code][1]
    return PALETTE[i % len(PALETTE)]


def series_name(cat: str) -> str:
    code = PSR_CODE_BY_SOURCE_TYPE.get(cat)
    return PSR_TYPES[code][0] if code in PSR_TYPES else cat


def _title(title: str, sub: str = "") -> dict:
    t = f"<b>{title}</b>" + (f"<br><span style='font-size:12px;color:#66788a'>{sub}</span>" if sub else "")
    return dict(text=t, x=0.01, xanchor="left")


def _order(df: pd.DataFrame) -> list:
    """Kategorie podle velikosti (největší dole ve stacku / první v legendě)."""
    return df.groupby("cat")["v"].apply(lambda s: s.abs().sum()).sort_values(ascending=False).index.tolist()


def fig_timeseries(df: pd.DataFrame, kind: str, title: str, unit: str, sub: str = "") -> go.Figure:
    fig = go.Figure()
    for i, cat in enumerate(_order(df)):
        d = df[df["cat"] == cat]
        x = d["b"].dt.tz_localize(None)
        color = series_color(cat, i)
        name = series_name(cat)
        if kind == "bar":
            fig.add_trace(go.Bar(x=x, y=d["v"], name=name, marker_color=color))
        elif kind == "area":
            fig.add_trace(go.Scatter(x=x, y=d["v"], name=name, mode="lines", stackgroup="one",
                                     line=dict(width=0.5, color=color)))
        else:
            fig.add_trace(go.Scatter(x=x, y=d["v"], name=name, mode="lines", line=dict(width=1.6, color=color)))
    fig.update_layout(**_LAYOUT, title=_title(title, sub), yaxis_title=unit, barmode="relative",
                      showlegend=df["cat"].nunique() > 1, bargap=0.05)
    return fig


def fig_breakdown(df: pd.DataFrame, title: str, unit: str, selected=()) -> go.Figure:
    d = df.dropna(subset=["v"]).head(40).iloc[::-1]
    colors = [series_color(c, i) for i, c in enumerate(d["cat"][::-1])][::-1]
    sel = set(selected or ())
    opacity = [1.0 if not sel or c in sel else 0.35 for c in d["cat"]]
    fig = go.Figure(go.Bar(x=d["v"], y=[series_name(c) for c in d["cat"]], customdata=d["cat"], orientation="h",
                           marker=dict(color=colors, opacity=opacity),
                           hovertemplate="%{y}: %{x:,.1f} " + unit + "<extra></extra>"))
    fig.update_layout(template="plotly_white", title=_title(title, "klik = filtr na kategorii, znovu = zrušit"),
                      margin=dict(l=160, r=20, t=60, b=40), xaxis_title=unit,
                      height=max(260, 60 + 22 * len(d)))
    return fig


def fig_season(df: pd.DataFrame, grain: str, title: str, unit: str) -> go.Figure:
    """df: year, x, v. Aktuální rok tučně, pásmo min–max a medián ostatních let."""
    fig = go.Figure()
    if df.empty:
        return fig
    years = sorted(df["year"].unique())
    cur = years[-1]
    others = df[df["year"] != cur]
    if len(years) >= 3:
        st = others.groupby("x")["v"].agg(["min", "max", "median"]).reset_index()
        fig.add_trace(go.Scatter(x=st["x"], y=st["max"], mode="lines", line=dict(width=0), showlegend=False,
                                 hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=st["x"], y=st["min"], mode="lines", line=dict(width=0), fill="tonexty",
                                 fillcolor="rgba(120,144,156,0.18)", name=f"min–max {years[0]}–{years[-2]}"))
        fig.add_trace(go.Scatter(x=st["x"], y=st["median"], mode="lines", name="medián",
                                 line=dict(color="#78909C", dash="dash", width=1.4)))
    for y in years:
        d = df[df["year"] == y]
        fig.add_trace(go.Scatter(x=d["x"], y=d["v"], mode="lines", name=str(y),
                                 line=dict(color=year_color(int(y)), width=2.8 if y == cur else 1.3)))
    xt = {"month": "měsíc", "quarter": "měsíc", "year": "měsíc", "week": "týden v roce"}.get(grain, "den v roce")
    layout = dict(_LAYOUT, hovermode="x unified")
    fig.update_layout(**layout, title=_title(title, "porovnání let"), yaxis_title=unit, xaxis_title=xt)
    if xt == "měsíc":
        fig.update_xaxes(tickmode="array", tickvals=list(range(1, 13)), ticktext=MONTHS)
    return fig


def fig_heat_year(df: pd.DataFrame, title: str, unit: str) -> go.Figure:
    """df: b (den), v → osa x den v roce, osa y rok."""
    d = pd.DataFrame({"year": df["b"].dt.year, "doy": df["b"].dt.dayofyear, "v": df["v"]})
    z = d.pivot_table(index="year", columns="doy", values="v", aggfunc="mean")
    fig = go.Figure(go.Heatmap(z=z.values, x=z.columns, y=[str(y) for y in z.index], colorscale="RdYlBu_r",
                               colorbar=dict(title=unit), hovertemplate="rok %{y}, den %{x}: %{z:,.1f}<extra></extra>"))
    fig.update_layout(template="plotly_white", title=_title(title, "rok × den v roce"),
                      margin=dict(l=60, r=20, t=60, b=50), xaxis_title="den v roce",
                      height=max(320, 120 + 34 * len(z.index)))
    return fig


def fig_heat_hour(df: pd.DataFrame, title: str, unit: str) -> go.Figure:
    """df: b (hodina), v → osa x den, osa y hodina dne (profil)."""
    d = pd.DataFrame({"day": df["b"].dt.tz_localize(None).dt.normalize(), "hour": df["b"].dt.hour, "v": df["v"]})
    z = d.pivot_table(index="hour", columns="day", values="v", aggfunc="mean")
    fig = go.Figure(go.Heatmap(z=z.values, x=z.columns, y=z.index, colorscale="RdYlBu_r", colorbar=dict(title=unit),
                               hovertemplate="%{x|%d.%m.%Y} %{y}:00 — %{z:,.1f}<extra></extra>"))
    fig.update_layout(template="plotly_white", title=_title(title, "den × hodina"),
                      margin=dict(l=60, r=20, t=60, b=50), yaxis=dict(title="hodina", dtick=3), height=420)
    return fig
