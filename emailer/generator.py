"""
Generate email address candidates for a person given their name, domain,
and optionally a detected pattern.

If pattern is known  → return [pattern_email, first.last as backup]
If pattern unknown   → return all common variants for SMTP testing
"""

import re


def generate_candidates(
    first: str,
    last: str,
    domain: str,
    pattern: str | None = None,
) -> list[str]:
    """
    Return a list of candidate email addresses, most likely first.
    Deduplicates automatically.
    """
    f = _clean(first)
    l = _clean(last)

    if not f or not l or not domain:
        return []

    fi = f[0]   # first initial
    li = l[0]   # last initial

    # All candidate local-parts in preference order (ranked by real-world frequency
    # across 941 O&G domains / 2,978 contacts from HubSpot KB):
    #   flast 42%  |  first.last 31%  |  first 20%  |  firstlast 6%
    #   f.last 1.4%  |  first.l 0.5%  |  first_last ~rare  |  last ~rare
    # last.first and lfirst were removed — zero occurrences in O&G data.
    variants = [
        f"{fi}{l}",     # flast       — 42% of O&G domains
        f"{f}.{l}",     # first.last  — 31%
        f"{f}",         # first       — 20% (boutique/small firms)
        f"{f}{l}",      # firstlast   — 6%
        f"{fi}.{l}",    # f.last      — 1.4%
        f"{f}.{li}",    # first.l     — 0.5%
        f"{f}_{l}",     # first_last  — older IT/Microsoft-provisioned systems
        f"{l}",         # last        — rare, European/international firms
    ]

    if pattern:
        preferred = _apply_pattern(pattern, f, l, fi, li)
        if preferred:
            # Put detected pattern first, then first.last, then rest
            head = [preferred]
            if f"{f}.{l}" != preferred:
                head.append(f"{f}.{l}")
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
        "last":        f"{l}",
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
    # Collapse hyphenated names: mary-kate → marykate
    name = name.replace("-", "")
    # Keep only lowercase letters
    name = re.sub(r"[^a-z]", "", name)
    return name
