"""
Standalone batch employment verifier.
Reads round2.csv, checks each LinkedIn URL against the contact's company,
writes round2_verified.csv with an emp_status column added.
"""

import csv
import time
import random
import sys
from pathlib import Path

# Make sure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from verifier.linkedin_checker import check_employment

if len(sys.argv) < 2:
    print("Usage: python _verify_batch.py <input.csv> [output.csv]")
    sys.exit(1)

INPUT  = Path(sys.argv[1])
OUTPUT = Path(sys.argv[2]) if len(sys.argv) > 2 else INPUT.parent / (INPUT.stem + "_verified.csv")

DELAY = (4.0, 9.0)  # seconds between requests

rows = list(csv.DictReader(INPUT.open(encoding="utf-8")))
total = len(rows)
print(f"Verifying {total} contacts...\n")

fieldnames = list(rows[0].keys())
if "emp_status" not in fieldnames:
    fieldnames.append("emp_status")

with OUTPUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()

    for i, row in enumerate(rows):
        url     = row.get("linkedin_url", "").strip()
        company = row.get("company", "").strip()
        name    = row.get("name", "Unknown").strip()

        if not url or not company:
            row["emp_status"] = "unknown"
            writer.writerow(row)
            print(f"[{i+1}/{total}] {name} — skipped (missing url/company)")
            continue

        status = check_employment(url, company, name)
        row["emp_status"] = status

        symbol = {"current": "[+]", "stale": "[-]", "unknown": "[?]"}.get(status, "[?]")
        print(f"[{i+1}/{total}] {symbol} {name} ({company}) -> {status}")

        # Flush so output is visible in real time
        f.flush()

        if i < total - 1:
            time.sleep(random.uniform(*DELAY))

print(f"\nDone. Results saved to: {OUTPUT}")

# Print summary
current = sum(1 for r in rows if r.get("emp_status") == "current")
stale   = sum(1 for r in rows if r.get("emp_status") == "stale")
unknown = sum(1 for r in rows if r.get("emp_status") == "unknown")
print(f"  current : {current}")
print(f"  stale   : {stale}")
print(f"  unknown : {unknown}")
