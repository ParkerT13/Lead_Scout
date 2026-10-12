"""
Find the email domain for a company name.
Strategy:
  1. Check persistent cache (domain_cache.json)
  2. Guess domain from company name and HTTP-validate it
     (most reliable — "Chord Energy" → chordenergy.com → HEAD request confirms it exists)
  3. DDG search — collect all candidates, return best-scoring domain
  4. Last resort: return the unvalidated guess
"""

import re
import logging

logger = logging.getLogger(__name__)

from emailer.smtp_verifier import get_mx

_SKIP_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com",
    "glassdoor.com", "indeed.com", "bloomberg.com", "reuters.com",
    "wikipedia.org", "crunchbase.com", "dnb.com", "zoominfo.com",
    "pitchbook.com", "sec.gov", "bizapedia.com", "opencorporates.com",
    "yelp.com", "bbb.org", "yellowpages.com", "bizjournals.com",
    "wsj.com", "ft.com", "cnbc.com", "businesswire.com", "prnewswire.com",
    "globenewswire.com", "accesswire.com", "newswire.com",
    "spotify.com", "apple.com", "google.com", "microsoft.com",
    "hartenergy.com", "energyintel.com",  # O&G media sites, not company websites
}

_GENERIC_WORDS = {
    "energy", "oil", "gas", "petroleum", "resources", "company",
    "corp", "inc", "llc", "ltd", "group", "holdings", "partners",
    "operating", "operations", "exploration", "production", "the", "and",
}

# Only strip true legal entity suffixes — keep industry words like Energy, Resources
# The suffix must be a separate word: "Hilcorp" must not lose "corp", "Zinc" must not lose "inc".
_ENTITY_STRIP = re.compile(
    r"(?:\s*,\s*|\s+)(Incorporated|Inc\.?|LLC\.?|L\.L\.C\.?|Corp\.?|Corporation"
    r"|Ltd\.?|Limited|L\.P\.?|LP)\.?\s*$",
    re.IGNORECASE,
)

# For _guess_domain fallback only
_LEGAL_STRIP = re.compile(
    r"\s+(Inc\.?|LLC\.?|Corp\.?|Ltd\.?|L\.P\.?|LP|Company|Co\.?|Partners"
    r"|Energy|Petroleum|Oil\s*&?\s*Gas|Resources|E&P|Operating|Operations"
    r"|Holdings?|Group|Limited)\.?\s*$",
    re.IGNORECASE,
)


_TITLE_WORDS = {
    "engineer", "engineering", "manager", "geologist", "geophysicist", "geoscientist", "director",
    "vp", "vice", "president", "senior", "staff", "principal", "analyst", "specialist", "lead",
    "supervisor", "technician", "superintendent", "consultant", "coordinator", "associate",
    "petrophysicist", "chief", "officer", "head", "region", "asset", "production", "reservoir",
    "drilling", "completions", "completion", "operations", "geoscience", "exploration",
    "development", "of", "and", "the", "petroleum", "subsurface", "facilities", "well",
}


def looks_like_job_title(name: str) -> bool:
    """'STAFF ENGINEER', 'Region Manager', 'VP Engineering' ended up in the company column of
    some pulls; they are roles, not employers."""
    words = re.findall(r"[a-z]+", name.lower())
    return bool(words) and all(w in _TITLE_WORDS for w in words)


