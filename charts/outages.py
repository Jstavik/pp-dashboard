import pandas as pd
import plotly.graph_objects as go

from config import (
    C_MUTED, C_GRID, C_NEW, C_SURPLUS,
    psr_lookup,
    _base_layout, _now_marker, _weekend_shading,
)


def pct_to_color(available_pct):
    r = max(0.0, min(1.0, 1.0 - (available_pct or 0) / 100))
    if r < 0.5:
        t = r / 0.5
        return f"rgb({int(46 + (255-46)*t)},{int(125 + (143-125)*t)},{int(50 + (0-50)*t)})"
    else:
        t = (r - 0.5) / 0.5
        return f"rgb({int(255 + (198-255)*t)},{int(143 + (40-143)*t)},0)"


def parse_outages(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    df = raw.copy().rename(columns={
        "start":                    "outage_start",
        "end":                      "outage_end",
        "nominal_power":            "installed_MW",
        "avail_qty":                "available_MW",
        "businesstype":             "outage_type",
        "production_resource_name": "unit_raw",
        "production_resource_id":   "eic_code",
    })
    df["unit_name"]    = df["unit_raw"].apply(lambda x: str(x).replace("_", " ").strip()
                                               if isinstance(x, str) else str(x))
    df["installed_MW"] = pd.to_numeric(df["installed_MW"], errors="coerce")
    df["available_MW"] = pd.to_numeric(df["available_MW"], errors="coerce")
    df["unavailable_MW"] = df["installed_MW"] - df["available_MW"]
    df["available_pct"]  = (df["available_MW"] / df["installed_MW"] * 100).round(1)
    for col in ["outage_start", "outage_end"]:
        if col in df.columns:
            if df[col].dt.tz is None:
                df[col] = df[col].dt.tz_localize("UTC").dt.tz_convert("Europe/Prague")
            else:
                df[col] = df[col].dt.tz_convert("Europe/Prague")
    df = (df.drop_duplicates(subset=["unit_raw", "outage_start", "outage_end"])
            .sort_values(["unit_level", "unavailable_MW"], ascending=[True, False])
            .reset_index(drop=True))
    keep = ["unit_raw","eic_code","unit_name","unit_level",
            "outage_start","outage_end","installed_MW","available_MW",
            "unavailable_MW","available_pct","outage_type","mrid"]
    return df[[c for c in keep if c in df.columns]]


def detect_changes(df_prev, df_curr):
    key   = ["unit_raw", "outage_start", "outage_end"]
    empty = {"new": set(), "ended": set(), "changed_mw": pd.DataFrame()}
    if df_prev is None or df_prev.empty or df_curr.empty:
        return empty
    prev_keys = set(df_prev[key].apply(tuple, axis=1))
    curr_keys = set(df_curr[key].apply(tuple, axis=1))
    merged = df_curr.merge(
        df_prev[key + ["available_MW"]].rename(columns={"available_MW": "prev_MW"}),
        on=key, how="inner",
    )
    changed = merged[abs(merged["available_MW"] - merged["prev_MW"]) > 0.5].copy()
    changed["delta_MW"] = changed["available_MW"] - changed["prev_MW"]
    return {
        "new":        curr_keys - prev_keys,
        "ended":      prev_keys - curr_keys,
        "changed_mw": changed[["unit_name","prev_MW","available_MW","delta_MW"]]
                      if not changed.empty else pd.DataFrame(),
    }


def fig_outages_gantt(df_out, level, now, changes=None, height_per_unit=32):
    fig = go.Figure()
    sub = df_out[df_out["unit_level"] == level].copy() if not df_out.empty else pd.DataFrame()
    if sub.empty:
        fig.add_annotation(text=f"Žádné odstávky — {level}",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font=dict(size=12, color=C_MUTED))
        return _base_layout(fig, height=160, margin_l=180)
    impact = (sub.groupby("unit_name")["unavailable_MW"]
                 .max().sort_values(ascending=True))
    sub["unit_name"] = pd.Categorical(sub["unit_name"], categories=impact.index, ordered=True)
    n_units = max(1, sub["unit_name"].nunique())
    height  = max(180, n_units * height_per_unit + 80)
    xstart  = now - pd.Timedelta(days=1)
    xend    = now.normalize() + pd.Timedelta(days=7)
    _weekend_shading(fig, xstart, xend)
    fig.add_vrect(x0=now, x1=now + pd.Timedelta(hours=24),
                  fillcolor=C_SURPLUS, opacity=0.04, layer="below", line_width=0)
    new_keys = (changes or {}).get("new", set())
    for _, r in sub.iterrows():
        key      = (r["unit_raw"], r["outage_start"], r["outage_end"])
        is_new   = key in new_keys
        bar_col  = C_NEW if is_new else pct_to_color(r.get("available_pct", 0))
        border   = dict(width=2, color=C_NEW) if is_new \
                   else dict(width=0.5, color="rgba(0,0,0,0.15)")
        y_lbl    = f"{r['unit_name']}  ({r['installed_MW']:.0f} MW)"
        hover    = (
            f"<b>{r['unit_name']}</b> [{level}]<br>"
            f"Typ: {r['outage_type']}<br>"
            f"Instalovaný: {r['installed_MW']:.0f} MW | Dostupný: {r['available_MW']:.0f} MW<br>"
            f"<b>Výpadek: {r['unavailable_MW']:.0f} MW ({100-r['available_pct']:.0f} %)</b><br>"
            f"Od: {r['outage_start'].strftime('%a %d.%m %H:%M')}  →  "
            f"Do: {r['outage_end'].strftime('%a %d.%m %H:%M')}"
            + ("  🆕 NOVÁ" if is_new else "")
        )
        fig.add_trace(go.Bar(
            x=[(r["outage_end"] - r["outage_start"]).total_seconds() * 1000],
            y=[y_lbl], base=[r["outage_start"].timestamp() * 1000],
            orientation="h", marker_color=bar_col, marker_line=border,
            hovertext=hover, hoverinfo="text", showlegend=False, width=0.65,
        ))
    _now_marker(fig, now)
    _base_layout(fig, height=height, margin_l=200)
    fig.update_xaxes(type="date", tickformat="%a %d.%m\n%H:%M",
                     range=[xstart.isoformat(), xend.isoformat()])
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=10))
    fig.update_layout(barmode="overlay", margin=dict(l=200, r=15, t=10, b=35))
    return fig


