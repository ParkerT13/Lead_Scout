"""
Pre-flight domain and email pattern checker.

For each company in a CSV, detects the email domain and format pattern,
displays the result, and lets you correct either one before running the
email generator. Saves all corrections to the domain cache so the main
run uses the right values automatically.

Usage:
    python _check_domains.py <input.csv>
    or drag and drop onto Check Domains.bat
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from emailer.domain_finder import find_domain
from emailer.pattern_detector import detect_pattern
from emailer.smtp_verifier import get_mx
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

for company in companies:
    count = len(by_company[company])
    print(f"\n  Company : {company} ({count} contacts)")

    # Check cache first
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

    # Domain override
    prompt_d = f"  Override domain? [Enter to keep '{domain or ''}', or type new domain]: "
    try:
        new_domain = input(prompt_d).strip()
    except EOFError:
        new_domain = ""

    if new_domain:
        domain = new_domain
        print(f"  Domain set to: {domain}")
        # Re-detect pattern for new domain
        mx = get_mx(domain)
        print(f"  MX      : {mx or 'not found'}")
        pattern, pattern_source = detect_pattern(domain)
        print(f"  Pattern : {pattern or 'unknown'}  ({pattern_source})")
        corrections += 1

    # Pattern override
    print(f"  Valid patterns: {', '.join(VALID_PATTERNS)}")
    prompt_p = f"  Override pattern? [Enter to keep '{pattern or 'unknown'}', or type new pattern]: "
    try:
        new_pattern = input(prompt_p).strip().lower()
    except EOFError:
        new_pattern = ""

    if new_pattern:
        if new_pattern not in VALID_PATTERNS:
            print(f"  [!] '{new_pattern}' not in known patterns — saving anyway")
        pattern        = new_pattern
        pattern_source = "manual"
        print(f"  Pattern set to: {pattern}")
        corrections += 1

    # Save to cache
    if domain:
        cache_put(company, domain, pattern, pattern_source)
        print(f"  [saved] {company} -> {domain} / {pattern or 'unknown'}")

    print("-" * 60)

print(f"\nDone. {corrections} correction(s) made.")
print("Run Generate Emails.bat (or _generate_emails.py) to build addresses.\n")
