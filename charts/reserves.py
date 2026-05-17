import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import (
    C_DEFICIT, C_SURPLUS, C_GRID, C_MUTED, C_TEXT,
    PEAK_HOURS, BAR_W_MS,
    _base_layout, _now_marker, _weekend_shading,
)

_SIG_COLORS = {"CHARGE": "#2E7D32", "DISCHARGE": "#E65100",
               "STANDBY": "#9E9E9E", "HOLD": "#1565C0"}
_SIG_VALS   = {"CHARGE": 1, "DISCHARGE": -1, "STANDBY": 0, "HOLD": 0}


def _get_rseries(reserves, key, col) -> pd.Series:
    df = reserves.get(key, pd.DataFrame())
    if df is None or df.empty: return pd.Series(dtype=float)
    if col in df.columns:
        s = df[col].dropna()
        return s.tz_convert("Europe/Prague") if s.index.tz else s
    return pd.Series(dtype=float)


def _fig_reserve_simple(traces_cfg, title, y_label, now, start, end, height=380):
    fig = go.Figure()
    has_data = False
    for series, name, color, dash, width, hover_fmt in traces_cfg:
        if series.empty:
            continue
        has_data = True
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values,
            name=name, mode="lines",
            line=dict(color=color, width=width, dash=dash),
            hovertemplate=f"{name}: %{{y:{hover_fmt}}}<extra></extra>",
        ))
    if not has_data:
        fig.add_annotation(text="Data nejsou dostupná",
                           x=0.5, y=0.5, xref="paper", yref="paper",
                           showarrow=False, font=dict(size=12, color=C_MUTED))
    _now_marker(fig, now)
    _weekend_shading(fig, start, end)
    fig.update_layout(
        height=height,
        title_text=title,
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.25, x=0, font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=65, r=20, t=45, b=80),
        xaxis=dict(
            type="date",
            tickformat="%a %d.%m",
            range=[start.isoformat(), end.isoformat()],
            gridcolor=C_GRID,
        ),
        yaxis=dict(title_text=y_label, gridcolor=C_GRID),
    )
    return fig


def fig_reserve_volumes(reserves, now, start, end, height=400):
    traces = [
        (_get_rseries(reserves, "afrr_d_amt", "Up"),        "aFRR denní Up",   "#1565C0", "solid", 2.0, ",.0f"),
        (_get_rseries(reserves, "afrr_d_amt", "Down"),       "aFRR denní Down", "#C62828", "solid", 2.0, ",.0f"),
        (_get_rseries(reserves, "afrr_y_amt", "Up"),         "aFRR roční Up",   "#42A5F5", "dash",  1.8, ",.0f"),
        (_get_rseries(reserves, "afrr_y_amt", "Down"),       "aFRR roční Down", "#EF5350", "dash",  1.8, ",.0f"),
        (_get_rseries(reserves, "mfrr_d_amt", "Symmetric"),  "mFRR denní",      "#2E7D32", "solid", 2.0, ",.0f"),
    ]
    return _fig_reserve_simple(traces, "Rezervy — objemy [MW]", "MW", now, start, end, height)


def fig_reserve_prices(reserves, now, start, end, height=400):
    traces = [
        (_get_rseries(reserves, "afrr_d_pri", "Up"),        "aFRR denní Up",   "#1565C0", "solid", 2.0, ",.2f"),
        (_get_rseries(reserves, "afrr_d_pri", "Down"),       "aFRR denní Down", "#C62828", "solid", 2.0, ",.2f"),
        (_get_rseries(reserves, "afrr_y_pri", "Up"),         "aFRR roční Up",   "#42A5F5", "dash",  1.8, ",.2f"),
        (_get_rseries(reserves, "afrr_y_pri", "Down"),       "aFRR roční Down", "#EF5350", "dash",  1.8, ",.2f"),
        (_get_rseries(reserves, "mfrr_d_pri", "Symmetric"),  "mFRR denní",      "#2E7D32", "solid", 2.0, ",.2f"),
    ]
    return _fig_reserve_simple(traces, "Rezervy — ceny [EUR/MW]", "EUR/MW", now, start, end, height)


