"""
QThread worker that runs LinkedIn employment verification on a batch of contacts.
Each contact's LinkedIn URL is fetched once; the og:title is checked against
the contact's company name.  Results are emitted per-contact so the UI can
update in real time.
"""

import time
import random
import logging

from PySide6.QtCore import QThread, Signal

from verifier.linkedin_checker import check_employment

logger = logging.getLogger(__name__)

# Seconds between LinkedIn requests — be polite to avoid IP blocks
_VERIFY_DELAY = (4.0, 9.0)


class VerifyWorker(QThread):
    contact_verified = Signal(str, str)  # linkedin_url, emp_status
    progress         = Signal(int, int)  # current, total
    status_update    = Signal(str)
    all_done         = Signal()

    def __init__(self, contacts: list[dict]):
        super().__init__()
        self._contacts = contacts
        self._stop     = False

    def run(self):
        total = len(self._contacts)
        for i, contact in enumerate(self._contacts):
            if self._stop:
                break

            url     = contact.get("linkedin_url", "")
            company = contact.get("company", "")
            name    = contact.get("name", "Unknown")

            if not url or not company:
                self.contact_verified.emit(url, "unknown")
                self.progress.emit(i + 1, total)
                continue

            self.status_update.emit(f"[{i+1}/{total}] Verifying {name} at {company}...")
            status = check_employment(url, company, name)
            self.contact_verified.emit(url, status)
            self.progress.emit(i + 1, total)

            if not self._stop and i < total - 1:
                time.sleep(random.uniform(*_VERIFY_DELAY))

        self.all_done.emit()

    def stop(self):
        self._stop = True
