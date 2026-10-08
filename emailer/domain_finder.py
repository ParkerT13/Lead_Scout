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
_ENTITY_STRIP = re.compile(
    r",?\s*(Incorporated|Inc\.?|LLC\.?|L\.L\.C\.?|Corp\.?|Corporation"
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


def find_domain(company_name: str) -> str:
    """
    Return the bare email domain for a company (e.g. 'chordenergy.com').
    Checks cache first, then validates a guessed domain, then falls back to DDG.
    """
    from emailer.domain_cache import get as cache_get

    cached = cache_get(company_name)
    if cached and cached.get("domain"):
        logger.info("Domain from cache for %s: %s", company_name, cached["domain"])
        return cached["domain"]

    # Build candidate slugs: full name (entity suffix stripped) + legal suffix stripped
    guesses = _build_guesses(company_name)

    # HTTP-validate each guess. A guess only wins outright if the site also
    # mentions the company (parked / unrelated domains resolve too).
    resolving: list[str] = []
    for guess in guesses:
        if _domain_resolves(guess):
            if _site_mentions_company(guess, company_name):
                logger.info("Domain validated for %s: %s", company_name, guess)
                return guess
            resolving.append(guess)

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

    best_domain = ""
    best_score  = 0

    for query in queries:
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=10))
            for r in results:
                domain, score = _score_result(r, all_words, key_words, expected_slugs)
                if score > best_score:
                    best_score  = score
                    best_domain = domain
        except Exception as e:
            logger.warning("Domain search error: %s", e)

        if best_score >= 10:
            break

    if best_domain and best_score > 0:
        logger.info("Domain via DDG for %s: %s (score %d)", company_name, best_domain, best_score)
        return best_domain

    # Resolving guess that never mentioned the company beats a blind guess
    if resolving:
        logger.info("Domain weakly validated for %s: %s", company_name, resolving[0])
        return resolving[0]

    # Last resort: return unvalidated first guess
    if guesses:
        logger.info("Domain guessed (unvalidated) for %s: %s", company_name, guesses[0])
        return guesses[0]

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


def _domain_resolves(domain: str) -> bool:
    """
    Quick HEAD request to check if a domain serves any HTTP response.
    Returns True even for 4xx/5xx — we just need to confirm the domain exists.
    """
    try:
        import requests
        resp = requests.head(
            f"https://{domain}", timeout=6, allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        return resp.status_code < 600
    except Exception:
        try:
            import requests
            resp = requests.head(
                f"http://{domain}", timeout=6, allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            return resp.status_code < 600
        except Exception:
            return False


def _site_mentions_company(domain: str, company_name: str) -> bool:
    """True if the homepage <title>/body text contains the company's distinctive words."""
    words = [w for w in re.findall(r"[a-z0-9]+", company_name.lower())
             if len(w) > 2 and w not in _GENERIC_WORDS]
    if not words:
        words = [w for w in re.findall(r"[a-z0-9]+", company_name.lower()) if len(w) > 2]
    if not words:
        return True
    try:
        import requests
        r = requests.get(f"https://{domain}", timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        text = r.text[:60000].lower()
    except Exception:
        return False
    if any(p in text for p in ("domain is for sale", "buy this domain", "parked free",
                               "this domain may be for sale", "sedoparking")):
        return False
    return any(w in text for w in words)


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