def fig_installed_capacity(cap: pd.Series, height=280):
    fig = go.Figure()
    if cap.empty:
        return _base_layout(fig, height=height)
    items = []
    for col, val in cap.items():
        name, color = psr_lookup(col)
        if pd.notna(val) and float(val) > 0:
            items.append((float(val), name, color))
    items.sort(reverse=True)
    fig.add_trace(go.Bar(
        x=[v for v, _, _ in items],
        y=[n for _, n, _ in items],
        orientation="h",
        marker_color=[c for _, _, c in items],
        marker_line=dict(width=0.5, color="rgba(0,0,0,0.15)"),
        hovertemplate="<b>%{y}</b><br>%{x:,.0f} MW<extra></extra>",
        text=[f"{v:,.0f} MW" for v, _, _ in items],
        textposition="outside",
        textfont=dict(size=10),
    ))
    total = sum(v for v, _, _ in items)
    fig.update_layout(
        height=height,
        title_text=f"Instalovaná kapacita CZ podle zdroje — celkem {total:,.0f} MW",
        template="plotly_white",
        margin=dict(l=160, r=80, t=40, b=20),
        xaxis=dict(title_text="MW", gridcolor=C_GRID),
        yaxis=dict(autorange="reversed", tickfont=dict(size=11)),
        showlegend=False,
    )
    return fig
