"""
QThread wrapper around emailer.engine.Enricher (all enrichment logic lives there).

Catch-all policy:
  catch-all + evidence-backed pattern -> 'catch-all-confirmed' (safer to send)
  catch-all + guessed pattern         -> 'catch-all-risky'     (high bounce risk)
"""

import time
import random
import logging
from collections import defaultdict

from PySide6.QtCore import QThread, Signal

from emailer.engine import Enricher, RELIABLE_SOURCES
from emailer.smtp_verifier import port25_available
from emailer.api_verifier import verify_via_api, reset_session as _api_reset

logger = logging.getLogger(__name__)

_COMPANY_DELAY = (5.0, 10.0)     # seconds between companies


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
        self._contacts      = contacts
        self._stop          = False
        self._nb_api_key    = nb_api_key.strip()
        self._reoon_api_key = reoon_api_key.strip()

    def run(self):
        port25_ok = port25_available()
        if not port25_ok:
            self.port25_blocked.emit()
        _api_reset()  # allow NeverBounce another shot at the start of each run

        enricher = Enricher(
            nb_key=self._nb_api_key, reoon_key=self._reoon_api_key, port25_ok=port25_ok,
            say=self.company_status.emit, detail=self.status_update.emit,
            should_stop=lambda: self._stop,
        )

        by_company: dict[str, list[dict]] = defaultdict(list)
        for c in self._contacts:
            by_company[c.get("company", "Unknown")].append(c)

        companies = list(by_company.keys())
        prefetch = enricher.prefetch(companies)
        for i, company in enumerate(companies):
            if self._stop:
                break
            contacts = by_company[company]
            self.company_started.emit(company, len(contacts))
            self.company_status.emit(company, "Researching company...")
            prefetch.wait(company)
            good = 0
            try:
                for result in enricher.enrich_company(company, contacts):
                    self.contact_enriched.emit(result)
                    if result.get("email_status") in ("verified", "found-on-site"):
                        good += 1
            except Exception as e:                   # one bad company must not end the run
                logger.exception("Enrichment failed for %s", company)
                self.company_status.emit(company, f"Error: {e}")
                for c in contacts:
                    self.contact_enriched.emit({**c, "email": "", "email_status": "error",
                                                "email_source": str(e)[:60], "confidence": ""})
            self.company_done.emit(company, good, len(contacts))

            if not self._stop and i < len(companies) - 1:
                delay = random.uniform(*_COMPANY_DELAY) if enricher.port25_ok else 0.5   # only SMTP needs politeness
                self.status_update.emit(f"Cooling down {delay:.0f}s...")
                time.sleep(delay)

        prefetch.close()
        self.all_done.emit()

    def stop(self):
        self._stop = True


# ── API re-verify worker (for CRM export pre-flight check) ───────────────────

# Statuses that are worth re-checking via API before export
_REVERIFY_STATUSES = {"bounced", "unverified", "unknown", "catch-all-risky",
                      "pattern-confirmed", "pattern-ddg", "best-guess"}

# Statuses that upgrade to confirmed when the pattern source is reliable


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
                if any(s in src for s in RELIABLE_SOURCES):
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
