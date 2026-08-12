"""
Standalone batch email enricher.
Reads round2_verified.csv, enriches each contact with an email address,
writes round2_enriched.csv with email, email_status, email_source columns added.
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
from emailer.smtp_verifier import get_mx, port25_available
from emailer.email_verifier import verify_email as _verify_email

if len(sys.argv) < 2:
    print("Usage: python _enrich_batch.py <input.csv> [output.csv]")
    sys.exit(1)

INPUT  = Path(sys.argv[1])
OUTPUT = Path(sys.argv[2]) if len(sys.argv) > 2 else INPUT.parent / (INPUT.stem + "_enriched.csv")

CONTACT_DELAY = (1.5, 3.0)
COMPANY_DELAY = (5.0, 10.0)

# ── Load contacts ────────────────────────────────────────────────────────────

with INPUT.open(encoding="utf-8") as f:
    reader = csv.DictReader(f)
    base_fields = list(reader.fieldnames)
    contacts = list(reader)

total = len(contacts)
print(f"Enriching {total} contacts across companies...\n")

# ── Add output fields ────────────────────────────────────────────────────────

out_fields = base_fields.copy()
for col in ("email", "email_status", "email_source"):
    if col not in out_fields:
        out_fields.append(col)

# ── Check port 25 ────────────────────────────────────────────────────────────

if not port25_available():
    print("WARNING: Outbound port 25 appears blocked. SMTP checks may fail.")
    print("         Try a VPN with a clean IP if you see all 'error' results.\n")

# ── Group by company ─────────────────────────────────────────────────────────

by_company = defaultdict(list)
for c in contacts:
    by_company[c.get("company", "Unknown")].append(c)

# ── Process ──────────────────────────────────────────────────────────────────

results = []
companies = list(by_company.keys())

for ci, company in enumerate(companies):
    group = by_company[company]
    print(f"--- {company.upper()} ({len(group)} contacts) ---")

    # Domain
    domain = find_domain(company)
    if not domain:
        print(f"  [!] No domain found for {company}")
        for c in group:
            c.update({"email": "", "email_status": "no domain", "email_source": ""})
            results.append(c)
        continue
    print(f"  Domain : {domain}")

    # MX
    mx = get_mx(domain)
    if not mx:
        print(f"  [!] No MX record for {domain}")
        for c in group:
            c.update({"email": "", "email_status": "no MX", "email_source": domain})
            results.append(c)
        continue
    print(f"  MX     : {mx}")

    # Pattern
    pattern, pattern_source = detect_pattern(domain)
    if pattern:
        print(f"  Pattern: {pattern} ({pattern_source})")
    else:
        print(f"  Pattern: unknown — trying all variants")

    # Per-contact
    for c in group:
        first = c.get("first_name") or ""
        last  = c.get("last_name") or ""
        if not first or not last:
            name  = c.get("name", "")
            parts = name.strip().split()
            first = parts[0] if parts else ""
            last  = parts[-1] if len(parts) > 1 else ""
        name = c.get("name", f"{first} {last}".strip())

        candidates = generate_candidates(first, last, domain, pattern)
        if not candidates:
            print(f"  [?] {name} — could not build candidates")
            c.update({"email": "", "email_status": "no candidates", "email_source": domain})
            results.append(c)
            continue

        # Default to first candidate (pattern-preferred); only override on verified
        email  = candidates[0] if candidates else ""
        status = "unknown"

        for candidate in candidates:
            r = _verify_email(candidate)
            print(f"    -> {candidate} [{r['status']}]")
            if r["status"] == "verified":
                email, status = candidate, "verified"
                break
            if r["status"] == "catch-all-risky":
                status = "catch-all-risky"
                break
            if r["status"] == "bounced" and status == "unknown":
                status = "bounced"

        # Upgrade catch-all-risky to catch-all-confirmed if pattern was scraped
        if status == "catch-all-risky" and pattern_source == "scraped":
            status = "catch-all-confirmed"

        catch_all = status in ("catch-all-confirmed", "catch-all-risky")
        source = domain
        if catch_all:
            source += f" ({status})"

        c.update({"email": email, "email_status": status, "email_source": source})
        results.append(c)

        label = f"{email or '—'} [{status}]"
        print(f"  {name} -> {label}")

        time.sleep(random.uniform(*CONTACT_DELAY))

    if ci < len(companies) - 1:
        delay = random.uniform(*COMPANY_DELAY)
        print(f"  Cooling down {delay:.0f}s...\n")
        time.sleep(delay)

# ── Write output ─────────────────────────────────────────────────────────────

with OUTPUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(results)

verified   = sum(1 for r in results if r.get("email_status") == "verified")
confirmed  = sum(1 for r in results if r.get("email_status") == "catch-all-confirmed")
risky      = sum(1 for r in results if r.get("email_status") == "catch-all-risky")
other      = len(results) - verified - confirmed - risky

print(f"\nDone. Results saved to: {OUTPUT}")
print(f"  verified              : {verified}")
print(f"  catch-all-confirmed   : {confirmed}")
print(f"  catch-all-risky       : {risky}")
print(f"  unresolved/error      : {other}")
