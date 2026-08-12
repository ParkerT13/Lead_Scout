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

def infer_pattern(fn: str, ln: str, email: str) -> str | None:
    local = re.sub(r"[^a-z.]", "", email.split("@")[0].lower())
    fi = fn[0] if fn else ""
    li = ln[0] if ln else ""
    mapping = {
        f"{fn}.{ln}": "first.last",
        f"{fi}.{ln}": "f.last",
        f"{fn}.{li}": "first.l",
        f"{fi}{ln}":  "flast",
        f"{fn}{ln}":  "firstlast",
        f"{fn}":      "first",
        f"{ln}.{fn}": "last.first",
        f"{li}{fn}":  "lfirst",
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
    fn = r.get("first_name", "").strip().lower()
    ln = r.get("last_name", "").strip().lower()
    if fn and ln:
        by_domain[domain].append((fn, ln, email))

kb = {}
for domain, contacts in by_domain.items():
    patterns, samples = [], []
    for fn, ln, em in contacts:
        p = infer_pattern(fn, ln, em)
        if p:
            patterns.append(p)
            samples.append(em)
    if not patterns:
        continue
    counts  = Counter(patterns)
    top, n  = counts.most_common(1)[0]
    total   = len(patterns)
    conf    = round(n / total, 2)
    if conf >= 0.7:
        kb[domain] = {
            "pattern":      top,
            "confidence":   conf,
            "sample_count": total,
            "samples":      samples[:3],
        }

OUT.write_text(json.dumps(kb, indent=2, sort_keys=True), encoding="utf-8")
pat_dist = Counter(v["pattern"] for v in kb.values())
print(f"Written {len(kb)} domain patterns to {OUT.name}")
print("Pattern distribution:")
for p, n in pat_dist.most_common():
    print(f"  {p}: {n}")
