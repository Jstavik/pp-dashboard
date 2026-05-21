import math
import re
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from data.entsog import POINTS_CONFIG

FLOW_COLORS = [
    "#1565C0","#C62828","#2E7D32","#F57F17","#6A1B9A",
    "#00838F","#E65100","#4527A0","#558B2F","#AD1457",
]


def fig_flow_timeseries(
    df: pd.DataFrame,
    countries: list,
    points: list,
    systems: list,
    directions: list,
    chart_type: str = "Linie",
    height: int = 380,
) -> go.Figure:
    """
    Časová osa fyzických toků.
    df má sloupce: date, countryLabel, pointsNames,
                   adjacentSystemsKey, directionKey, value_GWh
    """
    fig = go.Figure()

    if any([countries, points, systems, directions]):
        mask = pd.Series(True, index=df.index)
        if countries:
            mask &= df["countryLabel"].isin(countries)
        if points:
            mask &= df["pointsNames"].isin(points)
        if systems:
            mask &= df["adjacentSystemsKey"].isin(systems)
        if directions:
            mask &= df["directionKey"].isin(directions)
        filtered = df[mask].copy()
    else:
        filtered = df.copy()

    if filtered.empty:
        fig.add_annotation(
            text="Žádná data pro vybranou kombinaci filtrů",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=14, color="#888"),
        )
        return fig

    group_cols = ["countryLabel", "pointsNames"]
    groups = filtered.groupby(group_cols)

    for i, (key, grp) in enumerate(groups):
        series = grp.groupby("date")["value_GWh"].sum().sort_index()
        label  = f"{key[0]} · {key[1]}"
        color  = FLOW_COLORS[i % len(FLOW_COLORS)]
        if chart_type == "Sloupcový":
            fig.add_trace(go.Bar(
                x=series.index, y=series.values, name=label,
                marker_color=color,
                hovertemplate=f"<b>{label}</b><br>%{{x|%d.%m.%Y}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))
        elif chart_type == "Plocha":
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, mode="lines", name=label,
                line=dict(color=color, width=1.8),
                fill="tozeroy",
                fillcolor="rgba({},{},{},0.2)".format(
                    int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
                ),
                hovertemplate=f"<b>{label}</b><br>%{{x|%d.%m.%Y}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))
        else:
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, mode="lines", name=label,
                line=dict(color=color, width=1.8),
                hovertemplate=f"<b>{label}</b><br>%{{x|%d.%m.%Y}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))

    fig.add_hline(y=0, line_color="black", line_width=0.8)
    if chart_type == "Sloupcový":
        fig.update_layout(barmode="relative")
    fig.update_layout(
        height=height,
        title="Fyzické toky — časová osa [GWh/d]",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(tickformat="%d.%m.%Y", gridcolor="#f0f0f0"),
        yaxis=dict(title="GWh/d", gridcolor="#f0f0f0"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_flow_seasonality(
    df: pd.DataFrame,
    countries: list,
    points: list,
    systems: list,
    directions: list,
    years: list,
    chart_type: str = "Linie",
    height: int = 360,
) -> go.Figure:
    """
    Sezonnost — agregát vybraných filtrů, jedna křivka = jeden rok.
    Osa X = den v roce (1–366).
    """
    YEAR_COLORS = {
        2020: "#BDBDBD", 2021: "#90A4AE", 2022: "#42A5F5",
        2023: "#1565C0", 2024: "#F57F17", 2025: "#C62828",
        2026: "#2E7D32",
    }

    fig = go.Figure()

    if any([countries, points, systems, directions]):
        mask = pd.Series(True, index=df.index)
        if countries:
            mask &= df["countryLabel"].isin(countries)
        if points:
            mask &= df["pointsNames"].isin(points)
        if systems:
            mask &= df["adjacentSystemsKey"].isin(systems)
        if directions:
            mask &= df["directionKey"].isin(directions)
        filtered = df[mask].copy()
    else:
        filtered = df.copy()

    if filtered.empty:
        fig.add_annotation(
            text="Žádná data pro vybranou kombinaci filtrů",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=14, color="#888"),
        )
        return fig

    filtered["date"]        = pd.to_datetime(filtered["date"])
    filtered["year"]        = filtered["date"].dt.year
    filtered["day_of_year"] = filtered["date"].dt.day_of_year

    sel_years = years if years else sorted(filtered["year"].unique())

    for yr in sorted(sel_years):
        grp = filtered[filtered["year"] == yr]
        if grp.empty:
            continue
        series = grp.groupby("day_of_year")["value_GWh"].sum().sort_index()
        color  = YEAR_COLORS.get(yr, "#9E9E9E")
        width  = 2.5 if yr == pd.Timestamp.now().year else 1.5
        if chart_type == "Sloupcový":
            fig.add_trace(go.Bar(
                x=series.index, y=series.values, name=str(yr),
                marker_color=color,
                hovertemplate=f"<b>{yr}</b><br>Den %{{x}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))
        elif chart_type == "Plocha":
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, mode="lines", name=str(yr),
                line=dict(color=color, width=width),
                fill="tozeroy",
                fillcolor="rgba({},{},{},0.2)".format(
                    int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
                ),
                hovertemplate=f"<b>{yr}</b><br>Den %{{x}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))
        else:
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, mode="lines", name=str(yr),
                line=dict(color=color, width=width),
                hovertemplate=f"<b>{yr}</b><br>Den %{{x}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))

    fig.add_hline(y=0, line_color="black", line_width=0.8)
    if chart_type == "Sloupcový":
        fig.update_layout(barmode="group")
    fig.update_layout(
        height=height,
        title="Sezonnost fyzických toků [GWh/d]",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(
            title="Den v roce",
            tickvals=[1,32,60,91,121,152,182,213,244,274,305,335],
            ticktext=["Led","Úno","Bře","Dub","Kvě","Čvn",
                      "Čvc","Srp","Zář","Říj","Lis","Pro"],
            gridcolor="#f0f0f0",
        ),
        yaxis=dict(title="GWh/d", gridcolor="#f0f0f0"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig

def fig_gas_flows_bar(pivot: pd.DataFrame, height: int = 380) -> go.Figure:
    """Sloupcový graf fyzických toků CZ — netto GWh/d."""
    colors = {
        "Brandov/Waidhaus (DE)": "#1565C0",
        "Lanžhot (SK)":          "#2E7D32",
        "Český Těšín (PL)":      "#F57F17",
        "Zásobníky":             "#6A1B9A",
        "Distribuce":            "#C62828",
        "Koneční spotřebitelé":  "#E65100",
    }
    fig = go.Figure()
    for col in pivot.columns:
        if pivot[col].abs().sum() < 0.1:
            continue
        fig.add_trace(go.Bar(
            x=pivot.index, y=pivot[col],
            name=col,
            marker_color=colors.get(col, "#9E9E9E"),
            hovertemplate=f"<b>{col}</b><br>%{{x|%d.%m.%Y}}<br>%{{y:.1f}} GWh/d<extra></extra>",
        ))
    fig.add_hline(y=0, line_color="black", line_width=0.8)
    fig.update_layout(
        barmode="relative", height=height, template="plotly_white",
        hovermode="x unified",
        title="Fyzické toky plynu CZ — netto (+ import, − export) [GWh/d]",
        legend=dict(orientation="h", y=-0.15),
        xaxis=dict(tickformat="%d.%m", gridcolor="#f0f0f0"),
        yaxis=dict(title="GWh/d"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_gas_point_history(pivot: pd.DataFrame, point: str, height: int = 260) -> go.Figure:
    """Historický graf pro jeden hraniční přechod."""
    fig = go.Figure()
    if point not in pivot.columns:
        return fig
    series = pivot[point]
    color  = "#1565C0" if series.iloc[-1] >= 0 else "#C62828"
    fig.add_trace(go.Scatter(
        x=series.index, y=series.values, mode="lines", name=point,
        line=dict(color=color, width=2),
        fill="tozeroy", fillcolor=color.replace(")", ",0.1)").replace("rgb","rgba"),
        hovertemplate="%{x|%d.%m.%Y}  %{y:.1f} GWh/d<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="black", line_width=0.8)
    fig.update_layout(
        height=height, template="plotly_white", hovermode="x unified",
        title=f"Historický tok — {point}",
        xaxis=dict(tickformat="%d.%m", gridcolor="#f0f0f0"),
        yaxis=dict(title="GWh/d  (+ import, − export)"),
        margin=dict(l=60, r=20, t=50, b=40),
    )
    return fig


def fig_gas_map(df_history: pd.DataFrame, df_gie=None, height: int = 800) -> go.Figure:
    """Scattermapbox — CEE physical gas flow map, GasConnect-style."""

    DOMESTIC = {"Storage", "Distribution", "Final Consumers",
                "Production", "LNG Terminals", "Storage|Transmission"}

    CODE2C = {
        "AT":"Austria","BE":"Belgium","BG":"Bulgaria","CH":"Switzerland",
        "CZ":"Czechia","DE":"Germany","DK":"Denmark","EE":"Estonia",
        "ES":"Spain","FI":"Finland","FR":"France","GR":"Greece",
        "HR":"Croatia","HU":"Hungary","IE":"Ireland","IT":"Italy",
        "LT":"Lithuania","LU":"Luxemburg","LV":"Latvia","NL":"Netherlands",
        "NO":"Norway","PL":"Poland","PT":"Portugal","RO":"Romania",
        "SI":"Slovenia","SK":"Slovakia","UA":"Ukraine","UK":"United Kingdom",
    }
    C2CODE = {v: k for k, v in CODE2C.items()}

    # ── helpers ───────────────────────────────────────────────────
    def _neighbor(adj):
        if adj in DOMESTIC:
            return None
        codes = set(re.findall(r"Transmission([A-Z]{2})", adj))
        return CODE2C.get(codes.pop()) if codes else None

    def _bilateral(day):
        """Returns {(a,b): net} a<b alphabetically.
        Positive = a imports from b."""
        sub = df[(df["date"] == day)
                 & ~df["adjacentSystemsKey"].isin(DOMESTIC)].copy()
        sub["nb"] = sub["adjacentSystemsKey"].apply(_neighbor)
        sub = sub[sub["nb"].notna() & (sub["countryLabel"] != sub["nb"])]
        pairs = {}
        for (c, nb), g in sub.groupby(["countryLabel", "nb"]):
            e = g[g["directionKey"] == "entry"]["value_GWh"].sum()
            x = g[g["directionKey"] == "exit"]["value_GWh"].sum()
            net = e - x
            key = tuple(sorted([c, nb]))
            if key not in pairs:
                pairs[key] = net if c == key[0] else -net
        return pairs

    def _north_sea(day):
        sub = df[(df["date"] == day)
                 & (df["countryLabel"] == "Germany")
                 & (df["directionKey"] == "entry")
                 & df["pointsNames"].str.contains(
                     "Emden|Dornum", na=False, regex=True)]
        return sub["value_GWh"].sum()

    # ── Crossing definitions ──────────────────────────────────────
    # (name, lat, lon, source, key, label_offset_lat, label_offset_lon)
    CROSSINGS = [
        ("Emden/Dornum",    54.35,  7.50, "ns", None,                      0.0,   0.9),
        ("Mallnow",         52.45, 14.50, "bi", ("Germany","Poland"),       0.3,   0.7),
        ("Bunde",           53.20,  7.20, "bi", ("Germany","Netherlands"),  0.3,  -1.1),
        ("Eynatten",        50.70,  6.08, "bi", ("Belgium","Germany"),      0.3,  -0.9),
        ("Ellund",          54.80,  9.30, "bi", ("Denmark","Germany"),      0.3,   0.7),
        ("Medelsheim",      49.14,  7.18, "bi", ("France","Germany"),      -0.35, -0.7),
        ("Wallbach",        47.57,  7.88, "bi", ("Germany","Switzerland"), -0.35, -0.5),
        ("Oberkappel",      48.50, 13.70, "bi", ("Austria","Germany"),      0.0,  -0.9),
        ("Brandov",         50.61, 13.39, "bi", ("Czechia","Germany"),      0.3,  -0.8),
        ("Lanžhot",         48.72, 17.04, "bi", ("Czechia","Slovakia"),    -0.35,  0.0),
        ("Č. Těšín",        49.75, 18.62, "bi", ("Czechia","Poland"),       0.3,   0.5),
        ("Baumgarten",      48.10, 16.90, "bi", ("Austria","Slovakia"),     0.3,   0.5),
        ("Arnoldstein",     46.55, 13.70, "bi", ("Austria","Italy"),       -0.35, -0.8),
        ("Murfeld",         46.70, 15.90, "bi", ("Austria","Slovenia"),    -0.35,  0.5),
        ("Mosonmagyaróvár", 47.87, 17.27, "bi", ("Austria","Hungary"),    -0.35,  0.5),
        ("V. Kapušany",     48.68, 22.08, "bi", ("Slovakia","Ukraine"),     0.3,   0.5),
        ("Csanádpalota",    46.25, 20.73, "bi", ("Hungary","Romania"),     -0.35,  0.5),
        ("Gorizia",         45.95, 13.63, "bi", ("Italy","Slovenia"),      -0.25, -0.7),
    ]

    COUNTRY_LABELS = {
        "DE":(51.5,10.0), "CZ":(49.8,15.5), "AT":(47.3,13.5),
        "SK":(48.8,19.8), "PL":(52.0,19.5), "HU":(47.0,19.5),
        "IT":(44.0,11.0), "SI":(46.0,14.5), "CH":(46.8,8.0),
        "FR":(47.0,3.0),  "BE":(50.8,4.0),  "NL":(52.5,5.0),
        "DK":(55.5,9.5),  "UA":(49.5,26.0), "RO":(45.5,24.0),
        "HR":(45.3,16.0),
    }

    STORAGE_NODES = {
        "DE": (51.5, 10.5, "🇩🇪"),
        "AT": (47.5, 14.0, "🇦🇹"),
        "CZ": (49.8, 16.5, "🇨🇿"),
        "SK": (48.5, 18.5, "🇸🇰"),
        "HU": (47.0, 18.5, "🇭🇺"),
        "IT": (44.5, 11.0, "🇮🇹"),
        "FR": (46.5,  2.5, "🇫🇷"),
        "NL": (52.3,  5.5, "🇳🇱"),
    }

    GREEN = "#2E7D32"
    GREY = "#9E9E9E"

    fig = go.Figure()

    # ── empty guard ───────────────────────────────────────────────
    if df_history.empty:
        fig.add_annotation(
            text="Žádná data", x=0.5, y=0.5,
            xref="paper", yref="paper", showarrow=False)
        _mapbox_layout(fig, height, "N/A")
        return fig

    # ── data prep ─────────────────────────────────────────────────
    df = df_history.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df["value_GWh"] = pd.to_numeric(
        df.get("value_GWh", 0), errors="coerce").fillna(0)

    # Smart date: latest with CZ data (ENTSOG publishes with delay)
    cz_dates = sorted(
        df[df["countryLabel"] == "Czechia"]["date"].unique())
    if cz_dates:
        last_date = cz_dates[-1]
        prev_date = cz_dates[-2] if len(cz_dates) > 1 else pd.NaT
    else:
        last_date = df["date"].max()
        prev_date = pd.NaT

    fl = _bilateral(last_date)
    fp = _bilateral(prev_date) if pd.notna(prev_date) else {}
    ns_last = _north_sea(last_date)
    ns_prev = (
        _north_sea(prev_date) if pd.notna(prev_date) else 0.0)
    date_label = (
        last_date.strftime("%d.%m.%Y")
        if pd.notna(last_date) else "N/A")

    # ── render crossings ──────────────────────────────────────────
    for name, lat, lon, src, key, olat, olon in CROSSINGS:
        if src == "bi":
            val = fl.get(key, 0.0)
            val_prev = fp.get(key, 0.0)
            a_code = C2CODE.get(key[0], "??")
            b_code = C2CODE.get(key[1], "??")
            if val >= 0:
                fr, to = b_code, a_code
            else:
                fr, to = a_code, b_code
        elif src == "ns":
            val, val_prev = ns_last, ns_prev
            fr, to = "NS", "DE"
        else:
            continue

        absval = abs(val)

        # DoD %
        if abs(val_prev) > 0.5:
            dod_pct = (val - val_prev) / abs(val_prev) * 100
            sym = ("▲" if dod_pct > 0.5
                   else "▼" if dod_pct < -0.5 else "–")
            dod_str = f"{sym}{abs(dod_pct):.0f}%"
        else:
            dod_str = ""

        # Zero flow
        if absval < 0.1:
            fig.add_trace(go.Scattermapbox(
                lat=[lat], lon=[lon],
                mode="markers+text",
                marker=dict(size=4, color=GREY, opacity=0.3),
                text=[name],
                textfont=dict(size=7, color=GREY),
                textposition="top right",
                showlegend=False,
                hovertemplate=(
                    f"<b>{name}</b><br>"
                    f"0 GWh/d<extra></extra>")))
            continue

        sz = max(8, min(20, absval ** 0.5 * 2.5))

        # Green circle
        fig.add_trace(go.Scattermapbox(
            lat=[lat], lon=[lon],
            mode="markers",
            marker=dict(size=sz, color=GREEN, opacity=0.85),
            showlegend=False,
            hovertemplate=(
                f"<b>{name}</b><br>"
                f"{absval:.1f} GWh/d  {fr}→{to}<br>"
                f"DoD: {dod_str if dod_str else 'N/A'}"
                f"<extra></extra>")))

        # Annotation
        lbl = (f"<b>{name}</b>\n{absval:.0f} {fr}→{to}  {dod_str}"
               if dod_str
               else f"<b>{name}</b>\n{absval:.0f} {fr}→{to}")
        fig.add_trace(go.Scattermapbox(
            lat=[lat + olat], lon=[lon + olon],
            mode="text",
            text=[lbl],
            textfont=dict(size=9, color="#1B5E20", family="Arial"),
            showlegend=False,
            hoverinfo="skip"))

    # ── country labels ────────────────────────────────────────────
    for code, (clat, clon) in COUNTRY_LABELS.items():
        fig.add_trace(go.Scattermapbox(
            lat=[clat], lon=[clon],
            mode="text",
            text=[code],
            textfont=dict(
                size=15,
                color="rgba(120,120,120,0.3)",
                family="Arial Black"),
            showlegend=False,
            hoverinfo="skip"))

    # ── storage circles ───────────────────────────────────────────
    if df_gie is not None and not df_gie.empty:
        df_gie = df_gie.copy()
        df_gie["gasDayStart"] = pd.to_datetime(df_gie["gasDayStart"], utc=True)
        last_gie = (df_gie.sort_values("gasDayStart")
                         .groupby("country_code").last().reset_index())

        for _, row in last_gie.iterrows():
            cc = row["country_code"]
            if cc not in STORAGE_NODES:
                continue
            lat, lon, flag = STORAGE_NODES[cc]
            full = float(row.get("full", 0) or 0)
            gas  = float(row.get("gasInStorage", 0) or 0)
            date_str = row["gasDayStart"].strftime("%d.%m.%Y")

            fig.add_trace(go.Scattermapbox(
                lat=[lat], lon=[lon],
                mode="markers",
                marker=dict(
                    size=28, color="#E0E0E0", opacity=0.9,
                    sizemode="diameter",
                ),
                hoverinfo="skip",
                showlegend=False,
            ))

            color = (
                "#C62828" if full < 25 else
                "#FF8F00" if full < 50 else
                "#1565C0" if full < 75 else
                "#2E7D32"
            )
            fig.add_trace(go.Scattermapbox(
                lat=[lat], lon=[lon],
                mode="markers+text",
                marker=dict(
                    size=28, color=color,
                    opacity=max(0.3, full / 100),
                    sizemode="diameter",
                ),
                text=[f"{full:.0f}%"],
                textfont=dict(size=9, color="white", family="Arial Black"),
                textposition="middle center",
                hovertemplate=(
                    f"<b>{flag} {cc} Zásobníky</b><br>"
                    f"Plnost: <b>{full:.1f}%</b><br>"
                    f"Objem: {gas:.1f} TWh<br>"
                    f"Datum: {date_str}"
                    "<extra></extra>"
                ),
                showlegend=False,
            ))

    _mapbox_layout(fig, height, date_label)
    return fig


def _mapbox_layout(
    fig: go.Figure, height: int, date_label: str
) -> None:
    """Mapbox layout — carto-positron, no token needed."""
    GREEN = "#2E7D32"
    fig.update_layout(
        mapbox=dict(
            style="carto-positron",
            center=dict(lat=49.0, lon=13.5),
            zoom=4.3,
        ),
        height=height,
        margin=dict(l=0, r=0, t=55, b=0),
        title=dict(
            text=(
                f"<b>Fyzické toky plynu — CEE Flowchart</b>"
                f"<br><span style='font-size:11px;color:#757575'>"
                f"Gasday: {date_label} · 06:00–06:00 CET"
                f" · GWh/d · ENTSO-G TP</span>"
                f"<br><span style='font-size:10px;color:{GREEN}'>"
                f"● hraniční přechod · hodnoty GWh/d"
                f" · ▲▼ DoD %</span>"
            ),
            font=dict(size=15, color="#212121"),
            x=0.01,
        ),
        showlegend=False,
        autosize=True,
    )


# backward compat alias
_geo_layout = _mapbox_layout
