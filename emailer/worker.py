"""
QThread worker for email enrichment.

Full pipeline per company:
  1. Find domain
  2. Detect email pattern (scrape + DDG + cache)
  3. SMTP format probe — try all variations on one test contact to confirm
     the actual working pattern before generating for everyone
  4. Generate + SMTP-verify all contacts using confirmed pattern
  5. Emit each result; caller decides which to keep

Catch-all policy:
  catch-all + pattern scraped from website  -> 'catch-all-confirmed' (safer to send)
  catch-all + pattern from DDG or guessed   -> 'catch-all-risky'     (high bounce risk)
  catch-all + pattern smtp-verified         -> 'catch-all-confirmed' (probe confirmed pattern)
"""

import time
import random
import logging
from collections import defaultdict

from PySide6.QtCore import QThread, Signal

from emailer.domain_finder import find_domain
from emailer.pattern_detector import detect_pattern, get_kb_pattern2
from emailer.generator import generate_candidates
from emailer.smtp_verifier import get_mx, port25_available
from emailer.email_verifier import verify_email as _verify_email, get_mx_records, check_catch_all
from emailer.domain_cache import get as cache_get, put as cache_put
from emailer.bounce_tracker import is_bounced
from emailer.api_verifier import verify_via_api, reset_session as _api_reset

logger = logging.getLogger(__name__)

_PROBE_DELAY   = 0.8              # seconds between format probe attempts
_CONTACT_DELAY = (1.5, 3.0)      # seconds between per-contact SMTP checks
_COMPANY_DELAY = (5.0, 10.0)     # seconds between companies

# Statuses that mean the email is genuinely undeliverable — drop these in pipeline mode
DROP_STATUSES = {
    "bounced", "no domain", "no MX", "no candidates",
    "error", "invalid_syntax", "disposable",
}


