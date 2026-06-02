import plotly.graph_objects as go
import pandas as pd
from data.dap_europe import ISO3, CENTERS


def fig_dap_map(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return go.Figure()

    df = df.copy()
    fig = go.Figure()

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

    for _, r in df.iterrows():
        cc = r["cc"]
        if cc not in CENTERS:
            continue
        lat, lon = CENTERS[cc]

        dod_b = r["dod_base"]
        color = "#C62828" if dod_b > 0 else "#2E7D32"

        fig.add_trace(go.Scattermapbox(
            lat=[lat], lon=[lon],
            mode="markers",
            marker=dict(size=60, color="white", opacity=0.85),
            hoverinfo="skip", showlegend=False,
        ))

        fig.add_trace(go.Scattermapbox(
            lat=[lat], lon=[lon],
            mode="text",
            text=[
                f"{cc}\n"
                f"{r['base']:.0f} | {r['peak']:.0f}\n"
                f"Δ{r['dod_base']:+.0f} | Δ{r['dod_peak']:+.0f}"
            ],
            textfont=dict(size=11, color=color, family="Arial Black"),
            textposition="middle center",
            hovertemplate=(
                f"<b>{cc}</b><br>"
                f"Base: <b>{r['base']:.1f} EUR/MWh</b><br>"
                f"Peak: <b>{r['peak']:.1f} EUR/MWh</b><br>"
                f"Δ Base: <b>{r['dod_base']:+.1f} EUR/MWh</b><br>"
                f"Δ Peak: <b>{r['dod_peak']:+.1f} EUR/MWh</b><br>"
                "<extra></extra>"
            ),
            showlegend=False,
        ))

    last_date = df["date"].iloc[0] if len(df) > 0 else ""

    fig.add_trace(go.Scattermapbox(
        lat=[None], lon=[None],
        mode="markers",
        marker=dict(size=10, color="#C62828"),
        name="Δ kladné (DoD růst)",
        showlegend=True,
    ))
    fig.add_trace(go.Scattermapbox(
        lat=[None], lon=[None],
        mode="markers",
        marker=dict(size=10, color="#2E7D32"),
        name="Δ záporné (DoD pokles)",
        showlegend=True,
    ))

    fig.update_layout(
        mapbox=dict(
            style="carto-positron",
            zoom=3.5,
            center=dict(lat=52.0, lon=12.0),
        ),
        height=720,
        margin=dict(l=0, r=0, t=50, b=0),
        title=dict(
            text=(
                f"DAP ceny elektřiny — Evropa  |  {last_date}  |  "
                "CC / Base | Peak EUR/MWh  /  Δ Base | Δ Peak EUR/MWh (DoD)"
            ),
            font=dict(size=11),
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
