"""
API-based email verification — fallback when port 25 is blocked.

Uses MillionVerifier (recommended):
  Sign up at millionverifier.com — pay-as-you-go, ~$0.001/email ($10 = 10k checks)
  Enter your API key in Settings -> MillionVerifier API Key

Result codes returned by MillionVerifier:
  1 = ok         -> verified
  2 = catch-all  -> catch-all-risky (caller upgrades to confirmed if pattern is reliable)
  3 = unknown    -> unknown
  4 = invalid    -> bounced
  5 = error      -> unknown
  7 = disposable -> bounced
  8 = role       -> bounced (info@, sales@, etc. — not a personal contact)
"""

import logging
import requests

logger = logging.getLogger(__name__)

_MV_ENDPOINT = "https://api.millionverifier.com/api/v3/"
_TIMEOUT = 15


def verify_via_api(email: str, api_key: str) -> dict:
    """
    Verify a single email via MillionVerifier API.
    Returns dict with 'status' matching internal Lead Scout status strings.
    """
    if not api_key or not api_key.strip():
        return {"status": "unknown", "detail": "No API key configured"}

    try:
        r = requests.get(
            _MV_ENDPOINT,
            params={"api": api_key.strip(), "email": email},
            timeout=_TIMEOUT,
        )

        if r.status_code == 401:
            logger.warning("MillionVerifier: invalid API key")
            return {"status": "unknown", "detail": "Invalid API key"}

        if r.status_code != 200:
            logger.warning("MillionVerifier returned HTTP %s for %s", r.status_code, email)
            return {"status": "unknown", "detail": f"HTTP {r.status_code}"}

        data = r.json()
        code = data.get("resultcode", 0)
        quality = data.get("quality", "")

        # MillionVerifier returns 'free' when credits are exhausted (result unreliable)
        if data.get("free") is True:
            logger.warning("MillionVerifier credits exhausted — result for %s unreliable", email)
            return {"status": "unknown", "detail": "API credits exhausted"}

        if code == 1:
            return {"status": "verified", "detail": f"API: ok ({quality})"}
        elif code == 2:
            return {"status": "catch-all-risky", "detail": "API: catch-all"}
        elif code == 4:
            return {"status": "bounced", "detail": "API: invalid"}
        elif code == 7:
            return {"status": "bounced", "detail": "API: disposable domain"}
        elif code == 8:
            return {"status": "bounced", "detail": "API: role address"}
        else:
            return {"status": "unknown", "detail": f"API: resultcode {code}"}

    except requests.Timeout:
        logger.warning("MillionVerifier timeout for %s", email)
        return {"status": "unknown", "detail": "API timeout"}
    except Exception as e:
        logger.warning("MillionVerifier error for %s: %s", email, e)
        return {"status": "unknown", "detail": str(e)[:60]}
