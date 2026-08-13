"""
Generate email address candidates for a person given their name, domain,
and optionally detected patterns.

Candidate order is ranked by real-world O&G frequency (2,978 contacts / 941 domains):
  first.last 38.9% | flast 37.4% | first_last 10.9% | first 7.4%
  firstlast 2.4%   | lastf 1.4%  | last 0.7%        | f.last 0.6%

If pattern is known  -> pattern email first, then rest in frequency order
If two patterns known -> both pattern emails at top, maximizing hit on split-format companies
If pattern unknown   -> all 8 variants in frequency order for SMTP testing
"""

import re


def generate_candidates(
    first: str,
    last: str,
    domain: str,
    pattern: str | None = None,
    pattern2: str | None = None,
) -> list[str]:
    """
    Return a list of candidate email addresses, most likely first.
    pattern2 is used for companies known to use two email formats simultaneously.
    Deduplicates automatically.
    """
    f = _clean(first)
    l = _clean(last)

    if not f or not l or not domain:
        return []

    fi = f[0]   # first initial
    li = l[0]   # last initial

    # All candidate local-parts in frequency order (O&G HubSpot data, by contact count)
    variants = [
        f"{fi}{l}",     # flast        — 37.4%
        f"{f}.{l}",     # first.last   — 38.9%
        f"{f}_{l}",     # first_last   — 10.9% (Microsoft/IT-provisioned systems)
        f"{f}",         # first        — 7.4%  (boutique/small firms)
        f"{f}{l}",      # firstlast    — 2.4%
        f"{l}{fi}",     # lastf        — 1.4%  (bishopm style)
        f"{l}",         # last         — 0.7%
        f"{fi}.{l}",    # f.last       — 0.6%
    ]

    # Front-load known patterns — both if company uses two formats
    head = []
    for pat in (pattern, pattern2):
        if pat:
            preferred = _apply_pattern(pat, f, l, fi, li)
            if preferred and preferred not in head:
                head.append(preferred)

    if head:
        tail = [v for v in variants if v not in head]
        variants = head + tail

    # Deduplicate preserving order, then append domain
    seen: set[str] = set()
    result: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            result.append(f"{v}@{domain}")

    return result


def _apply_pattern(pattern: str, f: str, l: str, fi: str, li: str) -> str | None:
    mapping = {
        "first.last":  f"{f}.{l}",
        "flast":       f"{fi}{l}",
        "firstlast":   f"{f}{l}",
        "first":       f"{f}",
        "f.last":      f"{fi}.{l}",
        "first.l":     f"{f}.{li}",
        "last.first":  f"{l}.{f}",
        "lfirst":      f"{li}{f}",
        "lastfirst":   f"{l}{f}",
        "first_last":  f"{f}_{l}",
        "first-last":  f"{f}-{l}",
        "last":        f"{l}",
        "lastf":       f"{l}{fi}",
    }
    return mapping.get(pattern)


def _clean(name: str) -> str:
    """Normalize a name part for email construction."""
    name = name.strip().lower()
    name = name.split(",")[0].strip()
    # Remove credential suffixes
    name = re.sub(
        r"\b(phd|mba|pe|ms|bs|pg|cpg|jr|sr|ii|iii|iv)\b\.?", "", name, flags=re.IGNORECASE
    )
    # Collapse hyphenated names: mary-kate -> marykate
    name = name.replace("-", "")
    # Keep only lowercase letters
    name = re.sub(r"[^a-z]", "", name)
    return name
