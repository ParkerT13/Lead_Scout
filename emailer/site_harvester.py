"""
Harvest real email addresses from a company's own website.

Ported/adapted from the FORGE (Nuclear-Marmalade/dataforge) email extraction
layers, rewritten to be synchronous (requests + BeautifulSoup, both already
Lead Scout dependencies) and to keep name context next to each address.

Extraction layers (per page):
  1. mailto: links (anchor text / surrounding card gives the person's name)
  2. Plain-text regex scan
  3. Cloudflare data-cfemail / email-protection decoding
  4. HTML-entity and [at]/(at)/[dot] deobfuscation
  5. JSON-LD (schema.org Person/Organization name + email)
  6. Footer + HTML comments

Why this matters for Lead Scout: a published "John Smith  jsmith@co.com" pair
is *proof* of the company's email format, and a published address that matches
one of our own contacts is a real, deliverable address (no guessing).

Public API:
    extract_from_html(html, domain)   -> list[Finding]     (pure, testable)
    harvest(domain)                   -> list[Finding]     (network)
    infer_patterns(findings)          -> PatternResult
    find_contact_email(findings, first, last) -> str | None
"""

from __future__ import annotations

import html as html_module
import json
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from emailer.generator import PATTERNS, apply_pattern, clean_name, first_name_variants

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)*\.[a-zA-Z]{2,}")
_OBFUSCATED_RE = re.compile(
    r"([a-zA-Z0-9._%+\-]+)\s*[\[\(\{]\s*at\s*[\]\)\}]\s*"
    r"([a-zA-Z0-9\-]+(?:\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*[a-zA-Z0-9\-]+)+)",
    re.IGNORECASE,
)
_DOT_RE = re.compile(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", re.IGNORECASE)
_CF_ATTR_RE = re.compile(r'data-cfemail="([a-fA-F0-9]+)"')
_CF_HREF_RE = re.compile(r"/cdn-cgi/l/email-protection#([a-fA-F0-9]+)")
_NAME_RE = re.compile(
    r"\b([A-Z][a-z]{1,15}(?:[-'][A-Z][a-z]{1,15})?)\s+(?:[A-Z]\.?\s+)?"
    r"([A-Z][a-z]{2,20}(?:[-'][A-Z][a-z]{2,20})?)\b"
)

# Words that look like a "First Last" capitalised pair but are not people
_NAME_STOP = {
    "contact", "email", "send", "our", "meet", "business", "houston", "oil", "gas",
    "energy", "corporate", "office", "phone", "fax", "main", "customer", "investor",
    "media", "press", "human", "general", "head", "north", "south", "east", "west",
    "new", "united", "texas", "denver", "dallas", "midland", "oklahoma", "privacy",
    "terms", "read", "learn", "more", "view", "follow", "join", "apply", "get",
    "sales", "support", "info", "service", "services", "operations", "exploration",
    "production", "drilling", "board", "executive", "senior", "vice", "chief",
    "president", "director", "manager", "engineer", "geologist", "geophysicist",
    "copyright", "all", "rights", "the", "and", "for", "with", "inc", "llc",
}

_ROLE_LOCALS = {
    "noreply", "no-reply", "donotreply", "info", "contact", "admin", "support",
    "hello", "sales", "office", "mail", "team", "media", "press", "marketing",
    "hr", "jobs", "careers", "legal", "billing", "accounts", "general",
    "enquiries", "inquiries", "webmaster", "postmaster", "abuse", "security",
    "privacy", "newsletter", "service", "ir", "investors", "investorrelations",
    "recruiting", "recruitment", "payables", "accounting", "reception", "help",
    "compliance", "ethics", "safety", "land", "operations", "ap", "ar",
}

_FAKE_DOMAINS = {
    "example.com", "email.com", "domain.com", "yoursite.com", "yourdomain.com",
    "company.com", "test.com", "placeholder.com", "sentry.io", "wixpress.com",
    "wordpress.org", "w3.org", "schema.org", "googleapis.com", "gravatar.com",
    "wp.com", "sentry-next.wixpress.com", "changedetection.io",
}

_FIXED_PATHS = [
    "/contact", "/contact-us", "/about", "/about-us", "/team", "/our-team",
    "/leadership", "/management", "/people", "/staff", "/about/team",
    "/about/leadership", "/team-members", "/who-we-are", "/company",
    "/directors", "/officers", "/executive-team", "/our-people",
]

_LINK_HINT_RE = re.compile(
    r"team|people|leader|manage|staff|about|contact|director|officer|executive|"
    r"who-we-are|our-company|bios?\b|professionals|personnel",
    re.IGNORECASE,
)

_MAX_BODY = 3 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    email: str
    name: str = ""          # "First Last" if a person's name was found next to it
    url: str = ""
    kind: str = "text"      # mailto | text | cfemail | obfuscated | jsonld | comment


@dataclass
class PatternResult:
    pattern: str | None = None
    pattern2: str | None = None
    score: int = 0                  # weighted evidence for `pattern`
    support: int = 0                # distinct emails supporting `pattern`
    samples: list[str] = field(default_factory=list)

    @property
    def strong(self) -> bool:
        """True when at least one name+email pair proves the format."""
        return self.pattern is not None and self.score >= 3


# ── Pure extraction ───────────────────────────────────────────────────────────

def decode_cf_email(encoded: str) -> str | None:
    """Decode Cloudflare's data-cfemail obfuscation."""
    try:
        key = int(encoded[:2], 16)
        out = "".join(chr(int(encoded[i:i + 2], 16) ^ key) for i in range(2, len(encoded), 2))
    except (ValueError, IndexError):
        return None
    if "@" in out and "." in out.split("@")[-1]:
        return out.lower()
    return None


def _domain_ok(email: str, domain: str | None) -> bool:
    host = email.rsplit("@", 1)[-1]
    if host in _FAKE_DOMAINS or "." not in host or len(email) > 254:
        return False
    if email.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".webp")):
        return False
    if domain:
        d = domain.lower()
        return host == d or host.endswith("." + d)
    return True


