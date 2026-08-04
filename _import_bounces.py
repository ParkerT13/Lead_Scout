"""
Import bounce data from your ESP after a campaign send.

Usage:
    python _import_bounces.py bounces.csv [campaign_name]
    python _import_bounces.py bounces.txt [campaign_name]

Input file formats accepted:
  - CSV with an "email" column (HubSpot, Mailchimp, Instantly exports)
  - CSV where the first column is email addresses
  - Plain text, one email per line

After import:
  - Bounced addresses are stored in ~/ContactPuller_Output/bounces.json
  - Any enriched CSVs in Downloads are updated with a "bounced" flag
  - Domain cache patterns with high bounce rates are flagged
"""

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from emailer.bounce_tracker import record_bounces, all_bounces, bounce_rate_for_domain

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def load_bounce_emails(path: Path) -> list[str]:
    text    = path.read_text(encoding="utf-8", errors="replace")
    suffix  = path.suffix.lower()
    emails  = []

    if suffix == ".csv":
        reader = csv.DictReader(text.splitlines())
        # Try to find an email column
        email_col = next(
            (c for c in (reader.fieldnames or [])
             if "email" in c.lower()),
            None,
        )
        for row in reader:
            if email_col:
                e = row.get(email_col, "").strip()
            else:
                # Fall back to first column
                e = list(row.values())[0].strip() if row else ""
            if _EMAIL_RE.match(e):
                emails.append(e.lower())
    else:
        # Plain text — pull any email-looking token from each line
        for line in text.splitlines():
            m = _EMAIL_RE.search(line)
            if m:
                emails.append(m.group(0).lower())

    return emails


def update_enriched_csvs(bounced_set: set[str]):
    """Flag bounced addresses in any round*_enriched.csv files in Downloads."""
    downloads = Path.home() / "Downloads"
    updated   = 0
    for csv_path in downloads.glob("*_enriched.csv"):
        rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        if not rows:
            continue
        fields = list(rows[0].keys())
        if "bounced" not in fields:
            fields.append("bounced")
        changed = False
        for row in rows:
            email = row.get("email", "").lower().strip()
            if email in bounced_set:
                row["bounced"] = "yes"
                changed = True
            else:
                row.setdefault("bounced", "")
        if changed:
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            print(f"  Updated: {csv_path.name}")
            updated += 1
    return updated


def check_pattern_health():
    """Report any domains where bounce rate suggests the pattern is wrong."""
    bounces = all_bounces()
    domains: dict[str, int] = {}
    for email in bounces:
        if "@" in email:
            d = email.split("@")[1]
            domains[d] = domains.get(d, 0) + 1

    if not domains:
        return

    print("\nBounce counts by domain:")
    for domain, count in sorted(domains.items(), key=lambda x: -x[1]):
        print(f"  {domain}: {count} bounce(s)")
        if count >= 3:
            print(f"    ^ HIGH — consider updating pattern in domain_cache.json")


# ── Main ─────────────────────────────────────────────────────────────────────

if len(sys.argv) < 2:
    print("Usage: python _import_bounces.py <bounce_file.csv|.txt> [campaign_name]")
    sys.exit(1)

input_path = Path(sys.argv[1])
campaign   = sys.argv[2] if len(sys.argv) > 2 else ""

if not input_path.exists():
    print(f"File not found: {input_path}")
    sys.exit(1)

print(f"Loading bounces from: {input_path.name}")
emails = load_bounce_emails(input_path)

if not emails:
    print("No email addresses found in file.")
    sys.exit(1)

print(f"Found {len(emails)} bounced addresses.")
record_bounces(emails, campaign)
print(f"Saved to ~/ContactPuller_Output/bounces.json")

print("\nUpdating enriched CSVs in Downloads...")
n = update_enriched_csvs(set(emails))
if n == 0:
    print("  No enriched CSVs found to update.")

check_pattern_health()

print(f"\nDone. Total bounces on record: {len(all_bounces())}")
