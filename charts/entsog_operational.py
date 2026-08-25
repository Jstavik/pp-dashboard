import pandas as pd
import plotly.graph_objects as go

DIR_COLORS = {"entry": "#1565C0", "exit": "#C62828"}
DIR_LABELS = {"entry": "Entry", "exit": "Exit"}


def fig_cz_operational(
    df: pd.DataFrame,
    point_label: str,
    indicator: str,
    date_from,
    date_to,
    chart_type: str = "Linie",
    height: int = 420,
) -> go.Figure:
    """Entry/Exit časová osa pro jeden bod + indikátor (Nomination/
    Renomination) — stejná chart_type konvence (Linie/Plocha/Sloupcový)
    jako fig_flow_timeseries."""
    fig = go.Figure()

    sub = df[
        (df["pointLabel"] == point_label) &
        (df["indicator"] == indicator)
    ].copy()
    if date_from is not None:
        sub = sub[sub["periodFrom_dt"] >= date_from]
    if date_to is not None:
        sub = sub[sub["periodFrom_dt"] <= date_to]

    if sub.empty:
        fig.add_annotation(
            text="Žádná data pro vybranou kombinaci",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=14, color="#888"),
        )
        return fig

    for direction in ["entry", "exit"]:
        series = (
            sub[sub["directionKey"] == direction]
            .groupby("periodFrom_dt")["value_GWh"].sum().sort_index()
        )
        if series.empty:
            continue
        color = DIR_COLORS[direction]
        label = DIR_LABELS[direction]
        hover = (
            f"<b>{label}</b><br>%{{x|%d.%m.%Y}}<br>%{{y:.1f}} GWh/d<extra></extra>"
        )
        if chart_type == "Sloupcový":
            fig.add_trace(go.Bar(
                x=series.index, y=series.values, name=label,
                marker_color=color, hovertemplate=hover,
            ))
        elif chart_type == "Plocha":
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, mode="lines", name=label,
                line=dict(color=color, width=1.8),
                fill="tozeroy",
                fillcolor="rgba({},{},{},0.15)".format(
                    int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
                ),
                hovertemplate=hover,
            ))
        else:
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, mode="lines", name=label,
                line=dict(color=color, width=1.8),
                hovertemplate=hover,
            ))

    fig.add_hline(y=0, line_color="black", line_width=0.8)
    if chart_type == "Sloupcový":
        fig.update_layout(barmode="relative")

    fig.update_layout(
        height=height,
        title=f"{point_label} — {indicator} (Entry/Exit)",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.15, xanchor="center", x=0.5),
        margin=dict(l=60, r=20, t=60, b=60),
    )
    fig.update_xaxes(tickformat="%d.%m.%Y", gridcolor="#f0f0f0", title_text="Datum")
    fig.update_yaxes(title_text="GWh/d", gridcolor="#f0f0f0")
    return fig
