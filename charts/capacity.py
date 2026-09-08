import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

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

    data_start   = pd.to_datetime(sub["periodFrom"], utc=True).min().date()
    data_end     = pd.to_datetime(sub["periodTo"],   utc=True).max().date()
    target_dates = list(pd.date_range(data_start, data_end, freq="W").date)
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


def fig_flow_stacked(df_flow: pd.DataFrame, country_label: str, direction: str) -> go.Figure:
    """Stacked Physical Flow za VŠECHNY body dané země/směru — jeden trace
    na jednu DISTINCT hodnotu pointsNames (i "A|B" kombinovaný název —
    ENTSOG ho takhle publikuje jako jeden bod, nikdy se nerozdělí ani
    nesčítá napříč různými pointsNames, viz app.py::tab_cap). Duplicitní
    řádky pro STEJNOU (date, pointsNames) dvojici (různé adjacentSystemsKey
    do stejného bodu) se sečtou — to je legitimní, pořád jeden fyzický bod.

    Klikací legenda je nativní Plotly chování (žádný extra kód) — uživatel
    v prohlížeči postupně skrývá body kliknutím, dokud nezůstane jeden."""
    if df_flow.empty:
        return go.Figure()

    pivot = (
        df_flow.groupby(["date", "pointsNames"])["value_GWh"]
        .sum()
        .unstack("pointsNames")
        .fillna(0)
        .sort_index()
    )

    fig = go.Figure()
    for pt in pivot.columns:
        fig.add_trace(go.Scatter(
            x=pivot.index, y=pivot[pt],
            mode="lines", name=pt,
            stackgroup="flow",
            line=dict(width=0.5),
            hovertemplate=f"%{{x|%d.%m.%Y}}<br>{pt}: <b>%{{y:.0f}} GWh/d</b><extra></extra>",
        ))

    arrow = "→" if direction == "exit" else "←"
    fig.update_layout(
        title=f"Fyzický tok — {country_label} {arrow} {direction.upper()}  "
              f"(klikni v legendě pro izolaci jednoho bodu)",
        template="plotly_white",
        height=520,
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.3, xanchor="center", x=0.5, font=dict(size=10)),
        margin=dict(l=60, r=20, t=50, b=140),
    )
    fig.update_xaxes(tickformat="%d.%m.%Y", gridcolor="#f0f0f0", title_text="Datum")
    fig.update_yaxes(title_text="GWh/d", gridcolor="#f0f0f0")
    return fig


_INTERRUPTION_COLOR = {"unplanned": "#C62828", "planned": "#E65100", "actual": "#6A1B9A"}


def _interruption_kind(indicator: str) -> str:
    low = str(indicator).lower()
    if "unplanned" in low:
        return "unplanned"
    if "actual" in low:
        return "actual"
    return "planned"


