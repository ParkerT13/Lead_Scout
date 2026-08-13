"""
Detect a company's email pattern. All sources are free, no API keys required.
Sources tried in order:

  0. HubSpot knowledge base — patterns derived from real name+email pairs in
                              our own CRM. Most reliable source: definitively
                              confirmed from actual contacts. Checked first.
  1. emailformat.com  — crowdsourced format database, no key needed
  2. Company website  — scrapes contact/team/about pages looking for
                        name+email pairs (definitive) or dot-pattern emails (reliable)
  3. DDG multi-query  — same strategy against search snippets

Only two detection methods are considered reliable:
  A. Name+email pair: find "John Smith jsmith@domain.com" -> definitively flast
  B. Dot-pattern email: find "j.smith@domain.com" -> definitively f.last

For no-dot emails without a name, we cannot reliably distinguish flast from
firstlast. In those cases we return (None, 'none') so the SMTP format probe
can try all 8 variations (works on non-catch-all domains with port 25 access).

Catch-all domains + blocked port 25 = no automated way to determine format.
Use Check Domains.bat to set the pattern manually for those companies.
"""

import json
import re
import logging
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_KB_PATH = Path(__file__).parent / "hubspot_knowledge.json"
_KB: dict | None = None

def _load_kb() -> dict:
    global _KB
    if _KB is None:
        try:
            _KB = json.loads(_KB_PATH.read_text(encoding="utf-8")) if _KB_PATH.exists() else {}
        except Exception:
            _KB = {}
    return _KB


def get_kb_samples(domain: str) -> list[str]:
    """Return up to 3 sample emails from the HubSpot KB for a domain (for reference/debugging)."""
    return _load_kb().get(domain, {}).get("samples", [])


def get_kb_pattern2(domain: str) -> str | None:
    """Return secondary pattern for domains with two active email formats, or None."""
    return _load_kb().get(domain, {}).get("pattern2")

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b")
_NAME_RE  = re.compile(r"\b([A-Z][a-z]{1,15})\s+([A-Z][a-z]{2,20})\b")
_HEADERS  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

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
    "/who-we-are",
    "/company",
    "/investor-relations",
    "/press",
    "/news",
]

# Map pattern strings from various sources to internal names
_PATTERN_MAP = {
    "first.last":     "first.last",
    "f.last":         "f.last",
    "first.l":        "first.l",
    "flast":          "flast",
    "firstlast":      "firstlast",
    "first":          "first",
    "last":           "last",
    "last.first":     "last.first",
    "lfirst":         "lfirst",
    "l.first":        "lfirst",
    "lastfirst":      "last.first",
    "first_last":     "first_last",
    "first-last":     "first-last",
    "lastf":          "lastf",
    "f_last":         "flast",
    # emailformat.com template tokens
    "{first}.{last}": "first.last",
    "{f}.{last}":     "f.last",
    "{first}.{l}":    "first.l",
    "{f}{last}":      "flast",
    "{first}{last}":  "firstlast",
    "{first}":        "first",
    "{last}":         "last",
    "{last}.{first}": "last.first",
    "{l}{first}":     "lfirst",
    "{last}{f}":      "lfirst",
    "{last}{first}":  "last.first",
}

# Role/generic addresses to exclude when looking for personal emails
_ROLE_LOCALS = {
    "noreply", "no-reply", "info", "contact", "admin", "support",
    "hello", "sales", "office", "mail", "team", "media", "press",
    "marketing", "hr", "jobs", "careers", "legal", "billing",
    "accounts", "general", "enquiries", "webmaster", "postmaster",
    "abuse", "security", "privacy", "newsletter", "service",
}


def detect_pattern(domain: str) -> tuple[str | None, str]:
    """
    Return (pattern, source). pattern is None when detection fails.
    source is 'hubspot', 'emailformat', 'scraped', 'ddg', or 'none'.
    """
    # 0. HubSpot knowledge base — real name+email pairs, most reliable
    kb = _load_kb()
    entry = kb.get(domain)
    if entry and entry.get("pattern"):
        pattern = entry["pattern"]
        logger.info("Pattern for %s: %s (HubSpot KB, confidence=%.0f%%, n=%d)",
                    domain, pattern, entry.get("confidence", 1) * 100, entry.get("sample_count", 1))
        return pattern, "hubspot"

    # 1. emailformat.com
    pattern = _emailformat_lookup(domain)
    if pattern:
        logger.info("Pattern for %s: %s (emailformat.com)", domain, pattern)
        return pattern, "emailformat"

    # 2. Company website — name+email pairs take priority over solo dot-emails
    for path in _SITE_PATHS:
        result = _scrape_page(f"https://{domain}{path}", domain)
        if result is None:
            continue
        email, name = result
        if name:
            p = _match_from_name(email, name)
            if p:
                logger.info("Pattern for %s: %s (scraped+name from %s, %s)", domain, p, path or "/", email)
                return p, "scraped"
        # No name — only use dot-pattern emails (reliable without name context)
        p = _infer_dot_pattern(email)
        if p:
            logger.info("Pattern for %s: %s (scraped dot-email from %s, %s)", domain, p, path or "/", email)
            return p, "scraped"

    # 3. DDG multi-query search
    result = _ddg_multi(domain)
    if result:
        email, name = result
        if name:
            p = _match_from_name(email, name)
            if p:
                logger.info("Pattern for %s: %s (DDG+name, %s)", domain, p, email)
                return p, "ddg"
        p = _infer_dot_pattern(email)
        if p:
            logger.info("Pattern for %s: %s (DDG dot-email, %s)", domain, p, email)
            return p, "ddg"

    # No reliable pattern found — SMTP format probe will try all 8 variations
    logger.info("No reliable pattern detected for %s — SMTP probe will try all formats", domain)
    return None, "none"


