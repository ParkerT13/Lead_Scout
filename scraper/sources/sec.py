"""
Officers and directors of public companies, from SEC EDGAR insider filings (Form 4).

Free and official: no key, no account. Every Form 4 names the reporting person, whether they are
a director/officer, and their exact officer title ("EVP, Exploration"). We read the most recent
filings and keep the distinct people.

Only public companies are covered, and mostly executives and directors, not rank-and-file staff;
it complements the other sources rather than replacing them.
"""

from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

from scraper.people import make_contact

logger = logging.getLogger(__name__)

def _headers() -> dict:
    """SEC rejects anonymous clients. A browser-style agent is accepted; if credentials.json has
    "sec_contact" (an email/phone) it is appended, which is what SEC's fair-access policy asks for."""
    import os
    from pathlib import Path
    contact = os.environ.get("LEADSCOUT_SEC_CONTACT", "")
    if not contact:
        try:
            cred = Path(__file__).resolve().parent.parent.parent / "credentials.json"
            contact = json.loads(cred.read_text(encoding="utf-8")).get("sec_contact", "") if cred.exists() else ""
        except Exception:
            contact = ""
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 LeadScout/1.0"
    return {"User-Agent": f"{ua} {contact}".strip(), "Accept-Encoding": "gzip, deflate"}
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
_MAX_FORMS = 40

_ENTITY_WORDS = re.compile(
    r"\b(llc|l\.l\.c|lp|l\.p|inc|corp|corporation|trust|fund|capital|partners|holdings|ltd|"
    r"company|group|management|advisors|investments|bank|equity|ventures|foundation|estate)\b", re.I)
_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "md", "phd"}

_tickers_cache: dict | None = None


def _norm(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace(".", ""))        # "L.P." -> "lp"
    n = re.sub(r"\b(inc|corp|corporation|company|co|ltd|llc|lp|plc|holdings|the|group)\b", " ", n)
    return re.sub(r"\s+", "", n)


def _get(url: str, session: requests.Session, timeout: float = 15.0):
    try:
        r = session.get(url, headers=_headers(), timeout=timeout)
        return r if r.status_code == 200 else None
    except Exception as e:
        logger.debug("SEC fetch failed %s: %s", url, e)
        return None


def find_cik(company: str, session: requests.Session | None = None) -> str | None:
    """10-digit CIK for a company name, or None when it is not a listed company."""
    global _tickers_cache
    session = session or requests.Session()
    if _tickers_cache is None:
        r = _get(_TICKERS_URL, session)
        if r is None:
            return None
        _tickers_cache = {_norm(v["title"]): v["cik_str"] for v in json.loads(r.text).values()}
    key = _norm(company)
    if not key:
        return None
    cik = _tickers_cache.get(key)
    if cik is None:
        # "chord energy" vs "Chord Energy Corp": accept a unique prefix match of a distinctive name
        hits = [c for k, c in _tickers_cache.items() if len(key) >= 6 and (k.startswith(key) or key.startswith(k) and len(k) >= 6)]
        cik = hits[0] if len(set(hits)) == 1 else None
    return f"{int(cik):010d}" if cik is not None else None


def parse_form4(xml_text: str) -> list[dict]:
    """[{'name': 'First Last', 'title': ...}] for each reporting owner in one Form 4 XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    people = []
    for owner in root.findall(".//reportingOwner"):
        raw = (owner.findtext("reportingOwnerId/rptOwnerName") or "").strip()
        if not raw or _ENTITY_WORDS.search(raw):
            continue
        parts = [p for p in re.split(r"[\s,]+", raw) if p.strip(".").lower() not in _SUFFIX]
        if len(parts) < 2:
            continue
        last, first = parts[0].title(), parts[1].title()      # EDGAR lists "LAST FIRST MIDDLE"
        if re.match(r"^(mc|mac)", last, re.I) and len(last) > 3:
            last = last[:2].title() + last[2:].title() if last.lower().startswith("mc") else last
        rel = owner.find("reportingOwnerRelationship")
        title = ""
        if rel is not None:
            title = (rel.findtext("officerTitle") or "").strip()
            if not title and (rel.findtext("isDirector") or "").strip() in ("1", "true"):
                title = "Director"
            if not title and (rel.findtext("isOfficer") or "").strip() in ("1", "true"):
                title = "Officer"
        if title:
            people.append({"name": f"{first} {last}", "title": title})
    return people


def discover(company: str, max_forms: int = _MAX_FORMS, pause: float = 0.15) -> list[dict]:
    """Officers/directors of `company` from recent insider filings ([] when not a public company)."""
    session = requests.Session()
    cik = find_cik(company, session)
    if not cik:
        return []
    r = _get(_SUBMISSIONS.format(cik=cik), session)
    if r is None:
        return []
    try:
        recent = r.json()["filings"]["recent"]
    except Exception:
        return []
    cik_plain = str(int(cik))
    seen: dict[str, dict] = {}
    forms = [(recent["accessionNumber"][i], recent["primaryDocument"][i])
             for i, f in enumerate(recent["form"]) if f in ("4", "3")][:max_forms]
    for acc, doc in forms:
        raw_doc = doc.split("/")[-1]                  # drop the xslF345 rendering folder -> raw XML
        x = _get(_ARCHIVE.format(cik=cik_plain, acc=acc.replace("-", ""), doc=raw_doc), session)
        time.sleep(pause)                              # SEC fair-access limit: <10 requests/second
        if x is None:
            continue
        for p in parse_form4(x.text):
            seen.setdefault(p["name"].lower(), {**p, "url": _ARCHIVE.format(
                cik=cik_plain, acc=acc.replace("-", ""), doc=raw_doc)})
    contacts = []
    for p in seen.values():
        c = make_contact(p["name"], p["title"], company, "SEC filings", source_url=p["url"])
        if c:
            contacts.append(c)
    logger.info("SEC for %s (CIK %s): %d form(s), %d people", company, cik, len(forms), len(contacts))
    return contacts
