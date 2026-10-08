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
import unicodedata

PATTERNS = [
    "first.last", "flast", "firstlast", "first", "f.last", "first.l",
    "last.first", "lfirst", "first_last", "first-last", "last", "lastf",
]

# nickname -> formal names (people list "Mike", mailboxes are often "michael")
_NICKNAMES = {
    "bob": ["robert"], "rob": ["robert"], "bobby": ["robert"], "bill": ["william"],
    "will": ["william"], "billy": ["william"], "mike": ["michael"], "jim": ["james"],
    "jimmy": ["james"], "tom": ["thomas"], "tommy": ["thomas"], "dave": ["david"],
    "steve": ["steven", "stephen"], "chris": ["christopher", "christian"],
    "matt": ["matthew"], "joe": ["joseph"], "dan": ["daniel"], "danny": ["daniel"],
    "rick": ["richard"], "rich": ["richard"], "dick": ["richard"], "tony": ["anthony"],
    "jeff": ["jeffrey", "jeffery"], "greg": ["gregory"], "ken": ["kenneth"],
    "andy": ["andrew"], "drew": ["andrew"], "ed": ["edward"], "eddie": ["edward"],
    "tim": ["timothy"], "ben": ["benjamin"], "sam": ["samuel"], "pat": ["patrick", "patricia"],
    "nick": ["nicholas"], "jon": ["jonathan"], "alex": ["alexander", "alexandra"],
    "ron": ["ronald"], "don": ["donald"], "larry": ["lawrence"], "jerry": ["gerald", "jerome"],
    "terry": ["terrence", "terence"], "charlie": ["charles"], "chuck": ["charles"],
    "hank": ["henry"], "harry": ["harold", "henry"], "jack": ["john"], "johnny": ["john"],
    "kate": ["katherine", "kathryn"], "katie": ["katherine", "kathryn"],
    "kathy": ["katherine", "kathleen"], "liz": ["elizabeth"], "beth": ["elizabeth"],
    "jen": ["jennifer"], "jenny": ["jennifer"], "sue": ["susan"], "debbie": ["deborah"],
    "deb": ["deborah"], "becky": ["rebecca"], "cindy": ["cynthia"], "vicki": ["victoria"],
    "zach": ["zachary"], "josh": ["joshua"], "ray": ["raymond"],
    "randy": ["randall"], "fred": ["frederick"], "gene": ["eugene"], "walt": ["walter"],
    "doug": ["douglas"], "phil": ["philip", "phillip"], "russ": ["russell"],
    "stan": ["stanley"], "ted": ["theodore", "edward"], "vince": ["vincent"],
}
_FORMAL_TO_NICK: dict[str, list[str]] = {}
for _nick, _formals in _NICKNAMES.items():
    for _f in _formals:
        _FORMAL_TO_NICK.setdefault(_f, []).append(_nick)


def first_name_variants(first: str) -> list[str]:
    """The given first name plus its formal/nickname equivalents (cleaned, lowercase)."""
    f = clean_name(first)
    if not f:
        return []
    out = [f]
    for v in _NICKNAMES.get(f, []) + _FORMAL_TO_NICK.get(f, []):
        if v not in out:
            out.append(v)
    return out


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

    # Same known pattern applied to the formal/nickname first name ("Mike" -> michael.smith)
    alt_head = []
    for pat in (pattern, pattern2):
        if not pat:
            continue
        for alt in first_name_variants(first)[1:2]:
            cand = _apply_pattern(pat, alt, l, alt[0], li)
            if cand and cand not in head and cand not in alt_head:
                alt_head.append(cand)

    if head:
        tail = [v for v in variants if v not in head]
        variants = head + alt_head + tail

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


def apply_pattern(pattern: str, f: str, l: str) -> str | None:
    """Public wrapper: build a local part from already-cleaned first/last names."""
    if not f or not l:
        return None
    return _apply_pattern(pattern, f, l, f[0], l[0])


def clean_name(name: str) -> str:
    return _clean(name)


def _clean(name: str) -> str:
    """Normalize a name part for email construction."""
    # Jose-with-accent -> jose (mail systems use ASCII; plain [a-z] stripping would give "jos")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = name.strip().lower()
    name = name.split(",")[0].strip()
    name = re.sub(r"\([^)]*\)", "", name)          # "John (Jack)" -> "John"
    # Remove credential suffixes
    name = re.sub(
        r"\b(phd|mba|pe|ms|bs|pg|cpg|jr|sr|ii|iii|iv)\b\.?", "", name, flags=re.IGNORECASE
    )
    # Collapse hyphenated names: mary-kate -> marykate
    name = name.replace("-", "")
    # Keep only lowercase letters
    name = re.sub(r"[^a-z]", "", name)
    return name
