"""CoT — ICE MiFID II Commitments of Traders (dřív "📈 CoT")."""
import pandas as pd

from web.registry import page, tab, chart

PAGE = "cot"
page(PAGE, "CoT", "📈")

DEFAULT_COMMODITIES = [
    ("ttf", "NDEX", "TFM", "🇳🇱 TTF"),
    ("eua", "NDEX", "C", "🌍 EUA"),
    ("brent", "IFEU", "B", "🛢️ Brent"),
]


def _fmt_ts(iso_str) -> str:
    try:
        return pd.Timestamp(iso_str).tz_convert("Europe/Prague").strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return str(iso_str)


def _status(df: pd.DataFrame) -> list:
    from data.ice_cot import load_ice_cot_status, VENUES
    status_df = load_ice_cot_status()
    out = []
    for venue in VENUES:
        item = {"venue": venue, "latest": None, "last_ok": None, "last_fail": None, "no_log": False}
        in_store = df[df["venue"] == venue] if not df.empty else df
        if not in_store.empty:
            item["latest"] = in_store["report_date"].max()
        v = status_df[status_df["venue"] == venue].sort_values("timestamp")
        if v.empty:
            item["no_log"] = True
        else:
            ok = v[v["success"] == True]  # noqa: E712
            if not ok.empty:
                item["last_ok"] = (_fmt_ts(ok.iloc[-1]["timestamp"]), ok.iloc[-1]["report_date"])
            last = v.iloc[-1]
            if not bool(last["success"]):
                item["last_fail"] = (_fmt_ts(last["timestamp"]), last.get("error") or f"HTTP {last.get('http_status')}")
        out.append(item)
    return out


def _products(df):
    return (df[["venue", "product_code", "product_name"]].drop_duplicates()
            .sort_values(["venue", "product_name"]).reset_index(drop=True))


def _selection(p, key: str, df: pd.DataFrame):
    for k, venue, code, _label in DEFAULT_COMMODITIES:
        if k == key:
            return venue, code
    prods = _products(df)
    values = [f"{r.venue}|{r.product_code}" for r in prods.itertuples()]
    sel = p.str("product", values[0] if values else None, choices=values)
    if not sel:
        return None, None
    return tuple(sel.split("|", 1))


def _section(p, key: str) -> dict:
    from data.ice_cot import load_ice_cot
    from charts.cot import compute_financial_net_position
    df = load_ice_cot()
    ctx = {"status": _status(df), "empty": df.empty}
    if df.empty:
        return ctx
    if key == "custom":
        prods = _products(df)
        ctx["products"] = [f"{r.venue}|{r.product_code}" for r in prods.itertuples()]
        ctx["product_labels"] = {f"{r.venue}|{r.product_code}": f"{r.venue} / {r.product_name} ({r.product_code})"
                                 for r in prods.itertuples()}
    venue, code = _selection(p, key, df)
    ctx["product"] = f"{venue}|{code}" if venue else None
    kpi = compute_financial_net_position(df, venue, code) if venue else None
    if kpi:
        kpi = dict(kpi)
        kpi.pop("history")
    ctx["kpi"] = kpi
    return ctx


def _make(key, label):
    @tab(PAGE, key, label)
    def _tab(p, _key=key):
        return _section(p, _key)

    @chart(PAGE, key, "positioning")
    def _ch_pos(p, _key=key):
        from data.ice_cot import load_ice_cot
        from charts.cot import fig_cot_positioning
        df = load_ice_cot()
        venue, code = _selection(p, _key, df)
        return None if df.empty or not venue else fig_cot_positioning(df, venue, code)

    @chart(PAGE, key, "net")
    def _ch_net(p, _key=key):
        import plotly.graph_objects as go
        from data.ice_cot import load_ice_cot
        from charts.cot import compute_financial_net_position
        df = load_ice_cot()
        venue, code = _selection(p, _key, df)
        kpi = compute_financial_net_position(df, venue, code) if venue else None
        if not kpi:
            return None
        h = kpi["history"]
        fig = go.Figure(go.Scatter(x=list(h.index), y=h.values, mode="lines", line=dict(color="#1565C0", width=2),
                                   hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>"))
        fig.update_layout(height=260, margin=dict(l=60, r=15, t=10, b=30), template="plotly_white",
                          yaxis_title=kpi["unit_label"])
        return fig


for _k, _v, _c, _label in DEFAULT_COMMODITIES:
    _make(_k, _label)
_make("custom", "🔍 Vlastní výběr")
