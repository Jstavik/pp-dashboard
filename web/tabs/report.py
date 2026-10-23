"""Report — ranní přehledy (dřív app.py sekce "📋 Report")."""
import pandas as pd
import plotly.graph_objects as go

from config import GASSCO_REPORT_DEFAULT_RANGE_DAYS, storage_color
from web.registry import page, tab, chart
from web.tabs.gas import flows, _gie
from web.util import decat

PAGE = "report"
page(PAGE, "Report", "📋")

REPORT_POINTS = ["Emden", "Dornum", "Zeebrugge", "Nybro", "Dunkerque", "Easington", "St.Fergus"]
SHOW_CC = ["EU", "AT", "BE", "CZ", "DE", "ES", "FR", "HR", "HU", "IT", "LV", "NL", "PL", "PT", "RO", "SK", "UA"]


def _img(filename: str, height: int, width: int = 794) -> dict:
    return {"displayModeBar": True,
            "toImageButtonOptions": {"format": "png", "filename": filename, "height": height,
                                     "width": width, "scale": 2}}


@tab(PAGE, "toky", "Toky plynu")
def toky(p):
    return {"empty": flows().empty}


@chart(PAGE, "toky", "map", displayModeBar=True, scrollZoom=True)
def _ch_map(p):
    from data.gassco import load_gassco
    from charts.gas import fig_gas_map
    df = flows()
    if df.empty:
        return None
    cutoff = df["date"].max() - pd.Timedelta(days=14)
    return fig_gas_map(decat(df[df["date"] >= cutoff]), df_gassco=load_gassco())


def _gassco_df():
    from data.gassco import load_gassco
    return load_gassco()


@tab(PAGE, "gassco", "GASSCO")
def gassco(p):
    return {"empty": _gassco_df().empty}


@chart(PAGE, "gassco", "kpi", **_img("gassco_kpi", 400))
def _ch_kpi(p):
    from charts.gassco import fig_gassco_kpi
    df = _gassco_df()
    return None if df.empty else fig_gassco_kpi(df)


@chart(PAGE, "gassco", "timeseries", **_img("gassco_ts", 400))
def _ch_ts(p):
    from charts.gassco import fig_gassco_timeseries
    df = _gassco_df()
    if df.empty:
        return None
    end = df["date"].max()
    return fig_gassco_timeseries(df, REPORT_POINTS, end - pd.Timedelta(days=GASSCO_REPORT_DEFAULT_RANGE_DAYS), end)


@chart(PAGE, "gassco", "season", **_img("gassco_sea", 400))
def _ch_season(p):
    from charts.gassco import fig_gassco_seasonality
    df = _gassco_df()
    if df.empty:
        return None
    years = sorted(df["date"].dt.tz_convert("Europe/Prague").dt.year.unique())[-5:]
    return fig_gassco_seasonality(df, REPORT_POINTS, years)


def _storage_last():
    df = _gie()
    if df.empty:
        return df
    d = df.copy()
    for c in ["full", "gasInStorage", "injection", "withdrawal"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna(subset=["gasDayStart"]).sort_values("gasDayStart").groupby("country_code").last().reset_index()


@tab(PAGE, "zasobniky", "Zásobníky")
def zasobniky(p):
    df = _gie()
    if df.empty:
        return {"empty": True}
    return {"last_date": df["gasDayStart"].dropna().dt.date.max()}


@chart(PAGE, "zasobniky", "table", **_img(f"zasobniky_stav_{pd.Timestamp.now():%Y%m%d}", 600))
def _ch_table(p):
    last = _storage_last()
    if last.empty:
        return None
    tbl = last[last["country_code"].isin(SHOW_CC)].copy()
    tbl = tbl.set_index("country_code").reindex(SHOW_CC).reset_index()
    tbl["net"] = tbl["injection"].fillna(0) - tbl["withdrawal"].fillna(0)
    cell_colors = [
        ["#F5F5F5"] * len(tbl),
        ["#BDBDBD" if pd.isna(v) else storage_color(v) for v in tbl["full"]],
        ["white"] * len(tbl),
        ["#E8F5E9" if n >= 0 else "#FFEBEE" for n in tbl["net"]],
        ["white"] * len(tbl),
    ]
    font_colors = [
        ["#333"] * len(tbl), ["white"] * len(tbl), ["#333"] * len(tbl),
        ["#2E7D32" if n >= 0 else "#C62828" for n in tbl["net"]], ["#555"] * len(tbl),
    ]
    fig = go.Figure(data=[go.Table(
        columnwidth=[60, 80, 90, 90, 90],
        header=dict(values=["<b>Země</b>", "<b>Plnost %</b>", "<b>Objem TWh</b>", "<b>Net GWh/d</b>",
                            "<b>Vtláčení GWh/d</b>"],
                    fill_color="#1565C0", font=dict(color="white", size=11), align="center", height=32),
        cells=dict(values=[
            tbl["country_code"],
            tbl["full"].apply(lambda x: f"{x:.1f}%" if not pd.isna(x) else "n/a"),
            tbl["gasInStorage"].apply(lambda x: f"{x:.1f} TWh" if not pd.isna(x) else "n/a"),
            tbl["net"].apply(lambda x: f"+{x:.0f}" if x >= 0 else f"{x:.0f}"),
            tbl["injection"].apply(lambda x: f"+{x:.0f}" if not pd.isna(x) else "n/a"),
        ], fill_color=cell_colors, font=dict(color=font_colors, size=11), align="center", height=28),
    )])
    fig.update_layout(height=len(tbl) * 28 + 60, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="white")
    return fig


@chart(PAGE, "zasobniky", "grid", **_img(f"zasobniky_grid_{pd.Timestamp.now():%Y%m%d}", 700))
def _ch_grid(p):
    from charts.storage import fig_storage_grid
    df = _gie()
    if df.empty:
        return None
    years = sorted(df["gasDayStart"].dt.year.dropna().unique().astype(int))[-5:]
    return fig_storage_grid(df, "full", years)


@tab(PAGE, "dap", "DAP mapa")
def dap(p):
    from data.dap_europe import load_dap_europe
    return {"empty": load_dap_europe().empty}


@chart(PAGE, "dap", "map", **_img(f"dap_mapa_{pd.Timestamp.now():%Y%m%d}", 800, 1200))
def _ch_dap(p):
    from data.dap_europe import load_dap_europe
    from charts.dap_map import fig_dap_map
    df = load_dap_europe()
    return None if df.empty else fig_dap_map(df)
