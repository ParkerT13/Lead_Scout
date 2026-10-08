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
      3. DDG snippets
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

    # Search-engine snippets: collect *all* published addresses and vote
    extra = _ddg_findings(domain)
    if extra:
        merged = {f.email: f for f in findings}
        for f in extra:
            if f.email not in merged or (not merged[f.email].name and f.name):
                merged[f.email] = f
        findings = list(merged.values())
        res = sh.infer_patterns(findings)
        if res.strong:      # weak dot-style hints were right only ~1 in 3 in testing: ignore them
            logger.info("Pattern for %s: %s (search, %d email(s): %s)",
                        domain, res.pattern, res.support, ", ".join(res.samples))
            return Detection(res.pattern, "ddg", res.pattern2, res.support, findings)

    logger.info("No reliable pattern detected for %s", domain)
    return Detection(None, "none", None, 0, findings)


# ── emailformat.com ───────────────────────────────────────────────────────────

# email-format.com descriptor tokens -> our token names
_TOKENS = {"first_name": "first", "last_name": "last", "first_initial": "f", "last_initial": "l"}

# (token tuple, separator in the example address) -> pattern
_FORMAT_TABLE = {
    (("first", "last"), "."): "first.last", (("first", "last"), "_"): "first_last",
    (("first", "last"), "-"): "first-last", (("first", "last"), ""): "firstlast",
    (("f", "last"), ""): "flast", (("f", "last"), "."): "f.last",
    (("first", "l"), "."): "first.l",
    (("first",), ""): "first", (("last",), ""): "last",
    (("last", "first"), "."): "last.first", (("last", "f"), ""): "lastf",
    (("l", "first"), ""): "lfirst",
}

_FORMAT_MIN_PCT = 60      # below this the site is not decisive (50-60% shares proved wrong in testing)


def _emailformat_lookup(domain: str) -> str | None:
    """Most common format per email-format.com (needs >=50% share and a decodable example)."""
    from emailer.site_harvester import decode_cf_email
    try:
        r = requests.get(f"https://www.email-format.com/d/{domain}/", headers=_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        best = None
        for box in soup.select("div.format_score_container"):
            fmt = box.select_one("div.format")
            pct = box.select_one("div.confidence_value")
            if not fmt or not pct:
                continue
            m = re.search(r"(\d+)", pct.get_text())
            tokens = tuple(_TOKENS.get(t) for t in fmt.get_text(" ", strip=True).split())
            if not m or None in tokens or not tokens:
                continue
            example = box.select_one("[data-cfemail]")
            local = (decode_cf_email(example["data-cfemail"]) or "").split("@")[0] if example else ""
            sep = next((c for c in local if c in "._-"), "")
            pattern = _FORMAT_TABLE.get((tokens, sep))
            if pattern and (best is None or int(m.group(1)) > best[0]):
                best = (int(m.group(1)), pattern)
        if best and best[0] >= _FORMAT_MIN_PCT:
            return best[1]
    except Exception as e:
        logger.debug("email-format.com failed for %s: %s", domain, e)
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


_COMPANY_MAIL_QUERIES = ['"{company}" email "@"', '"{company}" contact email address']


def ddg_mail_domain(company: str, current_domain: str = "") -> str:
    """
    When a company's website reveals no addresses, ask the search engine which domain real
    @addresses for this company use (e.g. Scout Energy Partners -> scoutep.com).
    Returns "" unless >=2 distinct addresses on one non-free domain AND the domain name
    contains a distinctive word of the company name.
    """
    import re
    from collections import defaultdict
    from emailer import site_harvester as sh
    words = [w for w in re.findall(r"[a-z0-9]+", company.lower())
             if len(w) >= 4 and w not in ("energy", "resources", "company", "partners", "operating",
                                            "petroleum", "holdings", "group", "limited", "corporation")]
    if not words:
        return ""
    seen: dict[str, set] = defaultdict(set)
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            for tpl in _COMPANY_MAIL_QUERIES:
                try:
                    results = list(ddgs.text(tpl.replace("{company}", company), max_results=10))
                except Exception:
                    continue
                for r in results:
                    text = r.get("title", "") + " . " + r.get("body", "")
                    for f in sh.extract_from_text(text, None, r.get("href", "")):
                        host = f.email.rsplit("@", 1)[-1]
                        if host not in sh._FREE_MAIL:
                            seen[host].add(f.email)
                time.sleep(1)
    except Exception as e:
        logger.warning("Company mail-domain search error: %s", e)
        return ""
    for host, addrs in sorted(seen.items(), key=lambda kv: -len(kv[1])):
        label = re.sub(r"[^a-z0-9]", "", host.split(".")[0])
        if len(addrs) >= 2 and any(w in label for w in words) and host != current_domain:
            return host
    return ""
