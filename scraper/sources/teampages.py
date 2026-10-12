"""
Contacts from a company's own team / leadership / people pages.

Reuses the email harvester's HTTP and link-discovery helpers. Each page is scanned for
"card" structures: a person's name next to a job title, in the layouts websites commonly use
(heading + subtitle, name | title on one line, <li> lists, table rows).

A name only counts when a job-title word sits right next to it, so navigation text such as
"Contact Us" or "Our Team" can never become a contact.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, wait
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from emailer import site_harvester as sh
from scraper.people import make_contact

logger = logging.getLogger(__name__)

# One capitalised word each; allows O'Brien, Smith-Jones, McDonald, middle initial, Jr./III suffix
_WORD = r"[A-Z][a-zA-Z'’\-]{1,20}"
_FULL_NAME_RE = re.compile(
    rf"^(?:Dr\.?\s+)?({_WORD})(?:\s+[A-Z]\.?)?(?:\s+(?:{_WORD}))?\s+({_WORD})(?:,?\s+(?:Jr|Sr|II|III|IV)\.?)?"
    r"(?:,\s*[A-Z][A-Za-z.]{1,6}(?:,\s*[A-Z][A-Za-z.]{1,6})*)?$"
)

_BAD_WORDS = {
    "contact", "us", "our", "team", "about", "meet", "read", "more", "learn", "view", "home",
    "news", "careers", "career", "privacy", "policy", "terms", "investors", "investor", "relations",
    "leadership", "management", "board", "directors", "executive", "executives", "officers",
    "oil", "gas", "energy", "resources", "company", "corporation", "partners", "operating",
    "services", "solutions", "texas", "houston", "denver", "dallas", "oklahoma", "midland",
    "permian", "basin", "headquarters", "office", "phone", "email", "address", "street",
    "avenue", "suite", "follow", "share", "press", "media", "release", "annual", "report",
    "sustainability", "responsibility", "values", "mission", "vision", "history", "overview",
    "welcome", "find", "get", "all", "see", "join", "apply", "back", "next", "previous",
    "copyright", "rights", "reserved", "website", "site", "map", "directions", "login",
}

# Words that make a short text look like a job title (company-agnostic)
_TITLE_HINT_RE = re.compile(
    r"\b(president|vice president|vp|svp|evp|chief|ceo|cfo|coo|cto|cio|director|manager|"
    r"engineer\w*|geolog\w*|geophys\w*|geoscien\w*|petrophys\w*|landman|land|analyst|supervisor|"
    r"superintendent|specialist|advisor|principal|partner|founder|owner|controller|treasurer|"
    r"counsel|technician|coordinator|operations|exploration|production|reservoir|drilling|"
    r"completions?|development|geomatics|surveyor|consultant|associate|secretary|chairman|"
    r"officer|head of|lead|senior|staff|administrator|accountant|buyer|foreman|pumper)\b",
    re.IGNORECASE,
)

_SEPARATORS = re.compile(r"\s*[–—|\-:,/]\s*|\s{2,}")


def _titlecase(word: str) -> str:
    """KYLE -> Kyle, but leave McDonald / O'Brien alone."""
    return word.capitalize() if word.isupper() else word


def _clean_title(title: str) -> str:
    """Normalise whitespace; repair letter-spaced text from some builders ('Foun d er' -> 'Founder')."""
    t = re.sub(r"\s+", " ", title.replace("�", "")).strip(" ,;|-–—")
    ok = {"of", "to", "in", "on", "at", "an", "as", "or", "by", "vp", "and", "&"}
    tokens = t.split(" ")
    frag = [i for i, w in enumerate(tokens) if i > 0 and w.islower() and len(w) <= 2 and w not in ok]
    if len(frag) >= 2:                       # glue each stray fragment onto the word before it
        out: list[str] = []
        for i, w in enumerate(tokens):
            if i in frag and out:
                out[-1] += w
            else:
                out.append(w)
        t = " ".join(out)
    return t


def _name_from(text: str) -> tuple[str, str] | None:
    """('First', 'Last') if the text is shaped like a person's name, else None."""
    text = text.strip()
    if not (4 <= len(text) <= 48) or any(ch.isdigit() for ch in text) or "@" in text:
        return None
    m = _FULL_NAME_RE.match(text)
    if not m:
        return None
    words = re.findall(r"[A-Za-z'’\-]+", text.split(",")[0])
    if any(w.lower().strip(".") in _BAD_WORDS for w in words):
        return None
    if _TITLE_HINT_RE.search(text.split(",")[0]):         # "Senior Geologist" is a title, not a name
        return None
    return m.group(1), m.group(2)


