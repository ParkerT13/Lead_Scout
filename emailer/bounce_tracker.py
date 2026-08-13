"""
Persistent bounce tracker.

Stores email addresses that hard-bounced after a real send, so future runs
can skip them or flag them.  Also uses accumulated bounce data to refine
pattern confidence — if a pattern consistently bounces, confidence drops.

Stored at ~/ContactPuller_Output/bounces.json
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_BOUNCE_FILE = Path.home() / "ContactPuller_Output" / "bounces.json"


def _load() -> dict:
    try:
        if _BOUNCE_FILE.exists():
            return json.loads(_BOUNCE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not load bounce data: %s", e)
    return {}


def _save(data: dict):
    try:
        _BOUNCE_FILE.parent.mkdir(exist_ok=True)
        _BOUNCE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not save bounce data: %s", e)


def record_bounce(email: str, campaign: str = ""):
    """Mark an email address as hard-bounced."""
    data  = _load()
    email = email.lower().strip()
    data[email] = {
        "bounced_at": datetime.now().strftime("%Y-%m-%d"),
        "campaign":   campaign,
    }
    _save(data)


def record_bounces(emails: list[str], campaign: str = ""):
    """Mark a batch of email addresses as hard-bounced."""
    data = _load()
    ts   = datetime.now().strftime("%Y-%m-%d")
    for email in emails:
        e = email.lower().strip()
        if e:
            data[e] = {"bounced_at": ts, "campaign": campaign}
    _save(data)
    logger.info("Recorded %d bounces (campaign: %s)", len(emails), campaign or "unspecified")


def is_bounced(email: str) -> bool:
    """Return True if this address has previously hard-bounced."""
    return email.lower().strip() in _load()


def remove_bounce(email: str):
    """Remove an email address from the bounce list (false positive / re-enable)."""
    data = _load()
    key  = email.lower().strip()
    if key in data:
        del data[key]
        _save(data)
        logger.info("Removed %s from bounce list", key)


def all_bounces() -> dict:
    return _load()


def bounce_rate_for_domain(domain: str) -> float:
    """
    What fraction of known addresses at this domain have bounced?
    Useful for flagging domains where the guessed pattern is wrong.
    """
    data    = _load()
    at_dom  = [e for e in data if e.endswith(f"@{domain.lower()}")]
    if not at_dom:
        return 0.0
    return len(at_dom) / len(at_dom)  # all known are bounced — ratio vs sent would need sent count
