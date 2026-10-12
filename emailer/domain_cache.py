"""
Persistent cache for company -> (domain, pattern, pattern_source) lookups.

Stored as a JSON file at ~/LeadScout_Output/domain_cache.json so entries
survive across sessions and tool runs.  Once a company's domain and pattern
are confirmed, they never need to be re-discovered.

Thread-safe: the enrichment engine pre-warms several companies in parallel and
every writer does load -> modify -> save under one lock.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()

# LEADSCOUT_OUTPUT_DIR lets tests run against a scratch folder instead of real data
def _cache_file() -> Path:
    """Resolved on every call so tests (and a moved output folder) can redirect it."""
    return Path(os.environ.get("LEADSCOUT_OUTPUT_DIR") or (Path.home() / "LeadScout_Output")) / "domain_cache.json"

_HARVEST_TTL = 14 * 24 * 3600     # website harvest results stay fresh for 14 days


def _load() -> dict:
    try:
        if _cache_file().exists():
            return json.loads(_cache_file().read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not load domain cache: %s", e)
    return {}


def _save(data: dict):
    try:
        _cache_file().parent.mkdir(parents=True, exist_ok=True)
        tmp = _cache_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_cache_file())          # atomic: a crash never leaves a half-written cache
    except Exception as e:
        logger.warning("Could not save domain cache: %s", e)


def get(company: str) -> dict | None:
    """Return cached entry for company or None. Keys: domain, pattern, pattern_source, pattern2 (optional)."""
    with _LOCK:
        return _load().get(company.lower().strip())


def put(company: str, domain: str, pattern: str | None, pattern_source: str,
        pattern2: str | None = None):
    """Save a company's domain and pattern(s) to the cache.
    pattern2 is stored when a company is known to use two email formats simultaneously.
    """
    with _LOCK:
        data = _load()
        key = company.lower().strip()
        entry = {
            "domain":         domain,
            "pattern":        pattern,
            "pattern_source": pattern_source,
        }
        if pattern2:
            entry["pattern2"] = pattern2
        old = data.get(key, {})
        if old.get("domain") == domain:       # keep cached site harvest for the same domain
            for k in ("harvest", "harvest_ts"):
                if k in old:
                    entry[k] = old[k]
        data[key] = entry
        _save(data)
    p2_msg = f" + {pattern2}" if pattern2 else ""
    logger.info("Cached %s -> %s / %s%s (%s)", company, domain, pattern, p2_msg, pattern_source)


def all_entries() -> dict:
    with _LOCK:
        return _load()


# ── Website harvest results (published emails + names) ───────────────────────

def get_harvest(company: str) -> list[dict] | None:
    """Return cached site-harvest rows for the company, or None if missing/stale."""
    with _LOCK:
        entry = _load().get(company.lower().strip())
    if not entry or "harvest" not in entry:
        return None
    if time.time() - entry.get("harvest_ts", 0) > _HARVEST_TTL:
        return None
    return entry["harvest"]


def put_harvest(company: str, rows: list[dict]):
    with _LOCK:
        data = _load()
        entry = data.setdefault(company.lower().strip(), {})
        entry["harvest"] = rows
        entry["harvest_ts"] = int(time.time())
        _save(data)


def replace_all(new_entries: dict):
    """Save an edited table of entries (the in-app cache editor) atomically, keeping the website
    harvest data and secondary formats of entries whose domain did not change."""
    with _LOCK:
        old = _load()
        merged = {}
        for key, entry in new_entries.items():
            prev = old.get(key, {})
            entry = dict(entry)
            if prev.get("domain") == entry.get("domain"):
                for k in ("harvest", "harvest_ts", "pattern2"):
                    if k in prev and k not in entry:
                        entry[k] = prev[k]
            merged[key] = entry
        _save(merged)