class EmailWorker(QThread):
    contact_enriched = Signal(dict)          # contact dict with email fields added
    company_started  = Signal(str, int)      # company, contact_count
    company_status   = Signal(str, str)      # company, status message
    company_done     = Signal(str, int, int) # company, verified, total
    status_update    = Signal(str)
    port25_blocked   = Signal()
    all_done         = Signal()

    def __init__(self, contacts: list[dict], nb_api_key: str = "", reoon_api_key: str = ""):
        super().__init__()
        self._contacts     = contacts
        self._stop         = False
        self._port25_ok    = True
        self._nb_api_key   = nb_api_key.strip()
        self._reoon_api_key = reoon_api_key.strip()

    def run(self):
        self._port25_ok = port25_available()
        if not self._port25_ok:
            self.port25_blocked.emit()
        _api_reset()  # allow NeverBounce another shot at the start of each run

        by_company: dict[str, list[dict]] = defaultdict(list)
        for c in self._contacts:
            by_company[c.get("company", "Unknown")].append(c)

        companies = list(by_company.keys())
        for i, company in enumerate(companies):
            if self._stop:
                break

            contacts = by_company[company]
            self.company_started.emit(company, len(contacts))
            verified_count = self._process_company(company, contacts)
            self.company_done.emit(company, verified_count, len(contacts))

            if not self._stop and i < len(companies) - 1:
                delay = random.uniform(*_COMPANY_DELAY)
                self.status_update.emit(f"Cooling down {delay:.0f}s...")
                time.sleep(delay)

        self.all_done.emit()

    def stop(self):
        self._stop = True

    # ── Per-company logic ────────────────────────────────────────────────

    def _process_company(self, company: str, contacts: list[dict]) -> int:
        # Step 1: Domain
        self.company_status.emit(company, "Finding domain...")
        domain = find_domain(company)
        if not domain:
            self.company_status.emit(company, "No domain found")
            for c in contacts:
                self.contact_enriched.emit({**c, "email": "", "email_status": "no domain", "email_source": ""})
            return 0

        # Step 2: MX
        self.company_status.emit(company, f"Checking MX for {domain}...")
        mx = get_mx(domain)
        if not mx:
            self.company_status.emit(company, f"No MX record for {domain}")
            for c in contacts:
                self.contact_enriched.emit({**c, "email": "", "email_status": "no MX", "email_source": domain})
            return 0

        # Step 3: Pattern detection — cache first, then scrape/DDG
        self.company_status.emit(company, f"Detecting email pattern for {domain}...")
        cached = cache_get(company)
        if cached and cached.get("domain") == domain and cached.get("pattern") is not None:
            pattern        = cached["pattern"]
            pattern_source = cached.get("pattern_source", "cache")
            pattern2       = cached.get("pattern2")
        else:
            pattern, pattern_source = detect_pattern(domain)
            pattern2 = get_kb_pattern2(domain)
            cache_put(company, domain, pattern, pattern_source, pattern2)

        if pattern:
            confidence = {"hubspot": "HubSpot KB", "scraped": "from website",
                          "emailformat": "emailformat.com", "ddg": "via search",
                          "smtp-verified": "SMTP confirmed",
                          "manual": "manual"}.get(pattern_source, pattern_source)
            p2_msg = f" + {pattern2}" if pattern2 else ""
            self.company_status.emit(company, f"Pattern: {pattern}{p2_msg} ({confidence})")
        else:
            self.company_status.emit(company, "Pattern unknown — will probe formats")

        # Step 4: SMTP format probe — try all variations on one contact to confirm
        # which format actually gets through. Skip if pattern already smtp-verified
        # or if the domain is catch-all (probe would accept everything).
        if pattern_source not in ("smtp-verified", "scraped", "emailformat", "hubspot", "manual"):
            probed = self._probe_format(company, domain, contacts)
            if probed:
                pattern        = probed
                pattern_source = "smtp-verified"
                self.company_status.emit(company, f"Pattern confirmed via SMTP: {pattern}")
                cache_put(company, domain, pattern, pattern_source)

        # Step 5: Per-contact email generation + SMTP verification
        verified = 0
        for contact in contacts:
            if self._stop:
                break

            first, last, name = self._name_parts(contact)
            candidates = generate_candidates(first, last, domain, pattern, pattern2)
            self.status_update.emit(f"[{company}] {name} — {len(candidates)} candidate(s)")

            email  = candidates[0] if candidates else ""
            status = "unknown"

            if self._port25_ok:
                # SMTP verification — direct port 25 probe
                for candidate in candidates:
                    r = _verify_email(candidate)
                    self.status_update.emit(f"  -> {candidate} [{r['status']}]")
                    if r["status"] == "verified":
                        email, status = candidate, "verified"
                        break
                    if r["status"] == "catch-all-risky":
                        status = "catch-all-risky"
                        break
                    if r["status"] == "bounced" and status == "unknown":
                        status = "bounced"
            elif self._nb_api_key or self._reoon_api_key:
                # Port 25 blocked — use API verification (NeverBounce first, Reoon fallback)
                # Limit candidates checked based on pattern confidence to conserve credits:
                #   Reliable source (KB/scraped/confirmed) -> top 2 max
                #   Unknown pattern                        -> top 3 max
                #   Catch-all domain (API returns catchall for all) -> stop after 1
                _reliable = ("hubspot", "scraped", "emailformat", "smtp-verified", "manual")
                max_checks = 2 if pattern_source in _reliable else 3
                api_candidates = candidates[:max_checks]

                for candidate in api_candidates:
                    r = verify_via_api(candidate, self._nb_api_key, self._reoon_api_key)
                    self.status_update.emit(f"  -> {candidate} [{r['status']}] ({r.get('detail', 'API')})")
                    if r["status"] == "verified":
                        email, status = candidate, "verified"
                        break
                    if r["status"] == "catch-all-risky":
                        # Domain is catch-all — checking more candidates wastes credits
                        status = "catch-all-risky"
                        break
                    if r["status"] == "bounced":
                        # Wrong format — record it and try next candidate
                        if status == "unknown":
                            status = "bounced"
                    if r["status"] == "unknown":
                        # API couldn't determine — stop burning credits on this contact
                        break
            else:
                # Port 25 blocked, no API keys — use pattern-preferred candidate as-is
                status = "unverified"
                self.status_update.emit(f"  -> {email} [unverified — port 25 blocked, no API key]")

            # Upgrade catch-all-risky if pattern is reliably sourced
            if status == "catch-all-risky" and pattern_source in ("scraped", "emailformat", "hubspot", "smtp-verified", "manual"):
                status = "catch-all-confirmed"

            catch_all = status in ("catch-all-confirmed", "catch-all-risky")
            source = domain + (f" ({status})" if catch_all else "")

            # Check bounce history
            if email and is_bounced(email):
                status = "bounced"

            # Confidence label for CRM export
            confidence = {
                "verified":            "High",
                "catch-all-confirmed": "Medium",
                "catch-all-risky":     "Low",
                "unverified":          "Low",
            }.get(status, "")

            enriched = {**contact, "email": email, "email_status": status,
                        "email_source": source, "confidence": confidence}
            self.contact_enriched.emit(enriched)

            if status == "verified":
                verified += 1

            time.sleep(random.uniform(*_CONTACT_DELAY))

        return verified

    # ── Format probe ─────────────────────────────────────────────────────

    def _probe_format(self, company: str, domain: str, contacts: list[dict]) -> str | None:
        """
        Try all email format variations on one test contact.
        Returns the confirmed pattern string, or None if unresolvable.
        """
        mx_records = get_mx_records(domain)
        if not mx_records:
            return None
        mx_host = mx_records[0][1]

        # Catch-all check — if server accepts everything, probe is meaningless
        if check_catch_all(domain, mx_host):
            self.company_status.emit(company, f"{domain} is catch-all — format probe skipped")
            return None

        # Pick a usable test contact (needs real first + last name)
        first = last = None
        for c in contacts:
            f, l, _ = self._name_parts(c)
            if f and l and len(l) > 1:
                first, last = f, l
                break

        if not first:
            return None

        self.company_status.emit(company, f"Probing formats: {first} {last} @ {domain}")
        candidates = generate_candidates(first, last, domain, pattern=None)

        for candidate in candidates:
            r = _verify_email(candidate)
            self.status_update.emit(f"  Probe: {candidate} -> {r['status']}")
            if r["status"] == "verified":
                local = candidate.split("@")[0].lower()
                confirmed = _local_to_pattern(local, first.lower(), last.lower())
                self.status_update.emit(f"  Confirmed pattern: {confirmed}")
                return confirmed
            time.sleep(_PROBE_DELAY)

        return None

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _name_parts(contact: dict) -> tuple[str, str, str]:
        first = contact.get("first_name") or ""
        last  = contact.get("last_name") or ""
        if not first or not last:
            raw   = contact.get("name", "").split(",")[0].strip()
            parts = raw.split()
            first = parts[0] if parts else ""
            last  = parts[-1] if len(parts) > 1 else ""
        name = contact.get("name", f"{first} {last}".strip())
        return first, last, name


