import pandas as pd
import plotly.graph_objects as go

from config import C_DEFICIT, MONTH_TICKS

_C_YEAR_BG = "#BDBDBD"


def fig_nuclear_fr_unavailability(data_hist: dict, data_future: dict) -> go.Figure:
    """Nedostupný jaderný výkon FR — historie od začátku roku do dnes + predikce
    plánovaných odstávek od zítra do konce příštího roku."""
    fig = go.Figure()
    history = data_hist["history"]
    forecast = data_future["forecast_long"]

    if not history.empty:
        fig.add_trace(go.Scatter(
            x=history["date"], y=history["unavail_mw"] / 1000,
            name="Historie", mode="lines",
            line=dict(color=C_DEFICIT, width=2),
            hovertemplate="Nedostupnost: <b>%{y:,.2f} GW</b><extra></extra>",
        ))

    if not forecast.empty:
        fc_x, fc_y = forecast["date"], forecast["unavail_mw"] / 1000
        if not history.empty:
            # napojit na poslední bod historie, aby čárkovaná predikce navazovala plynule
            fc_x = pd.concat([history["date"].iloc[[-1]], fc_x], ignore_index=True)
            fc_y = pd.concat([history["unavail_mw"].iloc[[-1]] / 1000, fc_y], ignore_index=True)
        fig.add_trace(go.Scatter(
            x=fc_x, y=fc_y,
            name="Predikce", mode="lines",
            line=dict(color=C_DEFICIT, width=2, dash="dash"),
            hovertemplate="Predikce: <b>%{y:,.2f} GW</b><extra></extra>",
        ))

    fig.update_layout(
        height=380,
        title="Jaderné odstávky FR — nedostupný výkon [GW]",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(title="Datum", gridcolor="#f0f0f0"),
        yaxis=dict(title="GW", gridcolor="#f0f0f0"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_nuclear_fr_seasonality_with_forecast(df_gen: pd.DataFrame, data_future: dict) -> go.Figure:
    """Sezonnost jaderné výroby FR — skutečná výroba aktuálního roku + predikce
    dostupnosti (instalovaný výkon − plánované odstávky), ostatní roky šedě v pozadí."""
    fig = go.Figure()
    now = pd.Timestamp.now()
    current_year = now.year

    if not df_gen.empty:
        for yr in sorted(df_gen["year"].unique()):
            if yr >= current_year:
                continue
            grp = df_gen[df_gen["year"] == yr]
            series = grp.groupby("day_of_year")["nuclear_mw"].mean().sort_index()
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values / 1000, mode="lines", name=str(yr),
                line=dict(color=_C_YEAR_BG, width=1),
                hovertemplate=f"<b>{yr}</b><br>Den %{{x}}<br>%{{y:,.2f}} GW<extra></extra>",
            ))

        grp_cur = df_gen[(df_gen["year"] == current_year) & (df_gen["day_of_year"] <= now.day_of_year)]
        series_cur = grp_cur.groupby("day_of_year")["nuclear_mw"].mean().sort_index()
        fig.add_trace(go.Scatter(
            x=series_cur.index, y=series_cur.values / 1000, mode="lines",
            name=f"{current_year} — skutečnost",
            line=dict(color=C_DEFICIT, width=2.5),
            hovertemplate=f"<b>{current_year}</b><br>Den %{{x}}<br>%{{y:,.2f}} GW<extra></extra>",
        ))

    forecast = data_future["forecast_long"]
    total_installed = data_future["total_installed"]
    if not forecast.empty:
        fc = forecast.copy()
        fc["day_of_year"] = pd.DatetimeIndex(fc["date"]).day_of_year
        fc["avail_gw"] = (total_installed - fc["unavail_mw"]) / 1000
        series_fc = fc.groupby("day_of_year")["avail_gw"].mean().sort_index()
        fig.add_trace(go.Scatter(
            x=series_fc.index, y=series_fc.values, mode="lines", name="Predikce dostupnosti",
            line=dict(color=C_DEFICIT, width=2, dash="dash"),
            hovertemplate="Predikce<br>Den %{x}<br>%{y:,.2f} GW<extra></extra>",
        ))

    fig.update_layout(
        height=380,
        title="Jaderná výroba FR — sezonnost s predikcí dostupnosti [GW]",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(title="Den v roce", **MONTH_TICKS, gridcolor="#f0f0f0"),
        yaxis=dict(title="GW", gridcolor="#f0f0f0"),
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