# ── emailformat.com ───────────────────────────────────────────────────────────

def _emailformat_lookup(domain: str) -> str | None:
    try:
        r = requests.get(f"https://www.emailformat.com/d/{domain}", headers=_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
        # Exact token match first
        for label, pattern in _PATTERN_MAP.items():
            if label.startswith("{") and label in text:
                return pattern
        # Percentage-ranked table: "74% {first}.{last}"
        hits = re.findall(r"(\d+)\s*%\s*(\{[^}]+\}(?:[._]?\{[^}]+\})?)", text)
        if hits:
            best = max(hits, key=lambda x: int(x[0]))
            return _PATTERN_MAP.get(best[1])
    except Exception as e:
        logger.debug("emailformat.com failed for %s: %s", domain, e)
    return None


# ── Website scraping ──────────────────────────────────────────────────────────

def _scrape_page(url: str, domain: str) -> tuple[str, str] | None:
    """
    Returns (email, name_or_empty_string) or None if no personal email found.
    Searches HTML elements for name+email proximity first, then falls back to
    a full-page scan for any personal email.
    """
    try:
        r = requests.get(url, headers=_HEADERS, timeout=8)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        html_text = r.text

        # Pass 1: look for name+email pairs in reasonably small elements
        for tag in soup.find_all(True):
            tag_text = tag.get_text(" ", strip=True)
            if not (30 < len(tag_text) < 800):
                continue
            emails = _EMAIL_RE.findall(tag_text)
            personal = [e for e in emails if _is_personal(e, domain)]
            if not personal:
                continue
            email = personal[0]
            names = _NAME_RE.findall(tag_text)
            if names:
                name = f"{names[0][0]} {names[0][1]}"
                return email, name

        # Pass 2: full-page scan for any personal email (no name context)
        all_emails = _EMAIL_RE.findall(html_text)
        personal = [e for e in all_emails if _is_personal(e, domain)]
        if personal:
            return personal[0], ""

    except Exception:
        pass
    return None


def _is_personal(email: str, domain: str) -> bool:
    local = email.split("@")[0].lower()
    return (
        email.lower().endswith(f"@{domain}")
        and "example" not in email
        and local not in _ROLE_LOCALS
    )


# ── DDG multi-strategy search ─────────────────────────────────────────────────

_DDG_QUERIES = [
    '"@{domain}"',
    '"{domain}" email contact staff',
    '"{domain}" "first" "last" email',
]

def _ddg_multi(domain: str) -> tuple[str, str] | None:
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            for tpl in _DDG_QUERIES:
                query = tpl.replace("{domain}", domain)
                try:
                    results = list(ddgs.text(query, max_results=8))
                except Exception:
                    continue
                for r in results:
                    text = r.get("body", "") + " " + r.get("title", "")
                    emails = _EMAIL_RE.findall(text)
                    personal = [e for e in emails if _is_personal(e, domain)]
                    if not personal:
                        continue
                    email = personal[0]
                    names = _NAME_RE.findall(text)
                    name = f"{names[0][0]} {names[0][1]}" if names else ""
                    return email, name
                time.sleep(1)
    except Exception as e:
        logger.warning("DDG search error: %s", e)
    return None


# ── Pattern matching ──────────────────────────────────────────────────────────

def _match_from_name(email: str, name: str) -> str | None:
    """
    Definitively determine pattern when we have both the email AND the person's name.
    Returns pattern string or None if no template matches.
    """
    parts = name.split()
    if len(parts) < 2:
        return None
    first = parts[0].lower()
    last  = parts[-1].lower()
    fi    = first[0] if first else ""
    li    = last[0]  if last  else ""
    local = re.sub(r"[^a-z.]", "", email.split("@")[0].lower())

    mapping = {
        f"{first}.{last}": "first.last",
        f"{fi}.{last}":    "f.last",
        f"{first}.{li}":   "first.l",
        f"{fi}{last}":     "flast",
        f"{first}{last}":  "firstlast",
        f"{first}":        "first",
        f"{last}.{first}": "last.first",
        f"{li}{first}":    "lfirst",
        f"{first}_{last}": "first_last",
        f"{first}-{last}": "first-last",
        f"{last}{fi}":     "lastf",
        f"{last}":         "last",
    }
    return mapping.get(local)


def _infer_dot_pattern(email: str) -> str | None:
    """
    Infer pattern from a dot-containing email local part.
    Dot-pattern emails are unambiguous — no name context needed.
    Returns None for no-dot emails (those require name context or SMTP probe).
    """
    local = re.sub(r"[^a-z.]", "", email.split("@")[0].lower())
    if "." not in local:
        return None  # cannot reliably determine without name context
    parts = local.split(".")
    if len(parts) == 2:
        if len(parts[0]) == 1:
            return "f.last"
        if len(parts[-1]) == 1:
            return "first.l"
        return "first.last"
    # 3+ parts: first.middle.last or similar — treat as first.last
    return "first.last"
