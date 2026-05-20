import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from data.gie import YEAR_COLORS, VARIABLES, FIXED_COUNTRIES


def _prep(df: pd.DataFrame, cc: str) -> pd.DataFrame:
    sub = df[df["country_code"] == cc].copy()
    sub["year"]        = sub["gasDayStart"].dt.year
    sub["day_of_year"] = sub["gasDayStart"].dt.day_of_year
    return sub


def fig_storage_main(
    df: pd.DataFrame,
    country: str,
    variable: str,
    years: list,
) -> go.Figure:
    """Hlavní graf — jeden vybraný stát, sezonnost, výběr let."""
    label, unit = VARIABLES.get(variable, (variable, ""))
    sub = _prep(df, country)

    fig = go.Figure()
    for yr in sorted(years):
        grp = sub[sub["year"] == yr].sort_values("day_of_year")
        if grp.empty:
            continue
        color = YEAR_COLORS.get(yr, "#9E9E9E")
        width = 2.5 if yr == pd.Timestamp.now().year else 1.5
        fig.add_trace(go.Scatter(
            x=grp["day_of_year"], y=grp[variable],
            mode="lines", name=str(yr),
            line=dict(color=color, width=width),
            hovertemplate=(
                f"{yr} · den %{{x}}: "
                f"<b>%{{y:.2f}} {unit}</b><extra></extra>"
            ),
        ))

    cc_name = next((n for c, n in FIXED_COUNTRIES if c == country), country)
    fig.update_layout(
        title=f"{cc_name} — {label} (sezonnost)",
        height=380,
        template="plotly_white",
        hovermode="x unified",
        xaxis=dict(
            title="Den v roce",
            tickvals=[1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335],
            ticktext=["Led", "Úno", "Bře", "Dub", "Kvě", "Čvn",
                      "Čvc", "Srp", "Zář", "Říj", "Lis", "Pro"],
            gridcolor="#f0f0f0",
        ),
        yaxis=dict(title=f"{label} [{unit}]", gridcolor="#f0f0f0"),
        legend=dict(orientation="h", y=-0.22, xanchor="center", x=0.5),
        margin=dict(l=60, r=20, t=50, b=90),
    )
    return fig


def fig_storage_grid(
    df: pd.DataFrame,
    variable: str,
    years: list,
) -> go.Figure:
    """3×2 grid — 6 pevných zemí, sdílené roky."""
    label, unit = VARIABLES.get(variable, (variable, ""))

    fig = make_subplots(
        rows=2, cols=3,
        subplot_titles=[
            f"{name} ({cc})" for cc, name in FIXED_COUNTRIES
        ],
        vertical_spacing=0.14,
        horizontal_spacing=0.06,
    )

    for i, (cc, name) in enumerate(FIXED_COUNTRIES):
        row = i // 3 + 1
        col = i %  3 + 1
        sub = _prep(df, cc)

        for yr in sorted(years):
            grp = sub[sub["year"] == yr].sort_values("day_of_year")
            if grp.empty:
                continue
            color = YEAR_COLORS.get(yr, "#9E9E9E")
            width = 2.5 if yr == pd.Timestamp.now().year else 1.5

            fig.add_trace(go.Scatter(
                x=grp["day_of_year"], y=grp[variable],
                mode="lines", name=str(yr),
                line=dict(color=color, width=width),
                legendgroup=str(yr),
                showlegend=(i == 0),
                hovertemplate=(
                    f"{name} {yr}<br>"
                    f"Den %{{x}}: <b>%{{y:.1f}} {unit}</b>"
                    f"<extra></extra>"
                ),
            ), row=row, col=col)

        fig.update_xaxes(
            tickvals=[1, 60, 121, 182, 244, 305],
            ticktext=["Led", "Bře", "Kvě", "Čvn", "Srp", "Lis"],
            gridcolor="#f0f0f0",
            row=row, col=col,
        )
        fig.update_yaxes(
            title_text=unit if col == 1 else "",
            gridcolor="#f0f0f0",
            row=row, col=col,
        )

    fig.update_layout(
        height=600,
        template="plotly_white",
        hovermode="x unified",
        title=f"Zásobníky — {label} | sezonnost",
        legend=dict(
            orientation="h", y=-0.06,
            xanchor="center", x=0.5,
        ),
        margin=dict(l=50, r=20, t=60, b=80),
    )
    return fig