def _is_personal(email: str) -> bool:
    return email.split("@")[0].lower() not in _ROLE_LOCALS


def _find_names(text: str) -> list[str]:
    names = []
    for m in _NAME_RE.finditer(text):
        first, last = m.group(1), m.group(2)
        if first.lower() in _NAME_STOP or last.lower() in _NAME_STOP:
            continue
        names.append(f"{first} {last}")
    return names


def _best_name_for(email: str, names: list[str]) -> str:
    """Pick the candidate name whose tokens best explain the email local part."""
    local = re.sub(r"[^a-z]", "", email.split("@")[0].lower())
    if not names:
        return ""
    for n in names:
        first, _, last = n.partition(" ")
        f, l = clean_name(first), clean_name(last)
        if f and l and (l in local or (f in local) or local.startswith(f[:1] + l[:3])):
            return n
    return names[0]


def _normalize_email(raw: str) -> str:
    return raw.strip().strip(".,;:<>()[]\"'").lower()


def extract_from_html(html: str, domain: str | None = None, url: str = "") -> list[Finding]:
    """Extract every email (with name context where possible) from one page."""
    if not html:
        return []
    found: dict[str, Finding] = {}

    def add(email: str, name: str, kind: str):
        email = _normalize_email(email)
        if not _EMAIL_RE.fullmatch(email) or not _domain_ok(email, domain):
            return
        if not _is_personal(email):
            name = ""           # "Fannin Street" next to info@ is not a person
        prev = found.get(email)
        # Prefer a finding that carries a name
        if prev is None or (not prev.name and name):
            found[email] = Finding(email, name, url, kind)

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # Layer 5: JSON-LD (name + email in the same object)
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        try:
            _walk_jsonld(json.loads(script.string or script.get_text() or ""), add)
        except (ValueError, TypeError):
            continue

    # Layer 3: Cloudflare obfuscation (attribute + href form)
    for tag in soup.find_all(True):
        enc = tag.get("data-cfemail")
        href = tag.get("href", "") if tag.name == "a" else ""
        if not enc and href:
            m = _CF_HREF_RE.search(href)
            enc = m.group(1) if m else None
        if enc:
            email = decode_cf_email(enc)
            if email:
                add(email, _name_from_context(tag, email), "cfemail")

    # Layer 1: mailto links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = html_module.unescape(href[7:]).split("?")[0].split(",")[0]
            add(addr, _name_from_context(a, addr), "mailto")

    # Layers 2/4/6: text, entities, [at] obfuscation, comments, footer
    for tag in soup.find_all(True):
        if tag.name in ("script", "style", "noscript"):
            continue
        # Only act on "leaf-ish" containers so the name stays close to the email
        own_text = tag.get_text(" ", strip=True)
        if not own_text or len(own_text) > 700 or "@" not in own_text and "[at]" not in own_text.lower() \
                and "(at)" not in own_text.lower():
            continue
        for email in _emails_in_text(own_text):
            add(email, _best_name_for(email, _find_names(own_text)), "text")

    flat = soup.get_text(" ", strip=True)
    for email in _emails_in_text(flat):
        add(email, "", "text")
    for email in _emails_in_text(html_module.unescape(html)):
        add(email, "", "text")

    from bs4 import Comment
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        for email in _EMAIL_RE.findall(str(c)):
            add(email, "", "comment")

    return list(found.values())