def _looks_like_title(text: str) -> bool:
    t = text.strip()
    return 3 <= len(t) <= 110 and bool(_TITLE_HINT_RE.search(t)) and "@" not in t \
        and not any(w in t.lower() for w in ("read more", "view profile", "learn more", "click here"))


_ROLE_VERBS = (r"(?:currently\s+|also\s+|now\s+)?(?:has\s+served\s+as|serves\s+as|serves|is|was\s+appointed|"
               r"was\s+named|was\s+elected|holds\s+the\s+position\s+of|joined\s+[^.]{0,60}?\s+as|"
               r"has\s+been|became|works\s+as|leads\s+(?:our|the)\s+\w+\s+as)")
_MONTHS = r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
_TITLE_CUT = re.compile(rf"\s+(?:of\s+(?:the\s+)?(?:company|our)|since|in\s+(?:{_MONTHS}|\d{{4}})|upon|prior\s+to|"
                        r"and\s+was|and\s+has|where|with\s+over|with\s+more|following|effective|\(|-\s+he|and\s+he)",
                        re.IGNORECASE)


def title_from_sentence(text: str, first: str, last: str) -> str:
    """'Bobby Riley holds the position of President and CEO of ...' -> 'President and CEO'."""
    pat = re.compile(rf"\b{re.escape(first)}\b[^.]{{0,40}}?\b{re.escape(last)}\b[,\s]+{_ROLE_VERBS}\s+"
                     rf"(?:the\s+|our\s+|a\s+|an\s+)?(?P<t>[^.;]{{4,110}})", re.IGNORECASE)
    m = pat.search(text[:600])
    if not m:
        return ""
    t = _TITLE_CUT.split(m.group("t"))[0].strip(" ,")
    t = re.sub(r"^as\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^(?:[A-Z][\w&.]*\s+){0,3}[A-Z][\w&.]*['’�]s\s+", "", t)     # "Riley Permian's Chief ..."
    return t if _looks_like_title(t) else ""


def _title_near(el) -> str:
    """Job title belonging to the name element `el`: next siblings first, then the rest of its card."""
    name_text = el.get_text(" ", strip=True)
    # 1) following siblings (subtitle directly under the heading)
    seen = 0
    for sib in el.next_siblings:
        txt = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib).strip()
        if not txt:
            continue
        seen += 1
        words = re.findall(r"[A-Za-z'’\-]+", name_text.split(",")[0])
        if len(words) >= 2 and len(txt) > 40:            # biography sentence under the heading
            t = title_from_sentence(txt, words[0], words[-1])
            if t:
                return t
        if _looks_like_title(txt):
            return txt
        if seen >= 3:
            break
    # 1b) accordion / tab layouts: the name is a button or heading and the biography sits in a
    #     neighbouring panel. Only a sentence naming the same person is accepted here, so another
    #     person's title can never be borrowed.
    words = re.findall(r"[A-Za-z'’\-]+", name_text.split(",")[0])
    if len(words) >= 2:
        node = el
        for _ in range(5):
            hops = 0
            for sib in node.next_siblings:
                txt = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else ""
                if not txt:
                    continue
                t = title_from_sentence(txt, words[0], words[-1])
                if t:
                    return t
                hops += 1
                if hops >= 2:
                    break
            node = node.parent
            if node is None or node.name in ("body", "html"):
                break

    # 2) the enclosing card, with the name removed
    node = el.parent
    for _ in range(3):
        if node is None or node.name in ("body", "html"):
            break
        card = node.get_text("\n", strip=True)
        if len(card) > 400:
            break
        lines = [ln.strip() for ln in card.replace(name_text, "\n").split("\n") if ln.strip()]
        for ln in lines:
            if _looks_like_title(ln):
                return ln
        node = node.parent
    return ""


_COMPANY_FILLER = {"the", "inc", "llc", "lp", "corp", "company", "corporation", "co", "ltd"}


def _strip_company(title: str, company: str) -> str:
    """'President and CEO of Riley Permian' -> 'President and CEO' (the employer is already known)."""
    if not company:
        return title
    m = re.search(r"\s+(?:of|at|for|with)\s+([A-Za-z&.' -]+)$", title)
    if not m:
        return title
    words = {w for w in re.findall(r"[a-z0-9]+", m.group(1).lower())} - _COMPANY_FILLER
    comp = set(re.findall(r"[a-z0-9]+", company.lower())) | _COMPANY_FILLER
    return title[:m.start()].strip() if words and words <= comp else title


