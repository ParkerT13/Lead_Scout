"""
Flag contacts that are already in your CRM, so you do not re-pull or re-mail them.

Uses a HubSpot (or any CRM) contacts export CSV. Looked up automatically in:
  1. the path you give
  2. ~/LeadScout_Output/crm_contacts.csv
  3. the newest Downloads/*hubspot*contacts*.csv
A contact counts as "in CRM" when its email is in the export, or when the same person (first +
last name) is listed under the same company.
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

from scraper.people import company_key, name_key

logger = logging.getLogger(__name__)


def find_export(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    out = Path(os.environ.get("LEADSCOUT_OUTPUT_DIR") or (Path.home() / "LeadScout_Output"))
    if (out / "crm_contacts.csv").exists():
        return out / "crm_contacts.csv"
    downloads = Path.home() / "Downloads"
    if downloads.exists():
        hits = [p for p in downloads.glob("*.csv") if "hubspot" in p.name.lower() and "contact" in p.name.lower()]
        if hits:
            return max(hits, key=lambda p: p.stat().st_mtime)
    return None


class CrmIndex:
    def __init__(self, emails: set[str], people: set[str]):
        self.emails, self.people = emails, people

    def __len__(self):
        return len(self.emails) + len(self.people)

    def contains(self, contact: dict) -> bool:
        email = (contact.get("email") or "").lower().strip()
        if email and email in self.emails:
            return True
        name = contact.get("name") or f"{contact.get('first_name', '')} {contact.get('last_name', '')}"
        nk = name_key(name)
        return bool(nk and f"{company_key(contact.get('company', ''))}|{nk}" in self.people)

    def mark(self, rows: list[dict]) -> int:
        n = 0
        for r in rows:
            r["in_crm"] = "yes" if self.contains(r) else ""
            n += bool(r["in_crm"])
        return n


def load(path: str | Path | None = None) -> CrmIndex | None:
    p = find_export(path)
    if p is None:
        return None
    emails: set[str] = set()
    people: set[str] = set()
    try:
        with open(p, newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
            col_email = next((cols[c] for c in cols if c in ("email", "email address")), None)
            col_first = next((cols[c] for c in cols if c in ("first name", "first_name", "firstname")), None)
            col_last = next((cols[c] for c in cols if c in ("last name", "last_name", "lastname")), None)
            col_co = next((cols[c] for c in cols if c in ("company name", "company", "associated company",
                                                          "primary associated company")), None)
            for row in reader:
                if col_email and (row.get(col_email) or "").strip():
                    emails.add(row[col_email].strip().lower())
                if col_first and col_last and col_co:
                    nk = name_key(f"{row.get(col_first, '')} {row.get(col_last, '')}")
                    ck = company_key(row.get(col_co, ""))
                    if nk and ck:
                        people.add(f"{ck}|{nk}")
    except Exception as e:
        logger.warning("Could not read CRM export %s: %s", p, e)
        return None
    logger.info("CRM export %s: %d emails, %d named people", p.name, len(emails), len(people))
    return CrmIndex(emails, people)
