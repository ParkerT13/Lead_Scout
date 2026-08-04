import re

# All engineer and geoscientist titles to search for
TARGET_TITLES = [
    # Engineering
    "petroleum engineer",
    "reservoir engineer",
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


def build_queries(company_name: str) -> list[str]:
    """
    Build all LinkedIn dork queries for a company.
    Each query targets one job title across all company name variants.
    """
    variants = generate_variants(company_name)
    queries: list[str] = []
    for variant in variants:
        for title in TARGET_TITLES:
            queries.append(f'site:linkedin.com/in "{variant}" "{title}"')
    return queries
