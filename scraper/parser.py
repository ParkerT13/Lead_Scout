import re

# Titles to exclude — non-employees and non-professionals only.
# We intentionally keep C-suite, VPs, Directors, etc. — those are valuable contacts.
_SKIP_TITLES = {
    # Clearly not employees of the company
    "investor", "board member", "board director", "board of directors",
    "independent consultant", "independent contractor",
    "self-employed", "self employed", "freelance",
    "entrepreneur",
    # Job-seekers / students
    "seeking", "open to work", "open to opportunities",
    "intern", "student", "graduate", "recent graduate",
    # Scope exceptions
    "drilling engineer",   # not in Mercury ICP — keep this out
}

# A job title must contain at least one of these to be accepted.
# Broad enough to include all professional roles at an O&G company:
# engineers, geoscientists, executives, managers, land professionals, etc.
_TITLE_KEYWORDS = {
    # Technical disciplines
    "engineer",    # → petroleum engineer, reservoir engineer, engineering manager
    "geoscient",   # → geoscientist, geoscience
    "geophys",     # → geophysicist, geophysical manager
    "geolog",      # → geologist, geological, geology
    "petrophys",   # → petrophysicist, petrophysics
    "reservoir",   # → reservoir engineer, VP reservoir engineering
    "completions",
    "petroleum",
    "subsurface",
    "seismic",
    "stratigraphy",
    "geomechanics",
    "formation",   # → formation evaluation
    # Executive / leadership
    "ceo", "coo", "cfo", "cto", "cso",
    "president",   # catches VP, EVP, SVP, President
    "director",
    "vice president",
    "vp ",         # standalone "VP" — space after to avoid matching "VPN"
    "executive",
    "officer",
    "founder",
    "owner",
    # Management / operational
    "manager",
    "supervisor",
    "lead",
    "principal",
    "staff",
    "senior",
    "analyst",
    "specialist",
    "technician",
    "landman",
    "land professional",
    "asset",       # → asset manager, asset team lead
    "field development",
    "operations",
    "exploration",
    "production",
    "commercial",
    "business development",
}

# Patterns that suggest a string is a company name rather than a job title
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(Inc\.?|LLC\.?|Corp\.?|Ltd\.?|L\.P\.?|Services?|Solutions?|"
    r"Consulting|Group|Holdings?|Partners?|International|Global|"
    r"Energy|Resources?|Petroleum|Oilfield|Upstream|Midstream|Downstream)\b",
    re.IGNORECASE,
)

# Country-code LinkedIn subdomains that indicate non-US profiles
# Any country-code LinkedIn subdomain is non-US — reject all of them.
# We keep only https://www.linkedin.com/in/ profiles.
_INTL_LI_RE = re.compile(
    r"^https?://(?!www\.)[a-z]{2,3}\.linkedin\.com/",
    re.IGNORECASE,
)

# Basin detection: basin display name -> list of lowercase trigger strings
BASIN_MAP: dict[str, list[str]] = {
    "Permian": [
        "permian", "midland basin", "delaware basin",
        "west texas", "midland, tx", "midland, texas",
        "odessa, tx", "odessa, texas", "waha",
    ],
    "Eagle Ford": [
        "eagle ford", "south texas", "laredo", "corpus christi",
    ],
    "Bakken / Williston": [
        "bakken", "williston", "north dakota", "bismarck", "dickinson",
    ],
    "DJ Basin / Niobrara": [
        "dj basin", "denver-julesburg", "wattenberg", "niobrara", "weld county",
    ],
    "Haynesville": [
        "haynesville", "shreveport", "bossier", "north louisiana",
    ],
    "Marcellus / Appalachia": [
        "marcellus", "appalachia", "appalachian", "pittsburgh", "west virginia", "morgantown",
    ],
    "Utica": ["utica"],
    "Anadarko / SCOOP / STACK": [
        "anadarko", "scoop", "stack", "oklahoma city", "oklahoma", "enid",
    ],
    "Barnett": ["barnett", "fort worth"],
    "Powder River": ["powder river", "prb", "casper", "wyoming"],
    "Gulf of Mexico": [
        "gulf of mexico", "deepwater", "offshore gulf",
        "new orleans", "metairie", "lafayette, la",
    ],
    "Offshore": ["offshore", "subsea"],
    "Woodford": ["woodford"],
    "Fayetteville": ["fayetteville", "arkansas"],
    "Piceance / Green River": ["piceance", "green river", "grand junction", "rock springs"],
}

