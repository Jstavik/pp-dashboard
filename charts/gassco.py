import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import timedelta
from config import year_color, MONTH_TICKS, _base_layout


def fig_gassco_kpi(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return go.Figure()

    max_date  = df["date"].dt.date.max()
    prev_date = max_date - timedelta(days=1)

    today_val = (df[df["date"].dt.date == max_date]
                 .groupby("point")["value_GWh"].sum())
    yest_val  = (df[df["date"].dt.date == prev_date]
                 .groupby("point")["value_GWh"].sum())
    avg7_val  = (df[df["date"].dt.date >= (max_date - timedelta(days=7))]
                 .groupby("point")["value_GWh"].mean())

    kpi = pd.DataFrame({
        "Dnes":  today_val,
        "Včera": yest_val,
        "Avg7d": avg7_val,
    }).fillna(0)
    kpi["DoD"]     = kpi["Dnes"] - kpi["Včera"]
    kpi["vs7d"]    = kpi["Dnes"] - kpi["Avg7d"]
    kpi["DoD_pct"] = kpi["DoD"]  / kpi["Včera"].replace(0, float("nan")) * 100
    kpi["v7d_pct"] = kpi["vs7d"] / kpi["Avg7d"].replace(0, float("nan")) * 100
    # Odděl sumární řádek
    summary = kpi[kpi.index == "Sum Exit Nominations NCS"]
    detail  = kpi[kpi.index != "Sum Exit Nominations NCS"]
    detail  = detail.sort_values("Dnes", ascending=True)
    kpi     = pd.concat([summary, detail])

    fig = make_subplots(
        rows=1, cols=3,
        subplot_titles=[
            f"Nominace {max_date.strftime('%d.%m.%Y')} [GWh/d]",
            "DoD Δ [GWh/d]",
            "vs Ø 7 dní Δ [GWh/d]",
        ],
        horizontal_spacing=0.08,
    )

    fig.add_trace(go.Bar(
        x=kpi["Dnes"], y=kpi.index,
        orientation="h",
        marker_color="#1565C0",
        text=kpi["Dnes"].round(0).astype(int).astype(str),
        textposition="outside",
        hovertemplate="%{y}: <b>%{x:.0f} GWh/d</b><extra></extra>",
        name="Dnes",
    ), row=1, col=1)

    colors_dod = ["#C62828" if v < 0 else "#2E7D32" for v in kpi["DoD"]]
    fig.add_trace(go.Bar(
        x=kpi["DoD"], y=kpi.index,
        orientation="h",
        marker_color=colors_dod,
        text=[f"{v:+.0f}" for v in kpi["DoD"]],
        textposition="outside",
        hovertemplate="%{y}: <b>%{x:+.0f} GWh/d</b><extra></extra>",
        name="DoD Δ",
    ), row=1, col=2)

    colors_7d = ["#C62828" if v < 0 else "#2E7D32" for v in kpi["vs7d"]]
    fig.add_trace(go.Bar(
        x=kpi["vs7d"], y=kpi.index,
        orientation="h",
        marker_color=colors_7d,
        text=[f"{v:+.0f}" for v in kpi["vs7d"]],
        textposition="outside",
        hovertemplate="%{y}: <b>%{x:+.0f} GWh/d</b><extra></extra>",
        name="vs Ø7d Δ",
    ), row=1, col=3)

    fig.update_layout(
        height=320,
        template="plotly_white",
        showlegend=False,
        margin=dict(l=160, r=60, t=50, b=20),
    )
    fig.update_xaxes(gridcolor="#f0f0f0")
    fig.update_yaxes(showticklabels=True,  row=1, col=1)
    fig.update_yaxes(showticklabels=False, row=1, col=2)
    fig.update_yaxes(showticklabels=False, row=1, col=3)
    return fig


def fig_gassco_timeseries(
    df: pd.DataFrame,
    points: list,
    date_from: pd.Timestamp,
    date_to: pd.Timestamp,
) -> go.Figure:
    fig = go.Figure()
    if df.empty:
        return fig

    POINT_COLORS = {
        "Emden":                        "#1565C0",
        "Dornum":                       "#2E7D32",
        "Zeebrugge":                    "#7B1FA2",
        "Nybro":                        "#E65100",
        "Dunkerque":                    "#C62828",
        "Easington":                    "#00838F",
        "St.Fergus":                    "#FF8F00",
        "Fields Delivering into SEGAL": "#AD1457",
    }

    mask = (df["date"] >= date_from) & (df["date"] <= date_to)
    if points:
        mask &= df["point"].isin(points)
    filtered = df[mask].copy()

    for pt in filtered["point"].unique():
        sub = filtered[filtered["point"] == pt].sort_values("date")
        fig.add_trace(go.Scatter(
            x=sub["date"], y=sub["value_GWh"],
            mode="lines", name=pt,
            line=dict(color=POINT_COLORS.get(pt, "#9E9E9E"), width=2),
            hovertemplate=(
                f"<b>{pt}</b><br>"
                f"%{{x|%d.%m.%Y}}: <b>%{{y:.0f}} GWh/d</b>"
                f"<extra></extra>"
            ),
        ))

    fig.add_vline(
        x=pd.Timestamp.now(tz="UTC").timestamp() * 1000,
        line_dash="dot", line_color="#555", line_width=1.5,
        annotation_text="Dnes",
    )
    fig.update_layout(
        title="GASSCO — nominace per výstupní bod [GWh/d]",
        height=380,
        template="plotly_white",
        hovermode="x unified",
        xaxis=dict(tickformat="%d.%m.%Y", gridcolor="#f0f0f0", title="Datum"),
        yaxis=dict(title="GWh/d", gridcolor="#f0f0f0"),
        legend=dict(orientation="h", y=-0.2),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_gassco_seasonality(
    df: pd.DataFrame,
    points: list,
    years: list,
) -> go.Figure:
    fig = go.Figure()
    if df.empty:
        return fig

    filtered = df.copy()
    if points:
        filtered = filtered[filtered["point"].isin(points)]

    filtered["year"]        = filtered["date"].dt.year
    filtered["day_of_year"] = filtered["date"].dt.day_of_year

    agg = (filtered.groupby(["year", "day_of_year"])["value_GWh"]
           .sum().reset_index())

    sel_years = years if years else sorted(agg["year"].unique())[-6:]

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
        title="GASSCO — sezonnost exportu [GWh/d]",
        height=360,
        template="plotly_white",
        hovermode="x unified",
        xaxis=dict(
            title="Den v roce",
            **MONTH_TICKS,
            gridcolor="#f0f0f0",
        ),
        yaxis=dict(title="GWh/d", gridcolor="#f0f0f0"),
        legend=dict(orientation="h", y=-0.2),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def _umm_table(df_umm: pd.DataFrame, title: str) -> go.Figure:
    """Generická tabulka UMM zpráv — používá se pro aktivní i zrušené
    pohledy (fig_gassco_umm_active/fig_gassco_umm_cancelled), stejný
    layout, jiný vstupní (už předfiltrovaný) dataframe a titulek."""
    if df_umm.empty:
        fig = go.Figure()
        fig.update_layout(height=120, margin=dict(l=0, r=0, t=30, b=0), title=title)
        return fig

    cols = ["affectedAsset", "eventStatus", "eventType", "unavailabilityType",
            "technicalCapacity", "availableCapacity", "unavailableCapacity", "unitMeasure",
            "eventStart", "eventStop", "unavailabilityReason"]
    cols = [c for c in cols if c in df_umm.columns]
    sub  = df_umm[cols].fillna("")

    header_labels = []
    for c in cols:
        label = (c.replace("eventStatus", "Status")
                  .replace("eventType", "Type")
                  .replace("unavailabilityType", "Plán/Neplán")
                  .replace("eventStart", "Start")
                  .replace("eventStop", "Stop")
                  .replace("technicalCapacity", "Tech cap")
                  .replace("availableCapacity", "Dostupná")
                  .replace("unavailableCapacity", "Nedostupná")
                  .replace("affectedAsset", "Asset")
                  .replace("unitMeasure", "Jednotka")
                  .replace("unavailabilityReason", "Důvod"))
        header_labels.append(label)

    row_colors = [["#F5F5F5", "white"][i % 2] for i in range(len(sub))]

    fig = go.Figure(go.Table(
        header=dict(
            values=header_labels,
            fill_color="#1565C0",
            font=dict(color="white", size=11),
            align="left",
        ),
        cells=dict(
            values=[sub[c] for c in cols],
            fill_color=[row_colors] * len(cols),
            align="left",
            font=dict(size=10),
        ),
    ))
    fig.update_layout(
        height=max(200, len(sub) * 35 + 60),
        margin=dict(l=0, r=0, t=30, b=0),
        title=title,
    )
    return fig


def fig_gassco_umm_active(df_active: pd.DataFrame) -> go.Figure:
    return _umm_table(df_active, "Aktivní UMM zprávy (odstávky polí)")


def fig_gassco_umm_cancelled(df_cancelled: pd.DataFrame, since_label: str = "") -> go.Figure:
    title = f"Zrušené UMM zprávy{f' od {since_label}' if since_label else ''}"
    return _umm_table(df_cancelled, title)


def _expand_daily(df: pd.DataFrame, days: pd.DatetimeIndex) -> pd.DataFrame:
    """Rozšíří UMM řádky (eventStart/eventStop, jeden řádek = jedno okno
    odstávky) na denní řádky pro outlook graf — analogie k
    charts/electricity_outages.py::_daily_unavail_by_block, ale po assetu
    místo bloku a přes výrazně řidší data (jednotky událostí, ne stovky)."""
    records = []
    for _, row in df.iterrows():
        start, stop = row.get("eventStart"), row.get("eventStop")
        if pd.isna(start) or pd.isna(stop):
            continue
        mask = (days >= start.normalize()) & (days <= stop.normalize())
        val = row.get("unavailableCapacity_GWh") or 0
        asset = row.get("affectedAsset") or "?"
        for d in days[mask]:
            records.append({"date": d, "affectedAsset": asset, "unavailableCapacity_GWh": val})
    return pd.DataFrame(records)


def fig_gassco_umm_outlook(df_active: pd.DataFrame, days_forward: int = 60) -> go.Figure:
    """Výhled nedostupné kapacity podle assetu — analogie k fig_outlook
    u elektřinových odstávek. Bere df_active PŘED filtrem na eventStop
    >= teď (chceme i budoucí, zatím neaktivní odstávky uvnitř okna)."""
    fig = go.Figure()
    if df_active.empty:
        return _base_layout(fig, height=320)

    now = pd.Timestamp.now(tz="UTC").normalize()
    days = pd.date_range(now, now + pd.Timedelta(days=days_forward), freq="D")
    daily = _expand_daily(df_active, days)
    if daily.empty:
        return _base_layout(fig, height=320)

    wide = daily.pivot_table(index="date", columns="affectedAsset",
                              values="unavailableCapacity_GWh", aggfunc="sum").fillna(0)
    palette = px.colors.qualitative.Set2
    for i, asset in enumerate(sorted(wide.columns)):
        series = wide[asset]
        if series.sum() <= 0:
            continue
        color = palette[i % len(palette)]
        fig.add_trace(go.Scatter(
            x=wide.index, y=series.values, stackgroup="out", name=asset,
            line=dict(width=0, color=color),
            hovertemplate=f"{asset}: %{{y:.2f}} GWh/d<extra></extra>",
        ))

    _base_layout(fig, height=320, margin_l=55)
    fig.update_layout(title=f"Výhled nedostupné kapacity — {days_forward} dní dopředu [GWh/d]",
                       hovermode="x unified")
    fig.update_xaxes(title_text="Datum")
    fig.update_yaxes(title_text="GWh/d")
    return fig


def fig_gassco_umm_delta(delta: dict) -> go.Figure:
    """Tabulka 3 kategorií změn (nové/zrušené/změněné) z
    data/gassco.py::compute_umm_delta — jeden sloupec navíc oproti
    _umm_table s kategorií, jinak stejný vzhled."""
    frames = []
    for cat, label in [("new", "🆕 Nové"), ("cancelled", "❌ Zrušené"), ("changed", "✏️ Změněné")]:
        df = delta.get(cat)
        if df is not None and not df.empty:
            sub = df.copy()
            sub["Kategorie"] = label
            frames.append(sub)

    if not frames:
        fig = go.Figure()
        fig.update_layout(height=120, margin=dict(l=0, r=0, t=30, b=0),
                           title="Žádné změny za sledované období")
        return fig

    combined = pd.concat(frames, ignore_index=True)
    cols = ["Kategorie", "affectedAsset", "eventStatus", "revision",
            "eventStart", "eventStop", "unavailableCapacity"]
    cols = [c for c in cols if c in combined.columns]
    sub = combined[cols].fillna("")

    header_labels = [c.replace("affectedAsset", "Asset")
                       .replace("eventStatus", "Status")
                       .replace("revision", "Revize")
                       .replace("eventStart", "Start")
                       .replace("eventStop", "Stop")
                       .replace("unavailableCapacity", "Nedostupná kapacita")
                     for c in cols]
    row_colors = [["#F5F5F5", "white"][i % 2] for i in range(len(sub))]

    fig = go.Figure(go.Table(
        header=dict(values=header_labels, fill_color="#1565C0",
                    font=dict(color="white", size=11), align="left"),
        cells=dict(values=[sub[c] for c in cols],
                   fill_color=[row_colors] * len(cols), align="left", font=dict(size=10)),
    ))
    fig.update_layout(height=max(200, len(sub) * 35 + 60),
                       margin=dict(l=0, r=0, t=30, b=0),
                       title="Δ Změny (nové / zrušené / změněné)")
    return fig
