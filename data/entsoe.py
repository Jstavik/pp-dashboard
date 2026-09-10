import concurrent.futures

import pandas as pd
import streamlit as st
from entsoe import EntsoePandasClient

from config import ENTSOE_TOKEN, ENTSOE_REQUEST_TIMEOUT_S


@st.cache_resource
def _get_client():
    # timeout — bez tohohle requests čeká na odpověď neomezeně dlouho
    # (Python requests default = None = žádný timeout), takže try/except
    # kolem volání nikdy nezachytí viset spojení, jen skutečnou chybu.
    # EntsoePandasClient/EntsoeRawClient timeout param podporuje přímo
    # (předává se beze změny do requests.get, ověřeno ve zdroji knihovny)
    # — 20s konzistentní s timeouty používanými jinde v projektu.
    # retry_count — knihovna defaultuje na 3 pokusy × 10s retry_delay,
    # což při výpadku ENTSO-E znamená až ~90s čekání na jedno volání
    # (3× 20s timeout + 2× 10s delay). Snížení na 2 drží worst-case
    # na ~50s a appka se rychleji dostane k except/fallbacku.
    #
    # DŮLEŽITÁ MEZERA (ověřeno živě 2026-09-09, entsoe-py 0.6.x zdroj
    # decorators.py::retry): retry_wrapper chytá jen requests.ConnectionError/
    # gaierror/RemoteDisconnected — NE requests.exceptions.ReadTimeout
    # (v requests hierarchii Timeout ⊄ ConnectionError). Živý test proti
    # ENTSO-E s tímhle retry_count=2 na visícím spojení: 1 HTTP request,
    # 1× 20s read timeout, chyba propaguje OKAMŽITĚ bez jediného retry.
    # Tenhle parametr tedy zkracuje worst-case jen pro connection-refused/
    # DNS/remote-disconnect chyby, NE pro read-timeout (dosud pozorovaný
    # dominantní způsob selhání proti ENTSO-E) — tam žádný retry_count
    # rozdíl nedělá, jeden hang = jeden timeout.
    return EntsoePandasClient(api_key=ENTSOE_TOKEN, timeout=ENTSOE_REQUEST_TIMEOUT_S, retry_count=2)


client = _get_client()


