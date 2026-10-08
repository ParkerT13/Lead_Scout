"""
DNS / port-25 helpers. (Actual SMTP address verification lives in email_verifier.py;
the engine in engine.py decides when to use it.)
"""

import socket
import logging
from functools import lru_cache

import dns.resolver
import dns.exception

logger = logging.getLogger(__name__)


@lru_cache(maxsize=512)
def get_mx(domain: str) -> str | None:
    """Best (lowest-preference) MX host for a domain, or None. Cached per session."""
    try:
        records = dns.resolver.resolve(domain, "MX", lifetime=8)
        return str(sorted(records, key=lambda r: r.preference)[0].exchange).rstrip(".")
    except Exception as e:
        logger.debug("MX lookup failed for %s: %s", domain, e)
        return None


def port25_available() -> bool:
    """Quick check whether outbound port 25 is accessible."""
    try:
        socket.create_connection(("aspmx.l.google.com", 25), timeout=6).close()
        return True
    except Exception:
        return False
