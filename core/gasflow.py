"""Toky plynu po hranicích pro CEE flowchart + kontrola entry/exit.

Zdroje (vše z úložiště, žádná síť):
- `entsog_flows` (ENTSO-G /aggregateddata): denní Physical Flow a Allocation
  po zemích — každá země (countryKey = zóna TSO) hlásí entry/exit vůči
  sousednímu systému (adjacentSystemsKey: TransmissionDE-THE, LNG Terminals,
  Storage, Production, Distribution, Final Consumers, „Transmission“ = mimo EU).
- `entsog_operational` (ENTSO-G /operationaldata): Renomination po bodech
  a operátorech (strana každého TSO na propojovacím bodě).
- `gie_all_storage` (AGSI+): plnění, TWh, vtláčení/těžba GWh/d.
- `lng_storage` (ALSI): send-out a zásoba LNG terminálů (do tooltipu).

Plynárenský den D = řádky ENTSO-G s datem D 00:00 Europe/Prague
(stejně jako stávající mapa a grafy toků).
"""
import re
from datetime import date

import pandas as pd

from config import MSMM3_TO_GWH
from core.cache import ttl_cache
from core.settings import env

TZ = "Europe/Prague"

# ── uzly flowchartu ─────────────────────────────────────────────────────
# (český název, x, y, druh) — schéma na plátně 1200 × 1060, ne mapa.
# main = země s boxem (zásobníky, bilance), ext = menší sousední/vnější uzel,
# bar = Norsko jako pruh nahoře, toky z něj jdou svisle dolů (NO_BAR, NO_DROP_X).
NODES = {
    "NL": ("Nizozemsko", 310, 300, "main"),
    "BE": ("Belgie", 110, 445, "main"),
    "DE": ("Německo", 545, 350, "main"),
    "PL": ("Polsko", 820, 245, "main"),
    "CZ": ("Česko", 710, 455, "main"),
    "SK": ("Slovensko", 915, 530, "main"),
    "UA": ("Ukrajina", 1095, 395, "main"),
    "FR": ("Francie", 180, 640, "main"),
    "CH": ("Švýcarsko", 430, 660, "main"),
    "AT": ("Rakousko", 680, 655, "main"),
    "HU": ("Maďarsko", 940, 705, "main"),
    "IT": ("Itálie", 400, 865, "main"),
    "SI": ("Slovinsko", 630, 830, "main"),
    "HR": ("Chorvatsko", 850, 880, "main"),
    # menší uzly — vnější zdroje a sousedé mimo výběr (bez toku se nekreslí)
    "NO": ("Norsko", 320, 100, "bar"),
    "UK": ("Velká Británie", 85, 200, "ext"),
    "DK": ("Dánsko", 700, 100, "ext"),
    "LT": ("Litva", 1010, 140, "ext"),
    "LU": ("Lucembursko", 300, 525, "ext"),
    "ES": ("Španělsko", 85, 830, "ext"),
    "RO": ("Rumunsko", 1120, 650, "ext"),
    "RS": ("Srbsko", 1085, 850, "ext"),
    "SV": ("Jih: DZ/LY/TAP", 400, 990, "ext"),
}
MAIN = [k for k, v in NODES.items() if v[3] == "main"]
NO_BAR = (40, 600, 86, 114)  # x0, x1, y0, y1
NO_DROP_X = {"FR": 222, "BE": 165}  # svislé toky z Norska mimo jiné boxy
BOX_W, BOX_H = 176, 100
EXT_W, EXT_H = 104, 34
# LNG terminály jako přítoky: pozice štítku „LNG …“ u země
LNG_POS = {"NL": (430, 205), "BE": (60, 550), "DE": (400, 455), "FR": (70, 745), "IT": (230, 965),
           "PL": (820, 150), "HR": (740, 975)}

