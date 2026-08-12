"""
Full pipeline — CLI version.

For each company in the input CSV:
  1. Find the email domain
  2. Detect email pattern (website scrape + DDG)
  3. SMTP-probe all format variations on one test contact to confirm the
     working pattern before generating for everyone
  4. Generate emails for all contacts using the confirmed pattern
  5. SMTP-verify each email
  6. Drop bounced / invalid addresses
  7. Save only viable contacts to the output CSV

Usage:
    python _pipeline.py <input.csv> [output.csv]
    or drag and drop onto Run Pipeline.bat
"""

import csv
import sys
import time
import random
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from emailer.domain_finder import find_domain
from emailer.pattern_detector import detect_pattern
from emailer.generator import generate_candidates
from emailer.smtp_verifier import get_mx, port25_available
from emailer.email_verifier import verify_email, get_mx_records, check_catch_all
from emailer.domain_cache import get as cache_get, put as cache_put
from emailer.bounce_tracker import is_bounced

DROP_STATUSES = {
    "bounced", "no domain", "no MX", "no candidates",
    "error", "invalid_syntax", "disposable",
}

PROBE_DELAY   = 0.8
CONTACT_DELAY = (1.5, 3.0)
COMPANY_DELAY = (5.0, 10.0)

if len(sys.argv) < 2:
    print("Usage: python _pipeline.py <input.csv> [output.csv]")
    sys.exit(1)

INPUT  = Path(sys.argv[1])
OUTPUT = Path(sys.argv[2]) if len(sys.argv) > 2 else INPUT.parent / (INPUT.stem + "_pipeline.csv")

with INPUT.open(encoding="utf-8") as f:
    reader = csv.DictReader(f)
    base_fields = list(reader.fieldnames)
    contacts = list(reader)

total = len(contacts)
print(f"\nPipeline: {total} contacts\n")

if not port25_available():
    print("WARNING: Outbound port 25 appears blocked.")
    print("         SMTP probes will return 'unknown'. Use a VPN with a clean IP")
    print("         if you see no verified results.\n")

# ── Output fields ─────────────────────────────────────────────────────────────

out_fields = base_fields.copy()
for col in ("email", "email_status", "email_source"):
    if col not in out_fields:
        out_fields.append(col)

# ── Group by company ──────────────────────────────────────────────────────────

by_company = defaultdict(list)
for c in contacts:
    by_company[c.get("company", "Unknown")].append(c)

companies  = list(by_company.keys())
results    = []
viable_cnt = 0
dropped_cnt= 0


def get_name_parts(contact):
    first = contact.get("first_name") or ""
    last  = contact.get("last_name") or ""
    if not first or not last:
        raw   = contact.get("name", "").split(",")[0].strip()
        parts = raw.split()
        first = parts[0] if parts else ""
        last  = parts[-1] if len(parts) > 1 else ""
    return first, last


def local_to_pattern(local, first, last):
    fi = first[0] if first else ""
    li = last[0]  if last  else ""
    mapping = {
        f"{first}.{last}": "first.last",
        f"{fi}{last}":     "flast",
        f"{first}{last}":  "firstlast",
        f"{fi}.{last}":    "f.last",
        f"{first}.{li}":   "first.l",
        f"{first}":        "first",
        f"{last}.{first}": "last.first",
        f"{li}{first}":    "lfirst",
    }
    return mapping.get(local, local)


def probe_format(domain, group):
    """Try all format variations on one test contact. Returns confirmed pattern or None."""
    mx_records = get_mx_records(domain)
    if not mx_records:
        return None
    mx_host = mx_records[0][1]

    if check_catch_all(domain, mx_host):
        print(f"  Catch-all: {domain} accepts all addresses — format probe skipped")
        return None

    first = last = None
    for c in group:
        f, l = get_name_parts(c)
        if f and l and len(l) > 1:
            first, last = f, l
            break

    if not first:
        return None

    print(f"  Probing formats: {first} {last} @ {domain}")
    candidates = generate_candidates(first, last, domain, pattern=None)

    for candidate in candidates:
        r = verify_email(candidate)
        print(f"    {candidate:<42} -> {r['status']}")
        if r["status"] == "verified":
            local     = candidate.split("@")[0].lower()
            confirmed = local_to_pattern(local, first.lower(), last.lower())
            print(f"  [+] Confirmed pattern: {confirmed}")
            return confirmed
        time.sleep(PROBE_DELAY)

    print("  [-] No format confirmed via SMTP")
    return None


