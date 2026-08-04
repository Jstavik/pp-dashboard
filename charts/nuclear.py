import pandas as pd
import plotly.graph_objects as go

from config import C_OK, MONTH_TICKS
from charts.gas import _year_color_seasonality


def fig_nuclear_fr_capacity(data: dict) -> go.Figure:
    """Instalovaný vs. dostupný jaderný výkon FR — timeline plánovaných odstávek."""
    fig = go.Figure()
    daily = data["daily"]
    total = data["total_installed"]
    if daily.empty:
        return fig

    fig.add_trace(go.Scatter(
        x=daily["date"], y=[total] * len(daily),
        name="Instalovaný výkon", mode="lines",
        line=dict(color="#90CAF9", width=1.5),
        fill="tozeroy", fillcolor="rgba(144,202,249,0.3)",
        hovertemplate="Instalovaný: <b>%{y:,.0f} MW</b><extra></extra>",
    ))
    available = total - daily["unavail_mw"]
    fig.add_trace(go.Scatter(
        x=daily["date"], y=available,
        name="Dostupný výkon", mode="lines",
        line=dict(color=C_OK, width=2),
        fill="tozeroy", fillcolor="rgba(46,125,50,0.35)",
        hovertemplate="Dostupný: <b>%{y:,.0f} MW</b><extra></extra>",
    ))

    fig.update_layout(
        height=380,
        title=f"Jaderná kapacita FR — plán odstávek | {data['now'].date()}",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(title="Datum", gridcolor="#f0f0f0"),
        yaxis=dict(title="MW", gridcolor="#f0f0f0"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_nuclear_fr_seasonality(df_gen: pd.DataFrame) -> go.Figure:
    """Sezonnost jaderné výroby FR — jedna křivka = jeden rok."""
    fig = go.Figure()
    if df_gen.empty:
        return fig

    for yr in sorted(df_gen["year"].unique()):
        grp = df_gen[df_gen["year"] == yr]
        series = grp.groupby("day_of_year")["nuclear_mw"].mean().sort_index()
        color = _year_color_seasonality(yr)
        width = 2.5 if yr == pd.Timestamp.now().year else 1.5
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values, mode="lines", name=str(yr),
            line=dict(color=color, width=width),
            hovertemplate=f"<b>{yr}</b><br>Den %{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
        ))

    fig.update_layout(
        height=380,
        title="Jaderná výroba FR — sezonnost [MW]",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(title="Den v roce", **MONTH_TICKS, gridcolor="#f0f0f0"),
        yaxis=dict(title="MW", gridcolor="#f0f0f0"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_nuclear_fr_table(data: dict) -> pd.DataFrame:
    """Tabulka aktivních jaderných odstávek FR pro st.dataframe()."""
    cols = ["Blok", "Instalovaný výkon (MW)", "Dostupný (MW)",
            "Nedostupný (MW)", "Typ", "Konec odstávky"]
    active = data["active"]
    if active.empty:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame({
        "Blok":                     active["production_resource_name"],
        "Instalovaný výkon (MW)":   active["nominal_power"],
        "Dostupný (MW)":            active["avail_qty"],
        "Nedostupný (MW)":          active["unavail_mw"],
        "Typ":                      active["businesstype"],
        "Konec odstávky":           active["end"],
    })
    return df.sort_values("Nedostupný (MW)", ascending=False).reset_index(drop=True)