# vnější body („Transmission“ bez kódu země) → partner podle názvu bodu
EXTERNAL_POINTS = [
    (r"Dornum|Emden|Zeebrugge ZPT|^Dunkerque$|St\. Fergus|Easington|North Sea Entry", "NO"),
    (r"Greifswald|Lubmin|Kotlovka", "RU"),
    (r"Kapu[sš]any|Uzhhorod|Bereg|Orlovka|Isaccea|/UA|Budince|Hermanowice|Drozdovichi", "UA"),
    (r"Horgos|Kiskundorozsma|Dimitrovgrad|Zaychar|Kireevo|Kalotina", "RS"),
    (r"Malkoclar|Strandzha|Kipi", "TR"),
    (r"Ungheni", "MD"),
    (r"Kyustendil|Zidilovo", "MK"),
    (r"Mazara|Gela|Melendugno|Almer", "SV"),
    (r"Tarifa", "MA"),
]
# zóny / subsystémy sloučené do země (BeLux je trh BE+LU; Yamal je úsek v PL)
ZONE_ALIASES = {"BE-LUX": "BE", "BE-L-ZONE": "BE", "UK-IUK": "UK", "PL-YAMAL": "PL", "DK-SE": "DK",
                "EE-LV": "EE", "RO-TBP": "RO", "BG-NGTS": "BG", "DE-THE": "DE"}
# countryKey CH v ENTSO-G je ve skutečnosti TAP (bod Kipoi) — švýcarské TSO nehlásí,
# toky CH se berou z pohledu DE/FR/IT/AT.
NOT_REPORTING = {"CH"}


def unit_factor(unit: str) -> float:
    """GWh/d → zvolená jednotka (mcm/d: 1 mil. m³ ≈ 10,55 GWh, stejný převod jako GASSCO)."""
    return 1.0 / MSMM3_TO_GWH if unit == "mcm" else 1.0


def thresholds() -> tuple:
    """(relativní %, absolutní GWh/d) — PPDASH_EE_REL_PCT, PPDASH_EE_ABS_GWH."""
    try:
        rel = float(env("PPDASH_EE_REL_PCT", "5"))
    except ValueError:
        rel = 5.0
    try:
        ab = float(env("PPDASH_EE_ABS_GWH", "5"))
    except ValueError:
        ab = 5.0
    return rel, ab


def partner_of(adj_key, points) -> str:
    """adjacentSystemsKey (+ názvy bodů) → kód partnera / druh."""
    k = str(adj_key or "")
    if k.startswith("Transmission") and len(k) > len("Transmission"):
        code = k[len("Transmission"):].rstrip("-")
        return ZONE_ALIASES.get(code, code[:2])
    if k == "Transmission":
        found = []
        for p in str(points or "").split("|"):
            p = p.strip()
            for rx, code in EXTERNAL_POINTS:
                if re.search(rx, p):
                    if code not in found:
                        found.append(code)
                    break
        return "/".join(sorted(found)) if found else "mimo EU"
    return {"LNG Terminals": "LNG", "Storage": "STOR", "Production": "PROD", "Distribution": "CONS",
            "Final Consumers": "CONS"}.get(k, "OTHER")


# ── čtení ───────────────────────────────────────────────────────────────

FLOW_COLS = ["countryKey", "indicator", "directionKey", "adjacentSystemsKey", "pointsNames", "value_GWh", "date"]