# ── Main loop ─────────────────────────────────────────────────────────────────

for ci, company in enumerate(companies):
    group = by_company[company]
    print(f"\n{'='*60}")
    print(f"  {company.upper()} ({len(group)} contacts)")
    print(f"{'='*60}")

    # Step 1: Domain
    cached = cache_get(company)
    if cached and cached.get("domain"):
        domain         = cached["domain"]
        pattern        = cached.get("pattern")
        pattern_source = cached.get("pattern_source", "cache")
        print(f"  Domain  : {domain}  [cache]")
        if not pattern or pattern_source in ("none",):
            pattern, pattern_source = detect_pattern(domain)
            cache_put(company, domain, pattern, pattern_source)
        print(f"  Pattern : {pattern or 'unknown'}  ({pattern_source})")
    else:
        domain = find_domain(company)
        if not domain:
            print(f"  [!] No domain found — skipping {company}")
            for c in group:
                c.update({"email": "", "email_status": "no domain", "email_source": ""})
                results.append(c)
                dropped_cnt += 1
            continue
        print(f"  Domain  : {domain}")
        mx = get_mx(domain)
        if not mx:
            print(f"  [!] No MX record for {domain} — skipping")
            for c in group:
                c.update({"email": "", "email_status": "no MX", "email_source": domain})
                results.append(c)
                dropped_cnt += 1
            continue
        print(f"  MX      : {mx}")
        pattern, pattern_source = detect_pattern(domain)
        print(f"  Pattern : {pattern or 'unknown'}  ({pattern_source})")
        cache_put(company, domain, pattern, pattern_source)

    # Step 2: SMTP format probe (skip if already reliably confirmed)
    if pattern_source not in ("smtp-verified", "scraped", "emailformat", "manual"):
        probed = probe_format(domain, group)
        if probed:
            pattern        = probed
            pattern_source = "smtp-verified"
            cache_put(company, domain, pattern, pattern_source)

    print()

    # Step 3: Generate + verify each contact
    for c in group:
        first, last = get_name_parts(c)
        name = c.get("name", f"{first} {last}".strip())

        candidates = generate_candidates(first, last, domain, pattern)
        if not candidates:
            c.update({"email": "", "email_status": "no candidates", "email_source": domain})
            results.append(c)
            dropped_cnt += 1
            print(f"  [?] {name} — no candidates")
            continue

        email  = candidates[0]
        status = "unknown"

        for candidate in candidates:
            r = verify_email(candidate)
            if r["status"] == "verified":
                email, status = candidate, "verified"
                break
            if r["status"] == "catch-all-risky":
                status = "catch-all-risky"
                break
            if r["status"] == "bounced" and status == "unknown":
                status = "bounced"
            time.sleep(random.uniform(*CONTACT_DELAY))

        if status == "catch-all-risky" and pattern_source in ("scraped", "emailformat", "smtp-verified", "manual"):
            status = "catch-all-confirmed"

        if email and is_bounced(email):
            status = "bounced"

        catch_all = status in ("catch-all-confirmed", "catch-all-risky")
        source    = domain + (f" ({status})" if catch_all else "")

        c.update({"email": email, "email_status": status, "email_source": source})
        results.append(c)

        if status in DROP_STATUSES:
            dropped_cnt += 1
            label = f"[DROPPED] {email or '--'} [{status}]"
        else:
            viable_cnt += 1
            label = f"{email} [{status}]"

        print(f"  {name:<30} -> {label}")

    if ci < len(companies) - 1:
        delay = random.uniform(*COMPANY_DELAY)
        print(f"\n  Cooling down {delay:.0f}s...")
        time.sleep(delay)

# ── Write full results ────────────────────────────────────────────────────────

with OUTPUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(results)

# ── Write viable-only output ──────────────────────────────────────────────────

viable_path = OUTPUT.parent / (OUTPUT.stem + "_viable.csv")
viable_contacts = [r for r in results if r.get("email") and r.get("email_status", "") not in DROP_STATUSES]
with viable_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(viable_contacts)

print(f"\n{'='*60}")
print(f"  Pipeline complete.")
print(f"  Total     : {total}")
print(f"  Viable    : {viable_cnt}  -> {viable_path.name}")
print(f"  Dropped   : {dropped_cnt} (bounced / invalid)")
print(f"  Full log  : {OUTPUT.name}")
print(f"{'='*60}\n")
