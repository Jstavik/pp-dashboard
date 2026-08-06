import pandas as pd
import plotly.graph_objects as go

from config import C_DEFICIT, C_OK, GEN_STACK_ORDER, PSR_CODE_BY_SOURCE_TYPE, psr_lookup, _base_layout


def _fill_rgba(color: str, alpha: float = 0.78) -> str:
    if color.startswith("#") and len(color) == 7:
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return color


def _stack_key(plant_type: str) -> int:
    code = PSR_CODE_BY_SOURCE_TYPE.get(plant_type)
    return GEN_STACK_ORDER.index(code) if code in GEN_STACK_ORDER else 999


def fig_outages_table(data: dict, plant_types: list = None) -> pd.DataFrame:
    """Tabulka aktivních odstávek — všechny typy zdrojů, volitelný filtr
    na konkrétní typy (plant_types) pro UI selectbox/multiselect v app.py."""
    cols = ["Blok", "Typ zdroje", "Instalovaný výkon (MW)", "Dostupný (MW)",
            "Nedostupný (MW)", "Druh", "Konec odstávky"]
    active = data["active"]
    if active.empty:
        return pd.DataFrame(columns=cols)

    df = active[active["plant_type"].isin(plant_types)] if plant_types else active
    if df.empty:
        return pd.DataFrame(columns=cols)

    out = pd.DataFrame({
        "Blok":                     df["production_resource_name"],
        "Typ zdroje":               df["plant_type"].apply(lambda pt: psr_lookup(pt)[0]),
        "Instalovaný výkon (MW)":   df["nominal_power"],
        "Dostupný (MW)":            df["avail_qty"],
        "Nedostupný (MW)":          df["unavail_mw"],
        "Druh":                     df["businesstype"],
        "Konec odstávky":           df["end"],
    })
    return out.sort_values("Nedostupný (MW)", ascending=False).reset_index(drop=True)


def fig_outlook(data: dict, days_forward: int = 30) -> go.Figure:
    """Výhled odstávek podle typu zdroje — od dneška do days_forward dní
    dopředu, rozšiřitelné až do konce dostupného okna (next_year_end,
    dané tím, co update_outages stáhl — viz data["daily_by_type"]).
    Skládaná plocha, jen typy zdrojů co se v datech reálně vyskytují."""
    fig = go.Figure()
    daily = data["daily_by_type"]
    if daily.empty:
        return _base_layout(fig, height=380)

    now = data["now"]
    cutoff = now.normalize() + pd.Timedelta(days=days_forward)
    daily = daily[daily["date"] <= cutoff]
    if daily.empty:
        return _base_layout(fig, height=380)

    wide = daily.pivot(index="date", columns="plant_type", values="unavail_mw").fillna(0) / 1000

    for plant_type in sorted(wide.columns, key=_stack_key):
        series = wide[plant_type]
        if series.sum() <= 0:
            continue
        name, color = psr_lookup(plant_type)
        fig.add_trace(go.Scatter(
            x=wide.index, y=series.values, stackgroup="out", name=name,
            line=dict(width=0, color=color), fillcolor=_fill_rgba(color),
            hovertemplate=f"{name}: %{{y:.3f}} GW<extra></extra>",
        ))

    _base_layout(fig, height=380)
    fig.update_layout(title=f"Výhled odstávek podle typu zdroje — {days_forward} dní dopředu [GW]",
                       hovermode="x unified")
    fig.update_xaxes(title_text="Datum")
    fig.update_yaxes(title_text="GW")
    return fig


def _daily_unavail_by_block(df: pd.DataFrame, days: pd.DatetimeIndex) -> pd.DataFrame:
    """Denní nedostupnost [MW] per blok (production_resource_name) — pro
    fig_outages_delta. Per-blok denní agregace místo per-record diffu,
    aby revize co jen posunou hranici intervalu nevytvářely false
    positives (per-record diff by je viděl jako smazaný+nový záznam,
    přestože jde furt o tu samou probíhající odstávku)."""
    if df.empty:
        return pd.DataFrame(columns=["date", "production_resource_name", "unavail_mw"])
    rows = []
    for day in days:
        active_day = df[(df["start"] <= day) & (df["end"] >= day) & (df["unavail_mw"] > 0)]
        for block, grp in active_day.groupby("production_resource_name"):
            rows.append({"date": day, "production_resource_name": block, "unavail_mw": grp["unavail_mw"].sum()})
    return pd.DataFrame(rows, columns=["date", "production_resource_name", "unavail_mw"])


def fig_outages_delta(df_now: pd.DataFrame, df_compare: pd.DataFrame,
                       window_days: int, compare_days_back: int = 7) -> go.Figure:
    """Delta graf odstávek — denní nedostupnost per blok teď vs. před
    compare_days_back dny, agregovaně po dnech přes window_days dopředu
    od dneška. Per-blok denní agregace (viz _daily_unavail_by_block), ne
    per-record diff — jinak false positives z revizí co jen posunou
    hranici intervalu.

    DŮLEŽITÉ: df_now i df_compare musí být SUROVÝ tvar odstávek (sloupce
    jako data/outages.py::load_outages_snapshot() vrací — country,
    plant_type, production_resource_name, start, end, unavail_mw, ...),
    NE zúžené na "aktivní právě teď" (data["active"] z load_outages() by
    tady nefungovalo — vynechalo by budoucí, zatím neaktivní odstávky
    uvnitř window_days). Typicky:
        df_now     = load_outages_snapshot(country, days_back=0)
        df_compare = load_outages_snapshot(country, days_back=compare_days_back)
    """
    fig = go.Figure()
    if df_now.empty and df_compare.empty:
        return _base_layout(fig, height=320)

    now = pd.Timestamp.now(tz="UTC").normalize()
    days = pd.date_range(now, now + pd.Timedelta(days=window_days), freq="D")

    now_daily = _daily_unavail_by_block(df_now, days)
    compare_daily = _daily_unavail_by_block(df_compare, days)

    now_wide = (now_daily.pivot(index="date", columns="production_resource_name", values="unavail_mw")
                if not now_daily.empty else pd.DataFrame(index=days))
    compare_wide = (compare_daily.pivot(index="date", columns="production_resource_name", values="unavail_mw")
                     if not compare_daily.empty else pd.DataFrame(index=days))

    all_blocks = sorted(set(now_wide.columns) | set(compare_wide.columns))
    now_wide = now_wide.reindex(index=days, columns=all_blocks, fill_value=0).fillna(0)
    compare_wide = compare_wide.reindex(index=days, columns=all_blocks, fill_value=0).fillna(0)

    delta_gw = (now_wide - compare_wide).sum(axis=1) / 1000

    colors = [C_DEFICIT if v > 0 else C_OK for v in delta_gw.values]
    fig.add_trace(go.Bar(
        x=delta_gw.index, y=delta_gw.values, marker_color=colors,
        name=f"Změna vs. před {compare_days_back} dny",
        hovertemplate="Den %{x}<br>Δ %{y:+.3f} GW<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="#888", line_width=1)

    _base_layout(fig, height=320)
    fig.update_layout(
        title=f"Δ odstávky — dnes vs. před {compare_days_back} dny [GW]",
        hovermode="x unified", showlegend=False,
    )
    fig.update_xaxes(title_text="Datum")
    fig.update_yaxes(title_text="Δ GW (+ = víc nedostupno)")
    return fig
