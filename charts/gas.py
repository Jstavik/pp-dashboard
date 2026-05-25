import math
import re
import numpy as np
import requests
import xml.etree.ElementTree as ET
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from datetime import date, timedelta
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

    filtered["date"] = filtered["date"].dt.tz_convert("Europe/Prague").dt.normalize()

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

    filtered["date"]        = filtered["date"].dt.tz_convert("Europe/Prague")
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


COUNTRY_CODES_MAP = {
    "AT": "Austria",      "BE": "Belgium",       "BG": "Bulgaria",
    "HR": "Croatia",      "CZ": "Czechia",       "DK": "Denmark",
    "EE": "Estonia",      "FI": "Finland",       "FR": "France",
    "DE": "Germany",      "GR": "Greece",        "HU": "Hungary",
    "IE": "Ireland",      "IT": "Italy",         "LV": "Latvia",
    "LT": "Lithuania",    "LU": "Luxemburg",     "NL": "Netherlands",
    "PL": "Poland",       "PT": "Portugal",      "RO": "Romania",
    "SK": "Slovakia",     "SI": "Slovenia",      "ES": "Spain",
    "CH": "Switzerland",  "GB": "United Kingdom","NO": "Norway",
    "UA": "Ukraine",      "RS": "Serbia",        "TR": "Turkey",
}

NEIGHBORS_MAP = {
    "Austria":        ["Germany","Italy","Switzerland","Slovenia",
                       "Slovakia","Hungary","Czechia"],
    "Belgium":        ["Netherlands","Germany","France","United Kingdom"],
    "Bulgaria":       ["Romania","Greece","Serbia"],
    "Croatia":        ["Slovenia","Hungary","Serbia"],
    "Czechia":        ["Germany","Austria","Slovakia","Poland"],
    "Denmark":        ["Germany"],
    "Estonia":        ["Latvia","Finland"],
    "Finland":        ["Estonia"],
    "France":         ["Belgium","Germany","Switzerland","Italy","Spain"],
    "Germany":        ["Netherlands","Belgium","France","Switzerland",
                       "Austria","Czechia","Poland","Denmark"],
    "Greece":         ["Bulgaria"],
    "Hungary":        ["Austria","Slovakia","Ukraine","Romania",
                       "Serbia","Croatia","Slovenia"],
    "Ireland":        ["United Kingdom"],
    "Italy":          ["France","Switzerland","Austria","Slovenia"],
    "Latvia":         ["Estonia","Lithuania"],
    "Lithuania":      ["Latvia","Poland"],
    "Netherlands":    ["Germany","Belgium","United Kingdom"],
    "Poland":         ["Germany","Czechia","Slovakia","Lithuania"],
    "Portugal":       ["Spain"],
    "Romania":        ["Hungary","Bulgaria","Serbia"],
    "Slovakia":       ["Czechia","Austria","Poland","Ukraine","Hungary"],
    "Slovenia":       ["Austria","Italy","Croatia","Hungary"],
    "Spain":          ["France","Portugal"],
    "Switzerland":    ["Germany","France","Italy","Austria"],
    "United Kingdom": ["Belgium","Netherlands","Ireland"],
    "Ukraine":        ["Slovakia","Hungary","Poland","Romania"],
}

CENTERS_MAP = {
    "Austria":        (47.5, 14.5),
    "Belgium":        (50.5,  4.5),
    "Bulgaria":       (42.7, 25.5),
    "Croatia":        (45.2, 15.5),
    "Czechia":        (49.8, 15.5),
    "Denmark":        (56.0, 10.0),
    "Estonia":        (58.7, 25.0),
    "Finland":        (64.0, 26.0),
    "France":         (46.5,  2.5),
    "Germany":        (51.0, 10.0),
    "Greece":         (39.5, 22.0),
    "Hungary":        (47.2, 19.3),
    "Ireland":        (53.5, -7.5),
    "Italy":          (42.5, 12.5),
    "Latvia":         (56.8, 24.8),
    "Lithuania":      (55.5, 24.0),
    "Luxemburg":      (49.8,  6.2),
    "Netherlands":    (52.3,  5.3),
    "Poland":         (52.0, 19.5),
    "Portugal":       (39.5, -8.0),
    "Romania":        (45.8, 24.8),
    "Slovakia":       (48.7, 19.5),
    "Slovenia":       (46.1, 14.8),
    "Spain":          (40.0, -3.5),
    "Switzerland":    (47.0,  8.3),
    "United Kingdom": (53.5, -1.5),
    "Ukraine":        (49.0, 32.0),
    "Norway":         (63.0,  8.0),
}

