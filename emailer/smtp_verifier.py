"""
DNS / port-25 helpers. (Actual SMTP address verification lives in email_verifier.py;
the engine in engine.py decides when to use it.)
"""

import socket
import logging
import threading

import dns.resolver
import dns.exception

logger = logging.getLogger(__name__)

_MX_CACHE: dict[str, str | None] = {}
_LOCK = threading.Lock()


def get_mx(domain: str) -> str | None:
    """
    Best (lowest-preference) MX host for a domain, or None.
    Only DEFINITIVE answers are cached (record found / domain or record does not exist).
    Timeouts and resolver failures are retried once and never cached, so a brief network
    outage cannot turn every later lookup into "no mail server" for the whole session.
    """
    domain = domain.lower().strip(".")
    with _LOCK:
        if domain in _MX_CACHE:
            return _MX_CACHE[domain]
    result, definitive = None, False
    for _ in range(2):
        try:
            records = dns.resolver.resolve(domain, "MX", lifetime=8)
            result = str(sorted(records, key=lambda r: r.preference)[0].exchange).rstrip(".")
            definitive = True
            break
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            result, definitive = None, True
            break
        except Exception as e:                      # timeout, SERVFAIL, no network...
            logger.debug("MX lookup failed for %s: %s", domain, e)
    if definitive:
        with _LOCK:
            _MX_CACHE[domain] = result
    return result


def network_up() -> bool:
    """True if we can reach the internet at all (distinguishes 'no such domain' from 'offline')."""
    for host, port in (("8.8.8.8", 53), ("1.1.1.1", 53), ("www.google.com", 443)):
        try:
            socket.create_connection((host, port), timeout=4).close()
            return True
        except Exception:
            continue
    return False


def port25_available() -> bool:
    """Quick check whether outbound port 25 is accessible."""
    try:
        socket.create_connection(("aspmx.l.google.com", 25), timeout=6).close()
        return True
    except Exception:
        return False
