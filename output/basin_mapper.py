"""
Map a LinkedIn location string to an O&G basin name.
Used to auto-populate the 'basin' field when pulling contacts.

Call: get_basin("Midland, Texas, United States") -> "Permian (Midland)"
"""

# Ordered from most-specific to least-specific so earlier entries win.
# Key = lowercase substring to search for in the location string.
_BASIN_MAP = [
    # Permian — Midland sub-basin
    ("midland, tx",     "Permian (Midland)"),
    ("midland, texas",  "Permian (Midland)"),
    ("midland-odessa",  "Permian"),
    # Permian — Delaware sub-basin
    ("pecos, tx",       "Permian (Delaware)"),
    ("pecos, texas",    "Permian (Delaware)"),
    ("reeves county",   "Permian (Delaware)"),
    ("loving county",   "Permian (Delaware)"),
    ("ward county",     "Permian (Delaware)"),
    ("carlsbad",        "Permian (Delaware)"),
    ("artesia",         "Permian (Delaware)"),
    ("roswell",         "Permian (Delaware)"),
    ("odessa, tx",      "Permian (Delaware)"),
    ("odessa, texas",   "Permian (Delaware)"),
    # Permian — general
    ("big spring",      "Permian"),
    ("san angelo",      "Permian"),
    ("abilene",         "Permian"),
    ("permian",         "Permian"),
    # DJ Basin
    ("greeley",         "DJ Basin"),
    ("weld county",     "DJ Basin"),
    ("fort collins",    "DJ Basin"),
    ("loveland, co",    "DJ Basin"),
    ("longmont",        "DJ Basin"),
    ("boulder, co",     "DJ Basin"),
    ("denver",          "DJ Basin"),
    ("greenwood village","DJ Basin"),
    ("englewood, co",   "DJ Basin"),
    ("broomfield",      "DJ Basin"),
    ("aurora, co",      "DJ Basin"),
    # Williston / Bakken
    ("williston",       "Williston"),
    ("dickinson, nd",   "Williston"),
    ("minot",           "Williston"),
    ("bismarck",        "Williston"),
    ("sidney, mt",      "Williston"),
    ("billings",        "Williston"),
    ("watford city",    "Williston"),
    # Anadarko / SCOOP / STACK
    ("oklahoma city",   "Anadarko"),
    ("norman, ok",      "Anadarko"),
    ("enid",            "Anadarko"),
    ("woodward",        "Anadarko"),
    ("ardmore",         "SCOOP"),
    ("chickasha",       "STACK"),
    ("kingfisher",      "STACK"),
    # Mid-Continent
    ("tulsa",           "Mid-Continent"),
    # Eagle Ford
    ("laredo",          "Eagle Ford"),
    ("cotulla",         "Eagle Ford"),
    ("three rivers",    "Eagle Ford"),
    ("alice, tx",       "Eagle Ford"),
    ("eagle ford",      "Eagle Ford"),
    ("san antonio",     "Eagle Ford"),
    ("corpus christi",  "Eagle Ford"),
    ("victoria, tx",    "Eagle Ford"),
    # Haynesville
    ("shreveport",      "Haynesville"),
    ("bossier city",    "Haynesville"),
    ("natchitoches",    "Haynesville"),
    ("marshall, tx",    "Haynesville"),
    ("haynesville",     "Haynesville"),
    # Marcellus / Appalachian
    ("pittsburgh",      "Appalachian"),
    ("canonsburg",      "Appalachian"),
    ("morgantown",      "Appalachian"),
    ("charleston, wv",  "Appalachian"),
    ("huntington, wv",  "Appalachian"),
    ("wheeling",        "Appalachian"),
    ("marcellus",       "Appalachian"),
    ("appalachian",     "Appalachian"),
    # Utica
    ("youngstown",      "Utica"),
    ("canton, oh",      "Utica"),
    ("akron",           "Utica"),
    ("utica",           "Utica"),
    # Barnett
    ("fort worth",      "Barnett"),
    ("barnett",         "Barnett"),
    # Fayetteville
    ("fayetteville, ar","Fayetteville"),
    ("little rock",     "Fayetteville"),
    # Piceance / Uinta
    ("grand junction",  "Piceance"),
    ("rifle, co",       "Piceance"),
    ("vernal",          "Uinta"),
    # Powder River
    ("gillette",        "Powder River"),
    ("sheridan, wy",    "Powder River"),
    ("casper",          "Powder River"),
    # HQ hubs — major cities with no single basin affiliation
    ("houston",         "HQ"),
    ("dallas",          "HQ"),
    ("midland, mi",     ""),    # Midland Michigan — NOT Permian
    ("midland, on",     ""),    # Midland Ontario — NOT Permian
]


def get_basin(location: str) -> str:
    """
    Return the O&G basin for a location string, or empty string if unknown.
    Location is typically from LinkedIn: "City, State, Country".
    """
    if not location:
        return ""
    loc = location.lower()
    for keyword, basin in _BASIN_MAP:
        if keyword in loc:
            return basin
    return ""
