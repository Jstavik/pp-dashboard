"""HTTP s ohledem na limity API: Retry-After, exponenciální backoff, pauzy.

- retry_after_s(resp): sekundy z hlavičky Retry-After (číslo i HTTP datum),
  případně z textu typu „Try again in 42 seconds“ (Energi Data Service).
- get_with_backoff(...): GET, při 429/502/503/504 čeká Retry-After (nebo
  backoff base·2^n, strop cap) a zkusí znovu; po vyčerpání pokusů vyhodí
  RateLimited (429) nebo requests.HTTPError — volající rozhodne, jestli to
  shodí jen jeden dataset (typicky ano), nebo celý běh (ne).
- Pacer: minimální rozestup mezi dotazy na stejný klíč (dataset, host).
"""
import email.utils
import logging
import re
import time

import requests

log = logging.getLogger("ppdash.update")

RETRY_STATUS = (429, 502, 503, 504)
_SECONDS_RE = re.compile(r"(?:try again|retry)[^0-9]{0,20}(\d+(?:\.\d+)?)\s*(?:s\b|sec)", re.I)


class RateLimited(requests.HTTPError):
    """429 i po opakování — dataset počká na další běh (není to chyba dat)."""

    def __init__(self, msg, wait_s=None):
        super().__init__(msg)
        self.wait_s = wait_s


def retry_after_s(resp):
    h = getattr(resp, "headers", None) or {}
    v = None
    for k in ("Retry-After", "retry-after", "X-RateLimit-Reset-After"):
        if k in h:
            v = h[k]
            break
    if v is not None:
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            try:
                dt = email.utils.parsedate_to_datetime(str(v))
                return max(0.0, dt.timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    text = ""
    try:
        text = resp.text or ""
    except Exception:
        pass
    m = _SECONDS_RE.search(text[:500])
    return float(m.group(1)) if m else None


def get_with_backoff(session, url, *, params=None, headers=None, timeout=90, tries=5,
                     base_s=5.0, cap_s=300.0, max_wait_s=None, sleep=None, label=""):
    """GET s opakováním. max_wait_s = když server chce čekat déle, nečeká se
    a vyhodí se RateLimited hned (např. denní kvóta)."""
    sleep = sleep or time.sleep
    last = None
    for attempt in range(tries):
        r = session.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code not in RETRY_STATUS:
            return r
        last = r
        wait = retry_after_s(r)
        backoff = min(cap_s, base_s * 2 ** attempt)
        wait = backoff if wait is None else min(cap_s, max(wait, 1.0))
        if max_wait_s is not None and wait > max_wait_s:
            raise RateLimited(f"{label} HTTP {r.status_code}, server chce čekat {wait:.0f} s", wait)
        if attempt == tries - 1:
            break
        log.info("%s HTTP %s — čekám %.0f s (pokus %d/%d)", label, r.status_code, wait, attempt + 1, tries)
        sleep(wait)
    if last.status_code == 429:
        raise RateLimited(f"{label} HTTP 429 i po {tries} pokusech", retry_after_s(last))
    raise requests.HTTPError(f"{label} HTTP {last.status_code} i po {tries} pokusech")


class Pacer:
    """Minimální rozestup mezi dotazy na stejný klíč (monotonic čas)."""

    def __init__(self, interval_s: float, sleep=None, clock=None):
        self.interval = float(interval_s)
        self._next = {}
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic

    def ready_in(self, key) -> float:
        return max(0.0, self._next.get(key, 0.0) - self._clock())

    def wait(self, key):
        d = self.ready_in(key)
        if d > 0:
            self._sleep(d)
        self._next[key] = self._clock() + self.interval

    def push(self, key, seconds: float):
        """Posunout další dotaz (např. po Retry-After)."""
        self._next[key] = max(self._next.get(key, 0.0), self._clock() + seconds)