def extract_from_text(text: str, domain: str | None = None, url: str = "",
                      kind: str = "search") -> list[Finding]:
    """Plain-text variant (search-result snippets). Name = best capitalised pair nearby."""
    out: dict[str, Finding] = {}
    names = _find_names(text)
    for raw in _emails_in_text(text):
        email = _normalize_email(raw)
        if _EMAIL_RE.fullmatch(email) and _domain_ok(email, domain) and email not in out:
            name = _best_name_for(email, names) if _is_personal(email) else ""
            out[email] = Finding(email, name, url, kind)
    return list(out.values())


def _emails_in_text(text: str) -> list[str]:
    out = [m.group(0) for m in _EMAIL_RE.finditer(text)]
    for m in _OBFUSCATED_RE.finditer(text):
        host = _DOT_RE.sub(".", m.group(2)).replace(" ", "")
        out.append(f"{m.group(1)}@{host}")
    return out


def _name_from_context(tag, email: str) -> str:
    """Find a person's name near a mailto/cf-email element."""
    text = tag.get_text(" ", strip=True)
    names = _find_names(text) if text and "@" not in text else []
    node = tag
    for _ in range(4):                      # climb a few levels (card/list item/row)
        if names:
            break
        node = node.parent
        if node is None or node.name in ("body", "html"):
            break
        ctx = node.get_text(" ", strip=True)
        if len(ctx) > 500:
            break
        names = _find_names(ctx)
    return _best_name_for(email, names)


def _walk_jsonld(data, add):
    if isinstance(data, dict):
        email = data.get("email")
        if isinstance(email, str):
            name = data.get("name") if isinstance(data.get("name"), str) else ""
            add(email.replace("mailto:", ""), name if len(name.split()) >= 2 else "", "jsonld")
        for v in data.values():
            _walk_jsonld(v, add)
    elif isinstance(data, list):
        for item in data:
            _walk_jsonld(item, add)


# ── Pattern inference ─────────────────────────────────────────────────────────

def _patterns_for(local: str, first: str, last: str) -> list[str]:
    f, l = clean_name(first), clean_name(last)
    if not f or not l:
        return []
    hits = []
    for pat in PATTERNS:
        cand = apply_pattern(pat, f, l)
        if cand and cand == local:
            hits.append(pat)
    return hits


def infer_patterns(findings: list[Finding]) -> PatternResult:
    """
    Weighted vote across every finding.

    name + email pair that matches exactly one template   -> weight 3 (proof)
    dot-style local part with no name (j.smith, john.smith) -> weight 1 (hint)
    """
    score: dict[str, int] = defaultdict(int)
    emails: dict[str, set[str]] = defaultdict(set)

    for fnd in findings:
        if not _is_personal(fnd.email):
            continue
        local = fnd.email.split("@")[0]
        if fnd.name:
            first, _, last = fnd.name.partition(" ")
            hits = _patterns_for(local.lower(), first, last.split()[-1] if last else "")
            if hits:
                # Ambiguous single-token matches (first vs last) still count but less
                w = 3 if len(hits) == 1 else 2
                for p in hits:
                    score[p] += w
                    emails[p].add(fnd.email)
                continue
        # No name (or name did not explain the address): only dot forms are unambiguous
        stripped = re.sub(r"[^a-z.]", "", local.lower())
        parts = stripped.split(".")
        if len(parts) == 2 and all(parts):
            p = "f.last" if len(parts[0]) == 1 else ("first.l" if len(parts[1]) == 1 else "first.last")
            score[p] += 1
            emails[p].add(fnd.email)

    if not score:
        return PatternResult()

    ranked = sorted(score, key=lambda p: (score[p], len(emails[p])), reverse=True)
    top = ranked[0]
    result = PatternResult(top, None, score[top], len(emails[top]), sorted(emails[top])[:3])
    for other in ranked[1:]:
        if len(emails[other]) >= 2 and score[other] >= 3:
            result.pattern2 = other
            break
    return result


