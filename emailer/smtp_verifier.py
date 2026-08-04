"""
SMTP email verification — no API needed.

Flow per domain:
  1. MX record lookup  (dnspython)
  2. Catch-all probe   (SMTP RCPT TO with a fake address)
  3. Per-address check (SMTP RCPT TO with real candidate)

Statuses returned:
  'verified'   — server returned 250 for this specific address
  'catch-all'  — domain accepts everything (can't distinguish real from fake)
  'invalid'    — server explicitly rejected this address (5xx)
  'error'      — could not connect, timeout, or port 25 blocked

Note: Many corporate mail servers block port 25 inbound from
residential/cloud IPs (anti-spam). If you see consistent 'error'
results, port 25 may be blocked by your ISP — a VPN with a clean
IP often resolves this.
"""

import smtplib
import socket
import logging
from functools import lru_cache

import dns.resolver
import dns.exception

try:
    from disposable_email_domains import blocklist as _DISPOSABLE_DOMAINS
except ImportError:
    _DISPOSABLE_DOMAINS = set()
    logging.getLogger(__name__).warning("disposable-email-domains not installed — skipping disposable check.")

logger = logging.getLogger(__name__)

_TIMEOUT       = 12          # seconds per SMTP operation
_PROBE_FROM    = "probe@contactpuller.test"
_PROBE_EHLO    = "contactpuller.test"
_FAKE_LOCAL    = "zzzfake99nobody"   # used for catch-all detection

# Cache MX lookups so we hit DNS once per domain per session
@lru_cache(maxsize=512)
def get_mx(domain: str) -> str | None:
    try:
        records = dns.resolver.resolve(domain, "MX", lifetime=8)
        best = sorted(records, key=lambda r: r.preference)[0]
        mx = str(best.exchange).rstrip(".")
        logger.debug(f"MX for {domain}: {mx}")
        return mx
    except (dns.exception.DNSException, Exception) as e:
        logger.debug(f"MX lookup failed for {domain}: {e}")
        return None


# Cache catch-all results per domain
@lru_cache(maxsize=512)
def is_catch_all(domain: str) -> bool:
    mx = get_mx(domain)
    if not mx:
        return False
    fake = f"{_FAKE_LOCAL}@{domain}"
    status, _ = _smtp_check(mx, fake)
    result = status == "verified"
    logger.debug(f"Catch-all {domain}: {result}")
    return result


def verify_email(email: str) -> tuple[str, str]:
    """
    Verify a single email address.
    Returns (status, detail).
    """
    if "@" not in email:
        return "error", "Invalid format"

    domain = email.split("@")[1].lower()

    # Fast pre-check: disposable/throwaway domain
    if domain in _DISPOSABLE_DOMAINS:
        return "invalid", f"{domain} is a disposable email provider"

    mx = get_mx(domain)
    if not mx:
        return "error", f"No MX record for {domain}"

    if is_catch_all(domain):
        return "catch-all", f"{domain} accepts all addresses — email likely valid but unverifiable"

    return _smtp_check(mx, email)


def verify_candidates(candidates: list[str]) -> tuple[str, str]:
    """
    Try each candidate in order.
    Returns the first verified email + status, or the last error.
    Stops early on the first 'verified' result.
    """
    if not candidates:
        return "", "no candidates"

    domain = candidates[0].split("@")[1].lower()
    mx = get_mx(domain)
    if not mx:
        return "", f"No MX record for {domain}"

    if is_catch_all(domain):
        # Can't verify — return first candidate as best guess
        return candidates[0], "catch-all"

    last_status, last_detail = "error", "no result"
    for email in candidates:
        status, detail = _smtp_check(mx, email)
        if status == "verified":
            return email, "verified"
        if status == "invalid":
            last_status, last_detail = status, detail
            continue
        # error — keep trying next candidate
        last_status, last_detail = status, detail

    return "", f"{last_status}: {last_detail}"


def port25_available() -> bool:
    """Quick check whether outbound port 25 is accessible."""
    try:
        s = socket.create_connection(("aspmx.l.google.com", 25), timeout=6)
        s.close()
        return True
    except Exception:
        return False


# ── Internal SMTP helper ──────────────────────────────────────────────────────

def _smtp_check(mx: str, email: str) -> tuple[str, str]:
    try:
        with smtplib.SMTP(timeout=_TIMEOUT) as smtp:
            smtp.connect(mx, 25)
            smtp.ehlo(_PROBE_EHLO)
            smtp.mail(_PROBE_FROM)
            code, raw = smtp.rcpt(email)
            msg = raw.decode(errors="replace")[:80] if isinstance(raw, bytes) else str(raw)[:80]

            if code == 250:
                return "verified", f"SMTP 250"
            elif code in (550, 551, 552, 553, 554, 450, 451, 452):
                return "invalid", f"SMTP {code}: {msg}"
            else:
                return "error", f"SMTP {code}: {msg}"

    except smtplib.SMTPConnectError as e:
        return "error", f"Connect failed: {e}"
    except smtplib.SMTPServerDisconnected:
        return "error", "Server disconnected"
    except socket.timeout:
        return "error", "Connection timed out"
    except ConnectionRefusedError:
        return "error", "Port 25 refused — may be blocked by ISP"
    except OSError as e:
        return "error", f"Network error: {e}"
    except Exception as e:
        return "error", str(e)[:80]