@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_entsoe_data():
    now        = pd.Timestamp.now(tz="Europe/Prague")
    start_day  = now.normalize()
    end_imbal  = now + pd.Timedelta(hours=1)
    end_load   = start_day + pd.Timedelta(days=2)
    end_out    = start_day + pd.Timedelta(days=7)

    # ── FETCH fáze — 7 nezávislých zdrojů paralelně přes ThreadPoolExecutor
    # (I/O-bound HTTP, GIL nevadí). vol nemá try/except stejně jako dřív —
    # selže-li, výjimka propaguje ven z fetch_entsoe_data() přesně jako
    # v sekvenční verzi (volající v app.py to obaluje vlastním try/except).
    # Ostatní 4 fetch helpery si drží try/except přesně jak je měly dřív
    # (fetch call + triviální reshape typu iloc/rename, co bez úspěšného
    # fetche nedává smysl volat zvlášť). PROCESSING fáze níž pak dělá jen
    # levý merge/reshape, co na I/O nezávisí.
    def _fetch_price():
        try:
            return client.query_imbalance_prices("CZ", start=start_day, end=end_imbal)
        except Exception:
            return None

    def _fetch_gen():
        try:
            return client.query_generation("CZ", start=start_day, end=end_imbal, psr_type=None)
        except Exception:
            return None

    def _fetch_load_actual():
        try:
            load_actual = client.query_load("CZ", start=start_day, end=end_load)
            if isinstance(load_actual, pd.DataFrame):
                load_actual = load_actual.iloc[:, 0]
            return load_actual.rename("actual_MW")
        except Exception:
            return pd.Series(dtype="float64", name="actual_MW")

    def _fetch_load_fc():
        try:
            load_fc = client.query_load_forecast("CZ", start=start_day, end=end_load)
            if isinstance(load_fc, pd.DataFrame):
                load_fc = load_fc.iloc[:, 0]
            return load_fc.rename("forecast_MW")
        except Exception:
            return pd.Series(dtype="float64", name="forecast_MW")

    def _fetch_unavail(level, fn):
        try:
            raw = fn("CZ", start=start_day, end=end_out)
            if raw is not None and not raw.empty:
                raw = raw.copy()
                raw["unit_level"] = level
                return raw
        except Exception:
            pass
        return None

    # Bez "with" — na explicitní vol.result() výjimku (stejně nechráněnou
    # jako dřív) chceme propagovat hned, ne čekat, až executor.shutdown()
    # v __exit__ dokyne i zbylé (třeba viset na timeoutu) vlákna, jejichž
    # výsledek stejně zahodíme. shutdown(wait=False) v finally nezabíjí
    # běžící vlákna, jen je nečeká — dokončí se na pozadí sama.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=7)
    try:
        fut_vol         = executor.submit(client.query_imbalance_volumes, "CZ", start=start_day, end=end_imbal)
        fut_price       = executor.submit(_fetch_price)
        fut_gen         = executor.submit(_fetch_gen)
        fut_load_actual = executor.submit(_fetch_load_actual)
        fut_load_fc     = executor.submit(_fetch_load_fc)
        fut_pu          = executor.submit(_fetch_unavail, "PU", client.query_unavailability_of_production_units)
        fut_gu          = executor.submit(_fetch_unavail, "GU", client.query_unavailability_of_generation_units)

        vol         = fut_vol.result()
        pri         = fut_price.result()
        gen         = fut_gen.result()
        load_actual = fut_load_actual.result()
        load_fc     = fut_load_fc.result()
        pu_raw      = fut_pu.result()
        gu_raw      = fut_gu.result()
    finally:
        executor.shutdown(wait=False)

    # ── PROCESSING fáze — sekvenčně, žádné I/O ─────────────────────────
    imbal = (vol.rename("odchylka_MWh").to_frame()
             if isinstance(vol, pd.Series)
             else vol.select_dtypes("number").sum(axis=1).rename("odchylka_MWh").to_frame())

    if pri is not None:
        try:
            imbal["price_Short"] = pri["Short"]
            imbal["price_Long"]  = pri["Long"]
        except Exception:
            imbal["price_Short"] = float("nan")
            imbal["price_Long"]  = float("nan")
    else:
        imbal["price_Short"] = float("nan")
        imbal["price_Long"]  = float("nan")

    if gen is not None:
        try:
            if isinstance(gen.columns, pd.MultiIndex):
                lvls = gen.columns.get_level_values(1)
                gen_actual = (gen.xs("Actual Aggregated", level=1, axis=1)
                              if "Actual Aggregated" in lvls
                              else gen.xs(lvls[0], level=1, axis=1))
            else:
                gen_actual = gen
        except Exception:
            gen_actual = pd.DataFrame()
    else:
        gen_actual = pd.DataFrame()

    out_frames = [f for f in (pu_raw, gu_raw) if f is not None]
    raw_out = pd.concat(out_frames, ignore_index=True) if out_frames else pd.DataFrame()

    return imbal, gen_actual, load_actual, load_fc, raw_out, now


