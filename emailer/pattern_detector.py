"""
Detect a company's email pattern by finding one real email at their domain.
Sources tried in order:
  1. Company website (/, /contact, /about, /team, /leadership, etc.)
  2. DDG search for "@domain" in results

Returns a (pattern, source) tuple:
  pattern — 'first.last', 'flast', etc., or None if not found
  source  — 'scraped' (found on company website),
             'ddg'     (found via search engine snippet),
             'none'    (no pattern detected — all candidates will be tried)
"""

import re
import logging

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_EMAIL_RE   = re.compile(r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b")
_HEADERS    = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Ordered from most likely to contain a real named email to least likely.
# We try all of them before falling back to DDG.
_SITE_PATHS = [
    "",
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/leadership",
    "/management",
    "/people",
    "/staff",
    "/about/team",
    "/about/leadership",
    "/team-members",
]


def detect_pattern(domain: str) -> tuple[str | None, str]:
    """
    Return (pattern, source) where pattern is a string like 'first.last' or
    None, and source is 'scraped', 'ddg', or 'none'.
    """
    # 1. Scrape company website pages
    for path in _SITE_PATHS:
        email = _scrape_page(f"https://{domain}{path}", domain)
        if email:
            pattern = _infer_pattern(email, domain)
            logger.info("Pattern for %s: %s (scraped from %s, email: %s)", domain, pattern, path or "/", email)
            return pattern, "scraped"

    # 2. DDG search
    email = _ddg_search(domain)
    if email:
        pattern = _infer_pattern(email, domain)
        logger.info("Pattern for %s: %s (via DDG, from %s)", domain, pattern, email)
        return pattern, "ddg"

    logger.info("No pattern detected for %s", domain)
    return None, "none"


def _scrape_page(url: str, domain: str) -> str | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=8)
        if r.status_code != 200:
            return None
        emails = _EMAIL_RE.findall(r.text)
        company_emails = [
            e for e in emails
            if e.lower().endswith(f"@{domain}")
            and "example" not in e
            and "noreply" not in e
            and "no-reply" not in e
            and "info" not in e.split("@")[0]
            and "contact" not in e.split("@")[0]
        ]
        return company_emails[0] if company_emails else None
    except Exception:
        return None


def _ddg_search(domain: str) -> str | None:
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(f'"@{domain}"', max_results=5))
        for r in results:
            text = r.get("body", "") + " " + r.get("title", "")
            emails = _EMAIL_RE.findall(text)
            company_emails = [
                e for e in emails
                if e.lower().endswith(f"@{domain}")
                and "noreply" not in e
                and "no-reply" not in e
            ]
            if company_emails:
                return company_emails[0]
    except Exception as e:
        logger.warning("DDG pattern search error: %s", e)
    return None


def _infer_pattern(email: str, domain: str) -> str:
    local = email.split("@")[0].lower()
    local = re.sub(r"[^a-z.]", "", local)  # strip non-alpha except dot

    if "." in local:
        parts = local.split(".")
        if len(parts[0]) == 1 and len(parts) == 2:
            return "f.last"
        if len(parts[-1]) == 1 and len(parts) == 2:
            return "first.l"
        return "first.last"

    # No dot — determine by length relative to a typical first name (3-6 chars)
    if len(local) <= 7:
        return "flast"   # likely first-initial + last
    return "firstlast"