STORAGE_COORDS_MAP = {
    "AT": (47.8, 15.5),
    "BE": (50.5,  4.5),
    "CZ": (49.2, 16.8),
    "DE": (52.5, 11.0),
    "FR": (47.0,  3.5),
    "HR": (45.2, 16.0),
    "HU": (47.2, 19.3),
    "IT": (42.5, 12.5),
    "LV": (56.8, 24.8),
    "NL": (52.8,  6.5),
    "PL": (52.0, 19.5),
    "PT": (39.5, -8.0),
    "RO": (45.8, 24.8),
    "SK": (48.5, 20.0),
    "ES": (40.0, -3.5),
    "UA": (49.0, 32.0),
}

NO_EXITS_MAP = {
    "Dornum":    (53.3,  7.3, "Germany"),
    "Emden":     (53.4,  7.2, "Germany"),
    "Nybro":     (55.7,  8.8, "Denmark"),
    "Dunkerque": (51.0,  2.4, "France"),
    "Zeebrugge": (51.3,  3.2, "Belgium"),
    "Easington": (53.7, -0.1, "United Kingdom"),
    "St.Fergus": (57.5, -1.9, "United Kingdom"),
}

_TAP_COORDS = {
    "Melendugno": (40.1, 18.3, "Melendugno (TAP/AZ)"),
    "Mazara":     (37.6, 12.5, "Mazara (Alžírsko)"),
    "Gela":       (37.5, 15.1, "Gela (Sev. Afrika)"),
}

MSMM3_TO_GWH = 10.55


