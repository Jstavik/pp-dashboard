import pandas as pd
import plotly.graph_objects as go

from config import C_DEFICIT, MONTH_TICKS, GEN_STACK_ORDER, PSR_CODE_BY_SOURCE_TYPE, psr_lookup, _base_layout
from charts.gas import _year_color_seasonality_bg


def _fill_rgba(color: str, alpha: float = 0.78) -> str:
    """Poloprůhledná výplň plochy z hex barvy — stejný vzor jako
    charts/generation.py::fig_generation_area."""
    if color.startswith("#") and len(color) == 7:
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return color


def _stack_key(source_type: str) -> int:
    """Řadí sloupce podle GEN_STACK_ORDER (psr_code). source_type bez
    mapování na psr_code (např. nová ENTSO-E kategorie 'Energy storage',
    co není v PSR_CODE_BY_SOURCE_TYPE) spadne na konec, ne na chybu."""
    code = PSR_CODE_BY_SOURCE_TYPE.get(source_type)
    return GEN_STACK_ORDER.index(code) if code in GEN_STACK_ORDER else 999


def _stacked_area(wide: pd.DataFrame) -> go.Figure:
    """Sdílený stavební blok — skládaná plocha ze source_type sloupců.
    Klíčuje se přes source_type (vždy vyplněný string), ne psr_code —
    ten je u nenamapovaných zdrojů NaN a psr_lookup(source_type) na to
    má vlastní fallback (hash barva), takže NaN psr_code nikdy nerozbije
    barvu/label."""
    fig = go.Figure()
    for source_type in sorted(wide.columns, key=_stack_key):
        series = wide[source_type].fillna(0)
        if series.sum() <= 0:
            continue
        name, color = psr_lookup(source_type)
        fig.add_trace(go.Scatter(
            x=wide.index, y=series.values, stackgroup="gen", name=name,
            line=dict(width=0, color=color), fillcolor=_fill_rgba(color),
            hovertemplate=f"{name}: %{{y:.2f}} GW<extra></extra>",
        ))
    return fig


def fig_generation_stacked(df: pd.DataFrame, day: pd.Timestamp = None) -> go.Figure:
    """Skládaný graf výroby podle zdroje pro konkrétní den (výchozí
    dnešek) — plné rozlišení dat (15min/hodinové), ne denní agregace.
    Nahrazuje starý koncept 'Teď' pevně vázaný na aktuální okamžik —
    tady je den parametrizovaný a volitelný."""
    if df.empty:
        return _base_layout(go.Figure(), height=380)

    day = (day or pd.Timestamp.now(tz="UTC")).normalize()
    day_end = day + pd.Timedelta(days=1)
    day_df = df[(df["date"] >= day) & (df["date"] < day_end)]
    if day_df.empty:
        return _base_layout(go.Figure(), height=380)

    wide = day_df.pivot_table(index="date", columns="source_type", values="mw", aggfunc="mean", observed=True) / 1000
    fig = _stacked_area(wide)
    _base_layout(fig, height=380)
    fig.update_layout(title=f"Výroba podle zdroje — {day.date()} [GW]", hovermode="x unified")
    fig.update_xaxes(title_text="Čas", tickformat="%H:%M")
    fig.update_yaxes(title_text="GW")
    return fig


def fig_generation_ytd(df: pd.DataFrame, start_date: pd.Timestamp = None) -> go.Figure:
    """Skládaný graf výroby podle zdroje — denní průměr, od start_date
    (výchozí 1.1. aktuálního roku) do dneška."""
    if df.empty:
        return _base_layout(go.Figure(), height=380)

    now = pd.Timestamp.now(tz="UTC")
    start_date = start_date or pd.Timestamp(year=now.year, month=1, day=1, tz="UTC")
    if start_date.tzinfo is None:
        start_date = start_date.tz_localize("UTC")

    ytd = df[(df["date"] >= start_date) & (df["date"] <= now)]
    if ytd.empty:
        return _base_layout(go.Figure(), height=380)

    daily = ytd.assign(day=ytd["date"].dt.normalize()).pivot_table(
        index="day", columns="source_type", values="mw", aggfunc="mean", observed=True
    ) / 1000
    fig = _stacked_area(daily)
    _base_layout(fig, height=380)
    fig.update_layout(title=f"Výroba podle zdroje — od {start_date.date()} do dnes [GW]", hovermode="x unified")
    fig.update_xaxes(title_text="Datum")
    fig.update_yaxes(title_text="GW")
    return fig


def _add_seasonality_trace(fig: go.Figure, series: pd.Series, name: str, color: str,
                            width: float, chart_type: str) -> None:
    if series.empty:
        return
    hover = f"<b>{name}</b><br>Den %{{x}}<br>%{{y:,.2f}} GW<extra></extra>"
    if chart_type == "Sloupcový":
        fig.add_trace(go.Bar(x=series.index, y=series.values, name=name,
                              marker_color=color, hovertemplate=hover))
    elif chart_type == "Plocha":
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values, mode="lines", name=name,
            line=dict(color=color, width=width), fill="tozeroy",
            fillcolor=_fill_rgba(color, 0.2), hovertemplate=hover,
        ))
    else:
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values, mode="lines", name=name,
            line=dict(color=color, width=width), hovertemplate=hover,
        ))


def fig_seasonality(df: pd.DataFrame, source_type: str, chart_type: str = "Linie") -> go.Figure:
    """Sezonnost výroby pro vybraný zdroj — historické roky v pozadí
    (barva podle stáří, sdílená s gas sezonností přes
    charts.gas._year_color_seasonality_bg), aktuální rok zvýrazněný.

    chart_type: 'Linie' / 'Plocha' / 'Sloupcový' — stejný projektový
    standard jako charts/gas.py::fig_flow_seasonality (st.radio v app.py)."""
    fig = go.Figure()
    now = pd.Timestamp.now(tz="UTC")
    current_year = now.year

    sub = df[df["source_type"] == source_type]
    if sub.empty:
        return fig

    name, _ = psr_lookup(source_type)

    for yr in sorted(sub["year"].unique()):
        if yr >= current_year:
            continue
        grp = sub[sub["year"] == yr]
        series = grp.groupby("day_of_year")["mw"].mean().sort_index() / 1000
        _add_seasonality_trace(fig, series, str(yr), _year_color_seasonality_bg(yr), 1.5, chart_type)

    grp_cur = sub[(sub["year"] == current_year) & (sub["day_of_year"] <= now.day_of_year)]
    series_cur = grp_cur.groupby("day_of_year")["mw"].mean().sort_index() / 1000
    _add_seasonality_trace(fig, series_cur, f"{current_year} — skutečnost", C_DEFICIT, 2.5, chart_type)

    if chart_type == "Sloupcový":
        fig.update_layout(barmode="group")
    fig.update_layout(
        height=380,
        title=f"Sezonnost výroby — {name} [GW]",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(title="Den v roce", **MONTH_TICKS, gridcolor="#f0f0f0"),
        yaxis=dict(title="GW", gridcolor="#f0f0f0"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig
