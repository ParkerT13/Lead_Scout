"""
LinkedIn employment verification via DDG search.

LinkedIn blocks all direct HTTP requests with HTTP 999.  Instead we verify
by re-searching DDG for the person's name + company and checking whether
their profile still appears — the same method the scraper uses to find
contacts initially.

Query: site:linkedin.com/in "Full Name" "Company"

If the person's profile slug appears in results AND the title shows the
target company, status is 'current'.  If the profile appears with a
different employer in the title, status is 'stale'.  If the profile
doesn't appear at all, status is 'unknown' (not indexed ≠ left company).

Returns:
    'current'  — confirmed still at company per DDG result title
    'stale'    — profile found but a different employer shown in title
    'unknown'  — profile not found in search results (inconclusive)
"""

import re
import logging

logger = logging.getLogger(__name__)

from scraper.query_builder import generate_variants

_LINKEDIN_SUFFIX = re.compile(r"\s*[|\-]\s*linkedin\s*$", re.IGNORECASE)


def check_employment(linkedin_url: str, company: str, name: str = "") -> str:
    """
    Verify current employment by searching DDG for the person's profile.

    Args:
        linkedin_url: The contact's LinkedIn profile URL.
        company:      Target company name.
        name:         Person's full name (improves precision — include if available).

    Returns 'current', 'stale', or 'unknown'.
    """
    variants = generate_variants(company)
    variant_lowers = {v.lower() for v in variants}
    first_words = {v.split()[0].lower() for v in variant_lowers if len(v.split()[0]) >= 4}

    slug_match = re.search(r"linkedin\.com/in/([^/?#]+)", linkedin_url)
    if not slug_match:
        return "unknown"
    slug = slug_match.group(1).lower()

    # Build query: name + company + site filter
    if name.strip():
        query = f'site:linkedin.com/in "{name.strip()}" "{variants[0]}"'
    else:
        query = f'site:linkedin.com/in "{variants[0]}"'

    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
    except Exception as e:
        logger.warning("DDG verify error for %s: %s", linkedin_url, e)
        return "unknown"

    if not results:
        logger.debug("No DDG results for %s / %s", name, company)
        return "unknown"

    for r in results:
        href = r.get("href", "")
        # Must be the right person's profile
        if slug not in href.lower():
            continue

        title_lower = _LINKEDIN_SUFFIX.sub("", r.get("title", "")).lower()

        full_match  = any(v in title_lower for v in variant_lowers)
        trunc_match = any(f" at {fw}" in title_lower for fw in first_words)

        if full_match or trunc_match:
            return "current"

        # Profile found but company not in title
        if " at " in title_lower:
            return "stale"

        return "unknown"

    # No result matched the expected slug
    return "unknown"