def _make_bar(full_pct, width=8):
    filled = round(full_pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _storage_color(full_pct):
    if full_pct < 25: return "#C62828"
    if full_pct < 50: return "#FF8F00"
    if full_pct < 75: return "#1565C0"
    return "#2E7D32"


def _shorten(lat1, lon1, lat2, lon2, margin=0.25):
    dlat, dlon = lat2 - lat1, lon2 - lon1
    return (lat1 + dlat*margin, lon1 + dlon*margin,
            lat2 - dlat*margin, lon2 - dlon*margin)


def _add_arrow(fig, lat1, lon1, lat2, lon2, color, size=0.2):
    dlat, dlon = lat2 - lat1, lon2 - lon1
    angle = np.arctan2(dlat, dlon)
    left_lat  = lat2 - size*np.sin(angle) + size*0.4*np.cos(angle)
    left_lon  = lon2 - size*np.cos(angle) - size*0.4*np.sin(angle)
    right_lat = lat2 - size*np.sin(angle) - size*0.4*np.cos(angle)
    right_lon = lon2 - size*np.cos(angle) + size*0.4*np.sin(angle)
    fig.add_trace(go.Scattermapbox(
        lat=[lat2, left_lat, right_lat, lat2],
        lon=[lon2, left_lon, right_lon, lon2],
        mode="lines", fill="toself",
        fillcolor=color, line=dict(width=0, color=color),
        hoverinfo="skip", showlegend=False,
    ))


def _draw_flow(fig, c_from, c_to, val, dod_pct, color, label=None):
    if c_from not in CENTERS_MAP or c_to not in CENTERS_MAP:
        return
    la1, lo1 = CENTERS_MAP[c_from]
    la2, lo2 = CENTERS_MAP[c_to]
    s1, s2, e1, e2 = _shorten(la1, lo1, la2, lo2, 0.25)
    mid_lat = (s1 + e1) / 2 + 0.5
    mid_lon = (s2 + e2) / 2
    width = max(1.5, min(8, val / 150))
    dod_str = f"+{dod_pct:.0f}%" if dod_pct >= 0 else f"{dod_pct:.0f}%"
    lbl = label or f"{c_from}→{c_to}"
    fig.add_trace(go.Scattermapbox(
        lat=[s1, e1], lon=[s2, e2], mode="lines",
        line=dict(width=width, color=color),
        hoverinfo="skip", showlegend=False,
    ))
    _add_arrow(fig, s1, s2, e1, e2, color, size=0.2)
    fig.add_trace(go.Scattermapbox(
        lat=[mid_lat], lon=[mid_lon], mode="text",
        text=[f"{lbl}\n{val:.0f} GWh/d\nDoD {dod_str}"],
        textfont=dict(size=8, color=color),
        hovertemplate=(
            f"<b>{lbl}</b><br>"
            f"Tok: <b>{val:.0f} GWh/d</b><br>"
            f"DoD: <b>{dod_str}</b><extra></extra>"
        ),
        showlegend=False,
    ))


def _get_flows_clean(df, target_date):
    sub = df[
        df["date"].dt.tz_convert("Europe/Prague").dt.date == target_date
    ].drop_duplicates(
        subset=["countryLabel", "directionKey",
                "adjacentSystemsKey", "pointsNames"]
    ).copy()
    sub = sub[sub["adjacentSystemsKey"].str.match(
        r"Transmission[A-Z]{2}", na=False)]
    sub["nb_code"] = sub["adjacentSystemsKey"].str.extract(
        r"Transmission([A-Z]{2})")
    sub["nb"] = sub["nb_code"].map(COUNTRY_CODES_MAP)
    sub = sub[sub["nb"].notna() & (sub["countryLabel"] != sub["nb"])]
    return (sub[sub["directionKey"] == "exit"]
            .groupby(["countryLabel", "nb"])["value_GWh"]
            .sum().reset_index())


def _fetch_no_flows():
    try:
        resp = requests.get(
            "https://umm.gassco.no/realTimeAtom.xml",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        root = ET.fromstring(resp.text)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        result = {}
        for entry in root.findall("atom:entry", ns):
            title   = entry.findtext("atom:title", "", ns)
            content = entry.findtext("atom:content", "0", ns)
            name = (title.replace("Exit Nomination ", "")
                         .replace(" (MSm3)", "").strip())
            try:
                result[name] = float(content) * MSMM3_TO_GWH
            except Exception:
                result[name] = 0.0
        return result
    except Exception:
        return {}


def fig_gas_map(
    df_history: pd.DataFrame,
    df_gie: pd.DataFrame = None,
    df_gassco: pd.DataFrame = None,
    show_storage: bool = True,
) -> go.Figure:
    """Mapa fyzických toků plynu — Evropa. D-2 ENTSO-G + live NO + GIE zásobníky."""
    fig = go.Figure()

    if df_history.empty:
        return fig

    df = df_history.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df["value_GWh"] = pd.to_numeric(
        df.get("value_GWh", 0), errors="coerce").fillna(0)

    d2 = date.today() - timedelta(days=2)
    d3 = date.today() - timedelta(days=3)

    flows_d2 = _get_flows_clean(df, d2)
    flows_d3 = _get_flows_clean(df, d3)

    flows = flows_d2.merge(
        flows_d3.rename(columns={"value_GWh": "value_d3"}),
        on=["countryLabel", "nb"], how="left"
    )
    flows["dod"] = flows["value_GWh"] - flows["value_d3"].fillna(0)
    flows = flows[
        flows.apply(
            lambda r: r["nb"] in NEIGHBORS_MAP.get(r["countryLabel"], []),
            axis=1,
        )
        & (flows["value_GWh"] > 5.0)
    ]

    sub_d2 = df[
        df["date"].dt.tz_convert("Europe/Prague").dt.date == d2
    ].drop_duplicates(
        subset=["countryLabel", "directionKey",
                "adjacentSystemsKey", "pointsNames"]
    )

    baumgarten_val = sub_d2[
        (sub_d2["countryLabel"] == "Slovakia") &
        (sub_d2["directionKey"] == "entry") &
        sub_d2["pointsNames"].str.contains("Baumgarten", na=False)
    ]["value_GWh"].sum()

    ua_hu_val = sub_d2[
        (sub_d2["countryLabel"] == "Hungary") &
        (sub_d2["directionKey"] == "entry") &
        sub_d2["pointsNames"].str.contains("Bereg|UA", case=False, na=False)
    ]["value_GWh"].sum() / 2

    tap_val = sub_d2[
        sub_d2["pointsNames"].str.contains(
            "Melendugno|Mazara|Gela", case=False, na=False)
        & (sub_d2["directionKey"] == "entry")
    ].groupby("pointsNames")["value_GWh"].sum()

    yamal_val = sub_d2[
        (sub_d2["adjacentSystemsKey"] == "TransmissionPL-YAMAL---") &
        (sub_d2["directionKey"] == "entry")
    ]["value_GWh"].sum() / 2

    baltic_val = sub_d2[
        (sub_d2["countryLabel"] == "Poland") &
        (sub_d2["directionKey"] == "entry") &
        (sub_d2["adjacentSystemsKey"] == "TransmissionDK-SE-------")
    ]["value_GWh"].sum() / 2

    # Norské nominace
    no_flows = _fetch_no_flows()
    no_hist  = {}
    if df_gassco is not None and not df_gassco.empty:
        dg = df_gassco.copy()
        dg["date"] = pd.to_datetime(dg["date"], utc=True)
        last_csv = dg["date"].dt.tz_convert("Europe/Prague").dt.date.max()
        prev = (dg[dg["date"].dt.tz_convert("Europe/Prague").dt.date == last_csv]
                .groupby("point")["value_GWh"].sum())
        no_hist = prev.to_dict()

    # 1. Evropské toky
    for _, row in flows.iterrows():
        c_from  = row["countryLabel"]
        c_to    = row["nb"]
        val     = row["value_GWh"]
        val_d3  = row.get("value_d3", 0) or 0
        dod_pct = (row["dod"] / val_d3 * 100) if val_d3 > 0 else 0
        color   = ("#2E7D32" if c_to   == "Czechia" else
                   "#C62828" if c_from == "Czechia" else
                   "#1565C0")
        _draw_flow(fig, c_from, c_to, val, dod_pct, color)

    # 2. AT→SK Baumgarten
    if baumgarten_val > 1:
        _draw_flow(fig, "Austria", "Slovakia",
                   baumgarten_val, 0.0, "#1565C0", "AT→SK Baumgarten")

    # 3. UA→HU
    if ua_hu_val > 1:
        _draw_flow(fig, "Ukraine", "Hungary", ua_hu_val, 0.0, "#FF8F00", "UA→HU")

    # 4. Norské toky
    NO_CENTER = CENTERS_MAP["Norway"]
    for point, (lat, lon, country) in NO_EXITS_MAP.items():
        val  = no_flows.get(point, 0.0)
        hist = float(no_hist.get(point, 0))
        dod_pct = ((val - hist) / hist * 100) if hist > 0 else 0
        if val < 10:
            continue
        s1, s2, e1, e2 = _shorten(NO_CENTER[0], NO_CENTER[1], lat, lon, 0.15)
        dod_str = f"+{dod_pct:.0f}%" if dod_pct >= 0 else f"{dod_pct:.0f}%"
        fig.add_trace(go.Scattermapbox(
            lat=[s1, e1], lon=[s2, e2], mode="lines",
            line=dict(width=max(1.5, min(6, val / 150)), color="#7B1FA2"),
            hoverinfo="skip", showlegend=False,
        ))
        _add_arrow(fig, s1, s2, e1, e2, "#7B1FA2", size=0.2)
        fig.add_trace(go.Scattermapbox(
            lat=[e1 + 0.5], lon=[e2], mode="text",
            text=[f"NO→{country}\n{val:.0f} GWh/d\nDoD {dod_str}"],
            textfont=dict(size=8, color="#7B1FA2"),
            hovertemplate=(
                f"<b>Norsko → {country} ({point})</b><br>"
                f"<b>{val:.0f} GWh/d</b><br>"
                f"DoD: <b>{dod_str}</b><extra></extra>"
            ),
            showlegend=False,
        ))

    # 5. Baltic Pipe
    if baltic_val > 10:
        s1, s2, e1, e2 = _shorten(NO_CENTER[0], NO_CENTER[1], 54.5, 14.3, 0.1)
        fig.add_trace(go.Scattermapbox(
            lat=[s1, 57.0, e1], lon=[s2, 9.5, e2], mode="lines",
            line=dict(width=3, color="#00838F"),
            hoverinfo="skip", showlegend=False,
        ))
        _add_arrow(fig, 57.0, 9.5, e1, e2, "#00838F", size=0.2)
        fig.add_trace(go.Scattermapbox(
            lat=[56.0], lon=[12.0], mode="text",
            text=[f"Baltic Pipe\nNO→DK→PL\n{baltic_val:.0f} GWh/d"],
            textfont=dict(size=8, color="#00838F"),
            hoverinfo="skip", showlegend=False,
        ))

    # 6. TAP / Sev. Afrika
    IT_CENTER = CENTERS_MAP["Italy"]
    for key, (lat, lon, lbl) in _TAP_COORDS.items():
        val = sum(v for k, v in tap_val.items() if key in k)
        if val < 5:
            continue
        s1, s2, e1, e2 = _shorten(lat, lon, IT_CENTER[0], IT_CENTER[1], 0.1)
        fig.add_trace(go.Scattermapbox(
            lat=[s1, e1], lon=[s2, e2], mode="lines",
            line=dict(width=max(1.5, min(5, val / 150)), color="#E65100"),
            hoverinfo="skip", showlegend=False,
        ))
        _add_arrow(fig, s1, s2, e1, e2, "#E65100", size=0.2)
        fig.add_trace(go.Scattermapbox(
            lat=[lat], lon=[lon], mode="markers+text",
            marker=dict(size=6, color="#E65100"),
            text=[f"{lbl}\n{val:.0f} GWh/d"],
            textposition="bottom right",
            textfont=dict(size=8, color="#E65100"),
            hovertemplate=(
                f"<b>{lbl}</b><br>"
                f"<b>{val:.0f} GWh/d</b><extra></extra>"
            ),
            showlegend=False,
        ))

    # 7. Yamal
    if yamal_val > 5:
        fig.add_trace(go.Scattermapbox(
            lat=[53.0], lon=[23.5], mode="text",
            text=[f"Yamal tranzit PL\n{yamal_val:.0f} GWh/d"],
            textfont=dict(size=8, color="#546E7A"),
            hoverinfo="skip", showlegend=False,
        ))

    # 8. Zásobníky GIE
    if show_storage and df_gie is not None and not df_gie.empty:
        df_gie2 = df_gie.copy()
        df_gie2["gasDayStart"] = pd.to_datetime(
            df_gie2["gasDayStart"], errors="coerce")
        last_gie = (df_gie2.dropna(subset=["gasDayStart"])
                    .sort_values("gasDayStart")
                    .groupby("country_code").last()
                    .reset_index())
        for _, row in last_gie.iterrows():
            cc = row["country_code"]
            if cc not in STORAGE_COORDS_MAP or cc == "EU":
                continue
            lat, lon = STORAGE_COORDS_MAP[cc]
            try:
                full = float(str(row.get("full", 0)).replace(",", "."))
            except Exception:
                full = 0.0
            try:
                twh   = float(row.get("gasInStorage", 0))
                inj   = float(row.get("injection", 0))
                with_ = float(row.get("withdrawal", 0))
            except Exception:
                twh = inj = with_ = 0.0
            net     = inj - with_
            net_str = f"+{net:.0f}" if net >= 0 else f"{net:.0f}"
            color   = _storage_color(full)
            bar     = _make_bar(full)
            fig.add_trace(go.Scattermapbox(
                lat=[lat], lon=[lon], mode="markers",
                marker=dict(size=55, color="white", opacity=0.85),
                hoverinfo="skip", showlegend=False,
            ))
            fig.add_trace(go.Scattermapbox(
                lat=[lat], lon=[lon], mode="text",
                text=[f"{cc} {full:.0f}%\n{bar}\n{twh:.1f} TWh  {net_str} GWh/d"],
                textfont=dict(size=9, color=color, family="Arial Black"),
                textposition="middle center",
                hovertemplate=(
                    f"<b>{cc} Zásobníky</b><br>"
                    f"Plnost: <b>{full:.1f}%</b><br>"
                    f"Objem: {twh:.1f} TWh<br>"
                    f"Vtláčení: +{inj:.0f} GWh/d<br>"
                    f"Těžba: -{with_:.0f} GWh/d<br>"
                    f"Net: <b>{net_str} GWh/d</b>"
                    f"<extra></extra>"
                ),
                showlegend=False,
            ))

    # 9. Legenda
    legend_items = [
        ("#2E7D32", "Import do CZ"),
        ("#C62828", "Export z CZ"),
        ("#1565C0", "Ostatní toky EU"),
        ("#7B1FA2", "Norský export (live)"),
        ("#00838F", "Baltic Pipe NO→PL"),
        ("#E65100", "TAP / Sev. Afrika"),
        ("#FF8F00", "UA→HU"),
        ("#546E7A", "Yamal tranzit"),
    ]
    for color, label in legend_items:
        fig.add_trace(go.Scattermapbox(
            lat=[None], lon=[None], mode="markers",
            marker=dict(size=10, color=color),
            name=label, showlegend=True,
        ))

    fig.update_layout(
        mapbox=dict(
            style="carto-positron",
            zoom=3.8,
            center=dict(lat=50.0, lon=10.0),
        ),
        height=720,
        margin=dict(l=0, r=0, t=50, b=0),
        title=dict(
            text=(f"Fyzické toky plynu — Evropa  |  "
                  f"{d2} (D-2)  |  NO nominace live"),
            font=dict(size=13),
        ),
        legend=dict(
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#ccc",
            borderwidth=1,
            x=0.01, y=0.99,
            xanchor="left", yanchor="top",
            font=dict(size=10),
        ),
        paper_bgcolor="white",
    )
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