@ttl_cache(ttl=600, maxsize=24)
def _flows_month(indicator: str, month: str) -> pd.DataFrame:
    """Řádky entsog_flows jednoho indikátoru za měsíc (YYYY-MM), s partnerem a dnem."""
    from data.entsog import ENTSOG_FLOWS_DIR
    from data.partitioned_store import read_partitioned
    m0 = pd.Timestamp(month + "-01")
    df = read_partitioned(ENTSOG_FLOWS_DIR, fmt="parquet", date_from=m0.date(),
                          date_to=(m0 + pd.offsets.MonthEnd(0)).date(), columns=FLOW_COLS,
                          filters=[("indicator", "==", indicator)])
    if df.empty or "countryKey" not in df.columns:
        return pd.DataFrame(columns=["day", "country", "direction", "partner", "points", "value"])
    d = pd.to_datetime(df["date"], utc=True).dt.tz_convert(TZ)
    out = pd.DataFrame({
        "day": d.dt.tz_localize(None).dt.normalize(),
        "country": df["countryKey"].astype(str),
        "direction": df["directionKey"].astype(str),
        "partner": [partner_of(a, p) for a, p in zip(df["adjacentSystemsKey"].astype(str),
                                                       df["pointsNames"].astype(str))],
        "points": df["pointsNames"].astype(str),
        "value": pd.to_numeric(df["value_GWh"], errors="coerce"),
    })
    out = out[out["day"].dt.strftime("%Y-%m") == month]
    return out.reset_index(drop=True)


def day_flows(day, indicator: str = "Physical Flow") -> pd.DataFrame:
    """Řádky jednoho plynárenského dne: country, direction, partner, points, value (GWh/d)."""
    day = pd.Timestamp(day).normalize()
    df = _flows_month(indicator, day.strftime("%Y-%m"))
    return df[df["day"] == day].drop(columns="day").reset_index(drop=True)


@ttl_cache(ttl=600, maxsize=2)
def available_days(indicator: str = "Physical Flow") -> list:
    """Dny s (téměř) úplnými daty — počet řádků ≥ 80 % mediánu posledních 30 dní."""
    from data.entsog import ENTSOG_FLOWS_DIR
    from data.partitioned_store import read_partitioned
    df = read_partitioned(ENTSOG_FLOWS_DIR, fmt="parquet", columns=["date", "indicator"],
                          filters=[("indicator", "==", indicator)])
    if df.empty:
        return []
    d = pd.to_datetime(df["date"], utc=True).dt.tz_convert(TZ).dt.tz_localize(None).dt.normalize()
    counts = d.value_counts().sort_index()
    ref = counts.tail(30).median()
    return [x.date() for x in counts[counts >= 0.8 * ref].index]


def latest_day(indicator: str = "Physical Flow"):
    days = available_days(indicator)
    return days[-1] if days else None


# ── hrany (toky mezi uzly) ──────────────────────────────────────────────

def border_table(day, indicator: str = "Physical Flow") -> pd.DataFrame:
    """Směrované toky mezi zeměmi (vč. vnějších partnerů): from, to,
    exit (hlášení země from), entry (hlášení země to), value (použitá
    hodnota: entry přijímající země, jinak exit odesílající), points."""
    df = day_flows(day, indicator)
    if df.empty:
        return pd.DataFrame(columns=["from", "to", "exit", "entry", "value", "points_exit", "points_entry"])
    df = df[~df["country"].isin(NOT_REPORTING)]
    df = df[~df["partner"].isin(["LNG", "STOR", "PROD", "CONS", "OTHER"]) & (df["partner"] != df["country"])]
    g = (df.groupby(["country", "direction", "partner"])
           .agg(value=("value", lambda s: s.sum(min_count=1)), points=("points", lambda s: " | ".join(sorted(set(s)))))
           .reset_index())
    ex = g[g["direction"] == "exit"].rename(columns={"country": "from", "partner": "to", "value": "exit",
                                                      "points": "points_exit"})
    en = g[g["direction"] == "entry"].rename(columns={"partner": "from", "country": "to", "value": "entry",
                                                       "points": "points_entry"})
    m = ex.drop(columns="direction").merge(en.drop(columns="direction"), on=["from", "to"], how="outer")
    m["value"] = m["entry"].where(m["entry"].notna(), m["exit"])
    return m[["from", "to", "exit", "entry", "value", "points_exit", "points_entry"]].reset_index(drop=True)


def _pair_net(bt: pd.DataFrame) -> dict:
    """{(a, b) s a < b: (tok a→b, tok b→a)} z border_table."""
    out = {}
    for r in bt.itertuples(index=False):
        if pd.isna(r.value):
            continue
        a, b = sorted((r[0], r[1]))
        cur = out.setdefault((a, b), [0.0, 0.0])
        cur[0 if r[0] == a else 1] += float(r.value)
    return out


