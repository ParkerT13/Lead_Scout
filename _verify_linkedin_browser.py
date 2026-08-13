"""
Batch LinkedIn verification using your real Chrome session.

Usage:
    python _verify_linkedin_browser.py input.csv [output.csv]
    python _verify_linkedin_browser.py --login-only

If output.csv is omitted, overwrites the input file.

First run: opens a Chrome window for you to log into LinkedIn.
           Session is saved -- future runs skip the login step entirely.
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verifier.browser_checker import verify_batch, ensure_logged_in

# --login-only: just open browser, wait for login, then exit
if len(sys.argv) >= 2 and sys.argv[1] == "--login-only":
    from playwright.sync_api import sync_playwright
    from verifier.browser_checker import _SESSION_DIR
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(_SESSION_DIR),
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            slow_mo=200,
        )
        ensure_logged_in(context)
        context.close()
    sys.exit(0)

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
