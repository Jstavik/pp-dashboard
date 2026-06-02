import plotly.graph_objects as go
import pandas as pd

CENTERS = {
    "CZ": (49.8, 15.5), "DE": (51.2, 10.4), "FR": (46.5, 2.5),
    "AT": (47.5, 14.5), "SK": (48.7, 19.5), "PL": (52.0, 19.5),
    "HU": (47.2, 19.3), "NL": (52.3, 5.3),  "BE": (50.5, 4.5),
    "ES": (40.0, -3.5), "PT": (39.5, -8.0), "IT": (42.5, 12.5),
    "RO": (45.8, 24.8), "BG": (42.7, 25.5), "GR": (39.5, 22.0),
    "RS": (44.0, 21.0), "HR": (45.2, 15.5), "SI": (46.1, 14.8),
    "CH": (47.0, 8.3),  "FI": (64.0, 26.0), "NO": (62.0, 10.0),
    "DK": (56.0, 10.0), "SE": (59.0, 15.0),
}


def fig_dap_map(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return go.Figure()

    df = df.copy()
    fig = go.Figure()

    for _, r in df.iterrows():
        cc = r["cc"]
        if cc not in CENTERS:
            continue
        lat, lon = CENTERS[cc]
        color = "#C62828" if r["dod_base"] > 0 else "#2E7D32"

        fig.add_trace(go.Scattermapbox(
            lat=[lat], lon=[lon],
            mode="text",
            text=[f"Base: {r['base']:.0f} | Peak: {r['peak']:.0f}<br>Δ {r['dod_base']:+.0f} | Δ {r['dod_peak']:+.0f}"],
            textfont=dict(size=10, color=color),
            hoverinfo="skip",
            showlegend=False,
        ))

    last_date = str(df["date"].iloc[0]) if len(df) > 0 else ""

    fig.update_layout(
        mapbox=dict(
            style="carto-positron",
            center=dict(lat=52, lon=12),
            zoom=3.5,
        ),
        height=720,
        margin=dict(l=0, r=0, t=50, b=0),
        title=dict(
            text=(
                f"DAP ceny elektřiny — Evropa  |  {last_date}  |  "
                "Base | Peak €/MWh  //  Δ Base | Δ Peak €/MWh DoD"
            ),
            font=dict(size=11),
        ),
        paper_bgcolor="white",
        showlegend=False,
    )
    return fig
