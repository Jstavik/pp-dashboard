import pandas as pd
import plotly.graph_objects as go


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