# ── kontrola entry/exit ─────────────────────────────────────────────────

def _flag(exit_v, entry_v, rel_pct, abs_gwh):
    if pd.isna(exit_v) or pd.isna(entry_v):
        return False, None, None
    diff = float(entry_v) - float(exit_v)
    base = max(abs(float(exit_v)), abs(float(entry_v)))
    rel = abs(diff) / base * 100 if base > 0 else 0.0
    return (abs(diff) > abs_gwh and rel > rel_pct), diff, rel


def check_borders(day, indicator: str = "Physical Flow", rel_pct=None, abs_gwh=None) -> pd.DataFrame:
    """Hraniční kontrola: exit země A do B vs. entry země B z A (obě strany
    hlásí ENTSO-G, stejný den, GWh/d). Vrací všechny páry s oběma stranami;
    sloupec flag = odchylka nad oběma prahy."""
    r0, a0 = thresholds()
    rel_pct = r0 if rel_pct is None else rel_pct
    abs_gwh = a0 if abs_gwh is None else abs_gwh
    bt = border_table(day, indicator)
    rows = []
    for r in bt.itertuples(index=False):
        if r[0] in NOT_REPORTING or r[1] in NOT_REPORTING or "/" in r[0] or "/" in r[1]:
            continue
        flag, diff, rel = _flag(r.exit, r.entry, rel_pct, abs_gwh)
        if diff is None:
            continue
        rows.append({"day": pd.Timestamp(day).date(), "level": "hranice", "indicator": indicator,
                     "from": r[0], "to": r[1], "point": f"{r[0]}→{r[1]}", "exit": float(r.exit),
                     "entry": float(r.entry), "diff": diff, "rel": rel, "flag": flag,
                     "detail": f"exit {r[0]}: {r.points_exit} · entry {r[1]}: {r.points_entry}"})
    return pd.DataFrame(rows, columns=["day", "level", "indicator", "from", "to", "point", "exit", "entry",
                                       "diff", "rel", "flag", "detail"])


OPER_COLS = ["indicator", "operatorKey", "pointKey", "pointLabel", "directionKey", "value_GWh",
             "periodFrom_dt", "pointType"]


@ttl_cache(ttl=600, maxsize=4)
def _renominations_month(month: str) -> pd.DataFrame:
    from data.entsog_operational import OPERATIONAL_DIR
    from data.partitioned_store import read_partitioned
    m0 = pd.Timestamp(month + "-01")
    df = read_partitioned(OPERATIONAL_DIR, fmt="parquet", date_from=m0.date(),
                          date_to=(m0 + pd.offsets.MonthEnd(0)).date(), columns=OPER_COLS,
                          filters=[("indicator", "==", "Renomination")])
    if df.empty or "pointKey" not in df.columns:
        return pd.DataFrame(columns=["day", "point", "label", "country", "direction", "value"])
    df = df[df["pointType"].astype(str).str.startswith("Cross-Border")]
    out = pd.DataFrame({
        "day": pd.to_datetime(df["periodFrom_dt"].astype(str), errors="coerce"),
        "point": df["pointKey"].astype(str), "label": df["pointLabel"].astype(str),
        "country": df["operatorKey"].astype(str).str[:2], "direction": df["directionKey"].astype(str),
        "value": pd.to_numeric(df["value_GWh"], errors="coerce"),
    })
    return out.dropna(subset=["day"]).reset_index(drop=True)


