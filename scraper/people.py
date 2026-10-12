"""
Source-agnostic contact helpers: build a contact dict from any source, dedupe and merge
across sources, and give contacts without a LinkedIn profile a stable unique reference.

A contact is the same dict the rest of the app already uses:
  name, first_name, last_name, title, company, location, basin, linkedin_url,
  source, date_pulled   (+ source_url / emp_status when known)
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from urllib.parse import quote

from scraper.parser import _extract_name

_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|phd|pe|mba|cpa|esq)\b\.?", re.IGNORECASE)


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")


def name_key(name: str) -> str:
    """first-initial + last name, accent/case/suffix-insensitive: 'Robert B. Smith Jr.' ~ 'bob smith'
    is NOT merged (nicknames differ) but 'Robert Smith' ~ 'Robert B. Smith, PE' is."""
    clean = _SUFFIX_RE.sub("", _ascii(_extract_name(name) or name).lower().split(",")[0])
    parts = [p for p in re.findall(r"[a-z]+", clean)]
    if len(parts) < 2:
        return ""
    return f"{parts[0]} {parts[-1]}"


_LEGAL_WORDS = re.compile(r"\b(inc|incorporated|llc|lp|llp|corp|corporation|company|co|ltd|limited|the)\b")


def company_key(company: str) -> str:
    """'Ring Energy, Inc.' ~ 'ring energy': legal suffixes do not distinguish companies."""
    text = _LEGAL_WORDS.sub(" ", _ascii(company).lower().replace(".", ""))
    return re.sub(r"[^a-z0-9]", "", text)


def person_key(contact: dict) -> str:
    nk = name_key(contact.get("name", ""))
    return f"{company_key(contact.get('company', ''))}|{nk}" if nk else ""


def synthetic_ref(source_url: str, name: str, company: str) -> str:
    """Unique, clickable reference for a contact with no LinkedIn profile: the page where we
    found them plus a #fragment naming them (keeps UI row-matching by 'linkedin_url' working)."""
    base = source_url or f"https://found.local/{company_key(company) or 'unknown'}"
    return f"{base.split('#')[0]}#{quote(name_key(name).replace(' ', '-') or 'person')}"


def is_linkedin(url: str) -> bool:
    return "linkedin.com/in/" in (url or "")


def make_contact(name: str, title: str, company: str, source: str, source_url: str = "",
                 linkedin_url: str = "", location: str = "", basin: str = "") -> dict | None:
    """Normalise a raw (name, title) pair into the app's contact dict, or None if unusable."""
    full = _extract_name(name or "")
    parts = full.split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    if len(last.strip(".")) < 2 or len(first.strip(".")) < 2:
        return None
    clean_name = f"{first} {last}"
    url = linkedin_url or synthetic_ref(source_url, clean_name, company)
    return {
        "name": clean_name, "first_name": first, "last_name": last,
        "title": (title or "").strip(), "company": company, "location": location, "basin": basin,
        "linkedin_url": url, "source": source, "source_url": source_url,
        "date_pulled": datetime.now().strftime("%Y-%m-%d"), "emp_status": "",
    }


def merge(existing: dict, new: dict) -> dict:
    """Combine two records of the same person; prefer a real LinkedIn URL and the richer title."""
    out = dict(existing)
    if is_linkedin(new.get("linkedin_url", "")) and not is_linkedin(out.get("linkedin_url", "")):
        out["linkedin_url"] = new["linkedin_url"]
    if len(new.get("title", "")) > len(out.get("title", "")):
        out["title"] = new["title"]
    for k in ("location", "basin"):
        out[k] = out.get(k) or new.get(k, "")
    seen = [s for s in (out.get("source", "") or "").split("+") if s]
    for s in (new.get("source", "") or "").split("+"):
        if s and s not in seen:
            seen.append(s)
    out["source"] = "+".join(seen)
    return out


class ContactBook:
    """Collects contacts from many sources, merging duplicates of the same person."""

    def __init__(self):
        self._by_key: dict[str, dict] = {}
        self._by_url: set[str] = set()

    def add(self, contact: dict | None) -> tuple[dict | None, bool]:
        """Returns (record, is_new). A duplicate returns the merged record and is_new=False."""
        if not contact:
            return None, False
        key = person_key(contact)
        url = contact.get("linkedin_url", "")
        if key and key in self._by_key:
            merged = merge(self._by_key[key], contact)
            self._by_key[key] = merged
            return merged, False
        if url and url in self._by_url:
            return contact, False
        if key:
            self._by_key[key] = contact
        if url:
            self._by_url.add(url)
        return contact, True

    def __len__(self):
        return len(self._by_key)

    def all(self) -> list[dict]:
        return list(self._by_key.values())
