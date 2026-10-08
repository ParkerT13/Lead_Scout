"""
Detect a company's email format (pattern). All sources free, no API keys.

Order of trust (see detect_pattern_ex):
  0. HubSpot knowledge base - patterns derived from real contacts in our CRM
  1. Company website        - emailer.site_harvester crawl; published name+email pairs are proof
  2. emailformat.com        - crowdsourced format database
  3. Search snippets        - every published @domain address, same voting
"""

import json
import re
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_KB_PATH        = Path(__file__).parent / "hubspot_knowledge.json"
_KB_PUBLIC_PATH = Path(__file__).parent / "hubspot_knowledge_public.json"
_KB: dict | None = None

def _load_kb() -> dict:
    global _KB
    if _KB is None:
        try:
            # Personal KB (with real samples) takes priority; fall back to shared public KB
            if _KB_PATH.exists():
                _KB = json.loads(_KB_PATH.read_text(encoding="utf-8"))
            elif _KB_PUBLIC_PATH.exists():
                _KB = json.loads(_KB_PUBLIC_PATH.read_text(encoding="utf-8"))
            else:
                _KB = {}
        except Exception:
            _KB = {}
    return _KB


def get_kb_entry(domain: str) -> dict | None:
    return _load_kb().get(domain)


def get_kb_pattern2(domain: str) -> str | None:
    """Return secondary pattern for domains with two active email formats, or None."""
    return _load_kb().get(domain, {}).get("pattern2")

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


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


@dataclass
class Detection:
    pattern: str | None = None
    source: str = "none"
    pattern2: str | None = None
    evidence: int = 0                      # distinct site emails supporting the pattern
    findings: list = field(default_factory=list)   # site_harvester.Finding objects


def detect_pattern_ex(domain: str, findings: list | None = None) -> Detection:
    """
    Full detection. Order of trust:
      0. HubSpot KB                   (real contacts from our own CRM)
      1. Website name+email pairs     (published proof; multi-page, deobfuscated)
      2. emailformat.com              (crowdsourced)
      3. Website dot-style hints      (j.smith@ with no name)
      4. DDG snippets
    `findings` may be passed in (e.g. from cache) to skip the crawl.
    """
    from emailer import site_harvester as sh

    kb = _load_kb()
    entry = kb.get(domain)

    if findings is None:
        try:
            findings = sh.harvest(domain)
        except Exception as e:
            logger.warning("Site harvest failed for %s: %s", domain, e)
            findings = []
    site = sh.infer_patterns(findings)

    if entry and entry.get("pattern"):
        logger.info("Pattern for %s: %s (HubSpot KB, confidence=%.0f%%, n=%d)",
                    domain, entry["pattern"], entry.get("confidence", 1) * 100,
                    entry.get("sample_count", 1))
        return Detection(entry["pattern"], "hubspot", entry.get("pattern2") or site.pattern2,
                         site.support if site.pattern == entry["pattern"] else 0, findings)

    if site.strong:
        logger.info("Pattern for %s: %s (website, %d email(s): %s)",
                    domain, site.pattern, site.support, ", ".join(site.samples))
        return Detection(site.pattern, "scraped", site.pattern2, site.support, findings)

    pattern = _emailformat_lookup(domain)
    if pattern:
        logger.info("Pattern for %s: %s (emailformat.com)", domain, pattern)
        return Detection(pattern, "emailformat", site.pattern2, 0, findings)

    if site.pattern:   # weak: dot-style hint only
        logger.info("Pattern for %s: %s (website dot-hint)", domain, site.pattern)
        return Detection(site.pattern, "scraped", site.pattern2, site.support, findings)

    # Search-engine snippets: collect *all* published addresses and vote
    extra = _ddg_findings(domain)
    if extra:
        merged = {f.email: f for f in findings}
        for f in extra:
            if f.email not in merged or (not merged[f.email].name and f.name):
                merged[f.email] = f
        findings = list(merged.values())
        res = sh.infer_patterns(findings)
        if res.pattern:
            logger.info("Pattern for %s: %s (search, %d email(s): %s)",
                        domain, res.pattern, res.support, ", ".join(res.samples))
            return Detection(res.pattern, "ddg", res.pattern2, res.support, findings)

    logger.info("No reliable pattern detected for %s", domain)
    return Detection(None, "none", None, 0, findings)


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


_DDG_FINDING_QUERIES = [
    '"@{domain}"',
    '"@{domain}" geologist OR geophysicist OR engineer OR landman',
    '"@{domain}" filetype:pdf',
    '"{domain}" email contact staff',
]


def _ddg_findings(domain: str) -> list:
    """Every address @domain visible in search snippets, with nearby names."""
    from emailer import site_harvester as sh
    out: dict = {}
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            for tpl in _DDG_FINDING_QUERIES:
                try:
                    results = list(ddgs.text(tpl.replace("{domain}", domain), max_results=10))
                except Exception:
                    continue
                for r in results:
                    text = r.get("title", "") + " . " + r.get("body", "")
                    for f in sh.extract_from_text(text, domain, r.get("href", "")):
                        if f.email not in out or (not out[f.email].name and f.name):
                            out[f.email] = f
                time.sleep(1)
    except Exception as e:
        logger.warning("DDG search error: %s", e)
    return list(out.values())


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
