"""
API-based email verification — fallback when port 25 is blocked.

Priority order:
  1. NeverBounce  (use existing credits first)
  2. Reoon        (automatic fallback when NeverBounce credits exhausted)

Once NeverBounce credits run out mid-session, the module switches to Reoon
automatically for all remaining emails — no restart needed.

Keys are loaded from credentials.json (shared by Parker) or Settings.
"""

import logging
import requests

logger = logging.getLogger(__name__)

_NB_ENDPOINT    = "https://api.neverbounce.com/v4/single/check"
_REOON_ENDPOINT = "https://emailverifier.reoon.com/api/v1/verify"
_TIMEOUT        = 15

# Module-level flag — once NB runs dry, skip it for the rest of the session
_nb_credits_ok = True


def reset_session():
    """Call at the start of each enrichment run to re-check NeverBounce credits."""
    global _nb_credits_ok
    _nb_credits_ok = True


def verify_via_api(email: str, nb_key: str = "", reoon_key: str = "") -> dict:
    """
    Verify a single email using NeverBounce first, Reoon as fallback.
    Returns dict with 'status' matching internal Lead Scout status strings:
      verified | catch-all-risky | bounced | unknown | error
    """
    global _nb_credits_ok

    # ── NeverBounce (use first while credits remain) ──────────────────────────
    if nb_key and _nb_credits_ok:
        result = _verify_neverbounce(email, nb_key)
        if result.get("_credits_exhausted"):
            _nb_credits_ok = False
            logger.info("NeverBounce credits exhausted — switching to Reoon for remainder of session")
        else:
            return result

    # ── Reoon (fallback) ──────────────────────────────────────────────────────
    if reoon_key:
        return _verify_reoon(email, reoon_key)

    return {"status": "unknown", "detail": "No API key configured or credits available"}


# ── NeverBounce ───────────────────────────────────────────────────────────────

def _verify_neverbounce(email: str, api_key: str) -> dict:
    try:
        r = requests.get(
            _NB_ENDPOINT,
            params={"key": api_key.strip(), "email": email},
            timeout=_TIMEOUT,
        )

        if r.status_code == 401:
            logger.warning("NeverBounce: invalid API key")
            return {"status": "unknown", "detail": "NeverBounce: invalid API key"}

        data = r.json()
        nb_status = data.get("status", "")
        result    = data.get("result", "")
        message   = data.get("message", "").lower()

        # Credit exhaustion comes back as general_failure or auth_failure
        if nb_status in ("general_failure", "auth_failure") or "credit" in message or "insufficient" in message:
            return {"status": "unknown", "detail": "NeverBounce: credits exhausted", "_credits_exhausted": True}

        if nb_status != "success":
            return {"status": "unknown", "detail": f"NeverBounce: {nb_status}"}

        if result == "valid":
            return {"status": "verified", "detail": "NeverBounce: valid"}
        elif result == "catchall":
            return {"status": "catch-all-risky", "detail": "NeverBounce: catch-all"}
        elif result in ("invalid", "disposable"):
            return {"status": "bounced", "detail": f"NeverBounce: {result}"}
        else:
            return {"status": "unknown", "detail": f"NeverBounce: {result}"}

    except requests.Timeout:
        return {"status": "unknown", "detail": "NeverBounce: timeout"}
    except Exception as e:
        logger.warning("NeverBounce error for %s: %s", email, e)
        return {"status": "unknown", "detail": str(e)[:60]}


# ── Reoon ─────────────────────────────────────────────────────────────────────

def _verify_reoon(email: str, api_key: str) -> dict:
    try:
        r = requests.get(
            _REOON_ENDPOINT,
            params={"email": email, "key": api_key.strip(), "mode": "power"},
            timeout=_TIMEOUT,
        )

        if r.status_code == 401:
            logger.warning("Reoon: invalid API key")
            return {"status": "unknown", "detail": "Reoon: invalid API key"}

        if r.status_code == 429:
            return {"status": "unknown", "detail": "Reoon: rate limited"}

        if r.status_code != 200:
            return {"status": "unknown", "detail": f"Reoon: HTTP {r.status_code}"}

        data   = r.json()
        status = data.get("status", "unknown").lower()

        if status == "valid":
            return {"status": "verified", "detail": "Reoon: valid"}
        elif status == "catch_all":
            return {"status": "catch-all-risky", "detail": "Reoon: catch-all"}
        elif status in ("invalid", "disposable", "role_account", "spamtrap"):
            return {"status": "bounced", "detail": f"Reoon: {status}"}
        else:
            return {"status": "unknown", "detail": f"Reoon: {status}"}

    except requests.Timeout:
        return {"status": "unknown", "detail": "Reoon: timeout"}
    except Exception as e:
        logger.warning("Reoon error for %s: %s", email, e)
        return {"status": "unknown", "detail": str(e)[:60]}
