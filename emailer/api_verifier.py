"""
API-based email verification — fallback when port 25 is blocked.

Uses Reoon Email Verifier:
  Sign up at reoon.com/email-verifier
  ~$0.0012/email ($29.66 for 25,000 credits, never expire)
  Enter your API key in Settings -> Reoon API Key

Reoon status values:
  valid       -> verified
  invalid     -> bounced
  disposable  -> bounced
  role_account-> bounced  (info@, sales@ etc — skip in a sales context)
  catch_all   -> catch-all-risky (caller upgrades to confirmed if pattern reliable)
  spamtrap    -> bounced
  unknown     -> unknown
"""

import logging
import requests

logger = logging.getLogger(__name__)

_REOON_ENDPOINT = "https://emailverifier.reoon.com/api/v1/verify"
_TIMEOUT = 15


def verify_via_api(email: str, api_key: str) -> dict:
    """
    Verify a single email via Reoon API.
    Returns dict with 'status' matching internal Lead Scout status strings.
    """
    if not api_key or not api_key.strip():
        return {"status": "unknown", "detail": "No API key configured"}

    try:
        r = requests.get(
            _REOON_ENDPOINT,
            params={"email": email, "key": api_key.strip(), "mode": "power"},
            timeout=_TIMEOUT,
        )

        if r.status_code == 401:
            logger.warning("Reoon: invalid API key")
            return {"status": "unknown", "detail": "Invalid API key"}

        if r.status_code == 429:
            logger.warning("Reoon: rate limited")
            return {"status": "unknown", "detail": "Rate limited — slow down"}

        if r.status_code != 200:
            logger.warning("Reoon returned HTTP %s for %s", r.status_code, email)
            return {"status": "unknown", "detail": f"HTTP {r.status_code}"}

        data = r.json()
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
        logger.warning("Reoon timeout for %s", email)
        return {"status": "unknown", "detail": "API timeout"}
    except Exception as e:
        logger.warning("Reoon error for %s: %s", email, e)
        return {"status": "unknown", "detail": str(e)[:60]}
