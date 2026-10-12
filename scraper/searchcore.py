"""
Shared search layer: every web/LinkedIn search goes through here.

* Result cache (sqlite, 14 days): re-runs, resumed runs and the weekly schedule cost no requests.
  Empty results are never cached (they may be a rate-limit, not "nothing exists").
* One global rate limiter, so several companies can be searched in parallel without the combined
  request rate getting the machine blocked.
* Engine circuit breakers: an engine that keeps coming back empty (Bing is CAPTCHA-blocked) is
  skipped for a while instead of being retried, with its own delay, on every single query.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

CACHE_TTL = 14 * 24 * 3600


def _dir() -> Path:
    return Path(os.environ.get("LEADSCOUT_OUTPUT_DIR") or (Path.home() / "LeadScout_Output"))


# ── Result cache ────────────────────────────────────────────────────────────

class SearchCache:
    def __init__(self, path: Path | None = None, ttl: int = CACHE_TTL):
        self._path = path
        self._ttl = ttl
        self._lock = threading.Lock()
        self._ready_for: Path | None = None

    def _db(self) -> sqlite3.Connection:
        path = self._path or (_dir() / "search_cache.sqlite")
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path), timeout=30)
        if self._ready_for != path:
            con.execute("CREATE TABLE IF NOT EXISTS results (q TEXT PRIMARY KEY, ts REAL, body TEXT)")
            con.commit()
            self._ready_for = path
        return con

    def get(self, query: str) -> list[dict] | None:
        try:
            with self._lock, self._db() as con:
                row = con.execute("SELECT ts, body FROM results WHERE q = ?", (query,)).fetchone()
            if row and time.time() - row[0] <= self._ttl:
                return json.loads(row[1])
        except Exception as e:
            logger.debug("search cache read failed: %s", e)
        return None

    def put(self, query: str, results: list[dict]):
        if not results:
            return
        try:
            with self._lock, self._db() as con:
                con.execute("INSERT OR REPLACE INTO results VALUES (?, ?, ?)",
                            (query, time.time(), json.dumps(results)))
        except Exception as e:
            logger.debug("search cache write failed: %s", e)


# ── Global rate limiter ─────────────────────────────────────────────────────

class RateLimiter:
    """Spaces the START of requests at least `min_interval` seconds apart across all threads."""

    def __init__(self, min_interval: float = 3.5, jitter: float = 0.8):
        self.min_interval = min_interval
        self.jitter = jitter
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.min_interval * random.uniform(1.0, 1.0 + self.jitter)
        delay = start - now
        if delay > 0:
            time.sleep(delay)


# ── Engine circuit breakers ─────────────────────────────────────────────────

class EngineHealth:
    """Skip an engine for `cooldown` seconds after `threshold` consecutive empty/failed results."""

    DEFAULTS = {"DDG": (12, 300), "Bing": (3, 1800), "Google": (3, 900)}

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def available(self, name: str) -> bool:
        with self._lock:
            return self._clock() >= self._open_until.get(name, 0.0)

    def ok(self, name: str):
        with self._lock:
            self._fails[name] = 0

    def fail(self, name: str):
        threshold, cooldown = self.DEFAULTS.get(name, (5, 600))
        with self._lock:
            n = self._fails.get(name, 0) + 1
            self._fails[name] = n
            if n >= threshold:
                self._open_until[name] = self._clock() + cooldown
                self._fails[name] = 0
                logger.info("Search engine %s paused for %d min after %d empty results in a row",
                            name, cooldown // 60, n)


# ── Module singletons + the one function the rest of the app calls ──────────

CACHE = SearchCache()
LIMITER = RateLimiter()
HEALTH = EngineHealth()


def configure(min_interval: float | None = None):
    if min_interval is not None:
        LIMITER.min_interval = float(min_interval)


def search(query: str, engines: list[tuple[str, Callable]], stop: Callable[[], bool] = lambda: False,
           use_cache: bool = True) -> list[dict]:
    """
    Run `query` through `engines` (ordered [(name, fn)]) with caching, rate limiting and
    circuit breakers. Each result is tagged with its engine ('_engine') or 'cache'.
    """
    if use_cache:
        cached = CACHE.get(query)
        if cached:
            return [{**r, "_engine": r.get("_engine", "cache")} for r in cached]
    for name, fn in engines:
        if stop():
            return []
        if not HEALTH.available(name):
            continue
        LIMITER.wait()
        try:
            results = fn(query)
        except Exception as e:
            logger.warning("%s raised: %s", name, e)
            results = []
        if results:
            HEALTH.ok(name)
            tagged = [{**r, "_engine": name} for r in results]
            CACHE.put(query, tagged)
            return tagged
        HEALTH.fail(name)
    return []
