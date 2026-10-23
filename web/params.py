"""Pohodlné čtení query parametrů záložky (stejné pro fragment i graf)."""
from datetime import date
from urllib.parse import urlencode

import pandas as pd


class Params:
    def __init__(self, multi_items):
        self._items = [(k, v) for k, v in multi_items if k not in ("tab", "_")]

    @classmethod
    def from_request(cls, request):
        return cls(request.query_params.multi_items())

    def has(self, name: str) -> bool:
        return any(k == name for k, _ in self._items)

    def str(self, name: str, default: str = None, choices=None) -> str:
        for k, v in self._items:
            if k == name and v != "":
                if choices is not None and v not in choices:
                    return default
                return v
        return default

    def list(self, name: str, default=None, choices=None) -> list:
        vals = [v for k, v in self._items if k == name and v != ""]
        if not self.has(name) and not self.has(f"{name}__set"):
            return list(default or [])
        if choices is not None:
            allowed = set(map(str, choices))
            vals = [v for v in vals if v in allowed]
        return vals

    def int(self, name: str, default: int = None, lo: int = None, hi: int = None) -> int:
        try:
            v = int(float(self.str(name)))
        except (TypeError, ValueError):
            return default
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    def float(self, name: str, default: float = None, lo: float = None, hi: float = None) -> float:
        try:
            v = float(self.str(name))
        except (TypeError, ValueError):
            return default
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    def bool(self, name: str, default: bool = False) -> bool:
        if not self.has(name) and not self.has(f"{name}__set"):
            return default
        return self.str(name) in ("1", "on", "true", "yes")

    def date(self, name: str, default: date = None) -> date:
        v = self.str(name)
        if not v:
            return default
        try:
            return pd.Timestamp(v).date()
        except (ValueError, TypeError):
            return default

    def query(self, **override) -> str:
        """Query string se změněnými hodnotami (pro odkazy/tlačítka)."""
        items = [(k, v) for k, v in self._items if k not in override]
        for k, v in override.items():
            if v is None:
                continue
            if isinstance(v, (list, tuple)):
                items.extend((k, str(x)) for x in v)
            else:
                items.append((k, str(v)))
        return urlencode(items)

    def __str__(self):
        return urlencode(self._items)
