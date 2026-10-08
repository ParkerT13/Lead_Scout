import re

# All engineer and geoscientist titles to search for
# Broad, high-yield titles first: results for "geologist" already include senior/
# staff/exploration geologists, so the narrow variants rarely add anyone new.
TARGET_TITLES = [
    "geologist",
    "geophysicist",
    "petroleum engineer",
    "reservoir engineer",
    "geoscientist",
    "drilling engineer",
    "completions engineer",
    "production engineer",
    "petrophysicist",
    "asset manager",
    "vp geoscience",
    "vp exploration",
    "vp engineering",
    "chief geoscientist",
]

# Narrow variants - only searched when the broad pass found few people
EXTRA_TITLES = [
    # Engineering
    "petroleum engineer",
    "reservoir engineer",
    "drilling engineer",
    "completions engineer",
    "completion engineer",
    "production engineer",
    "well engineer",
    "subsurface engineer",
    "facilities engineer",
    "staff engineer",
    "principal engineer",
    "field development manager",
    "asset manager",
    "asset team lead",
    "engineering manager",
    # Geoscience
    "geoscientist",
    "geophysicist",
    "exploration geologist",
    "development geologist",
    "production geologist",
    "petrophysicist",
    "geologist",
    "stratigraphic geologist",
    "structural geologist",
    "staff geoscientist",
    "principal geoscientist",
    "senior geologist",
    "senior geophysicist",
    # Technical leadership (decision-makers / buyers)
    "vp geoscience",
    "vp exploration",
    "vp engineering",
    "chief geoscientist",
]

# Legal suffixes to strip when generating name variants
_STRIP_RE = re.compile(
    r",?\s*(Incorporated|Inc\.?|LLC\.?|L\.L\.C\.?|Corp\.?|Corporation"
    r"|Ltd\.?|Limited|L\.P\.?|LP|Company|Co\.?|Partners|& Partners"
    r"|Group|Holdings?|Energy|Petroleum|Oil\s+&?\s*Gas?|Resources?"
    r"|E&P|Operating|Operations?)\.?\s*$",
    re.IGNORECASE,
)


def generate_variants(company_name: str) -> list[str]:
    """
    Produce 2-3 search-query variants for a company name:
      1. The name as given
      2. Stripped of legal suffix (if one exists)
      3. With "Inc" appended (if no legal suffix present)
    Caps at 3 to keep query volume manageable.
    """
    name = company_name.strip()
    variants: list[str] = [name]

    stripped = _STRIP_RE.sub("", name).strip()
    if stripped and stripped.lower() != name.lower() and len(stripped) >= 3:
        variants.append(stripped)

    if not _STRIP_RE.search(name) and not name.lower().endswith(" inc"):
        variants.append(f"{name} Inc")

    # Deduplicate, preserve order
    seen: set[str] = set()
    unique: list[str] = []
    for v in variants:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            unique.append(v)

    return unique[:3]


def build_query_plan(company_name: str) -> list[list[str]]:
    """
    Queries grouped per company-name variant, broad titles first then narrow ones.
    The worker stops a group early when it stops producing new people.
    """
    titles: list[str] = []
    for t in TARGET_TITLES + EXTRA_TITLES:
        if t not in titles:
            titles.append(t)
    return [[f'site:linkedin.com/in "{v}" "{t}"' for t in titles]
            for v in generate_variants(company_name)]
