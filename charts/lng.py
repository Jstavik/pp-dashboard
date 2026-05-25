import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from config import year_color


def _add_trace(fig, x, y, name, color, chart_type):
    if chart_type == "Sloupcový":
        fig.add_trace(go.Bar(
            x=x, y=y, name=name,
            marker_color=color, opacity=0.85,
            hovertemplate=(
                f"<b>{name}</b><br>"
                f"%{{x|%d.%m.%Y}}: <b>%{{y:.0f}} GWh/d</b>"
                f"<extra></extra>"
            ),
        ))
    elif chart_type == "Plocha":
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="lines", name=name,
            line=dict(color=color, width=1.5),
            fill="tozeroy",
            fillcolor=f"rgba({r},{g},{b},0.2)",
            hovertemplate=(
                f"<b>{name}</b><br>"
                f"%{{x|%d.%m.%Y}}: <b>%{{y:.0f}} GWh/d</b>"
                f"<extra></extra>"
            ),
        ))
    else:
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="lines", name=name,
            line=dict(color=color, width=2),
            hovertemplate=(
                f"<b>{name}</b><br>"
                f"%{{x|%d.%m.%Y}}: <b>%{{y:.0f}} GWh/d</b>"
                f"<extra></extra>"
            ),
        ))


def fig_lng_sendout_timeseries(
    df_flows: pd.DataFrame,
    countries: list,
    date_from: pd.Timestamp,
    date_to: pd.Timestamp,
    aggregation: str = "Denní",
    chart_type: str = "Linie",
) -> go.Figure:
    """Časová osa LNG send-out z ENTSO-G."""
    lng = df_flows[
        (df_flows["adjacentSystemsKey"] == "LNG Terminals") &
        (df_flows["directionKey"] == "entry")
    ].copy()

    if lng.empty:
        return go.Figure()

    lng["date_prague"] = (lng["date"]
                          .dt.tz_convert("Europe/Prague")
                          .dt.normalize())

    if countries:
        lng = lng[lng["countryLabel"].isin(countries)]

    lng = lng[
        (lng["date_prague"] >= pd.Timestamp(date_from, tz="Europe/Prague")) &
        (lng["date_prague"] <= pd.Timestamp(date_to,   tz="Europe/Prague"))
    ]

    if lng.empty:
        return go.Figure()

    if aggregation == "Týdenní":
        lng["period"] = pd.to_datetime(
            lng["date_prague"].dt.to_period("W").astype(str))
    elif aggregation == "Měsíční":
        lng["period"] = pd.to_datetime(
            lng["date_prague"].dt.to_period("M").astype(str))
    else:
        lng["period"] = lng["date_prague"]

    COUNTRY_COLORS = {
        "Spain": "#C62828", "France": "#1565C0",
        "Netherlands": "#FF8F00", "Italy": "#2E7D32",
        "Germany": "#7B1FA2", "Belgium": "#00838F",
        "Poland": "#AD1457", "Portugal": "#E65100",
        "United Kingdom": "#546E7A", "Croatia": "#558B2F",
        "Finland": "#4527A0", "Greece": "#F57F17",
        "Lithuania": "#6D4C41",
    }

    fig = go.Figure()
    if countries:
        grouped = (lng.groupby(["period", "countryLabel"])["value_GWh"]
                   .sum().reset_index())
        for country in sorted(grouped["countryLabel"].unique()):
            sub = grouped[grouped["countryLabel"] == country]
            _add_trace(fig, sub["period"], sub["value_GWh"],
                       country, COUNTRY_COLORS.get(country, "#9E9E9E"), chart_type)
    else:
        grouped = lng.groupby("period")["value_GWh"].sum().reset_index()
        _add_trace(fig, grouped["period"], grouped["value_GWh"],
                   "EU celkem", "#1565C0", chart_type)

    fig.update_layout(
        title=f"LNG send-out — {aggregation.lower()} [GWh/d]",
        height=400,
        template="plotly_white",
        hovermode="x unified",
        barmode="stack" if chart_type == "Sloupcový" else None,
        xaxis=dict(tickformat="%d.%m.%Y", gridcolor="#f0f0f0", title="Datum"),
        yaxis=dict(title="GWh/d", gridcolor="#f0f0f0"),
        legend=dict(orientation="h", y=-0.2),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_lng_seasonality(
    df_flows: pd.DataFrame,
    countries: list,
    years: list,
) -> go.Figure:
    """Sezonnost LNG send-out — každý rok = křivka."""
    lng = df_flows[
        (df_flows["adjacentSystemsKey"] == "LNG Terminals") &
        (df_flows["directionKey"] == "entry")
    ].copy()

    if lng.empty:
        return go.Figure()

    lng["date_prague"] = (lng["date"]
                          .dt.tz_convert("Europe/Prague")
                          .dt.normalize())
    if countries:
        lng = lng[lng["countryLabel"].isin(countries)]

    lng["year"]        = lng["date_prague"].dt.year
    lng["day_of_year"] = lng["date_prague"].dt.day_of_year

    agg = lng.groupby(["year", "day_of_year"])["value_GWh"].sum().reset_index()

    sel_years = years if years else sorted(agg["year"].unique())[-6:]

    fig = go.Figure()
    for yr in sorted(sel_years):
        grp = agg[agg["year"] == yr].sort_values("day_of_year")
        if grp.empty:
            continue
        fig.add_trace(go.Scatter(
            x=grp["day_of_year"], y=grp["value_GWh"],
            mode="lines", name=str(yr),
            line=dict(
                color=year_color(yr),
                width=2.5 if yr == pd.Timestamp.now().year else 1.5,
            ),
            hovertemplate=(
                f"{yr} · den %{{x}}: "
                f"<b>%{{y:.0f}} GWh/d</b><extra></extra>"
            ),
        ))

    fig.update_layout(
        title="LNG send-out — sezonnost [GWh/d]",
        height=380,
        template="plotly_white",
        hovermode="x unified",
        xaxis=dict(
            title="Den v roce",
            tickvals=[1,32,60,91,121,152,182,213,244,274,305,335],
            ticktext=["Led","Úno","Bře","Dub","Kvě","Čvn",
                      "Čvc","Srp","Zář","Říj","Lis","Pro"],
            gridcolor="#f0f0f0",
        ),
        yaxis=dict(title="GWh/d", gridcolor="#f0f0f0"),
        legend=dict(orientation="h", y=-0.2),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_lng_inventory(df_alsi: pd.DataFrame) -> go.Figure:
    """Aktuální stav zásobníků LNG terminálů z ALSI — plnost %."""
    if df_alsi.empty or "full_pct" not in df_alsi.columns:
        return go.Figure()

    df_alsi = df_alsi.copy()
    df_alsi["gasDayStart"] = pd.to_datetime(df_alsi["gasDayStart"])
    last_date = df_alsi["gasDayStart"].max()
    last = df_alsi[df_alsi["gasDayStart"] == last_date].copy()
    last = last.dropna(subset=["full_pct"])
    last = last[last["full_pct"] > 0]

    if last.empty:
        return go.Figure()

    last = last.sort_values("full_pct", ascending=True)
    name_col = "name" if "name" in last.columns else last.columns[0]

    colors = [
        "#C62828" if f < 25 else
        "#FF8F00" if f < 50 else
        "#1565C0" if f < 75 else
        "#2E7D32"
        for f in last["full_pct"]
    ]

    fig = go.Figure(go.Bar(
        x=last["full_pct"],
        y=last[name_col],
        orientation="h",
        marker_color=colors,
        text=[f"{f:.1f}%" for f in last["full_pct"]],
        textposition="outside",
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Plnost: <b>%{x:.1f}%</b><br>"
            "<extra></extra>"
        ),
    ))

    fig.update_layout(
        title=(f"LNG terminály — plnost %  |  "
               f"{last_date.strftime('%d.%m.%Y')}"),
        height=max(300, len(last) * 25 + 80),
        template="plotly_white",
        xaxis=dict(title="Plnost (%)", range=[0, 115], gridcolor="#f0f0f0"),
        yaxis=dict(title=""),
        margin=dict(l=200, r=80, t=50, b=40),
    )
    return fig


def fig_lng_monthly_bars(
    df_flows: pd.DataFrame,
    countries: list,
) -> go.Figure:
    """Měsíční sloupcový graf per rok — Power BI styl."""
    lng = df_flows[
        (df_flows["adjacentSystemsKey"] == "LNG Terminals") &
        (df_flows["directionKey"] == "entry")
    ].copy()

    if lng.empty:
        return go.Figure()

    lng["date_prague"] = (lng["date"]
                          .dt.tz_convert("Europe/Prague")
                          .dt.normalize())
    if countries:
        lng = lng[lng["countryLabel"].isin(countries)]

    lng["year"]  = lng["date_prague"].dt.year
    lng["month"] = lng["date_prague"].dt.month

    agg = (lng.groupby(["year", "month"])["value_GWh"]
           .sum().reset_index())

    MONTH_NAMES = ["Led","Úno","Bře","Dub","Kvě","Čvn",
                   "Čvc","Srp","Zář","Říj","Lis","Pro"]

    fig = go.Figure()
    for yr in sorted(agg["year"].unique()):
        grp = agg[agg["year"] == yr].sort_values("month")
        fig.add_trace(go.Bar(
            x=grp["month"],
            y=grp["value_GWh"],
            name=str(yr),
            marker_color=year_color(yr),
            hovertemplate=(
                f"{yr} %{{x}}. měsíc: "
                f"<b>%{{y:.0f}} GWh/d</b><extra></extra>"
            ),
        ))

    fig.update_layout(
        title="LNG send-out — měsíční průměr per rok [GWh/d]",
        height=400,
        template="plotly_white",
        barmode="group",
        hovermode="x unified",
        xaxis=dict(
            tickvals=list(range(1, 13)),
            ticktext=MONTH_NAMES,
            gridcolor="#f0f0f0",
        ),
        yaxis=dict(title="GWh/d", gridcolor="#f0f0f0"),
        legend=dict(orientation="h", y=-0.15),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_lng_storage_seasonality(
    df: pd.DataFrame,
    years: list,
) -> go.Figure:
    """Sezonnost plnosti LNG zásobníků % — každý rok = křivka."""
    if df.empty or "full_pct" not in df.columns:
        return go.Figure()

    df = df.copy()
    df["gasDayStart"] = pd.to_datetime(df["gasDayStart"])

    agg = (df.groupby("gasDayStart")
             .agg(
                 inventory_gwh=("inventory_gwh", "sum"),
                 dtmi_gwh=("dtmi_gwh", "sum"),
             )
             .reset_index())
    agg["full_pct"] = (
        agg["inventory_gwh"] / agg["dtmi_gwh"] * 100
    ).round(1)
    agg["year"]        = agg["gasDayStart"].dt.year
    agg["day_of_year"] = agg["gasDayStart"].dt.day_of_year

    sel_years = years if years else sorted(agg["year"].unique())[-5:]

    fig = go.Figure()
    for yr in sorted(sel_years):
        grp = agg[agg["year"] == yr].sort_values("day_of_year")
        if grp.empty:
            continue
        fig.add_trace(go.Scatter(
            x=grp["day_of_year"], y=grp["full_pct"],
            mode="lines", name=str(yr),
            line=dict(
                color=year_color(yr),
                width=2.5 if yr == pd.Timestamp.now().year else 1.5,
            ),
            hovertemplate=(
                f"{yr} · den %{{x}}: "
                f"<b>%{{y:.1f}}%</b><extra></extra>"
            ),
        ))

    fig.update_layout(
        title="LNG zásobníky — plnost % (sezonnost)",
        height=360,
        template="plotly_white",
        hovermode="x unified",
        xaxis=dict(
            title="Den v roce",
            tickvals=[1,32,60,91,121,152,182,213,244,274,305,335],
            ticktext=["Led","Úno","Bře","Dub","Kvě","Čvn",
                      "Čvc","Srp","Zář","Říj","Lis","Pro"],
            gridcolor="#f0f0f0",
        ),
        yaxis=dict(title="%", gridcolor="#f0f0f0", range=[0, 105]),
        legend=dict(orientation="h", y=-0.2),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig
