import plotly.graph_objects as go
import pandas as pd
from data.dap_europe import ISO3, CENTERS


def fig_dap_map(df: pd.DataFrame) -> go.Figure:
    """Choropleth mapa DAP cen elektřiny — Evropa."""
    if df.empty:
        return go.Figure()

    df = df.copy()
    df["iso3"] = df["cc"].map(ISO3)

    fig = go.Figure()

    fig.add_trace(go.Choropleth(
        locations=df["iso3"],
        z=df["base"],
        locationmode="ISO-3",
        colorscale=[
            [0.0, "#2E7D32"],
            [0.3, "#FFEB3B"],
            [0.6, "#FF8F00"],
            [1.0, "#C62828"],
        ],
        colorbar=dict(
            title="Base<br>EUR/MWh",
            thickness=14, len=0.65,
            tickfont=dict(size=10),
        ),
        zmin=0, zmax=160,
        hoverinfo="skip",
    ))

    for _, r in df.iterrows():
        cc = r["cc"]
        if cc not in CENTERS:
            continue
        lat, lon = CENTERS[cc]

        label = (
            f"Base: {r['base']:.0f} | Peak: {r['peak']:.0f} EUR/MWh<br>"
            f"Δ {r['dod_base']:+.0f} | Δ {r['dod_peak']:+.0f} EUR/MWh"
        )

        fig.add_trace(go.Scattergeo(
            lat=[lat], lon=[lon],
            mode="markers",
            marker=dict(
                size=34, color="white", opacity=0.75,
                line=dict(color="#CCCCCC", width=0.5),
            ),
            hoverinfo="skip", showlegend=False,
        ))

        fig.add_trace(go.Scattergeo(
            lat=[lat], lon=[lon],
            mode="text",
            text=[label],
            textfont=dict(size=8, color="black", family="Arial"),
            hovertemplate=(
                f"<b>{cc}</b><br>"
                f"Base: <b>{r['base']:.1f} EUR/MWh</b><br>"
                f"Peak: <b>{r['peak']:.1f} EUR/MWh</b><br>"
                f"Δ Base: <b>{r['dod_base']:+.1f} EUR/MWh</b><br>"
                f"Δ Peak: <b>{r['dod_peak']:+.1f} EUR/MWh</b><br>"
                f"Data: {r['date']}"
                "<extra></extra>"
            ),
            showlegend=False,
        ))

    fig.add_annotation(
        x=0.01, y=0.04,
        xref="paper", yref="paper",
        text=(
            "<b>Base | Peak</b> = denní průměr EUR/MWh<br>"
            "<b>Δ</b> = DoD změna (Base | Peak) v EUR/MWh"
        ),
        showarrow=False,
        bgcolor="white",
        bordercolor="#ccc",
        borderwidth=1,
        font=dict(size=9),
        align="left",
    )

    last_date = df["date"].iloc[0] if len(df) > 0 else ""
    fig.update_geos(
        scope="europe",
        showcoastlines=True, coastlinecolor="#888",
        showland=True, landcolor="#F5F5F5",
        showocean=True, oceancolor="#D6EAF8",
        showcountries=True, countrycolor="#888",
        projection_type="natural earth",
        lonaxis_range=[-12, 35],
        lataxis_range=[34, 72],
    )
    fig.update_layout(
        title=dict(
            text=(
                f"DAP ceny elektřiny — Evropa  |  {last_date}  |  "
                "Base | Peak EUR/MWh  //  Δ = DoD změna"
            ),
            font=dict(size=11),
        ),
        height=680,
        margin=dict(l=0, r=0, t=40, b=0),
        paper_bgcolor="white",
    )
    return fig
