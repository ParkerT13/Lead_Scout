"""
Contacts mentioned on the open web (press releases, conference programs, paper author lines,
news): "Jane Smith, Senior Geologist at Ring Energy", "Jane Smith - VP Exploration, Ring Energy".

Uses the same free search engines as the LinkedIn search. Every match must have a person-shaped
name, a job-title keyword and the company name in one short pattern, so precision stays high.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Callable

from scraper.engines import bing, duckduckgo
from scraper.parser import _TITLE_KEYWORDS
from scraper.people import make_contact
from scraper.query_builder import generate_variants

logger = logging.getLogger(__name__)

_W = r"[A-Z][a-zA-Z'’\-]{1,18}"
_NAME = rf"(?P<name>{_W}(?:\s+[A-Z]\.)?(?:\s+{_W})?\s+{_W})"
_TITLE = r"(?P<title>[A-Za-z][A-Za-z/&,\- ]{3,70}?)"
_SEP = r"\s*(?:,|–|—|\||-|\()\s*"

_QUERIES = [
    '"{co}" geologist OR geophysicist OR geoscientist',
    '"{co}" "reservoir engineer" OR "petroleum engineer" OR "drilling engineer" OR "completions engineer"',
    '"{co}" "vice president" OR director OR manager exploration OR operations OR engineering',
    '"{co}" speaker OR author OR presented SPE OR AAPG OR URTeC OR SEG',
]
# Plain (no OR) forms - engines that ignore OR still return useful pages
_QUERIES_PLAIN = [
    '"{co}" geologist', '"{co}" geophysicist', '"{co}" reservoir engineer', '"{co}" petroleum engineer',
    '"{co}" "vice president" exploration', '"{co}" "named" OR "appoints" OR "promoted" vice president',
]


def _is_title(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in _TITLE_KEYWORDS) and len(text) <= 80


def _bad_name(name: str) -> bool:
    low = name.lower()
    return any(w in low.split() for w in (
        "the", "and", "for", "inc", "llc", "corp", "oil", "gas", "energy", "company", "resources",
        "texas", "houston", "denver", "senior", "chief", "vice", "president", "director", "manager",
        "engineer", "geologist", "report", "news", "press", "release", "announces", "appoints", "named",
        "society", "association", "petroleum", "exploration", "production", "partners", "operating"))


def parse_mentions(text: str, company_variants: list[str]) -> list[tuple[str, str]]:
    """[(name, title)] for 'Name, Title at Company' style mentions of the company in `text`."""
    out: list[tuple[str, str]] = []
    text = re.sub(r"\s+", " ", text)
    for variant in company_variants:
        co = re.escape(variant)
        patterns = [
            # Jane Smith, Senior Geologist at Ring Energy  /  Jane Smith is VP Exploration at Ring Energy
            rf"{_NAME}(?:,|\s+is|\s+was|\s+serves as)?\s+(?:a\s+|an\s+|the\s+)?{_TITLE}\s+(?:at|with|for|of)\s+(?:the\s+)?{co}\b",
            # Jane Smith - Senior Geologist - Ring Energy  /  Jane Smith, VP Exploration, Ring Energy
            rf"{_NAME}{_SEP}{_TITLE}{_SEP}{co}\b",
            # Ring Energy's Jane Smith, Senior Geologist  /  Ring Energy Senior Geologist Jane Smith
            rf"{co}(?:'s|’s)?\s+{_TITLE}\s+{_NAME}\b",
        ]
        for idx, pat in enumerate(patterns):
            for m in re.finditer(pat, text):
                name, title = m.group("name").strip(), m.group("title").strip(" ,-")
                if idx == 2:
                    # "Ring Energy Reservoir Engineer Ann Ray": the lazy title stops early and the
                    # name swallows the rest of the title - hand those words back to the title
                    toks = name.split()
                    while len(toks) > 2 and _is_title(toks[0]):
                        title = f"{title} {toks.pop(0)}".strip()
                    name = " ".join(toks)
                if _bad_name(name) or not _is_title(title):
                    continue
                out.append((name, title))
    return out


def discover(company: str, stop: Callable[[], bool] = lambda: False, pause: tuple = (2.0, 4.0),
             max_queries: int = 8) -> list[dict]:
    variants = generate_variants(company)
    queries = [q.format(co=variants[0]) for q in _QUERIES + _QUERIES_PLAIN][:max_queries]
    contacts, seen = [], set()
    for q in queries:
        if stop():
            break
        results = []
        for engine in (duckduckgo, bing):
            try:
                results = engine.search(q, max_results=20) if engine is duckduckgo else engine.search(q)
            except Exception as e:
                logger.debug("web search error: %s", e)
            if results:
                break
        for r in results:
            href = r.get("href", "")
            if "linkedin.com" in href:
                continue                               # the LinkedIn source handles those
            text = f"{r.get('title', '')}. {r.get('body', '')}"
            for name, title in parse_mentions(text, variants):
                c = make_contact(name, title, company, "Web", source_url=href)
                if c and c["name"].lower() not in seen:
                    seen.add(c["name"].lower())
                    contacts.append(c)
        time.sleep(random.uniform(*pause))
    logger.info("Web mentions for %s: %d people", company, len(contacts))
    return contacts