# Ordered location patterns — most specific first
_LOCATION_PATTERNS = [
    # Named city + optional state
    re.compile(
        r"\b((?:Greater\s+)?(?:Houston|Midland|Odessa|Dallas|Fort\s+Worth|Denver|"
        r"Oklahoma\s+City|Tulsa|Casper|Bismarck|Pittsburgh|New\s+Orleans|Shreveport|"
        r"Lafayette|Bakersfield|Corpus\s+Christi|San\s+Antonio|Laredo|Lubbock|"
        r"Amarillo|Dickinson|Williston|Victoria|Calgary|Aberdeen|Anchorage))"
        r"(?:,\s*(?:Texas|TX|Oklahoma|OK|Colorado|CO|Wyoming|WY|North\s+Dakota|ND|"
        r"Louisiana|LA|Pennsylvania|PA|West\s+Virginia|WV|Ohio|OH|New\s+Mexico|NM|"
        r"Kansas|KS|Montana|MT|Alaska|AK|Alberta|AB|Scotland))?"
        r"(?:\s+(?:Area|Metropolitan\s+Area|Metro))?",
        re.IGNORECASE,
    ),
    # Generic city, state pattern
    re.compile(
        r"\b([A-Za-z][A-Za-z\s]{2,20}),\s*"
        r"(Texas|TX|Oklahoma|OK|Colorado|CO|Wyoming|WY|North\s+Dakota|ND|"
        r"Louisiana|LA|Pennsylvania|PA|West\s+Virginia|WV|Ohio|OH|"
        r"New\s+Mexico|NM|Kansas|KS|Montana|MT|Alaska|AK)",
        re.IGNORECASE,
    ),
]

_LINKEDIN_SUFFIX = re.compile(r"\s*[|\-\u2013]\s*LinkedIn\s*$", re.IGNORECASE)
# Require whitespace around dashes so hyphenated names (Jean-Pierre) aren't split;
# pipe | can appear without surrounding spaces in LinkedIn titles.
_TITLE_SPLIT     = re.compile(r"\s+[-\u2013]\s+|\s*\|\s*")
_AT_COMPANY      = re.compile(r"\s+(?:at|@)\s+.+$", re.IGNORECASE)
# Generic leadership words that, when they appear alone as parts[1], mean we
# should look at parts[2] for the actual technical context.
# e.g. "VP - Reservoir Engineering" → combine to "VP Reservoir Engineering"
_GENERIC_LEVEL   = re.compile(
    r"^(vice\s+president|vp|director|chief|managing\s+director|head|lead|senior)\s*$",
    re.IGNORECASE,
)
_CREDENTIALS     = re.compile(
    r",?\s*\b("
    # Academic degrees
    r"Ph\.?D\.?|M\.?D\.?|D\.?O\.?|D\.?V\.?M\.?|J\.?D\.?|L\.?L\.?M\.?|"
    r"M\.?B\.?A\.?|M\.?S\.?|M\.?Sc\.?|B\.?S\.?|B\.?Sc\.?|B\.?A\.?|"
    # Professional certifications
    r"P\.?M\.?P\.?|P\.?E\.?|E\.?I\.?T\.?|"
    r"C\.?P\.?A\.?|C\.?F\.?A\.?|C\.?F\.?P\.?|C\.?M\.?A\.?|"
    r"C\.?I\.?S\.?S\.?P\.?|C\.?I\.?S\.?M\.?|"
    # Medical / nursing
    r"R\.?N\.?|N\.?P\.?|A\.?P\.?R\.?N\.?|"
    # O&G specific
    r"PG|CPG|CEng|CGeol|RPG|AAPG|SEG|SPE|SPEE|"
    # Generational suffixes
    r"Jr\.?|Sr\.?|II|III|IV"
    r")\b\.?",
    re.IGNORECASE,
)
_PREFIXES = re.compile(r"^(Dr|Mr|Mrs|Ms|Prof)\.?\s+", re.IGNORECASE)


def parse_result(title: str, url: str, body: str) -> dict:
    """Extract all contact fields from a single search result."""
    name = _extract_name(title)
    parts = name.strip().split()
    first_name = parts[0] if parts else ""
    last_name  = parts[-1] if len(parts) > 1 else ""
    # name field is always "First Last" — no middle names, no credentials
    name = f"{first_name} {last_name}".strip() if first_name else name
    return {
        "name":         name,
        "first_name":   first_name,
        "last_name":    last_name,
        "title":        _extract_job_title(title),
        "location":     _extract_location(title, body),
        "basin":        _extract_basin(title, body),
        "linkedin_url": _clean_url(url),
    }