def extract_people(html: str, url: str = "", company: str = "") -> list[tuple[str, str]]:
    """[(full name, job title)] found on one page."""
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):   # NOT form: ASP.NET wraps whole pages in one
        tag.decompose()

    found: dict[str, tuple[str, str]] = {}

    def add(first: str, last: str, title: str):
        first, last = _titlecase(first), _titlecase(last)
        key = f"{first.lower()} {last.lower()}"
        title = _strip_company(_clean_title(title), company)
        if title and key not in found:
            found[key] = (f"{first} {last}", title)

    # Pattern A: a name element with a title nearby (cards, headings + subtitles, table cells)
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "a", "span", "p", "div", "li", "td", "dt"]):
        if el.find(["h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "li", "td"]):
            continue                                  # only leaf-ish elements are name candidates
        name = _name_from(el.get_text(" ", strip=True))
        if not name:
            continue
        title = _title_near(el)
        if title:
            add(name[0], name[1], title)

    # Pattern B: "Name - Title" / "Name, Title" / "Name | Title" inside one short element
    for el in soup.find_all(["li", "p", "td", "div", "span", "a"]):
        if el.find(["li", "p", "td", "div"]):
            continue
        text = el.get_text(" ", strip=True)
        if not (8 <= len(text) <= 140):
            continue
        parts = [p for p in re.split(r"\s+[–—|\-]\s+|\s*\|\s*|,\s+(?=[A-Z][a-z]+\s)", text, maxsplit=1) if p]
        if len(parts) == 2:
            name = _name_from(parts[0])
            if name and _looks_like_title(parts[1]):
                add(name[0], name[1], parts[1])
    return list(found.values())


def _page_urls(session: requests.Session, home_url: str, home_html: str, domain: str,
               timeout: float) -> list[str]:
    base = f"{urlparse(home_url).scheme}://{urlparse(home_url).hostname}"
    team_hint = re.compile(r"team|people|leader|manage|staff|director|officer|executive|who-we-are|"
                           r"our-company|bios?\b|professionals|personnel|about|board", re.I)
    urls: list[str] = []
    for u in sh._discover_links(home_html, home_url, domain):
        if team_hint.search(urlparse(u).path):
            urls.append(u)
    for p in ("/team", "/our-team", "/leadership", "/management", "/people", "/staff", "/about", "/about-us",
              "/about/team", "/about/leadership", "/company/leadership", "/company/team", "/who-we-are",
              "/executive-team", "/management-team", "/our-people", "/directors", "/officers", "/team-members"):
        urls.append(urljoin(base, p))
    urls += [u for u in sh._sitemap_links(session, base, domain, timeout) if team_hint.search(urlparse(u).path)]
    out, seen = [], {home_url.rstrip("/")}
    for u in urls:
        u = u.split("#")[0].rstrip("/")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:14]


def discover(company: str, domain: str, timeout: float = 8.0, deadline: float = 40.0,
             render_fallback: bool = True) -> list[dict]:
    """Contacts published on the company's team pages (empty list when the site is unreachable)."""
    if not domain:
        return []
    session = requests.Session()
    session.headers.update(sh._HEADERS)
    home = None
    for base in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
        home = sh._get(session, base + "/", timeout)
        if home is not None:
            break
    if home is None:
        return []

    pages: list[tuple[str, str]] = [(home.url, home.text)]
    urls = _page_urls(session, home.url, home.text, domain, timeout)

    def fetch(u: str):
        r = sh._get(session, u, timeout)
        return (r.url, r.text) if r is not None else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, u) for u in urls]
        done, not_done = wait(futures, timeout=deadline)
        for fut in done:
            try:
                res = fut.result()
                if res:
                    pages.append(res)
            except Exception:
                pass
        for fut in not_done:
            fut.cancel()

    contacts, seen = [], set()

    def collect(page_list: list[tuple[str, str]], source: str):
        for page_url, html in page_list:
            for name, title in extract_people(html, page_url, company):
                c = make_contact(name, title, company, source, source_url=page_url)
                if c and c["name"].lower() not in seen:
                    seen.add(c["name"].lower())
                    contacts.append(c)

    collect(pages, "Team page")

    # Many sites (IR platforms, React/Wix builds) only fill their team lists with JavaScript.
    # When the plain fetch found almost nobody, render the most promising pages in a real browser.
    if len(contacts) < 3 and render_fallback:
        from scraper.render import render_pages
        priority = re.compile(r"leader|manage|team|people|director|officer|executive|staff|about", re.I)
        candidates = [u for u in dict.fromkeys([home.url] + urls) if priority.search(urlparse(u).path or "/")]
        # skip the guessed paths that 404'd for the plain fetch; render real discovered links first
        fetched = {u.split("#")[0].rstrip("/") for u, _ in pages}
        candidates = sorted(candidates, key=lambda u: (u.rstrip("/") not in fetched, len(urlparse(u).path)))[:6]
        collect(render_pages(candidates), "Team page (rendered)")

    logger.info("Team pages for %s (%s): %d page(s), %d people", company, domain, len(pages), len(contacts))
    return contacts
