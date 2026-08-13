"""
Confirm a working email pattern for a company domain.

Use this after you've verified an email actually reached someone.
Records the pattern in both the domain cache (used for email generation)
and the HubSpot knowledge base (permanent reference).

Usage:
    python _confirm_pattern.py <company> <confirmed_email> <first_name> <last_name>

Example:
    python _confirm_pattern.py "Scout Energy Partners" travis.moreland@scoutep.com Travis Moreland

Or drag and drop onto "Confirm Pattern.bat" after editing the variables.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from emailer.domain_cache import get as cache_get, put as cache_put

def infer_pattern(fn: str, ln: str, email: str) -> str | None:
    local = re.sub(r"[^a-z._\-]", "", email.split("@")[0].lower())
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
        f"{fn}_{ln}": "first_last",
        f"{fn}-{ln}": "first-last",
        f"{ln}{fi}":  "lastf",
        f"{ln}":      "last",
    }
    return mapping.get(local)

def main():
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(1)

    company   = sys.argv[1]
    email     = sys.argv[2].strip().lower()
    first     = sys.argv[3].strip().lower()
    last      = sys.argv[4].strip().lower()

    if "@" not in email:
        print(f"ERROR: '{email}' does not look like an email address.")
        sys.exit(1)

    domain  = email.split("@")[1]
    pattern = infer_pattern(first, last, email)

    if not pattern:
        print(f"ERROR: Could not determine pattern from {first} {last} -> {email}")
        print("Make sure the name and email actually match.")
        sys.exit(1)

    # 1. Update domain cache (used for email generation)
    # Preserve any existing pattern2 for dual-format companies
    cached = cache_get(company)
    existing_pattern2 = cached.get("pattern2") if cached else None
    cache_put(company, domain, pattern, "manual", existing_pattern2)
    print(f"[+] Domain cache updated:")
    print(f"    Company : {company}")
    print(f"    Domain  : {domain}")
    print(f"    Pattern : {pattern}  (manual)")

    # 2. Update HubSpot knowledge base
    KB_PATH = Path(__file__).parent / "emailer" / "hubspot_knowledge.json"
    try:
        kb = json.loads(KB_PATH.read_text(encoding="utf-8")) if KB_PATH.exists() else {}
    except Exception:
        kb = {}

    existing = kb.get(domain, {})
    samples  = existing.get("samples", [])
    if email not in samples:
        samples = [email] + samples
    samples = samples[:5]  # keep up to 5 confirmed examples

    kb[domain] = {
        "pattern":      pattern,
        "confidence":   1.0,
        "sample_count": existing.get("sample_count", 0) + 1,
        "samples":      samples,
        "source":       "manual",
    }
    KB_PATH.write_text(json.dumps(kb, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[+] Knowledge base updated: {domain} -> {pattern}")
    print(f"    Example : {email}")

if __name__ == "__main__":
    main()
