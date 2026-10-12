"""
Full pipeline - CLI version (shared engine: emailer/engine.py).

For each company in the input CSV: find + sanity-check the domain, detect the
email format (HubSpot KB, website harvest, emailformat.com, search), use any
address published online, verify the rest (SMTP if port 25 is open, otherwise
NeverBounce/Reoon from credentials.json), then keep only viable contacts.

Usage:
    python _pipeline.py <input.csv> [output.csv]
    or drag and drop onto Run Pipeline.bat
Writes <output>.csv (everything) and <output>_viable.csv (deliverable only).
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from emailer import csv_runner
from emailer.engine import DROP_STATUSES

if __name__ == "__main__":
    paid = "--paid-verify" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--paid-verify"]
    if not args:
        print("Usage: python _pipeline.py <input.csv> [output.csv] [--paid-verify]")
        sys.exit(1)
    src = Path(args[0])
    dst = Path(args[1]) if len(args) > 1 else src.parent / (src.stem + "_pipeline.csv")
    results = csv_runner.run(src, dst, verify=True, paid=paid)

    viable = [r for r in results if r.get("email") and r.get("email_status") not in DROP_STATUSES]
    vpath = dst.parent / (dst.stem + "_viable.csv")
    with dst.open(encoding="utf-8", newline="") as f:
        fields = csv.DictReader(f).fieldnames
    with vpath.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(viable)
    print(f"\nViable: {len(viable)} of {len(results)} -> {vpath.name}")