def calc_dap_stats(s: pd.Series) -> dict:
    if s.empty:
        return {"base": None, "peak": None, "offpeak": None, "min": None, "max": None}
    pm = s.index.hour.isin(PEAK_HOURS)
    def _avg(x): return round(float(x.mean()), 2) if len(x) else None
    return {"base": _avg(s), "peak": _avg(s[pm]), "offpeak": _avg(s[~pm]),
            "min": round(float(s.min()), 2), "max": round(float(s.max()), 2)}


def fig_dap(s_d0, s_d1, now, height=320):
    start_d0 = now.normalize()
    start_d1 = start_d0 + pd.Timedelta(days=1)
    label0   = f"D0 — {now.strftime('%d.%m.%Y')}"
    label1   = f"D+1 — {(now + pd.Timedelta(days=1)).strftime('%d.%m.%Y')}"
    fig = make_subplots(rows=1, cols=2, subplot_titles=[label0, label1],
                        column_widths=[0.5, 0.5], horizontal_spacing=0.08)

    def _add_dap(series, row, col, color, peak_col, name):
        if series.empty:
            return
        fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines",
                                 name=name, line=dict(color=color, width=2, shape="hv"),
                                 fill="tozeroy",
                                 fillcolor=f"rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},.08)",
                                 hovertemplate=f"%{{x|%H:%M}}  %{{y:.2f}} EUR/MWh<extra>{name}</extra>"),
                      row=row, col=col)
        peak = series[series.index.hour.isin(PEAK_HOURS)]
        if not peak.empty:
            fig.add_trace(go.Scatter(x=peak.index, y=peak.values, mode="lines",
                                     name=f"{name} Peak", line=dict(color=peak_col, width=2.5, shape="hv"),
                                     hovertemplate=f"%{{x|%H:%M}}  %{{y:.2f}} EUR/MWh<extra>Peak</extra>"),
                          row=row, col=col)
        avg = float(series.mean())
        fig.add_hline(y=avg, line_dash="dot", line_color=color, line_width=1, row=row, col=col)

    _add_dap(s_d0, 1, 1, "#1565C0", "#F57F17", "D0")
    _add_dap(s_d1, 1, 2, "#2E7D32", "#E65100", "D+1")
    if s_d1.empty:
        fig.add_annotation(text="D+1 zatím nedostupné (aukce po 13:00)",
                           x=0.5, y=0.5, xref="x2 domain", yref="y2 domain",
                           showarrow=False, font=dict(size=11, color="#888"))
    fig.add_vline(x=now.isoformat(), line_dash="dot", line_color=C_DEFICIT, line_width=1.5)
    fig.update_layout(
        height=height, template="plotly_white", hovermode="x", showlegend=True,
        legend=dict(orientation="h", y=-0.15, x=0),
        margin=dict(l=50, r=15, t=50, b=40),
        xaxis =dict(type="date", tickformat="%H:%M",
                    range=[start_d0.isoformat(), (start_d0+pd.Timedelta(days=1)).isoformat()]),
        xaxis2=dict(type="date", tickformat="%H:%M",
                    range=[start_d1.isoformat(), (start_d1+pd.Timedelta(days=1)).isoformat()]),
    )
    fig.update_yaxes(title_text="EUR/MWh")
    return fig


def simulate_battery_dap(prices, bat_capacity_kwh, bat_power_kw,
                          max_cycles, cycle_cost, hold_enabled):
    interval_h     = 0.25
    soc            = 50.0
    cycles_done    = 0.0
    avg_price      = float(prices.mean())
    low_threshold  = avg_price - cycle_cost / 2
    high_threshold = avg_price + cycle_cost / 2
    results        = []

    for ts, price in prices.items():
        if cycles_done >= max_cycles:
            action, power = ("HOLD" if hold_enabled else "STANDBY"), 0.0
        elif price <= low_threshold and soc < 95:
            power  = min(bat_power_kw, (bat_capacity_kwh * (100 - soc) / 100) / interval_h)
            soc    = min(100, soc + (power * interval_h / bat_capacity_kwh) * 100)
            action = "CHARGE"
        elif price >= high_threshold and soc > 5:
            power  = -min(bat_power_kw, (bat_capacity_kwh * soc / 100) / interval_h)
            soc    = max(0, soc + (power * interval_h / bat_capacity_kwh) * 100)
            action = "DISCHARGE"
            cycles_done += abs(power) * interval_h / bat_capacity_kwh
        else:
            action, power = ("HOLD" if hold_enabled else "STANDBY"), 0.0

        revenue = -power * interval_h * price / 1000
        results.append({"time": ts, "price": price, "action": action,
                         "power_kw": power, "soc_pct": soc, "revenue_eur": revenue})

    return pd.DataFrame(results).set_index("time"), cycles_done


