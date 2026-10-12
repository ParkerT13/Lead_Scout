"""
Targeted search for one person's published email address (free search engines only).

Papers (SPE/AAPG author lines), conference programs, press releases, slide decks and org charts
sometimes print the real address. A hit here is better than any inferred format, so it is
reported as 'found-on-site' (published online).

Deliberately narrow so it stays fast and precise:
  * only a handful of queries per person (the caller decides who is worth it)
  * an address only counts if it is on the company's own domain AND its local part matches the
    person's name (nickname-aware) - never a generic or someone else's address
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable

from emailer import site_harvester as sh
from emailer.generator import clean_name

logger = logging.getLogger(__name__)

_QUERIES = [
    '"{first} {last}" "@{domain}"',
    '"{first} {last}" {company} email',
]


def find_personal_email(first: str, last: str, domain: str, company: str = "",
                        stop: Callable[[], bool] = lambda: False,
                        pause: tuple = (1.5, 3.0), search=None) -> tuple[str, str] | None:
    """(email, source_url) if a search result prints this person's address, else None."""
    if not (clean_name(first) and clean_name(last) and domain):
        return None
    if search is None:
        from scraper.engines import duckduckgo
        search = duckduckgo.search
    for tpl in _QUERIES:
        if stop():
            return None
        q = tpl.format(first=first, last=last, domain=domain, company=company or domain)
        try:
            results = search(q, max_results=10)
        except Exception as e:
            logger.debug("person search error: %s", e)
            results = []
        for r in results or []:
            text = f"{r.get('title', '')} . {r.get('body', '')}"
            findings = sh.extract_from_text(text, domain, r.get("href", ""))
            email = sh.find_contact_email(findings, first, last, domain)
            if email:
                return email, r.get("href", "")
        time.sleep(random.uniform(*pause))
    return None
