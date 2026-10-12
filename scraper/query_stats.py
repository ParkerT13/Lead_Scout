"""
Remembers which kinds of LinkedIn queries actually produce new people, so unattended runs stop
spending time on kinds that almost never pay off.

Kinds (see query_builder.build_query_plan_kinds): core, narrow, wide, city.
The 'core' kind is never skipped. Stats persist in ~/LeadScout_Output/query_stats.json and grow
across runs; pruning only starts once a kind has enough history to judge fairly.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_QUERIES = 120        # history required before a kind can be judged
MIN_YIELD = 0.03         # new people per query below which a kind is skipped
_LOCK = threading.Lock()


def _path() -> Path:
    return Path(os.environ.get("LEADSCOUT_OUTPUT_DIR") or (Path.home() / "LeadScout_Output")) / "query_stats.json"


def _read() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8")) if _path().exists() else {}
    except Exception:
        return {}


class QueryStats:
    def __init__(self):
        self.base = _read()
        self.delta: dict[str, dict[str, int]] = {}

    def _totals(self, kind: str) -> tuple[int, int]:
        b = self.base.get(kind, {})
        d = self.delta.get(kind, {})
        return b.get("q", 0) + d.get("q", 0), b.get("new", 0) + d.get("new", 0)

    def record(self, kind: str, new_people: int):
        slot = self.delta.setdefault(kind, {"q": 0, "new": 0})
        slot["q"] += 1
        slot["new"] += new_people

    def should_skip(self, kind: str) -> bool:
        if kind == "core":
            return False
        q, new = self._totals(kind)
        return q >= MIN_QUERIES and (new / q) < MIN_YIELD

    def save(self):
        """Merge this run's counts into the file (safe with several companies running at once)."""
        if not self.delta:
            return
        with _LOCK:
            disk = _read()
            for kind, d in self.delta.items():
                slot = disk.setdefault(kind, {"q": 0, "new": 0})
                slot["q"] += d["q"]
                slot["new"] += d["new"]
            try:
                _path().parent.mkdir(parents=True, exist_ok=True)
                tmp = _path().with_suffix(".tmp")
                tmp.write_text(json.dumps(disk, indent=2), encoding="utf-8")
                tmp.replace(_path())
            except Exception as e:
                logger.debug("could not save query stats: %s", e)
        self.base = _read()
        self.delta = {}