def find_domain(company_name: str) -> str:
    """
    Return the bare email domain for a company (e.g. 'chordenergy.com').
    Checks cache first, then validates a guessed domain, then falls back to DDG.
    """
    from emailer.domain_cache import get as cache_get

    if looks_like_job_title(company_name):
        logger.info("Skipping '%s': looks like a job title, not a company", company_name)
        return ""

    cached = cache_get(company_name)
    if cached and cached.get("domain"):
        logger.info("Domain from cache for %s: %s", company_name, cached["domain"])
        return cached["domain"]

    # Build candidate slugs: full name (entity suffix stripped) + legal suffix stripped
    guesses = _build_guesses(company_name)

    # HTTP-validate each guess. A guess only wins if the site also looks like THIS
    # company (parked / unrelated domains resolve too).
    fallback = ""
    for i, guess in enumerate(guesses):
        final = _resolve(guess)
        if not final:
            continue
        # A guess that redirects is really the destination site (scoutenergypartners.com -> scoutep.com)
        for cand in ([final, guess] if final != guess else [guess]):
            verdict = _site_check(cand, company_name)
            # None = page unreadable (blocked / JS-only). The exact full-name domain is then
            # accepted if it has a mail server; shortened guesses must be positively confirmed.
            # A guess our own CRM already holds real contacts for (hilcorp.com) is proven to be a
            # live mail domain, whatever its homepage looks like.
            if verdict is True or (cand == guess and _in_crm_kb(cand)):
                logger.info("Domain validated for %s: %s", company_name, cand)
                return cand
            if verdict is None and i == 0 and cand == guess and get_mx(cand) and not fallback:
                fallback = cand              # plausible but unproven: search gets a chance to do better

    # Fall back to DDG search
    all_words  = [w.lower() for w in company_name.split() if len(w) > 2]
    key_words  = [w for w in all_words if w not in _GENERIC_WORDS]
    if not key_words:
        key_words = all_words

    expected_slugs = {re.sub(r"[^a-z]", "", g.split(".")[0]) for g in guesses}

    queries = [
        f'"{company_name}" oil gas official website',
        f'"{company_name}" energy company contact email',
    ]

    scored: dict[str, int] = {}
    for query in queries:
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=10))
            for r in results:
                domain, score = _score_result(r, all_words, key_words, expected_slugs)
                if domain and score > scored.get(domain, 0):
                    scored[domain] = score
        except Exception as e:
            logger.warning("Domain search error: %s", e)

        if scored and max(scored.values()) >= 10:
            break

    # Validate the best few, not just the top one
    for domain, score in sorted(scored.items(), key=lambda kv: -kv[1])[:3]:
        verdict = _site_check(domain, company_name)
        # True = looks like this company. None = unreadable: trust only a decent match with a mail server
        if verdict is True or (verdict is None and score >= 3 and get_mx(domain)):
            logger.info("Domain via DDG for %s: %s (score %d)", company_name, domain, score)
            return domain
        logger.info("Rejected DDG domain %s for %s", domain, company_name)

    if fallback:
        logger.info("Domain for %s: %s (unreadable site, has mail server)", company_name, fallback)
        return fallback

    # Nothing validated. Better to say "no domain" (user can set it via Edit Domain Cache)
    # than to email strangers at an unrelated company's domain.
    logger.info("No validated domain for %s", company_name)
    return ""


def _build_guesses(company_name: str) -> list[str]:
    """
    Build domain candidates in preference order:
    1. Strip only legal entity suffix (keep industry words): "Chord Energy" → chordenergy.com
    2. Strip all generic words: "Chord Energy" → chord.com
    """
    guesses = []

    # Guess 1: keep industry words, only strip Inc/LLC/Corp etc.
    g1 = re.sub(r"[^a-zA-Z0-9]", "", _ENTITY_STRIP.sub("", company_name).strip()).lower()
    if g1:
        guesses.append(f"{g1}.com")

    # Guess 2: strip all legal/generic words
    g2 = re.sub(r"[^a-zA-Z0-9]", "", _LEGAL_STRIP.sub("", company_name).strip()).lower()
    if g2 and g2 != g1:
        guesses.append(f"{g2}.com")

    return guesses


_SLD = {"co", "com", "org", "net", "gov", "edu", "ac"}


def registrable(host: str) -> str:
    """corporate.exxonmobil.com -> exxonmobil.com ; www.foo.co.uk -> foo.co.uk"""
    labels = host.lower().strip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _SLD:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _in_crm_kb(domain: str) -> bool:
    """True if the HubSpot knowledge base holds real contacts at this domain."""
    from emailer.pattern_detector import get_kb_entry
    return bool(get_kb_entry(domain))


