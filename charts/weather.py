"""Grafy stránky Počasí."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from charts.explorer import _LAYOUT, _title

TZ = "Europe/Prague"


def _local(s: pd.Series) -> pd.Series:
    return s.dt.tz_convert(TZ).dt.tz_localize(None)


def _normal_on(dates: pd.Series, nrm: pd.DataFrame):
    doy = dates.dt.tz_convert(TZ).dt.dayofyear
    m = nrm.set_index("doy")
    return doy.map(m["mean"]).values, doy.map(m["std"]).values


def _daily_ens(ens: pd.DataFrame, var: str) -> pd.DataFrame:
    """Hodinový ensemble → denní pásmo p10–p90 (denní agregace kvantilů — přibližné)."""
    if ens is None or ens.empty:
        return pd.DataFrame()
    e = ens.assign(date=ens["time"].dt.tz_convert(TZ).dt.normalize())
    agg = "sum" if var == "precip" else "mean"
    d = e.groupby(["model", "date"])[["p10", "p50", "p90"]].agg(agg)
    cnt = e.groupby(["model", "date"])["p50"].count()
    d = d[cnt >= 18]
    if var == "solar":
        d = d * 24 / 1000
    return d.reset_index()


def fig_outlook(obs, fc, ens, nrm, var, title, unit, labels, colors) -> go.Figure:
    """Denně: pozorování (ERA5/recent) + předpověď modelů + ensemble p10–p90
    + normál ±1σ přes celé okno."""
    fig = go.Figure()
    dates = []
    if obs is not None and not obs.empty:
        dates.append(obs["date"])
    if fc is not None and not fc.empty:
        dates.append(fc["date"])
    if nrm is not None and not nrm.empty and dates:
        all_d = pd.Series(pd.concat(dates).drop_duplicates().sort_values().values)
        all_d = pd.Series(pd.date_range(all_d.min(), all_d.max(), freq="D", tz="UTC"))
        mean, std = _normal_on(all_d, nrm)
        x = _local(all_d)
        fig.add_trace(go.Scatter(mode="lines", x=x, y=mean + std, line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(mode="lines", x=x, y=mean - std, line=dict(width=0), fill="tonexty",
                                 fillcolor="rgba(120,144,156,0.18)", name=f"normál ±1σ ({nrm['years'].iloc[0]})"))
        fig.add_trace(go.Scatter(mode="lines", x=x, y=mean, name="normál", line=dict(color="#78909C", dash="dash", width=1.4)))
    de = _daily_ens(ens, var)
    for model, g in de.groupby("model") if not de.empty else []:
        x = g["date"].dt.tz_localize(None)
        c = colors.get(model, "#8E24AA")
        fig.add_trace(go.Scatter(mode="lines", x=x, y=g["p90"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(mode="lines", x=x, y=g["p10"], line=dict(width=0), fill="tonexty", opacity=0.3,
                                 fillcolor=_rgba(c, 0.15), name=f"{labels.get(model, model)} ENS p10–p90"))
    if obs is not None and not obs.empty:
        o = obs.sort_values("date")
        fig.add_trace(go.Scatter(mode="lines", x=_local(o["date"]), y=o["value"], name="skutečnost",
                                 line=dict(color="#1d2733", width=2.2)))
    if fc is not None and not fc.empty:
        for model, g in fc.sort_values("date").groupby("model"):
            fig.add_trace(go.Scatter(mode="lines", x=_local(g["date"]), y=g["value"], name=labels.get(model, model),
                                     line=dict(color=colors.get(model), width=1.8)))
    fig.add_vline(x=pd.Timestamp.now(tz=TZ).tz_localize(None).normalize(), line=dict(color="#90A4AE", dash="dot"))
    fig.update_layout(**_LAYOUT, title=_title(title, "denně: pozorování, předpověď modelů, ensemble a normál"),
                      yaxis_title=unit)
    return fig


def _rgba(hexc: str, a: float) -> str:
    h = (hexc or "#8E24AA").lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def fig_hourly(fc, ens, title, unit, labels, colors) -> go.Figure:
    fig = go.Figure()
    if ens is not None and not ens.empty:
        for model, g in ens.sort_values("time").groupby("model"):
            x = _local(g["time"])
            c = colors.get(model, "#8E24AA")
            fig.add_trace(go.Scatter(mode="lines", x=x, y=g["p90"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(mode="lines", x=x, y=g["p10"], line=dict(width=0), fill="tonexty", fillcolor=_rgba(c, 0.15),
                                     name=f"{labels.get(model, model)} ENS p10–p90"))
            fig.add_trace(go.Scatter(mode="lines", x=x, y=g["p50"], name=f"{labels.get(model, model)} ENS medián",
                                     line=dict(color=c, dash="dot", width=1)))
    for model, g in fc.sort_values("time").groupby("model"):
        fig.add_trace(go.Scatter(mode="lines", x=_local(g["time"]), y=g["value"], name=labels.get(model, model),
                                 line=dict(color=colors.get(model), width=1.5)))
    fig.update_layout(**_LAYOUT, title=_title(title), yaxis_title=unit)
    return fig


def fig_forecast_change(new, old, title, unit, labels, colors) -> go.Figure:
    m = new.merge(old, on=["date", "model"], suffixes=("", "_old"))
    m["d"] = m["value"] - m["value_old"]
    fig = go.Figure()
    for model, g in m.sort_values("date").groupby("model"):
        fig.add_trace(go.Bar(x=_local(g["date"]), y=g["d"], name=labels.get(model, model),
                             marker_color=colors.get(model)))
    fig.update_layout(**_LAYOUT, title=_title(title, "nová − předchozí předpověď, po dnech"), yaxis_title=unit,
                      barmode="group")
    return fig


def fig_hdd_season(obs, fc, nrm, title) -> go.Figure:
    """Kumulované HDD od 1. 7.: letošní sezóna (pozorování + ECMWF předpověď),
    loňská sezóna a normál."""
    from data.weather import hdd
    o = obs.assign(d=obs["date"].dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize())
    today = pd.Timestamp.now(tz=TZ).tz_localize(None).normalize()
    start = pd.Timestamp(today.year if today.month >= 7 else today.year - 1, 7, 1)
    fig = go.Figure()

    def season(st, label, color, width, dash=None, add_fc=False):
        s = o[(o["d"] >= st) & (o["d"] < st + pd.DateOffset(years=1))].sort_values("d")
        ser = pd.Series(hdd(s["value"]), index=s["d"])
        if add_fc and fc is not None and not fc.empty:
            f = fc[fc["model"] == fc["model"].iloc[0]] if "ecmwf_ifs025" not in set(fc["model"]) else \
                fc[fc["model"] == "ecmwf_ifs025"]
            f = f.assign(d=f["date"].dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize())
            f = f[f["d"] > (ser.index.max() if len(ser) else st - pd.Timedelta(days=1))]
            ser = pd.concat([ser, pd.Series(hdd(f["value"]), index=f["d"])])
        if ser.empty:
            return
        x = (ser.index - st).days
        fig.add_trace(go.Scatter(mode="lines", x=x, y=ser.cumsum().values, name=label, line=dict(color=color, width=width, dash=dash)))

    if nrm is not None and not nrm.empty:
        days = pd.date_range(start, start + pd.DateOffset(years=1) - pd.Timedelta(days=1), freq="D")
        m = nrm.set_index("doy")["mean"]
        fig.add_trace(go.Scatter(mode="lines", x=list(range(len(days))), y=np.cumsum(hdd(days.dayofyear.map(m).values)),
                                 name="normál", line=dict(color="#78909C", dash="dash")))
    season(start - pd.DateOffset(years=1), f"{start.year - 1}/{str(start.year)[2:]}", "#90A4AE", 1.4)
    season(start, f"{start.year}/{str(start.year + 1)[2:]} (+ předpověď ECMWF)", "#C62828", 2.6, add_fc=True)
    fig.update_layout(**_LAYOUT, title=_title(title, "HDD = 18 − Tm pro Tm ≤ 15 °C (Eurostat)"),
                      xaxis_title="dní od 1. 7.", yaxis_title="°C·den")
    return fig