# ── API re-verify worker (for CRM export pre-flight check) ───────────────────

# Statuses that are worth re-checking via API before export
_REVERIFY_STATUSES = {"bounced", "unverified", "unknown", "catch-all-risky"}

# Statuses that upgrade to confirmed when the pattern source is reliable
_RELIABLE_SOURCES = {"hubspot", "scraped", "emailformat", "smtp-verified", "manual"}


class ApiVerifyWorker(QThread):
    """
    Re-verifies contacts that have a questionable email status (bounced from
    SMTP probe, unverified, unknown) using NeverBounce / Reoon API.
    Emits each result as it completes so the UI can update in real time.
    """
    contact_result = Signal(int, dict)   # index into contacts list, updated contact dict
    progress       = Signal(int, int)    # done, total
    status_update  = Signal(str)
    all_done       = Signal(int)         # number of contacts updated

    def __init__(self, contacts: list[dict], nb_api_key: str = "", reoon_api_key: str = ""):
        super().__init__()
        self._contacts     = contacts
        self._nb_key       = nb_api_key.strip()
        self._reoon_key    = reoon_api_key.strip()
        self._stop         = False

    def stop(self):
        self._stop = True

    def run(self):
        _api_reset()
        to_check = [
            (i, c) for i, c in enumerate(self._contacts)
            if c.get("email") and c.get("email_status", "") in _REVERIFY_STATUSES
        ]
        total   = len(to_check)
        updated = 0

        for done, (i, contact) in enumerate(to_check):
            if self._stop:
                break

            email  = contact["email"]
            self.status_update.emit(f"Verifying {email}…")

            r      = verify_via_api(email, self._nb_key, self._reoon_key)
            status = r["status"]

            # Upgrade catch-all-risky if pattern source is reliable
            if status == "catch-all-risky":
                src = contact.get("email_source_raw") or contact.get("email_source", "")
                if any(s in src for s in _RELIABLE_SOURCES):
                    status = "catch-all-confirmed"

            confidence = {
                "verified":            "High",
                "catch-all-confirmed": "Medium",
                "catch-all-risky":     "Low",
            }.get(status, "")

            updated_contact = {**contact, "email_status": status, "confidence": confidence}
            self.contact_result.emit(i, updated_contact)
            self.progress.emit(done + 1, total)

            if status != contact.get("email_status"):
                updated += 1

            time.sleep(0.4)  # stay within API rate limits

        self.all_done.emit(updated)


def _local_to_pattern(local: str, first: str, last: str) -> str:
    fi = first[0] if first else ""
    li = last[0]  if last  else ""
    mapping = {
        f"{first}.{last}": "first.last",
        f"{fi}{last}":     "flast",
        f"{first}{last}":  "firstlast",
        f"{fi}.{last}":    "f.last",
        f"{first}.{li}":   "first.l",
        f"{first}":        "first",
        f"{last}.{first}": "last.first",
        f"{li}{first}":    "lfirst",
        f"{first}_{last}": "first_last",
        f"{first}-{last}": "first-last",
        f"{last}{fi}":     "lastf",
        f"{last}":         "last",
    }
    return mapping.get(local, local)
