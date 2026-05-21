import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from data.hydro import year_color, year_width, HYDRO_COUNTRY_NAMES, HYDRO_GRID_COUNTRIES


def _prep_hydro(df: pd.DataFrame, cc: str) -> pd.DataFrame:
    sub = df[df["country"] == cc].copy()
    sub["year"] = sub["date"].dt.year
    sub["week"] = sub["date"].dt.isocalendar().week.astype(int)
    return sub


def fig_hydro_main(
    df: pd.DataFrame,
    country: str,
    years: list,
) -> go.Figure:
    sub      = _prep_hydro(df, country)
    name     = HYDRO_COUNTRY_NAMES.get(country, country)
    last     = df[df["country"] == country]["date"].max()
    last_str = last.strftime("%d.%m.%Y") if pd.notna(last) else "N/A"

    fig = go.Figure()
    for yr in sorted(years):
        grp = sub[sub["year"] == yr].sort_values("week")
        if grp.empty:
            continue
        fig.add_trace(go.Scatter(
            x=grp["week"], y=grp["value_GWh"],
            mode="lines", name=str(yr),
            line=dict(color=year_color(yr), width=year_width(yr)),
            hovertemplate=(
                f"{yr} · týden %{{x}}: "
                f"<b>%{{y:.0f}} GWh</b><extra></extra>"
            ),
        ))

    fig.update_layout(
        title=(
            f"{name} — vodní zásobníky [GWh]  "
            f"<span style='font-size:11px;color:#888'>"
            f"poslední data: {last_str}</span>"
        ),
        height=380,
        template="plotly_white",
        hovermode="x unified",
        xaxis=dict(
            title="Týden v roce",
            tickvals=[1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50],
            gridcolor="#f0f0f0",
        ),
        yaxis=dict(title="GWh", gridcolor="#f0f0f0"),
        legend=dict(orientation="h", y=-0.22, xanchor="center", x=0.5),
        margin=dict(l=60, r=20, t=60, b=90),
    )
    return fig


def fig_hydro_grid(
    df: pd.DataFrame,
    years: list,
) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=3,
        subplot_titles=[
            f"{HYDRO_COUNTRY_NAMES.get(cc, cc)} ({cc})"
            for cc in HYDRO_GRID_COUNTRIES
        ],
        vertical_spacing=0.14,
        horizontal_spacing=0.06,
    )

    for i, cc in enumerate(HYDRO_GRID_COUNTRIES):
        row = i // 3 + 1
        col = i %  3 + 1
        sub = _prep_hydro(df, cc)

        for yr in sorted(years):
            grp = sub[sub["year"] == yr].sort_values("week")
            if grp.empty:
                continue
            fig.add_trace(go.Scatter(
                x=grp["week"], y=grp["value_GWh"],
                mode="lines", name=str(yr),
                line=dict(color=year_color(yr), width=year_width(yr)),
                legendgroup=str(yr),
                showlegend=(i == 0),
                hovertemplate=(
                    f"{HYDRO_COUNTRY_NAMES.get(cc, cc)} {yr}<br>"
                    f"Týden %{{x}}: <b>%{{y:.0f}} GWh</b>"
                    f"<extra></extra>"
                ),
            ), row=row, col=col)

        fig.update_xaxes(
            tickvals=[1, 10, 20, 30, 40, 50],
            gridcolor="#f0f0f0",
            row=row, col=col,
        )
        fig.update_yaxes(
            title_text="GWh" if col == 1 else "",
            gridcolor="#f0f0f0",
            row=row, col=col,
        )

    last_dates = (
        df[df["country"].isin(HYDRO_GRID_COUNTRIES)]
        .groupby("country")["date"].max()
    )
    last_str = (
        last_dates.min().strftime("%d.%m.%Y")
        if not last_dates.empty else "N/A"
    )

    fig.update_layout(
        height=600,
        template="plotly_white",
        hovermode="x unified",
        title=(
            f"Vodní zásobníky — sezonnost  "
            f"<span style='font-size:11px;color:#888'>"
            f"poslední data: {last_str}</span>"
        ),
        legend=dict(orientation="h", y=-0.06, xanchor="center", x=0.5),
        margin=dict(l=50, r=20, t=60, b=80),
    )
    return fig