def check_points(day, rel_pct=None, abs_gwh=None) -> pd.DataFrame:
    """Bodová kontrola z renominací: na propojovacím bodě (pointKey) exit
    operátora země A vs. entry operátora země B (A ≠ B)."""
    r0, a0 = thresholds()
    rel_pct = r0 if rel_pct is None else rel_pct
    abs_gwh = a0 if abs_gwh is None else abs_gwh
    day = pd.Timestamp(day).normalize()
    df = _renominations_month(day.strftime("%Y-%m"))
    df = df[df["day"] == day]
    rows = []
    if not df.empty:
        g = df.groupby(["point", "country", "direction"]).agg(value=("value", lambda s: s.sum(min_count=1)),
                                                              label=("label", "first")).reset_index()
        for pk, gp in g.groupby("point"):
            countries = sorted(gp["country"].unique())
            if len(countries) < 2:
                continue
            val = {(r.country, r.direction): r.value for r in gp.itertuples(index=False)}
            label = gp["label"].iloc[0]
            for a in countries:
                for b in countries:
                    if a == b or (a, "exit") not in val or (b, "entry") not in val:
                        continue
                    ex, en = val[(a, "exit")], val[(b, "entry")]
                    flag, diff, rel = _flag(ex, en, rel_pct, abs_gwh)
                    if diff is None or (abs(ex) < 1e-9 and abs(en) < 1e-9):
                        continue
                    rows.append({"day": day.date(), "level": "bod", "indicator": "Renomination", "from": a, "to": b,
                                 "point": label, "exit": float(ex), "entry": float(en), "diff": diff, "rel": rel,
                                 "flag": flag, "detail": pk})
    return pd.DataFrame(rows, columns=["day", "level", "indicator", "from", "to", "point", "exit", "entry",
                                       "diff", "rel", "flag", "detail"])


@ttl_cache(ttl=600, maxsize=16)
def mismatches(day, rel_pct=None, abs_gwh=None) -> pd.DataFrame:
    """Odchylky nad prahem: hranice (fyzický tok + alokace) a body (renominace)."""
    parts = [check_borders(day, "Physical Flow", rel_pct, abs_gwh), check_borders(day, "Allocation", rel_pct, abs_gwh),
             check_points(day, rel_pct, abs_gwh)]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(columns=["day", "level", "indicator", "from", "to", "point", "exit", "entry", "diff",
                                     "rel", "flag", "detail"])
    df = pd.concat(parts, ignore_index=True)
    df = df[df["flag"]].copy()
    return df.reindex(df["diff"].abs().sort_values(ascending=False).index).reset_index(drop=True)


# ── zásobníky, LNG, bilance ─────────────────────────────────────────────

@ttl_cache(ttl=900, maxsize=1)
def _gie_daily() -> pd.DataFrame:
    from data.gie import load_gie_all
    g = load_gie_all()
    if g.empty:
        return g
    g = g.copy()
    g["day"] = pd.to_datetime(g["gasDayStart"], utc=True).dt.tz_localize(None).dt.normalize()
    for c in ("full", "gasInStorage", "injection", "withdrawal", "workingGasVolume"):
        g[c] = pd.to_numeric(g[c], errors="coerce")
    return g.drop_duplicates(["country_code", "day"], keep="last")


def storage_on(day) -> dict:
    """{země: {full, twh, inj, wd, net (vtláčení − těžba, GWh/d), day}} k dni (max 3 dny zpět)."""
    g = _gie_daily()
    if g.empty:
        return {}
    day = pd.Timestamp(day).normalize()
    g = g[(g["day"] <= day) & (g["day"] >= day - pd.Timedelta(days=3))].sort_values("day")
    out = {}
    for cc, r in g.groupby("country_code").last().iterrows():
        inj, wd = r["injection"], r["withdrawal"]
        out[cc] = {"full": r["full"], "twh": r["gasInStorage"], "inj": inj, "wd": wd,
                   "net": (inj if pd.notna(inj) else 0) - (wd if pd.notna(wd) else 0), "day": r["day"].date()}
    return out