def fig_point_capacity(
    point_label: str,
    operator_label: str,
    direction: str,
    flow_series: pd.Series,
    capacity_layers: dict,
    interruptions: pd.DataFrame,
) -> go.Figure:
    """Hlavní detail graf pro JEDEN bod+operátor+směr — vrstvy se přidávají
    postupně (viz app.py::tab_cap checkboxy), tahle funkce jen vykreslí,
    co dostane:

    flow_series: date-indexed pd.Series (GWh/d) — fyzický tok pro
        izolovaný bod (z stackovaného grafu výš, JEDNA pointsNames
        hodnota, ne suma přes body).
    capacity_layers: {"Firm Technical": pd.Series|None, "Firm Available":
        pd.Series|None, ...} — každá řada už MUSÍ být výstup
        data/entsog_operational.py::active_capacity_dedup() (nikdy
        prostý .sum() přes revize, viz tam). Řady nemusí mít stejný
        rozsah dat.
    interruptions: DataFrame s periodFrom_dt/periodTo_dt/indicator/
        value_GWh — už vyfiltrované na value_GWh > 0 (nulové placeholder
        záznamy vyloučené volajícím) a dedup'nuté na skutečně distinct
        segmenty.

    Konec dostupných dat: šedá čárkovaná svislice + popisek na
    NEJPOZDĚJŠÍM datu, co má aspoň jedna zobrazená vrstva reálnou
    hodnotu — čára v žádné vrstvě nikdy nespadne na 0, prostě tam,
    kde reálná data končí, končí i ta čára (Scatter s chybějícími
    dny za tímhle bodem, ne vyplněná nulami)."""
    fig = go.Figure()
    all_last_dates = []

    def _naive(ts) -> pd.Timestamp:
        # flow_series index je tz-aware (Europe/Prague, z df_hist), kapacitní
        # vrstvy (active_capacity_dedup) jsou tz-naive — max() přes obojí
        # dohromady by na tz-mixu spadl, sjednoť na naive jen pro tohle srovnání.
        ts = pd.Timestamp(ts)
        return ts.tz_localize(None) if ts.tzinfo is not None else ts

    if flow_series is not None and not flow_series.empty:
        fig.add_trace(go.Scatter(
            x=flow_series.index, y=flow_series.values,
            mode="lines", name="Fyzický tok",
            line=dict(color="#7B1FA2", width=2, dash="dot"),
            hovertemplate="%{x|%d.%m.%Y}<br>Fyzický tok: <b>%{y:.0f} GWh/d</b><extra></extra>",
        ))
        all_last_dates.append(_naive(flow_series.index.max()))

    cap_colors = {"Firm Technical": "#C62828", "Firm Available": "#43A047",
                  "Interruptible Technical": "#E65100", "Interruptible Available": "#FFA000"}
    for name, series in capacity_layers.items():
        if series is None or series.empty:
            continue
        color = cap_colors.get(name, "#1565C0")
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values,
            mode="lines", name=name,
            line=dict(color=color, width=2),
            hovertemplate=f"%{{x|%d.%m.%Y}}<br>{name}: <b>%{{y:.0f}} GWh/d</b><extra></extra>",
        ))
        all_last_dates.append(_naive(series.index.max()))

    if not interruptions.empty:
        legend_shown = set()
        for _, r in interruptions.iterrows():
            kind = _interruption_kind(r["indicator"])
            color = _INTERRUPTION_COLOR[kind]
            x0 = pd.Timestamp(r["periodFrom_dt"]).timestamp() * 1000
            x1 = pd.Timestamp(r["periodTo_dt"]).timestamp() * 1000
            fig.add_vrect(
                x0=x0, x1=x1,
                fillcolor=color, opacity=0.15, line_width=0,
            )
            # Neviditelný trace jen kvůli legendě — vrect samo legendu nemá
            if kind not in legend_shown:
                fig.add_trace(go.Scatter(
                    x=[None], y=[None], mode="markers",
                    marker=dict(size=10, color=color, symbol="square"),
                    name=f"Odstávka ({kind})",
                ))
                legend_shown.add(kind)

    if all_last_dates:
        end_of_data = max(all_last_dates).timestamp() * 1000
        fig.add_vline(
            x=end_of_data, line_dash="dash", line_color="#888", line_width=1.5,
        )
        fig.add_annotation(
            x=end_of_data, y=1, yref="paper", yanchor="bottom",
            text="konec dostupných dat", showarrow=False,
            font=dict(size=10, color="#888"),
        )

    arrow = "→" if direction == "exit" else "←"
    fig.update_layout(
        title=f"{point_label}  {arrow} {direction.upper()}  |  {operator_label}",
        template="plotly_white",
        height=480,
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2, xanchor="center", x=0.5),
        margin=dict(l=60, r=20, t=50, b=100),
    )
    fig.update_xaxes(tickformat="%d.%m.%Y", gridcolor="#f0f0f0", title_text="Datum")
    fig.update_yaxes(title_text="GWh/d", gridcolor="#f0f0f0")
    return fig
