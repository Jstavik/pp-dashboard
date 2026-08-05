import pandas as pd
import plotly.graph_objects as go

from config import C_DEFICIT, C_MUTED, C_YEAR_SEASONALITY_ALT, MONTH_TICKS, NUCLEAR_FR_CF_WINDOW_DAYS
from charts.gas import _year_color_seasonality


def _year_color_seasonality_bg(yr: int) -> str:
    """Barva pro roky v pozadí sezonního grafu — stejná stupnice jako
    _year_color_seasonality(yr), jen current-1 dostane náhradní barvu.
    Aktuální rok má v tomhle grafu vlastní pevnou červenou (C_DEFICIT) mimo
    tuhle smyčku a current-1 by jinak dostal tu samou červenou z automatické
    gradace — nahrazeno fialovou, ostatní roky (current-2, current-3, staré)
    beze změny."""
    if pd.Timestamp.now().year - yr == 1:
        return C_YEAR_SEASONALITY_ALT
    return _year_color_seasonality(yr)


def _nuclear_fr_capacity_factor(df_gen: pd.DataFrame, data_future: dict, current_year: int) -> float | None:
    """Průměrný poměr skutečná_výroba / teoretická_dostupnost za posledních
    NUCLEAR_FR_CF_WINDOW_DAYS dní, kde jsou k dispozici obě hodnoty.

    Teoretická dostupnost (instalovaný výkon − plánované odstávky) je jen
    strop — reálná výroba je pod ním kvůli síťovým omezením, částečnému
    zatížení bloků atd. Bez tohohle škálování by predikce (počítaná ze
    stropu) skočila nahoru přesně tam, kde končí skutečná výroba."""
    history = data_future["history"]
    total_installed = data_future["total_installed"]
    if history.empty or df_gen.empty or not total_installed:
        return None

    hist = history.copy()
    hist["day_of_year"] = pd.DatetimeIndex(hist["date"]).day_of_year
    hist["theoretical_mw"] = total_installed - hist["unavail_mw"]

    actual = (df_gen[df_gen["year"] == current_year]
              .groupby("day_of_year")["nuclear_mw"].mean())

    merged = hist.merge(actual.rename("actual_mw"), on="day_of_year", how="inner")
    merged = merged[merged["theoretical_mw"] > 0]
    recent = merged.sort_values("day_of_year").tail(NUCLEAR_FR_CF_WINDOW_DAYS)
    if recent.empty:
        return None
    return float((recent["actual_mw"] / recent["theoretical_mw"]).mean())


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
    dostupnosti (teoretická dostupnost škálovaná kapacitním faktorem),
    ostatní roky barevně podle stáří v pozadí."""
    fig = go.Figure()
    now = pd.Timestamp.now()
    current_year = now.year
    series_cur = pd.Series(dtype=float)

    if not df_gen.empty:
        for yr in sorted(df_gen["year"].unique()):
            if yr >= current_year:
                continue
            grp = df_gen[df_gen["year"] == yr]
            series = grp.groupby("day_of_year")["nuclear_mw"].mean().sort_index()
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values / 1000, mode="lines", name=str(yr),
                line=dict(color=_year_color_seasonality_bg(yr), width=1.5),
                hovertemplate=f"<b>{yr}</b><br>Den %{{x}}<br>%{{y:,.2f}} GW<extra></extra>",
            ))

        grp_cur = df_gen[(df_gen["year"] == current_year) & (df_gen["day_of_year"] <= now.day_of_year)]
        series_cur = grp_cur.groupby("day_of_year")["nuclear_mw"].mean().sort_index() / 1000
        fig.add_trace(go.Scatter(
            x=series_cur.index, y=series_cur.values, mode="lines",
            name=f"{current_year} — skutečnost",
            line=dict(color=C_DEFICIT, width=2.5),
            hovertemplate=f"<b>{current_year}</b><br>Den %{{x}}<br>%{{y:,.2f}} GW<extra></extra>",
        ))

    forecast = data_future["forecast_long"]
    total_installed = data_future["total_installed"]
    if not forecast.empty:
        fc = forecast.copy()
        fc["day_of_year"] = pd.DatetimeIndex(fc["date"]).day_of_year
        fc["theoretical_gw"] = (total_installed - fc["unavail_mw"]) / 1000
        series_theoretical = fc.groupby("day_of_year")["theoretical_gw"].mean().sort_index()

        # tenká tečkovaná referenční čára — teoretický strop bez capacity factoru
        fig.add_trace(go.Scatter(
            x=series_theoretical.index, y=series_theoretical.values, mode="lines",
            name="Teoretická dostupnost (strop)",
            line=dict(color=C_MUTED, width=1, dash="dot"),
            hovertemplate="Strop<br>Den %{x}<br>%{y:,.2f} GW<extra></extra>",
        ))

        capacity_factor = _nuclear_fr_capacity_factor(df_gen, data_future, current_year)
        cf = capacity_factor if capacity_factor is not None else 1.0
        series_pred = series_theoretical * cf

        if not series_cur.empty:
            # bridge bod — navázat predikci přesně na poslední bod skutečné
            # výroby tam, kde se obě čáry na ose dne v roce potkávají
            series_pred.loc[series_cur.index[-1]] = series_cur.values[-1]
            series_pred = series_pred.sort_index()

        cf_label = f" (CF {cf:.2f})" if capacity_factor is not None else ""
        fig.add_trace(go.Scatter(
            x=series_pred.index, y=series_pred.values, mode="lines",
            name=f"Predikce dostupnosti{cf_label}",
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
