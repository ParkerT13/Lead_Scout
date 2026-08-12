"""
Pre-flight domain and email pattern checker.

For each company in a CSV:
  1. Detects (or loads from cache) the email domain and format pattern
  2. Lets you correct either one manually
  3. SMTP-probes all format variations against one test contact to find
     the format that actually gets accepted by the mail server
  4. Saves the confirmed pattern to cache so Generate Emails uses it automatically

Usage:
    python _check_domains.py <input.csv>
    or drag and drop onto Check Domains.bat
"""

import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from emailer.domain_finder import find_domain
from emailer.pattern_detector import detect_pattern
from emailer.smtp_verifier import get_mx
from emailer.email_verifier import verify_email, check_catch_all, get_mx_records
from emailer.generator import generate_candidates
from emailer.domain_cache import get as cache_get, put as cache_put

VALID_PATTERNS = [
    "first.last", "flast", "firstlast", "f.last",
    "first.l", "first", "last.first", "lfirst",
]

if len(sys.argv) < 2:
    print("Usage: python _check_domains.py <input.csv>")
    print("  or drag and drop a CSV onto Check Domains.bat")
    sys.exit(1)

INPUT = Path(sys.argv[1])
with INPUT.open(encoding="utf-8") as f:
    contacts = list(csv.DictReader(f))

by_company = defaultdict(list)
for c in contacts:
    by_company[c.get("company", "Unknown")].append(c)

companies = list(by_company.keys())
print(f"\nChecking domains for {len(companies)} company/companies...\n")
print("=" * 60)

corrections = 0


def _pick_test_contact(group):
    """Pick the best contact to use as a pattern probe — needs a real last name."""
    for c in group:
        first = c.get("first_name") or ""
        last  = c.get("last_name") or ""
        if not first or not last:
            name  = c.get("name", "").split(",")[0].strip()
            parts = name.split()
            first = parts[0] if parts else ""
            last  = parts[-1] if len(parts) > 1 else ""
        if first and last and len(last) > 1:
            return first, last, c.get("name", f"{first} {last}")
    return None, None, None


def _probe_patterns(domain, first, last):
    """
    Try all email format variations for one contact against the live mail server.
    Returns (confirmed_email, confirmed_pattern) or (None, None) if nothing verified.
    """
    mx_records = get_mx_records(domain)
    if not mx_records:
        print(f"  [!] No MX records found for {domain} — cannot probe")
        return None, None

    mx_host = mx_records[0][1]

    # Check catch-all first — if the server accepts everything, probing is meaningless
    print(f"  Checking catch-all on {domain}...")
    if check_catch_all(domain, mx_host):
        print(f"  [!] {domain} is a catch-all server — accepts all addresses.")
        print(f"      Cannot distinguish real formats via SMTP. Pattern probe skipped.")
        return None, None

    # Generate all format variations (no pattern hint = all variants)
    candidates = generate_candidates(first, last, domain, pattern=None)
    print(f"  Probing {len(candidates)} format(s) for {first} {last}:")

    for candidate in candidates:
        result = verify_email(candidate)
        status = result["status"]
        print(f"    {candidate:<40} -> {status}")
        if status == "verified":
            # Infer which pattern this candidate represents
            local = candidate.split("@")[0].lower()
            confirmed_pattern = _infer_confirmed_pattern(local, first.lower(), last.lower())
            return candidate, confirmed_pattern
        time.sleep(1.0)  # brief pause between probes

    return None, None


def _infer_confirmed_pattern(local, first, last):
    fi = first[0] if first else ""
    li = last[0]  if last  else ""
    mapping = {
        f"{first}.{last}": "first.last",
        f"{fi}{last}":     "flast",
        f"{first}{last}":  "firstlast",
        f"{first}":        "first",
        f"{fi}.{last}":    "f.last",
        f"{first}.{li}":   "first.l",
        f"{last}.{first}": "last.first",
        f"{li}{first}":    "lfirst",
    }
    return mapping.get(local, local)


for company in companies:
    group = by_company[company]
    count = len(group)
    print(f"\n  Company : {company} ({count} contacts)")

    # ── Step 1: Domain detection ─────────────────────────────────────────
    cached = cache_get(company)
    if cached and cached.get("domain"):
        domain         = cached["domain"]
        pattern        = cached.get("pattern")
        pattern_source = cached.get("pattern_source", "cache")
        print(f"  Domain  : {domain}  [from cache]")
        print(f"  Pattern : {pattern or 'unknown'}  ({pattern_source})")
    else:
        domain = find_domain(company)
        print(f"  Domain  : {domain or 'NOT FOUND'}")
        if domain:
            mx = get_mx(domain)
            print(f"  MX      : {mx or 'not found'}")
            pattern, pattern_source = detect_pattern(domain)
            print(f"  Pattern : {pattern or 'unknown'}  ({pattern_source})")
        else:
            pattern, pattern_source = None, "none"

    print()

    # ── Step 2: Domain override ──────────────────────────────────────────
    try:
        new_domain = input(f"  Override domain? [Enter to keep '{domain or ''}', or type new]: ").strip()
    except EOFError:
        new_domain = ""

    if new_domain:
        domain = new_domain
        mx = get_mx(domain)
        print(f"  MX      : {mx or 'not found'}")
        pattern, pattern_source = detect_pattern(domain)
        print(f"  Pattern : {pattern or 'unknown'}  ({pattern_source})")
        corrections += 1

    # ── Step 3: Pattern override ─────────────────────────────────────────
    print(f"  Valid patterns: {', '.join(VALID_PATTERNS)}")
    try:
        new_pattern = input(f"  Override pattern? [Enter to keep '{pattern or 'unknown'}', or type new]: ").strip().lower()
    except EOFError:
        new_pattern = ""

    if new_pattern:
        if new_pattern not in VALID_PATTERNS:
            print(f"  [!] '{new_pattern}' not recognized — saving anyway")
        pattern        = new_pattern
        pattern_source = "manual"
        corrections += 1

    # ── Step 4: SMTP probe ───────────────────────────────────────────────
    if domain:
        print()
        try:
            probe_ans = input("  Probe email formats via SMTP? [y/N]: ").strip().lower()
        except EOFError:
            probe_ans = "n"

        if probe_ans == "y":
            first, last, display_name = _pick_test_contact(group)
            if not first or not last:
                print("  [!] No usable test contact found — skipping probe")
            else:
                print(f"  Test contact: {display_name}")
                confirmed_email, confirmed_pattern = _probe_patterns(domain, first, last)
                if confirmed_email:
                    print(f"\n  [+] Confirmed: {confirmed_email}  (pattern: {confirmed_pattern})")
                    pattern        = confirmed_pattern
                    pattern_source = "smtp-verified"
                    corrections += 1
                else:
                    print(f"\n  [-] No format verified via SMTP.")
                    print(f"      Keeping pattern: {pattern or 'unknown'}")

    # ── Step 5: Save to cache ────────────────────────────────────────────
    if domain:
        cache_put(company, domain, pattern, pattern_source)
        print(f"\n  [saved] {company} -> {domain} / {pattern or 'unknown'} ({pattern_source})")

    print("-" * 60)

print(f"\nDone. {corrections} correction(s) made.")
print("Run Generate Emails.bat (or _generate_emails.py) to build addresses.\n")
