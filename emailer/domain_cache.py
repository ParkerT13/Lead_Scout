"""
Persistent cache for company -> (domain, pattern, pattern_source) lookups.

Stored as a JSON file at ~/LeadScout_Output/domain_cache.json so entries
survive across sessions and tool runs.  Once a company's domain and pattern
are confirmed, they never need to be re-discovered.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_FILE = Path.home() / "LeadScout_Output" / "domain_cache.json"


def _load() -> dict:
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not load domain cache: %s", e)
    return {}


def _save(data: dict):
    try:
        _CACHE_FILE.parent.mkdir(exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not save domain cache: %s", e)


def get(company: str) -> dict | None:
    """Return cached entry for company or None. Keys: domain, pattern, pattern_source, pattern2 (optional)."""
    return _load().get(company.lower().strip())


def put(company: str, domain: str, pattern: str | None, pattern_source: str,
        pattern2: str | None = None):
    """Save a company's domain and pattern(s) to the cache.
    pattern2 is stored when a company is known to use two email formats simultaneously.
    """
    data = _load()
    entry = {
        "domain":         domain,
        "pattern":        pattern,
        "pattern_source": pattern_source,
    }
    if pattern2:
        entry["pattern2"] = pattern2
    data[company.lower().strip()] = entry
    _save(data)
    p2_msg = f" + {pattern2}" if pattern2 else ""
    logger.info("Cached %s -> %s / %s%s (%s)", company, domain, pattern, p2_msg, pattern_source)


def all_entries() -> dict:
    return _load()
