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
    """Return cached entry for company or None. Keys: domain, pattern, pattern_source."""
    return _load().get(company.lower().strip())


def put(company: str, domain: str, pattern: str | None, pattern_source: str):
    """Save a company's domain and pattern to the cache."""
    data = _load()
    data[company.lower().strip()] = {
        "domain":         domain,
        "pattern":        pattern,
        "pattern_source": pattern_source,
    }
    _save(data)
    logger.info("Cached %s -> %s / %s (%s)", company, domain, pattern, pattern_source)


def all_entries() -> dict:
    return _load()
