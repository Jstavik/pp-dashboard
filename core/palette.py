"""Jednotné barvy entit (země, zóny, zdroje) — stejná země = stejná barva
na všech stránkách. Barva patří entitě, ne pořadí série (filtr, který ubere
série, nepřebarví zbylé).

Prvních 8 slotů = validovaná kategorická paleta (CVD ΔE ≥ 8 pro sousední
páry ve světlém režimu); hlavní země CZ a okolí dostaly sloty v tomto pořadí.
Ostatní entity mají pevné barvy z rozšířené sady (bez záruky rozlišitelnosti
všech párů — v grafech s víc než ~6 sériemi se doplňuje legenda/popisky).

web/app.py aplikuje entity_color() na každou figuru (podle názvu série),
takže sjednocení platí i pro starší grafy v charts/.
"""

SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
NEUTRAL = "#52514e"

_CODES = {
    "CZ": SLOTS[0], "DE": SLOTS[1], "AT": SLOTS[2], "SK": SLOTS[3], "PL": SLOTS[4], "HU": SLOTS[5],
    "FR": SLOTS[6], "NL": SLOTS[7], "EU": NEUTRAL,
    "IT": "#8c564b", "BE": "#17a2b8", "ES": "#b8a200", "CH": "#7f7f7f", "SI": "#9467bd", "HR": "#bc5090",
    "RO": "#58508d", "BG": "#3d7a3d", "GR": "#4aa3df", "RS": "#a0522d", "PT": "#2f9e44", "UA": "#c9a227",
    "LV": "#7a5195", "LT": "#ef5675", "EE": "#00876c", "IE": "#56b870", "GB": "#5f6b73", "UK": "#5f6b73",
    "NO": "#1f3a93", "SE": "#f2b134", "FI": "#4c72b0", "DK": "#c44e52", "NORDIC": "#0d9488",
}
_ALIASES = {
    "DE_LU": "DE", "DE-LU": "DE", "IE_SEM": "IE",
    "Česko": "CZ", "ČR": "CZ", "Czechia": "CZ", "Czech Republic": "CZ",
    "Německo": "DE", "Germany": "DE", "Rakousko": "AT", "Austria": "AT", "Slovensko": "SK", "Slovakia": "SK",
    "Polsko": "PL", "Poland": "PL", "Maďarsko": "HU", "Hungary": "HU", "Francie": "FR", "France": "FR",
    "Nizozemsko": "NL", "Netherlands": "NL", "Itálie": "IT", "Italy": "IT", "Belgie": "BE", "Belgium": "BE",
    "Španělsko": "ES", "Spain": "ES", "Švýcarsko": "CH", "Switzerland": "CH", "Slovinsko": "SI", "Slovenia": "SI",
    "Chorvatsko": "HR", "Croatia": "HR", "Rumunsko": "RO", "Romania": "RO", "Bulharsko": "BG", "Bulgaria": "BG",
    "Řecko": "GR", "Greece": "GR", "Portugalsko": "PT", "Portugal": "PT", "Ukrajina": "UA", "Ukraine": "UA",
    "Lotyšsko": "LV", "Latvia": "LV", "Litva": "LT", "Lithuania": "LT", "Estonsko": "EE", "Estonia": "EE",
    "Irsko": "IE", "Ireland": "IE", "United Kingdom": "GB", "Velká Británie": "GB",
    "Norsko": "NO", "Norway": "NO", "Švédsko": "SE", "Sweden": "SE", "Finsko": "FI", "Finland": "FI",
    "Dánsko": "DK", "Denmark": "DK", "EU celkem": "EU", "EU": "EU", "Nordic": "NORDIC",
}


def entity_color(name):
    """Barva entity podle názvu série (kód, český/anglický název), jinak None."""
    if not isinstance(name, str):
        return None
    n = name.strip()
    code = _ALIASES.get(n, n)
    return _CODES.get(code)


def country_color(cc: str, default: str = NEUTRAL) -> str:
    return entity_color(cc) or default
