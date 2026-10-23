"""Registr stránek, záložek a grafů.

Záložka = funkce vracející kontext pro šablonu templates/tabs/<page>/<tab>.html
(ovládací prvky + tabulky + KPI). Grafy se načítají zvlášť přes
/api/chart/<page>/<tab>/<id> se stejnými query parametry jako záložka —
proto tabulky/KPI i grafy staví na společných "resolve" funkcích v
web/tabs/*.py, které jsou levné díky cache v data/.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class TabSpec:
    page: str
    key: str
    title: str
    render: Callable
    refresh: Optional[int] = None  # auto-refresh v sekundách (ČEPS 60 s)


@dataclass
class PageSpec:
    key: str
    title: str
    icon: str
    tabs: list = field(default_factory=list)  # pořadí klíčů záložek


@dataclass
class ChartSpec:
    fn: Callable
    config: dict


PAGES: dict = {}
TABS: dict = {}
CHARTS: dict = {}
CSVS: dict = {}


def page(key: str, title: str, icon: str) -> None:
    PAGES.setdefault(key, PageSpec(key, title, icon))


def tab(page_key: str, key: str, title: str, refresh: int = None):
    def deco(fn):
        TABS[(page_key, key)] = TabSpec(page_key, key, title, fn, refresh)
        tabs = PAGES[page_key].tabs
        if key not in tabs:
            tabs.append(key)
        return fn
    return deco


def chart(page_key: str, tab_key: str, chart_id: str, **config):
    """config = Plotly config pro klienta (displayModeBar, scrollZoom, ...)."""
    def deco(fn):
        CHARTS[(page_key, tab_key, chart_id)] = ChartSpec(fn, config)
        return fn
    return deco


def csv(name: str):
    """CSV export: fn(params) -> (DataFrame, filename)."""
    def deco(fn):
        CSVS[name] = fn
        return fn
    return deco