def is_valid_contact(contact: dict, company: str) -> bool:
    """
    Quality gate — four hard rules:
    1. Must look like a real person (name, URL)
    2. Must be a US LinkedIn profile (reject country-code subdomains)
    3. Title must be a job role, NOT a company name (catches people whose
       current employer appears in the result title instead of a job title —
       those people do NOT work at our target company)
    4. Title must contain at least one engineering/geoscience keyword
       (or be blank — blank is acceptable, a company name is not)
    """
    name  = contact.get("name", "").strip()
    title = contact.get("title", "").strip()
    url   = contact.get("linkedin_url", "")

    # ── Basic profile sanity ──────────────────────────────────────────────
    if not name or len(name) < 4:
        return False
    if "linkedin.com/in/" not in url:
        return False
    if name.lower() == company.lower():
        return False
    if " " not in name and "." not in name:
        return False
    if len(name) > 60:
        return False
    # Reject contacts where last name is just an initial (e.g. "John S.")
    name_parts = name.split()
    if len(name_parts) >= 2 and len(name_parts[-1].rstrip(".")) <= 1:
        return False
    if any(c.isdigit() for c in name):
        return False

    # ── Reject international (non-US) profiles ────────────────────────────
    if _INTL_LI_RE.match(url):
        return False

    # ── Title quality checks ─────────────────────────────────────────────
    title_lower = title.lower()

    # Reject out-of-scope roles
    if title and any(skip in title_lower for skip in _SKIP_TITLES):
        return False

    # If a title was extracted, it must look like a job role, NOT a company name.
    # Exception: if the "company name" IS our target company, it just means
    # LinkedIn only showed the employer in the snippet with no job title —
    # treat it as a blank title (acceptable) rather than rejecting.
    if title and _title_is_company_name(title):
        if company.lower() not in title.lower():
            return False
        # It's our target company — zero out the title and fall through
        title = ""
        title_lower = ""

    # Title must contain at least one role keyword (or be blank).
    # Blank is fine — it just means the snippet didn't expose the title.
    if title and not any(kw in title_lower for kw in _TITLE_KEYWORDS):
        return False

    return True


def _title_is_company_name(title: str) -> bool:
    """
    Return True if the extracted 'title' string is actually a company name.
    Examples that return True: 'Saipem', 'GE', 'Harbour Energy', 'Aera Energy LLC'
    Examples that return False: 'Petroleum Engineer', 'Senior Geologist'
    """
    t = title.strip()
    # Short all-caps abbreviations (GE, BP, KBR, etc.)
    if t.isupper() and len(t) <= 6:
        return True
    # Has a company-suffix keyword but NO job-title keyword
    has_company = bool(_COMPANY_SUFFIX_RE.search(t))
    has_role    = any(kw in t.lower() for kw in _TITLE_KEYWORDS)
    if has_company and not has_role:
        return True
    return False


# ── Private helpers ─────────────────────────────────────────────────────────

def _clean_url(url: str) -> str:
    url = url.split("?")[0].rstrip("/")
    if url and not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    return url


def _extract_name(title: str) -> str:
    t = _LINKEDIN_SUFFIX.sub("", title).strip()
    parts = _TITLE_SPLIT.split(t)
    if not parts:
        return ""
    name = parts[0].strip()
    # Drop everything after first comma: "John Smith, PhD, PE" -> "John Smith"
    name = name.split(",")[0].strip()
    # Strip honorific prefixes (Dr., Mr., etc.)
    name = _PREFIXES.sub("", name).strip()
    # Strip inline credentials ("John Smith PhD" -> "John Smith")
    name = _CREDENTIALS.sub("", name).strip()
    # Collapse to first + last word only — drop middle names and initials
    words = name.split()
    if len(words) > 2:
        name = f"{words[0]} {words[-1]}"
    elif words:
        name = " ".join(words)
    return name


def _extract_job_title(title: str) -> str:
    t = _LINKEDIN_SUFFIX.sub("", title).strip()
    parts = _TITLE_SPLIT.split(t)
    if len(parts) < 2:
        return ""
    candidate = parts[1].strip()
    # If parts[1] is a bare seniority/level word (VP, Director, Chief…) and
    # parts[2] exists, combine them so "VP - Reservoir Engineering" becomes
    # "VP Reservoir Engineering" and the technical domain word is preserved.
    if _GENERIC_LEVEL.match(candidate) and len(parts) > 2:
        candidate = f"{candidate} {parts[2].strip()}"
    candidate = _AT_COMPANY.sub("", candidate).strip()
    return candidate


def _extract_location(title: str, body: str) -> str:
    combined = f"{title} {body}"
    for pattern in _LOCATION_PATTERNS:
        m = pattern.search(combined)
        if m:
            return m.group(0).strip()
    return ""


def _extract_basin(title: str, body: str) -> str:
    combined = f"{title} {body}".lower()
    for basin, keywords in BASIN_MAP.items():
        for kw in keywords:
            if kw in combined:
                return basin
    return ""
