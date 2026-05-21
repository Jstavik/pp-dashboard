import pandas as pd
import plotly.graph_objects as go


def year_color(year: int) -> str:
    PALETTE = [
        "#BDBDBD", "#90A4AE", "#42A5F5", "#1565C0",
        "#FF8F00", "#C62828", "#AD1457", "#6A1B9A",
    ]
    current = pd.Timestamp.now().year
    if year == current:
        return "#2E7D32"
    return PALETTE[(current - year - 1) % len(PALETTE)]


def fig_lng_overview(df: pd.DataFrame, height: int = 400) -> go.Figure:
    """Aktuální stav LNG terminálů — plnost %."""
    if df.empty:
        return go.Figure()

    last_date = df["gasDayStart"].max()
    last = df[df["gasDayStart"] == last_date].copy()
    if "full" not in last.columns or last["full"].isna().all():
        return go.Figure()
    last = last.dropna(subset=["full"])
    last = last.sort_values("full", ascending=True)

    colors = [
        "#C62828" if f < 25 else
        "#FF8F00" if f < 50 else
        "#1565C0" if f < 75 else
        "#2E7D32"
        for f in last["full"].fillna(0)
    ]

    fig = go.Figure(go.Bar(
        x=last["full"],
        y=last["name"],
        orientation="h",
        marker_color=colors,
        text=[f"{f:.1f}%" for f in last["full"].fillna(0)],
        textposition="outside",
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Plnost: <b>%{x:.1f}%</b><br>"
            "<extra></extra>"
        ),
    ))

    fig.update_layout(
        title=f"LNG terminály EU — plnost %  |  {last_date.strftime('%d.%m.%Y')}",
        height=height,
        template="plotly_white",
        xaxis=dict(title="Plnost (%)", range=[0, 110]),
        yaxis=dict(title=""),
        margin=dict(l=200, r=80, t=50, b=40),
    )
    return fig


def fig_lng_sendout(df: pd.DataFrame, height: int = 380) -> go.Figure:
    """Send-out (regasifikace) — sezonnost po letech."""
    if df.empty:
        return go.Figure()
    if "sendOut" not in df.columns:
        return go.Figure()

    df = df.copy()
    eu_agg = (df.groupby("gasDayStart")["sendOut"]
               .sum().reset_index())
    eu_agg["year"]        = eu_agg["gasDayStart"].dt.year
    eu_agg["day_of_year"] = eu_agg["gasDayStart"].dt.day_of_year

    fig = go.Figure()
    for yr in sorted(eu_agg["year"].unique())[-6:]:
        grp = eu_agg[eu_agg["year"] == yr].sort_values("day_of_year")
        fig.add_trace(go.Scatter(
            x=grp["day_of_year"], y=grp["sendOut"],
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
        title="LNG send-out EU — sezonnost [GWh/d]",
        height=height,
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
