"""
QThread worker for email enrichment.
Processes contacts grouped by company:
  1. Find domain (once per company)
  2. Detect email pattern + confidence (once per company)
  3. Check catch-all (once per company)
  4. Generate + SMTP-verify candidates (per contact)

Catch-all policy:
  catch-all + pattern scraped from website  → email_status 'catch-all-confirmed'
      (pattern verified against a real email found on their own site — safer to send)
  catch-all + pattern from DDG or guessed   → email_status 'catch-all-risky'
      (address is a best-guess; high bounce risk — do NOT auto-send)
"""

import time
import random
import logging
from collections import defaultdict

from PySide6.QtCore import QThread, Signal

from emailer.domain_finder import find_domain
from emailer.pattern_detector import detect_pattern
from emailer.generator import generate_candidates
from emailer.smtp_verifier import verify_candidates, is_catch_all, get_mx, port25_available
from emailer.domain_cache import get as cache_get, put as cache_put
from emailer.bounce_tracker import is_bounced

logger = logging.getLogger(__name__)

_CONTACT_DELAY = (1.5, 3.0)   # seconds between SMTP checks
_COMPANY_DELAY = (5.0, 10.0)  # seconds between companies


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
        self._contacts = contacts
        self._stop     = False

    def run(self):
        if not port25_available():
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
        # Step 1: domain
        self.company_status.emit(company, "Finding domain...")
        domain = find_domain(company)
        if not domain:
            self.company_status.emit(company, "No domain found")
            for c in contacts:
                self.contact_enriched.emit({**c, "email": "", "email_status": "no domain", "email_source": ""})
            return 0

        # Step 2: MX check
        self.company_status.emit(company, f"Checking MX for {domain}...")
        mx = get_mx(domain)
        if not mx:
            self.company_status.emit(company, f"No MX record for {domain}")
            for c in contacts:
                self.contact_enriched.emit({**c, "email": "", "email_status": "no MX", "email_source": domain})
            return 0

        # Step 3: Pattern detection — check cache first
        self.company_status.emit(company, f"Detecting email pattern for {domain}...")
        cached = cache_get(company)
        if cached and cached.get("domain") == domain and cached.get("pattern") is not None:
            pattern        = cached["pattern"]
            pattern_source = cached.get("pattern_source", "scraped")
        else:
            pattern, pattern_source = detect_pattern(domain)
            cache_put(company, domain, pattern, pattern_source)
        if pattern:
            confidence = "confirmed from website" if pattern_source == "scraped" else "found via search"
            self.company_status.emit(company, f"Pattern: {pattern} ({confidence})")
        else:
            self.company_status.emit(company, "Pattern: unknown — trying all variants")

        # Step 4: Catch-all check
        catch_all = is_catch_all(domain)
        if catch_all:
            risk = "lower risk (pattern scraped)" if pattern_source == "scraped" else "HIGH RISK (pattern guessed)"
            self.company_status.emit(company, f"{domain} is catch-all — {risk}")

        # Step 5: Per-contact verification
        verified = 0
        for contact in contacts:
            if self._stop:
                break

            name  = contact.get("name", "")
            parts = name.strip().split()
            first = parts[0] if parts else ""
            last  = parts[-1] if len(parts) > 1 else ""

            candidates = generate_candidates(first, last, domain, pattern)
            self.status_update.emit(
                f"[{company}] {name} — testing {len(candidates)} candidate(s) on {domain}"
            )

            email, status = verify_candidates(candidates)

            # Refine catch-all status based on how confident we are in the pattern
            if status == "catch-all":
                if pattern_source == "scraped":
                    status = "catch-all-confirmed"
                else:
                    status = "catch-all-risky"

            source = domain
            if catch_all:
                source += " (catch-all-confirmed)" if status == "catch-all-confirmed" else " (catch-all-risky)"

            enriched = {
                **contact,
                "email":        email,
                "email_status": status,
                "email_source": source,
            }
            self.contact_enriched.emit(enriched)

            # Override with known bounce data
            if email and is_bounced(email):
                status = "bounced"

            if status == "verified":
                verified += 1

            time.sleep(random.uniform(*_CONTACT_DELAY))

        return verified
