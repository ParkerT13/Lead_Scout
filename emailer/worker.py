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

    def __init__(self, contacts: list[dict]):
        super().__init__()
        self._contacts  = contacts
        self._stop      = False
        self._port25_ok = True

    def run(self):
        self._port25_ok = port25_available()
        if not self._port25_ok:
            self.port25_blocked.emit()

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
            else:
                # Port 25 blocked — skip SMTP, use pattern-preferred candidate as-is
                status = "unverified"
                self.status_update.emit(f"  -> {email} [unverified — port 25 blocked]")

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
