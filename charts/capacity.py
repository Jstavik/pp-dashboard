import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import date, timedelta
from data.entsog_capacity import expand_capacity, TSO_COUNTRY

COLORS = {
    "Firm Technical":           ("#C62828", "lines"),
    "Firm Available":           ("#43A047", "bar"),
    "Firm Booked":              ("#1565C0", "bar"),
    "Interruptible Total":      ("#E65100", "lines"),
    "Interruptible Available":  ("#FFD54F", "bar"),
    "Interruptible Booked":     ("#FF8F00", "bar"),
}
FIRM_INDS = ["Firm Technical", "Firm Available", "Firm Booked"]
INT_INDS  = ["Interruptible Total", "Interruptible Available",
             "Interruptible Booked"]


def fig_capacity(
    df_raw: pd.DataFrame,
    df_flows: pd.DataFrame,
    point_label: str,
    direction: str = "exit",
    cap_type: str = "Firm",
) -> go.Figure:

    sub = df_raw[
        (df_raw["pointLabel"] == point_label) &
        (df_raw["directionKey"] == direction)
    ].copy()
    if sub.empty:
        return go.Figure()

    target_dates = [
        date.today() + timedelta(weeks=w) for w in range(-104, 52 * 3)
    ]
    expanded = expand_capacity(sub, target_dates)
    if expanded.empty:
        return go.Figure()

    expanded["value_GWh"] = pd.to_numeric(
        expanded["value"], errors="coerce") / 1_000_000
    expanded["tso_country"] = expanded["operatorLabel"].apply(
        lambda x: next(
            (c for tso, c in TSO_COUNTRY.items()
             if tso.lower() in str(x).lower()), "??")
    )
    expanded["date"] = pd.to_datetime(expanded["date"])

    # Best TSO
    tech = expanded[expanded["indicator"] == "Firm Technical"]
    if tech.empty:
        return go.Figure()
    best_tc = tech.groupby("tso_country")["value_GWh"].sum().idxmax()
    best_op = (expanded[expanded["tso_country"] == best_tc]
               ["operatorLabel"].iloc[0])

    sub_best = expanded[expanded["tso_country"] == best_tc]
    pivot = sub_best.pivot_table(
        index="date", columns="indicator",
        values="value_GWh", aggfunc="sum"
    ).fillna(0)

    if "Firm Technical" in pivot and "Firm Booked" in pivot:
        pivot["Firm Available"] = (
            pivot["Firm Technical"] - pivot["Firm Booked"]
        ).clip(lower=0)
    if "Interruptible Total" in pivot and "Interruptible Booked" in pivot:
        pivot["Interruptible Available"] = (
            pivot["Interruptible Total"] - pivot["Interruptible Booked"]
        ).clip(lower=0)

    util_str = ""
    if "Firm Technical" in pivot.columns and "Firm Booked" in pivot.columns:
        t = pivot["Firm Technical"].iloc[-1]
        b = pivot["Firm Booked"].iloc[-1]
        if t > 0:
            util_str = f" | Utilizace: {b/t*100:.0f}%"

    # Fyzický tok z flows parquet
    flows_sub = pd.DataFrame()
    if not df_flows.empty:
        flows_sub = df_flows[
            (df_flows["pointsNames"].str.contains(
                point_label.replace("VIP ", ""), na=False, regex=False))
            & (df_flows["directionKey"] == direction)
        ].copy()
        if not flows_sub.empty:
            flows_sub["date"] = pd.to_datetime(flows_sub["date"], utc=True)
            flows_sub = (flows_sub
                         .groupby("date")["value_GWh"]
                         .sum()
                         .reset_index())

    rows = 2 if cap_type == "Both" else 1
    arrow = "→" if direction == "exit" else "←"
    cap_labels = {
        "Firm":          ["Pevná kapacita"],
        "Interruptible": ["Přerušitelná kapacita"],
        "Both":          ["Pevná kapacita", "Přerušitelná kapacita"],
    }
    subplot_titles = cap_labels[cap_type]

    fig = make_subplots(
        rows=rows, cols=1,
        shared_xaxes=True,
        subplot_titles=subplot_titles,
        vertical_spacing=0.18,
    )
    for ann in fig.layout.annotations:
        ann.update(yshift=10)

    def add_cap_traces(inds, row):
        for ind in inds:
            if ind not in pivot.columns or pivot[ind].sum() == 0:
                continue
            color, mode = COLORS[ind]
            if mode == "lines":
                fig.add_trace(go.Scatter(
                    x=pivot.index, y=pivot[ind],
                    mode="lines", name=ind,
                    line=dict(color=color, width=2),
                    legendgroup=ind,
                    showlegend=(row == 1),
                    hovertemplate=(
                        f"%{{x|%d.%m.%Y}}<br>"
                        f"{ind}: <b>%{{y:.0f}} GWh/d</b>"
                        f"<extra></extra>"
                    ),
                ), row=row, col=1)
            else:
                fig.add_trace(go.Bar(
                    x=pivot.index, y=pivot[ind],
                    name=ind, marker_color=color, opacity=0.85,
                    legendgroup=ind,
                    showlegend=(row == 1),
                    hovertemplate=(
                        f"%{{x|%d.%m.%Y}}<br>"
                        f"{ind}: <b>%{{y:.0f}} GWh/d</b>"
                        f"<extra></extra>"
                    ),
                ), row=row, col=1)

        if row == 1 and not flows_sub.empty:
            fig.add_trace(go.Scatter(
                x=flows_sub["date"],
                y=flows_sub["value_GWh"],
                mode="lines", name="Fyzický tok",
                line=dict(color="#7B1FA2", width=2, dash="dot"),
                legendgroup="flow",
                showlegend=True,
                hovertemplate=(
                    "%{x|%d.%m.%Y}<br>"
                    "Fyzický tok: <b>%{y:.0f} GWh/d</b>"
                    "<extra></extra>"
                ),
            ), row=row, col=1)

    if cap_type == "Firm":
        add_cap_traces(FIRM_INDS, 1)
    elif cap_type == "Interruptible":
        add_cap_traces(INT_INDS, 1)
    elif cap_type == "Both":
        add_cap_traces(FIRM_INDS, 1)
        add_cap_traces(INT_INDS, 2)

    fig.add_vline(
        x=pd.Timestamp.now().timestamp() * 1000,
        line_dash="dot", line_color="#555", line_width=1,
    )

    fig.update_layout(
        title=(
            f"{point_label}  {arrow} {direction.upper()}  "
            f"| {best_op} ({best_tc}){util_str}"
        ),
        height=420 if rows == 1 else 700,
        template="plotly_white",
        barmode="stack",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.08, xanchor="center", x=0.5),
        margin=dict(l=60, r=20, t=70, b=80),
    )
    fig.update_xaxes(
        tickformat="%b %Y", gridcolor="#f0f0f0", title_text="Datum"
    )
    fig.update_yaxes(title_text="GWh/d", gridcolor="#f0f0f0")
    return fig
