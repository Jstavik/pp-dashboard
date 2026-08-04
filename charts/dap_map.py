import plotly.graph_objects as go
import pandas as pd

from data.dap_europe import CENTERS
from config import GEOJSON_COUNTRIES_URL

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
            textfont=dict(size=11, color=color),
            hoverinfo="skip",
            showlegend=False,
        ))
    last_date = str(df["date"].iloc[0]) if len(df) > 0 else ""
    fig.update_layout(
        mapbox=dict(
            style="white-bg",
            center=dict(lat=52, lon=12),
            zoom=3.8,
            layers=[{
                "below": "traces",
                "sourcetype": "geojson",
                "source": GEOJSON_COUNTRIES_URL,
                "type": "line",
                "color": "#AAAAAA",
                "line": {"width": 0.5},
            }],
        ),
        height=720,
        margin=dict(l=0, r=0, t=50, b=0),
        title=dict(
            text=f"DAP ceny elektřiny — Evropa  |  {last_date}  |  Base | Peak €/MWh  //  Δ Base | Δ Peak €/MWh DoD",
            font=dict(size=11),
        ),
        paper_bgcolor="white",
        showlegend=False,
    )
    return fig
