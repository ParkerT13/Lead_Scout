"""
Build the HubSpot email pattern knowledge base.

Reads a HubSpot contacts export CSV and derives email format patterns
(first.last, flast, etc.) for each company domain by matching real
name+email pairs. Writes emailer/hubspot_knowledge.json.

Usage:
    python _build_kb.py <hubspot_contacts.csv>
    or drag and drop onto Build Knowledge Base.bat

The generated JSON is excluded from git (contains real email addresses).
Re-run any time you export a fresh HubSpot contacts list.

pattern2 is stored when a company has a clear second format used by >= 25% of
contacts (common in post-merger companies or those that migrated email systems).
"""

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

if len(sys.argv) < 2:
    print("Usage: python _build_kb.py <hubspot_contacts.csv>")
    sys.exit(1)

INPUT = Path(sys.argv[1])
OUT   = Path(__file__).parent / "emailer" / "hubspot_knowledge.json"

PERSONAL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "me.com", "aol.com", "protonmail.com", "live.com", "msn.com", "ymail.com",
}

def _clean(name: str) -> str:
    name = name.strip().lower().split(",")[0].strip()
    name = re.sub(r"\b(phd|mba|pe|ms|bs|pg|cpg|jr|sr|ii|iii|iv)\b\.?", "", name, flags=re.I)
    name = name.replace("-", "")
    return re.sub(r"[^a-z]", "", name)

def infer_pattern(fn: str, ln: str, email: str) -> str | None:
    fn = _clean(fn)
    ln = _clean(ln)
    if not fn or not ln:
        return None
    local = re.sub(r"[^a-z._\-]", "", email.split("@")[0].lower())
    fi = fn[0]
    li = ln[0]
    mapping = {
        f"{fn}.{ln}":  "first.last",
        f"{fi}.{ln}":  "f.last",
        f"{fn}.{li}":  "first.l",
        f"{fi}{ln}":   "flast",
        f"{fn}{ln}":   "firstlast",
        f"{fn}":       "first",
        f"{ln}.{fn}":  "last.first",
        f"{li}{fn}":   "lfirst",
        f"{fn}_{ln}":  "first_last",
        f"{fn}-{ln}":  "first-last",
        f"{ln}{fi}":   "lastf",
        f"{ln}":       "last",
    }
    return mapping.get(local)

rows = list(csv.DictReader(INPUT.open(encoding="utf-8-sig")))
print(f"Loaded {len(rows)} contacts from {INPUT.name}")

by_domain: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
for r in rows:
    email = (r.get("work_email") or r.get("email") or "").strip().lower()
    if not email or "@" not in email:
        continue
    domain = email.split("@")[-1]
    if domain in PERSONAL_DOMAINS or domain.endswith(".edu") or domain.endswith(".gov"):
        continue
    fn = r.get("first_name", "").strip()
    ln = r.get("last_name", "").strip()
    if fn and ln:
        by_domain[domain].append((fn, ln, email))

kb = {}
dual_pattern_count = 0

for domain, contacts in by_domain.items():
    patterns, samples = [], []
    for fn, ln, em in contacts:
        p = infer_pattern(fn, ln, em)
        if p:
            patterns.append(p)
            samples.append(em)
    if not patterns:
        continue

    counts = Counter(patterns)
    total  = len(patterns)
    top2   = counts.most_common(2)
    top, n = top2[0]
    conf   = round(n / total, 2)

    if conf < 0.65:
        continue  # too ambiguous even for primary pattern

    entry = {
        "pattern":      top,
        "confidence":   conf,
        "sample_count": total,
        "samples":      samples[:3],
    }

    # Store secondary pattern if it accounts for >= 25% of contacts
    # (genuine dual-format company — post-merger, system migration, etc.)
    if len(top2) > 1:
        p2, n2 = top2[1]
        conf2  = round(n2 / total, 2)
        if conf2 >= 0.25:
            entry["pattern2"]    = p2
            entry["confidence2"] = conf2
            dual_pattern_count  += 1

    kb[domain] = entry

OUT.write_text(json.dumps(kb, indent=2, sort_keys=True), encoding="utf-8")

pat_dist = Counter(v["pattern"] for v in kb.values())
print(f"Written {len(kb)} domain entries to {OUT.name}")
print(f"  Dual-pattern domains: {dual_pattern_count}")
print("Pattern distribution (primary):")
for p, n in pat_dist.most_common():
    print(f"  {p}: {n}")
