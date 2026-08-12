"""
Generate best-guess emails without SMTP verification.
Used when your IP is on the Spamhaus PBL and SMTP probes are blocked.

Outputs round2_enriched.csv with:
  email        — best-guess address (pattern-based if found, otherwise first.last)
  email_status — 'pattern-confirmed' or 'best-guess'
  email_source — domain used

Run the resulting CSV through a free verifier (MillionVerifier, NeverBounce, etc.)
"""

import csv
import time
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from emailer.domain_finder import find_domain
from emailer.pattern_detector import detect_pattern
from emailer.generator import generate_candidates
from emailer.smtp_verifier import get_mx
from emailer.domain_cache import get as cache_get, put as cache_put
from emailer.bounce_tracker import is_bounced

if len(sys.argv) < 2:
    print("Usage: python _generate_emails.py <input.csv> [output.csv]")
    print("  or drag and drop a CSV onto Generate Emails.bat")
    sys.exit(1)

INPUT  = Path(sys.argv[1])
OUTPUT = Path(sys.argv[2]) if len(sys.argv) > 2 else INPUT.parent / (INPUT.stem + "_enriched.csv")

with INPUT.open(encoding="utf-8") as f:
    reader = csv.DictReader(f)
    base_fields = list(reader.fieldnames)
    contacts = list(reader)

total = len(contacts)
print(f"Generating emails for {total} contacts...\n")

out_fields = base_fields.copy()
for col in ("email", "email_status", "email_source"):
    if col not in out_fields:
        out_fields.append(col)

by_company = defaultdict(list)
for c in contacts:
    by_company[c.get("company", "Unknown")].append(c)

results = []

for ci, company in enumerate(by_company.keys()):
    group = by_company[company]
    print(f"--- {company.upper()} ({len(group)} contacts) ---")

    # Check cache first — respects any manual corrections made via Check Domains
    cached = cache_get(company)
    if cached and cached.get("domain"):
        domain         = cached["domain"]
        pattern        = cached.get("pattern")
        pattern_source = cached.get("pattern_source", "cache")
        print(f"  Domain : {domain}  [cache]")
        # If domain is cached but pattern is unknown, re-run pattern detection
        if not pattern or pattern_source in ("none",):
            pattern, pattern_source = detect_pattern(domain)
            cache_put(company, domain, pattern, pattern_source)
        if pattern:
            print(f"  Pattern: {pattern} ({pattern_source})")
        else:
            print(f"  Pattern: unknown — using first.last")
    else:
        domain = find_domain(company)
        if not domain:
            print(f"  [!] No domain found")
            for c in group:
                c.update({"email": "", "email_status": "no domain", "email_source": ""})
                results.append(c)
            continue
        print(f"  Domain : {domain}")
        mx = get_mx(domain)
        print(f"  MX     : {mx or 'not found'}")
        pattern, pattern_source = detect_pattern(domain)
        if pattern:
            print(f"  Pattern: {pattern} ({pattern_source})")
        else:
            print(f"  Pattern: unknown — using first.last")
        cache_put(company, domain, pattern, pattern_source)

    if pattern:
        status_label = "pattern-confirmed" if pattern_source in ("scraped", "emailformat", "smtp-verified", "manual") else "pattern-ddg"
    else:
        status_label = "best-guess"

    for c in group:
        first = c.get("first_name") or ""
        last  = c.get("last_name") or ""
        if not first or not last:
            name  = c.get("name", "")
            clean = name.split(",")[0].strip()
            parts = clean.split()
            first = parts[0] if parts else ""
            last  = parts[-1] if len(parts) > 1 else ""
        name = c.get("name", f"{first} {last}".strip())

        candidates = generate_candidates(first, last, domain, pattern)
        email = candidates[0] if candidates else ""

        # Skip previously bounced addresses
        if email and is_bounced(email):
            c.update({"email": email, "email_status": "bounced", "email_source": domain})
            print(f"  {name} -> {email} [SKIPPED — known bounce]")
        else:
            c.update({"email": email, "email_status": status_label, "email_source": domain})
            print(f"  {name} -> {email}")
        results.append(c)

    if ci < len(by_company) - 1:
        time.sleep(random.uniform(2, 4))
    print()

with OUTPUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(results)

confirmed = sum(1 for r in results if r.get("email_status") == "pattern-confirmed")
guess     = sum(1 for r in results if r.get("email_status") in ("pattern-ddg", "best-guess"))
print(f"Done. {OUTPUT}")
print(f"  pattern-confirmed : {confirmed}  (scraped from company website)")
print(f"  best-guess        : {guess}  (run through MillionVerifier or NeverBounce)")
