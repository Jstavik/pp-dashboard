"""Projede AppTest harness (scripts/apptest_nomination_harness.py) přes
všechny kombinace země x bod x indikátor (cascade filtr — bod se mění
podle vybrané země) a ověří 0 výjimek. Použití:
    python scripts/run_apptest_nomination.py
"""
import sys
sys.path.insert(0, ".")

from streamlit.testing.v1 import AppTest
from data.entsog_operational import load_cz_operational, country_from_operator_key

df = load_cz_operational()
df["country"] = df["operatorKey"].apply(country_from_operator_key)
df_valid = df[df["pointLabel"].notna() & (df["country"] != "??")]

countries = sorted(df_valid["country"].unique())
indicators = sorted(df["indicator"].dropna().unique())
points_by_country = {
    c: sorted(df_valid.loc[df_valid["country"] == c, "pointLabel"].unique())
    for c in countries
}
total = sum(len(pts) for pts in points_by_country.values()) * len(indicators)
print(f"{len(countries)} zemí, {sum(len(p) for p in points_by_country.values())} bodů celkem "
      f"x {len(indicators)} indikátorů = {total} kombinací")

# AppTest instance se periodicky zahazuje a zakládá znovu — dlouhý běh
# přes jednu instanci hromadí paměť napříč reruny (streamlit.testing.v1
# neuvolňuje starý DeltaGenerator strom/session state), ověřeno naživo:
# ArrayMemoryError na df.copy() po ~200 opakovaných .run() voláních
# v jednom procesu. Nejde o bug appky (stejná operace v reálném
# interaktivním prohlížeči proběhne bez problému) — jen limitace
# testovacího harness při tisících reranů v jednom procesu.
RESTART_EVERY = 80


def new_at():
    a = AppTest.from_file("apptest_nomination_harness.py")
    a.run(timeout=60)
    if a.exception:
        print("RUN PO RESTARTU SELHAL:", list(a.exception))
        sys.exit(1)
    return a


at = new_at()

failures = []
n = 0
for country in countries:
    at.selectbox(key="nom_country").select(country)
    at.run(timeout=60)
    if at.exception:
        failures.append((country, None, None, str(list(at.exception))))
        print(f"[země {country}] přepnutí selhalo — {list(at.exception)}")
        continue

    point_key = f"nom_point__{country}"
    for point in points_by_country[country]:
        for ind in indicators:
            n += 1
            if n % RESTART_EVERY == 0:
                at = new_at()
                at.selectbox(key="nom_country").select(country)
                at.run(timeout=60)
            at.selectbox(key=point_key).select(point)
            at.selectbox(key="nom_indicator").select(ind)
            at.run(timeout=60)
            ok = not at.exception
            if not ok:
                failures.append((country, point, ind, str(list(at.exception))))
                print(f"[{n}/{total}] {country} / {point!r} / {ind}: FAIL — {list(at.exception)}")
            elif n % 50 == 0 or n == total:
                print(f"[{n}/{total}] {country} / {point!r} / {ind}: OK (poslední hlášený)")

print(f"\nHotovo: {n} kombinací, {len(failures)} selhání")
if failures:
    for f in failures:
        print(" -", f)
    sys.exit(1)