@ttl_cache(ttl=900, maxsize=1)
def _lng_daily() -> pd.DataFrame:
    from data.lng import load_lng
    df = load_lng()
    if df.empty:
        return df
    df = df.copy()
    df["day"] = pd.to_datetime(df["gasDayStart"], utc=True).dt.tz_localize(None).dt.normalize()
    for c in ("sendOut", "inventory_gwh", "full_pct"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.drop_duplicates(["country_code", "day"], keep="last")


def lng_on(day) -> dict:
    """{země: {sendout GWh/d, inventory TWh, full %}} z ALSI."""
    df = _lng_daily()
    if df.empty:
        return {}
    df = df[df["day"] == pd.Timestamp(day).normalize()]
    return {r.country_code: {"sendout": r.sendOut, "inv_twh": (r.inventory_gwh or 0) / 1000 if pd.notna(r.inventory_gwh)
                             else None, "full": r.full_pct} for r in df.itertuples(index=False)}


def balances(day, storage: dict = None) -> dict:
    """Bilance hlásících zemí (GWh/d): vstupy z hranic, výstupy, LNG, těžba,
    zásobníky (GIE těžba − vtláčení, jinak ENTSO-G Storage), odvozená
    spotřeba = vstupy − výstupy + LNG + těžba + čistá těžba ze zásobníků,
    hlášená spotřeba = ENTSO-G exit do distribuce a ke koncovým zákazníkům."""
    df = day_flows(day)
    storage = storage_on(day) if storage is None else storage
    out = {}
    if df.empty:
        return out
    for c, g in df.groupby("country"):
        if c in NOT_REPORTING:
            continue

        def s(direction, cond):
            x = g[(g["direction"] == direction) & cond]["value"]
            return float(x.sum()) if x.notna().any() else 0.0
        border = ~g["partner"].isin(["LNG", "STOR", "PROD", "CONS", "OTHER", c])
        b_in, b_out = s("entry", border), s("exit", border)
        lng = s("entry", g["partner"] == "LNG") - s("exit", g["partner"] == "LNG")
        prod = s("entry", g["partner"] == "PROD") - s("exit", g["partner"] == "PROD")
        st = storage.get(c)
        if st is not None and pd.notna(st.get("net")):
            stor_wd, stor_src = -float(st["net"]), "GIE"
        else:
            stor_wd, stor_src = s("entry", g["partner"] == "STOR") - s("exit", g["partner"] == "STOR"), "ENTSO-G"
        cons = s("exit", g["partner"] == "CONS")
        implied = b_in - b_out + lng + prod + stor_wd
        out[c] = {"in": b_in, "out": b_out, "lng": lng, "prod": prod, "stor_wd": stor_wd, "stor_src": stor_src,
                  "implied": implied, "reported": cons if cons else None,
                  "gap": (implied - cons) if cons else None}
    return out


# ── data pro flowchart ──────────────────────────────────────────────────

def _node_of(code: str):
    """Partner → uzel flowchartu (None = nekreslí se, je jen v bilanci země)."""
    return code if code in NODES else None


def flowchart(day=None, compare: str = "d", unit: str = "gwh", rel_pct=None, abs_gwh=None) -> dict:
    """Vše, co potřebuje SVG: uzly (s bilancí a zásobníky), hrany (čistý tok
    se směrem, oba směry, změna proti srovnávacímu dni, kontrola entry/exit),
    LNG přítoky. Hodnoty v GWh/d (převod na jednotku dělá renderer přes unit)."""
    day = pd.Timestamp(day or latest_day() or date.today()).normalize()
    cmp_day = day - pd.Timedelta(days=7 if compare == "w" else 1)
    bt, bt_c = border_table(day), border_table(cmp_day)
    net, net_c = _pair_net(bt), _pair_net(bt_c)
    chk = check_borders(day, "Physical Flow", rel_pct, abs_gwh)
    chk_by_pair = {}
    for r in chk[chk["flag"]].to_dict("records"):
        chk_by_pair.setdefault(tuple(sorted((r["from"], r["to"]))), []).append(r)
    edges = []
    for (a, b), (ab, ba) in sorted(net.items()):
        na, nb = _node_of(a), _node_of(b)
        if na is None or nb is None or (NODES[na][3] == "ext" and NODES[nb][3] == "ext"):
            continue
        n = ab - ba
        src, dst = (na, nb) if n >= 0 else (nb, na)
        c_ab, c_ba = net_c.get((a, b), (None, None))
        prev = None if c_ab is None else (c_ab - c_ba) * (1 if n >= 0 else -1)
        edges.append({"from": src, "to": dst, "value": abs(n), "ab": ab, "ba": ba, "a": a, "b": b,
                      "prev": prev, "change": None if prev is None else abs(n) - prev,
                      "checks": chk_by_pair.get((a, b), [])})
    storage = storage_on(day)
    storage_c = storage_on(cmp_day)
    bal = balances(day, storage)
    lng = lng_on(day)
    lng_flows = {}
    df = day_flows(day)
    dfc = day_flows(cmp_day)
    for c in LNG_POS:
        def lv(d):
            x = d[(d["country"] == c) & (d["partner"] == "LNG")] if not d.empty else d
            if x.empty or not x["value"].notna().any():
                return None
            return float(x[x["direction"] == "entry"]["value"].sum() - x[x["direction"] == "exit"]["value"].sum())
        v = lv(df)
        if v is not None:
            pv = lv(dfc)
            lng_flows[c] = {"value": v, "prev": pv, "change": None if pv is None else v - pv, "alsi": lng.get(c)}
    nodes = {}
    for code, (name, x, y, kind) in NODES.items():
        nodes[code] = {"code": code, "name": name, "x": x, "y": y, "kind": kind,
                       "storage": storage.get(code), "storage_prev": storage_c.get(code), "balance": bal.get(code)}
    return {"day": day.date(), "cmp_day": cmp_day.date(), "compare": compare, "unit": unit,
            "nodes": nodes, "edges": edges, "lng": lng_flows, "checks": chk,
            "has_data": not bt.empty}


def flowchart_table(fc: dict) -> pd.DataFrame:
    """Data za flowchartem (export CSV/XLSX): hrany, LNG, uzly."""
    rows = []
    for e in fc["edges"]:
        rows.append({"typ": "tok", "od": e["from"], "do": e["to"], "čistý tok GWh/d": round(e["value"], 2),
                     "země A": e["a"], "země B": e["b"], "A→B GWh/d": round(e["ab"], 2), "B→A GWh/d": round(e["ba"], 2),
                     "srovnání GWh/d": None if e["prev"] is None else round(e["prev"], 2),
                     "kontrola entry/exit": "; ".join(f"{c['from']}→{c['to']} exit {c['exit']:.1f} / entry {c['entry']:.1f}"
                                                      for c in e["checks"])})
    for c, l in fc["lng"].items():
        rows.append({"typ": "LNG", "od": "LNG", "do": c, "čistý tok GWh/d": round(l["value"], 2),
                     "srovnání GWh/d": None if l["prev"] is None else round(l["prev"], 2)})
    for code, n in fc["nodes"].items():
        st, b = n["storage"] or {}, n["balance"] or {}
        rows.append({"typ": "země", "od": code, "do": "", "plnění %": st.get("full"), "zásoba TWh": st.get("twh"),
                     "vtláčení GWh/d": st.get("inj"), "těžba GWh/d": st.get("wd"),
                     "vstupy GWh/d": b.get("in"), "výstupy GWh/d": b.get("out"), "LNG GWh/d": b.get("lng"),
                     "domácí těžba GWh/d": b.get("prod"), "odvozená spotřeba GWh/d": b.get("implied"),
                     "hlášená spotřeba GWh/d": b.get("reported")})
    return pd.DataFrame(rows)


def fmt_num(v, unit="gwh", digits=None) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v) * unit_factor(unit)
    d = digits if digits is not None else (1 if unit == "mcm" else 0)
    s = f"{v:,.{d}f}".replace(",", " ").replace(".", ",")
    return s

