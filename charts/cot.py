import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import C_TEXT, C_GRID, C_BG

CATEGORY_ORDER = [
    "Investment Firms or Credit Institutions",
    "Investment Funds",
    "Other Financial Institutions",
    "Commercial Undertakings",
    "ETS Compliance Operators",
]
_CATEGORY_COLORS = dict(zip(CATEGORY_ORDER, px.colors.qualitative.Set2))

# ICE si NotationofthePositionQuantity mezi týdny překódovalo (živě
# pozorováno na TFM: "OTHER/MWh" do 2026-07-17, "MWHO" od 2026-07-31) —
# stejná fyzická jednotka, jiný zápis. Normalizuje se jen na DISPLEJI
# (samotná uložená hodnota v parquetu zůstává syrová, beze změny), ať
# graf/KPI neukazuje ošklivé "MWHO/OTHER/MWh" za stejnou veličinu.
# Neznámé/nezmapované jednotky se NEslučují — ty se zobrazí zvlášť, ať
# je vidět, když by šlo o genuinely jinou jednotku, ne jen jiný zápis.
_UNIT_DISPLAY_MAP = {"MWHO": "MWh", "OTHER/MWh": "MWh", "LOTS": "Lots"}


def _unit_label(notation_units) -> str:
    display = sorted({_UNIT_DISPLAY_MAP.get(u, u) for u in notation_units if u})
    return "/".join(display) if display else "?"


# "Finanční" kategorie pro net-pozici (analog CFTC "speculators") —
# záměrně BEZ Commercial Undertakings a ETS Compliance Operators, ty
# jsou hedging/compliance flow, ne spekulativní pozicioning.
_FINANCIAL_CATEGORIES = [
    "Investment Firms or Credit Institutions",
    "Investment Funds",
    "Other Financial Institutions",
]


def fig_cot_positioning(df: pd.DataFrame, venue: str, product_code: str) -> go.Figure:
    """NOPS Long/Short po 5 MiFID kategoriích v čase pro jednu komoditu
    (scope=Total — ICE's vlastní součet, nepočítá se tu znovu). Long
    nahoře / Short dole na zrcadlených subplotech, + tučná přerušovaná
    Total čára přes všechny kategorie. notation_unit (MWh vs Lots atd.)
    jde vždy do titulku i popisku osy — komodity v ice_cot.parquet
    nemají jednotnou jednotku (TTF = MWh, Brent/EUA = Lots), nesmí se
    nikdy zaměnit."""
    sub = df[
        (df["venue"] == venue) & (df["product_code"] == product_code) & (df["scope"] == "Total")
    ]
    fig = go.Figure()
    if sub.empty:
        fig.add_annotation(text="Data nejsou dostupná", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False,
                           font=dict(size=12, color=C_TEXT))
        fig.update_layout(height=300, plot_bgcolor=C_BG, paper_bgcolor=C_BG)
        return fig

    product_name = sub["product_name"].iloc[0]
    unit_label = _unit_label(sub["notation_unit"].dropna().unique())

    pivot_long = sub[sub["side"] == "Long"].pivot_table(
        index="report_date", columns="category", values="nops", aggfunc="sum")
    pivot_short = sub[sub["side"] == "Short"].pivot_table(
        index="report_date", columns="category", values="nops", aggfunc="sum")
    total_long = pivot_long.sum(axis=1)
    total_short = pivot_short.sum(axis=1)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
        subplot_titles=(f"Long ({unit_label})", f"Short ({unit_label})"),
    )
    for cat in CATEGORY_ORDER:
        color = _CATEGORY_COLORS.get(cat)
        if cat in pivot_long.columns:
            fig.add_trace(go.Scatter(
                x=pivot_long.index, y=pivot_long[cat], name=cat, mode="lines",
                line=dict(color=color, width=1.5), legendgroup=cat,
            ), row=1, col=1)
        if cat in pivot_short.columns:
            fig.add_trace(go.Scatter(
                x=pivot_short.index, y=pivot_short[cat], name=cat, mode="lines",
                line=dict(color=color, width=1.5), legendgroup=cat, showlegend=False,
            ), row=2, col=1)

    fig.add_trace(go.Scatter(
        x=total_long.index, y=total_long.values, name="Total (všechny kategorie)",
        mode="lines", line=dict(color=C_TEXT, width=3, dash="dash"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=total_short.index, y=total_short.values, name="Total (všechny kategorie)",
        mode="lines", line=dict(color=C_TEXT, width=3, dash="dash"), showlegend=False,
    ), row=2, col=1)

    fig.update_yaxes(title_text=f"NOPS ({unit_label})", gridcolor=C_GRID, row=1, col=1)
    fig.update_yaxes(title_text=f"NOPS ({unit_label})", gridcolor=C_GRID, row=2, col=1)
    fig.update_xaxes(gridcolor=C_GRID)
    fig.update_layout(
        title_text=f"{product_name} ({venue}/{product_code}) — pozice tradérů · jednotka: {unit_label}",
        height=640, plot_bgcolor=C_BG, paper_bgcolor=C_BG,
        font=dict(color=C_TEXT, size=11),
        legend=dict(orientation="h", y=-0.1, x=0, xanchor="left",
                    bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        hoverlabel=dict(bgcolor="white", font_size=11, bordercolor=C_GRID),
        margin=dict(l=60, r=15, t=70, b=60),
    )
    return fig


def compute_financial_net_position(df: pd.DataFrame, venue: str, product_code: str) -> dict | None:
    """'Finanční net pozice' = (Investment Firms/Credit Institutions +
    Investment Funds + Other Financial Institutions) Long minus Short,
    scope=Other (NE Total, NE Risk_Reducing) — analog "spekulantů" ke
    klasickému CFTC COT reportu: jen finanční kategorie, jen jejich
    "Other" (směrové/spekulativní) pozice, ne hedging (Risk_Reducing).

    Vrací aktuální hodnotu + její percentil vůči CELÉ dostupné historii
    dané komodity — žádný pevný předpoklad na délku okna, počítá se
    s tím, co je k dispozici (třeba jen pár týdnů). None, pokud pro tu
    komoditu nejsou žádná scope=Other data."""
    sub = df[
        (df["venue"] == venue) & (df["product_code"] == product_code)
        & (df["scope"] == "Other") & (df["category"].isin(_FINANCIAL_CATEGORIES))
    ]
    if sub.empty:
        return None

    pivot = sub.pivot_table(index="report_date", columns="side", values="nops", aggfunc="sum")
    if "Long" not in pivot.columns and "Short" not in pivot.columns:
        return None
    net = (pivot.get("Long", 0) - pivot.get("Short", 0)).dropna().sort_index()
    if net.empty:
        return None

    current_date = net.index.max()
    current_value = float(net.loc[current_date])
    percentile = float((net <= current_value).mean() * 100)
    unit_label = _unit_label(sub["notation_unit"].dropna().unique())

    return {
        "current_date": current_date,
        "current_value": current_value,
        "percentile": percentile,
        "n_weeks": len(net),
        "unit_label": unit_label,
        "history": net,
    }