def _resolve(domain: str) -> str | None:
    """
    Follow redirects from the domain's homepage. Returns the final host (www. stripped), or
    None if nothing answers. 4xx/5xx still count as "exists" - we only need to know it is real.
    """
    import requests
    from urllib.parse import urlparse
    for scheme in ("https", "http"):
        for method in (requests.head, requests.get):     # some servers reset HEAD requests
            try:
                resp = method(f"{scheme}://{domain}", timeout=6, allow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"})
                return registrable(urlparse(resp.url).hostname or domain)
            except Exception:
                continue
    return None


def _domain_resolves(domain: str) -> bool:
    return _resolve(domain) is not None


_INDUSTRY_RE = re.compile(
    r"\b(oil|gas|petroleum|energy|drilling|exploration|midstream|upstream|minerals?|royalt\w*|"
    r"wellbore|reservoirs?|permian|bakken|eagle ford|e&p|hydrocarbons?|barrels|boe|operator)\b"
)
_LABEL_INDUSTRY_RE = re.compile(
    r"(energy|oil|gas|petroleum|resources|exploration|minerals|midstream|royalt|operating|production|drilling)")
_PARKED_MARKERS = ("domain is for sale", "buy this domain", "parked free",
                   "this domain may be for sale", "sedoparking", "hugedomains")


def _site_check(domain: str, company_name: str):
    """
    True  - homepage looks like THIS oil & gas company: every distinctive word of the name
            appears AND the page talks about oil/gas/energy (a lone distinctive word such as
            "Verdad" or "Magnolia" matches unrelated real-estate / lifestyle sites otherwise)
    False - page fetched but does not look like the company (or is parked)
    None  - site unreachable / blocked, cannot tell
    """
    words = [w for w in re.findall(r"[a-z0-9]+", company_name.lower())
             if len(w) > 2 and w not in _GENERIC_WORDS]
    if not words:
        words = [w for w in re.findall(r"[a-z0-9]+", company_name.lower()) if len(w) > 2]
    if not words:
        return False
    try:
        import requests
        r = requests.get(f"https://{domain}", timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code >= 400:
            return None
        text = r.text[:80000].lower()
    except Exception:
        return None
    if any(p in text for p in _PARKED_MARKERS):
        return False
    label = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())
    # silverhillenergy.com for "Silver Hill Energy Partners": the name AND an industry word are in
    # the domain itself - strong enough even when the page is a JS app with no readable text.
    if all(w in label for w in words) and _LABEL_INDUSTRY_RE.search(label):
        return True
    if len(text) < 2500:
        return None                      # splash page / JS app: nothing to judge by
    named = all(w in text for w in words) or all(w in label for w in words)
    return named and bool(_INDUSTRY_RE.search(text))


def _site_mentions_company(domain: str, company_name: str) -> bool:
    return _site_check(domain, company_name) is True


def _build_company_slugs(company_name: str) -> set[str]:
    guesses = _build_guesses(company_name)
    return {re.sub(r"[^a-z]", "", g.split(".")[0]) for g in guesses}


def _score_result(result: dict, all_words: list, key_words: list, expected_slugs: set) -> tuple[str, int]:
    href = result.get("href", "")
    m = re.match(r"https?://(?:www\.)?([^/]+)", href)
    if not m:
        return "", 0

    domain = m.group(1).lower().strip(".").split(":")[0]
    if any(domain == s or domain.endswith("." + s) for s in _SKIP_DOMAINS):
        return "", 0

    flat = re.sub(r"[.\-]", "", domain.split(".")[0])

    key_score = sum(1 for w in key_words if w in flat)
    if key_score == 0:
        return "", 0

    generic_score = sum(1 for w in all_words if w not in key_words and w in flat)
    score = key_score * 2 + generic_score

    if flat in expected_slugs:
        score += 10

    return domain, score


def _guess_domain(company: str) -> str:
    guesses = _build_guesses(company)
    return guesses[0] if guesses else ""