def find_contact_email(findings: list[Finding], first: str, last: str,
                       domain: str | None = None) -> str | None:
    """Return a published address that belongs to this contact, if any."""
    last_c = clean_name(last)
    if not last_c or not clean_name(first):
        return None
    for fnd in findings:
        if not _is_personal(fnd.email):
            continue
        if domain and not _domain_ok(fnd.email, domain):
            continue
        local = fnd.email.split("@")[0].lower()
        for fv in first_name_variants(first):
            if _patterns_for(local, fv, last):
                # Reject the weak single-token forms unless a published name backs them up
                if local in (clean_name(fv), last_c) and not _name_matches(fnd.name, fv, last):
                    continue
                return fnd.email
    return None


def _name_matches(published: str, first: str, last: str) -> bool:
    if not published:
        return False
    pf, _, pl = published.partition(" ")
    return clean_name(pl.split()[-1] if pl else "") == clean_name(last) and \
        clean_name(pf) in {clean_name(v) for v in first_name_variants(first)}


# ── Network ───────────────────────────────────────────────────────────────────

def _get(session: requests.Session, url: str, timeout: float) -> requests.Response | None:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
        if r.status_code != 200:
            return None
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "xml" not in ctype and ctype:
            return None
        r._content = r.raw.read(_MAX_BODY, decode_content=True)   # cap body size
        r._content_consumed = True
        return r
    except Exception:
        return None


def _same_site(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def _discover_links(html: str, base_url: str, domain: str) -> list[str]:
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0]
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url, href)
        if not _same_site(full, domain):
            continue
        if _LINK_HINT_RE.search(urlparse(full).path) or _LINK_HINT_RE.search(a.get_text(" ", strip=True)):
            if full not in links:
                links.append(full)
    return links


def _sitemap_links(session: requests.Session, base: str, domain: str, timeout: float) -> list[str]:
    r = _get(session, urljoin(base, "/sitemap.xml"), timeout)
    if r is None:
        return []
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
    return [u for u in urls if _same_site(u, domain) and _LINK_HINT_RE.search(urlparse(u).path)
            and not u.endswith(".xml")][:8]


def harvest(domain: str, max_pages: int = 18, workers: int = 4,
            timeout: float = 8.0, deadline: float = 40.0) -> list[Finding]:
    """Crawl the company's contact/team/about pages and return all findings."""
    domain = domain.lower().strip()
    session = requests.Session()
    session.headers.update(_HEADERS)

    home = None
    for base in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
        home = _get(session, base + "/", timeout)
        if home is not None:
            break
    if home is None:
        logger.info("Harvest: %s unreachable", domain)
        return []

    base = f"{urlparse(home.url).scheme}://{urlparse(home.url).hostname}"
    findings: dict[str, Finding] = {}

    def merge(items: list[Finding]):
        for f in items:
            prev = findings.get(f.email)
            if prev is None or (not prev.name and f.name):
                findings[f.email] = f

    merge(extract_from_html(home.text, domain, home.url))

    urls: list[str] = []
    for u in _discover_links(home.text, home.url, domain) \
            + [urljoin(base, p) for p in _FIXED_PATHS] \
            + _sitemap_links(session, base, domain, timeout):
        u = u.rstrip("/")
        if u not in urls and u != base and u != home.url.rstrip("/"):
            urls.append(u)
    urls = urls[:max_pages]

    def fetch(u: str) -> list[Finding]:
        r = _get(session, u, timeout)
        return extract_from_html(r.text, domain, r.url) if r is not None else []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, u) for u in urls]
        done, not_done = wait(futures, timeout=deadline)
        for fut in done:
            try:
                merge(fut.result())
            except Exception:
                pass
        for fut in not_done:
            fut.cancel()

    result = list(findings.values())
    logger.info("Harvest %s: %d page(s), %d address(es), %d with names",
                domain, len(urls) + 1, len(result), sum(1 for f in result if f.name))
    return result


def findings_to_json(findings: list[Finding]) -> list[dict]:
    return [{"email": f.email, "name": f.name, "url": f.url, "kind": f.kind} for f in findings]


def findings_from_json(rows: list[dict]) -> list[Finding]:
    return [Finding(r["email"], r.get("name", ""), r.get("url", ""), r.get("kind", "text"))
            for r in rows if r.get("email")]