@st.cache_data(ttl=60 * 15, show_spinner=False)
def fetch_dap(day_offset: int = 0):
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize() + pd.Timedelta(days=day_offset)
    if start.tzinfo is None:
        start = start.tz_localize("Europe/Prague")
    end = start + pd.Timedelta(days=1)
    try:
        raw = client.query_day_ahead_prices("CZ", start=start, end=end)
    except Exception:
        return pd.Series(dtype=float, name="dap_EUR_MWh")
    if raw is None or len(raw) == 0:
        return pd.Series(dtype=float, name="dap_EUR_MWh")
    raw = raw.tz_convert("Europe/Prague")
    raw.name = "dap_EUR_MWh"
    if len(raw) <= 25:
        idx_15 = pd.date_range(start=start, periods=96, freq="15min", tz="Europe/Prague")
        raw = raw.reindex(idx_15, method="ffill")
    return raw.dropna()


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def fetch_installed_capacity():
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize()
    end   = start + pd.Timedelta(days=1)
    try:
        raw = client.query_installed_generation_capacity(
            country_code="CZ", start=start, end=end, psr_type=None
        )
        if raw is None or raw.empty:
            return pd.Series(dtype=float)
        last = raw.iloc[-1]
        return last.dropna()
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=60 * 15, show_spinner=False)
def fetch_activation_prices():
    now   = pd.Timestamp.now(tz="Europe/Prague")
    start = now.normalize()
    end   = now + pd.Timedelta(hours=1)
    try:
        raw = client.query_activated_balancing_energy_prices(
            country_code="CZ", start=start, end=end
        )
        if raw is None or raw.empty:
            return pd.DataFrame()
        df_act = raw.pivot_table(
            index=raw.index, columns=["ReserveType", "Direction"], values="Price"
        )
        if df_act.index.tz is None:
            df_act.index = df_act.index.tz_localize("UTC").tz_convert("Europe/Prague")
        else:
            df_act.index = df_act.index.tz_convert("Europe/Prague")
        return df_act
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_wind_solar_forecast():
    now       = pd.Timestamp.now(tz="Europe/Prague")
    start_day = now.normalize()
    end_day   = start_day + pd.Timedelta(days=2)
    try:
        raw = client.query_wind_and_solar_forecast(
            country_code="CZ", start=start_day, end=end_day, psr_type=None
        )
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            return pd.DataFrame()
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.xs("Actual Aggregated", level=1, axis=1, drop_level=True) \
                if "Actual Aggregated" in raw.columns.get_level_values(1) \
                else raw.xs(raw.columns.get_level_values(1)[0], level=1, axis=1, drop_level=True)
        if raw.index.tz is None:
            raw.index = raw.index.tz_localize("UTC").tz_convert("Europe/Prague")
        else:
            raw.index = raw.index.tz_convert("Europe/Prague")
        return raw
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_reserves():
    now      = pd.Timestamp.now(tz="Europe/Prague")
    start    = now.normalize()
    end      = now.normalize() + pd.Timedelta(days=10)
    start_yr = pd.Timestamp(f"{now.year}-01-01", tz="Europe/Prague")
    end_yr   = pd.Timestamp(f"{now.year}-07-01", tz="Europe/Prague")
    if now.month >= 7:
        start_yr = pd.Timestamp(f"{now.year}-07-01", tz="Europe/Prague")
        end_yr   = pd.Timestamp(f"{now.year+1}-01-01", tz="Europe/Prague")

    def _q(fn, pt, ma, s, e):
        try:
            return fn(country_code="CZ", start=s, end=e,
                      process_type=pt, type_marketagreement_type=ma)
        except Exception:
            return pd.DataFrame()

    # Všech 6 dotazů je nezávislých (různé endpointy/období) — paralelně
    # přes ThreadPoolExecutor místo sekvenčně, ať se čekání na I/O
    # překrývá místo sčítá (N × timeout). _q() si drží vlastní
    # try/except beze změny, takže selhání jednoho dotazu neovlivní
    # ostatní.
    jobs = {
        "afrr_d_amt": (client.query_contracted_reserve_amount, "A51", "A01", start, end),
        "afrr_d_pri": (client.query_contracted_reserve_prices,  "A51", "A01", start, end),
        "afrr_y_amt": (client.query_contracted_reserve_amount, "A51", "A04", start_yr, end_yr),
        "afrr_y_pri": (client.query_contracted_reserve_prices,  "A51", "A04", start_yr, end_yr),
        "mfrr_d_amt": (client.query_contracted_reserve_amount, "A52", "A01", start, end),
        "mfrr_d_pri": (client.query_contracted_reserve_prices,  "A52", "A01", start, end),
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {key: executor.submit(_q, *args) for key, args in jobs.items()}
        results = {key: fut.result() for key, fut in futures.items()}

    results["now"]   = now
    results["start"] = start
    results["end"]   = end
    return results