def fig_battery_strategy(df_sim, low_thresh, high_thresh, avg_price, now, height=600):
    start_d0 = now.normalize()
    end_d1   = start_d0 + pd.Timedelta(days=2)

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        specs=[[{}], [{}], [{"secondary_y": True}]],
        subplot_titles=["DAP cena [EUR/MWh]", "SoC baterie [%]",
                        "Signál + kumulativní P&L [EUR]"],
        vertical_spacing=0.08,
        row_heights=[0.35, 0.25, 0.40],
    )

    fig.add_trace(go.Scatter(
        x=df_sim.index, y=df_sim["price"], mode="lines", name="DAP cena",
        line=dict(color="#1565C0", width=2),
        hovertemplate="Cena: %{y:.2f} EUR/MWh<extra></extra>",
    ), row=1, col=1)
    for y_val, color, lbl in [
        (low_thresh,  "#2E7D32", f"Nabíjecí práh ({low_thresh:.1f})"),
        (high_thresh, "#C62828", f"Vybíjecí práh ({high_thresh:.1f})"),
        (avg_price,   "#F9A825", f"Průměr ({avg_price:.1f})"),
    ]:
        fig.add_hline(y=y_val, line_color=color, line_dash="dash", line_width=1.2,
                      annotation_text=lbl, annotation_position="right", row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df_sim.index, y=df_sim["soc_pct"], mode="lines", name="SoC [%]",
        fill="tozeroy", fillcolor="rgba(46,125,50,0.15)",
        line=dict(color="#2E7D32", width=2),
        hovertemplate="SoC: %{y:.1f} %<extra></extra>",
    ), row=2, col=1)

    for action, color in _SIG_COLORS.items():
        mask = df_sim["action"] == action
        if not mask.any():
            continue
        fig.add_trace(go.Bar(
            x=df_sim.index[mask], y=[_SIG_VALS[action]] * int(mask.sum()),
            name=action, marker_color=color, width=BAR_W_MS,
            hovertemplate=f"{action}: %{{x|%a %H:%M}}<extra></extra>",
        ), row=3, col=1, secondary_y=False)

    cum_pnl = df_sim["revenue_eur"].cumsum()
    fig.add_trace(go.Scatter(
        x=df_sim.index, y=cum_pnl, mode="lines", name="Kum. P&L [EUR]",
        line=dict(color="#212121", width=2),
        hovertemplate="P&L: %{y:.2f} EUR<extra></extra>",
    ), row=3, col=1, secondary_y=True)

    fig.update_layout(
        height=height, template="plotly_white", hovermode="x unified", barmode="overlay",
        title_text=f"Strategie baterie — D0+D+1 ({now.strftime('%d.%m.%Y')})",
        legend=dict(orientation="h", y=-0.07, x=0, font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=65, r=70, t=50, b=60),
        xaxis=dict(type="date", gridcolor=C_GRID,
                   range=[start_d0.isoformat(), end_d1.isoformat()]),
    )
    fig.update_yaxes(title_text="EUR/MWh", gridcolor=C_GRID, row=1, col=1)
    fig.update_yaxes(title_text="%", gridcolor=C_GRID, row=2, col=1, range=[0, 105])
    fig.update_yaxes(title_text="Signál", gridcolor=C_GRID, row=3, col=1,
                     secondary_y=False,
                     tickvals=[-1, 0, 1], ticktext=["DISCHARGE", "STANDBY", "CHARGE"])
    fig.update_yaxes(title_text="EUR", row=3, col=1, secondary_y=True, showgrid=False)
    return fig
