"""
Batch LinkedIn verification using your real Chrome session.

Usage:
    python _verify_linkedin_browser.py input.csv [output.csv]

If output.csv is omitted, overwrites the input file.

First run: opens a Chrome window for you to log into LinkedIn.
           Session is saved — future runs skip the login step entirely.
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verifier.browser_checker import verify_batch

if len(sys.argv) < 2:
    print("Usage: python _verify_linkedin_browser.py <input.csv> [output.csv]")
    sys.exit(1)

input_path  = Path(sys.argv[1])
output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else input_path

with input_path.open(encoding="utf-8") as f:
    reader    = csv.DictReader(f)
    fieldnames = list(reader.fieldnames)
    contacts  = list(reader)

if "emp_status" not in fieldnames:
    fieldnames.append("emp_status")

total = len(contacts)
print(f"Verifying {total} contacts via browser...\n")

contacts = verify_batch(contacts)

with output_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(contacts)

current = sum(1 for c in contacts if c.get("emp_status") == "current")
stale   = sum(1 for c in contacts if c.get("emp_status") == "stale")
unknown = sum(1 for c in contacts if c.get("emp_status") == "unknown")
error   = sum(1 for c in contacts if c.get("emp_status") == "error")

print(f"\nDone. Saved to: {output_path}")
print(f"  current : {current}")
print(f"  stale   : {stale}")
print(f"  unknown : {unknown}")
print(f"  error   : {error}")
