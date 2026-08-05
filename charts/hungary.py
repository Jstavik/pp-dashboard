import pandas as pd
import plotly.graph_objects as go

from config import C_DEFICIT, MONTH_TICKS, GEN_STACK_ORDER, PSR_CODE_BY_SOURCE_TYPE, psr_lookup, _base_layout
from charts.nuclear import _year_color_seasonality_bg


def _fill_rgba(color: str, alpha: float = 0.78) -> str:
    """Poloprůhledná výplň plochy z hex barvy — stejný vzor jako
    charts/generation.py::fig_generation_area."""
    if color.startswith("#") and len(color) == 7:
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return color


def _stack_key_psr(psr_code) -> int:
    return GEN_STACK_ORDER.index(psr_code) if psr_code in GEN_STACK_ORDER else 999


def fig_hu_generation_stacked(df_gen: pd.DataFrame) -> go.Figure:
    """Skládaný graf výroby HU podle zdroje — od začátku aktuálního roku do dnes."""
    fig = go.Figure()
    if df_gen.empty:
        return _base_layout(fig, height=380)

    now = pd.Timestamp.now(tz="Europe/Budapest")
    current_year = now.year
    ytd = df_gen[(df_gen["year"] == current_year) & (df_gen["date"] <= now)]
    if ytd.empty:
        return _base_layout(fig, height=380)

    daily = (ytd.assign(day=ytd["date"].dt.tz_convert("Europe/Budapest").dt.normalize())
                .groupby(["day", "psr_code"])["mw"].mean().unstack("psr_code"))

    for psr in sorted(daily.columns, key=_stack_key_psr):
        series = daily[psr].fillna(0) / 1000
        if series.sum() <= 0:
            continue
        name, color = psr_lookup(psr)
        fig.add_trace(go.Scatter(
            x=daily.index, y=series.values, stackgroup="gen", name=name,
            line=dict(width=0, color=color), fillcolor=_fill_rgba(color),
            hovertemplate=f"{name}: %{{y:.2f}} GW<extra></extra>",
        ))

    _base_layout(fig, height=380)
    fig.update_layout(
        title=f"Výroba HU podle zdroje — {current_year} od 1.1. do dnes [GW]",
        hovermode="x unified",
    )
    fig.update_xaxes(title_text="Datum")
    fig.update_yaxes(title_text="GW")
    return fig


def fig_hu_outages_by_type(data: dict) -> go.Figure:
    """Odstávky HU podle typu zdroje — skládaná plocha od dneška do konce
    příštího roku. Zobrazuje jen typy zdrojů, které se v datech reálně
    vyskytují (žádné prázdné kategorie za typy, co HU nemá)."""
    fig = go.Figure()
    daily = data["daily_by_type"]
    if daily.empty:
        return _base_layout(fig, height=380)

    wide = daily.pivot(index="date", columns="plant_type", values="unavail_mw").fillna(0)

    def _key(plant_type):
        return _stack_key_psr(PSR_CODE_BY_SOURCE_TYPE.get(plant_type))

    for plant_type in sorted(wide.columns, key=_key):
        series = wide[plant_type] / 1000
        if series.sum() <= 0:
            continue
        name, color = psr_lookup(plant_type)
        fig.add_trace(go.Scatter(
            x=wide.index, y=series.values, stackgroup="out", name=name,
            line=dict(width=0, color=color), fillcolor=_fill_rgba(color),
            hovertemplate=f"{name}: %{{y:.3f}} GW<extra></extra>",
        ))

    now = data["now"]
    next_year_end = pd.Timestamp(year=now.year + 1, month=12, day=31)
    _base_layout(fig, height=380)
    fig.update_layout(
        title=f"Odstávky HU podle typu zdroje — do {next_year_end.date()} [GW]",
        hovermode="x unified",
    )
    fig.update_xaxes(title_text="Datum")
    fig.update_yaxes(title_text="GW")
    return fig


def fig_hu_seasonality(df_gen: pd.DataFrame, source_type: str) -> go.Figure:
    """Sezonnost výroby HU pro vybraný zdroj — historické roky v pozadí
    (barva podle stáří, stejná logika jako u FR), aktuální rok zvýrazněný."""
    fig = go.Figure()
    now = pd.Timestamp.now(tz="Europe/Budapest")
    current_year = now.year

    sub = df_gen[df_gen["source_type"] == source_type]
    if sub.empty:
        return fig

    name, _ = psr_lookup(source_type)

    for yr in sorted(sub["year"].unique()):
        if yr >= current_year:
            continue
        grp = sub[sub["year"] == yr]
        series = grp.groupby("day_of_year")["mw"].mean().sort_index()
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values / 1000, mode="lines", name=str(yr),
            line=dict(color=_year_color_seasonality_bg(yr), width=1.5),
            hovertemplate=f"<b>{yr}</b><br>Den %{{x}}<br>%{{y:,.2f}} GW<extra></extra>",
        ))

    grp_cur = sub[(sub["year"] == current_year) & (sub["day_of_year"] <= now.day_of_year)]
    series_cur = grp_cur.groupby("day_of_year")["mw"].mean().sort_index()
    fig.add_trace(go.Scatter(
        x=series_cur.index, y=series_cur.values / 1000, mode="lines",
        name=f"{current_year} — skutečnost",
        line=dict(color=C_DEFICIT, width=2.5),
        hovertemplate=f"<b>{current_year}</b><br>Den %{{x}}<br>%{{y:,.2f}} GW<extra></extra>",
    ))

    fig.update_layout(
        height=380,
        title=f"Sezonnost výroby HU — {name} [GW]",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(title="Den v roce", **MONTH_TICKS, gridcolor="#f0f0f0"),
        yaxis=dict(title="GW", gridcolor="#f0f0f0"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_hu_outages_table(data: dict) -> pd.DataFrame:
    """Tabulka aktivních odstávek HU pro st.dataframe()."""
    cols = ["Blok", "Typ zdroje", "Instalovaný výkon (MW)", "Dostupný (MW)",
            "Nedostupný (MW)", "Druh", "Konec odstávky"]
    active = data["active"]
    if active.empty:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame({
        "Blok":                     active["production_resource_name"],
        "Typ zdroje":               active["plant_type"].apply(lambda pt: psr_lookup(pt)[0]),
        "Instalovaný výkon (MW)":   active["nominal_power"],
        "Dostupný (MW)":            active["avail_qty"],
        "Nedostupný (MW)":          active["unavail_mw"],
        "Druh":                     active["businesstype"],
        "Konec odstávky":           active["end"],
    })
    return df.sort_values("Nedostupný (MW)", ascending=False).reset_index(drop=True)
