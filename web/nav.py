"""Hlavní navigace po skupinách (Elektřina / Plyn / Trh EU / Nordic / Počasí /
Průzkumník / Reporty). Položka = (klíč aktivní stránky, popisek, URL)."""

NAV = [
    ("prehled", "Přehled", "📰", [("prehled", "Přehled dne", "/")]),
    ("elektrina", "Elektřina", "⚡", [("elektrina", "Elektřina ČR", "/elektrina"),
                                    ("odstavky-ceps", "Odstávky ČEPS", "/odstavky-ceps"),
                                    ("energy-charts", "Energy-Charts", "/energy-charts")]),
    ("plyn", "Plyn", "🔵", [("plyn-analyza", "Analýza toků plynu", "/plyn/analyza"),
                         ("plyn", "Plyn", "/plyn")]),
    ("trh", "Trh EU", "💶", [("trh-eu", "Ceny, spotřeba, OZE, toky", "/trh-eu"),
                            ("cot", "CoT (pozice ICE)", "/cot")]),
    ("nordic", "Nordic", "🌊", [("nordic", "Nordic", "/nordic")]),
    ("pocasi", "Počasí", "🌦", [("pocasi", "Počasí", "/pocasi")]),
    ("pruzkumnik", "Průzkumník", "🔎", [("pruzkumnik", "Průzkumník", "/pruzkumnik")]),
    ("reporty", "Reporty", "📋", [("reporty", "Denní report", "/reporty?tab=denni"),
                                 ("reporty", "Týdenní report", "/reporty?tab=tydenni"),
                                 ("report", "Report plynu (záložky)", "/report")]),
]


def active_group(page_key: str):
    for gkey, _t, _i, items in NAV:
        if any(k == page_key for k, _l, _u in items):
            return gkey
    return None
