"""
Generate evidence-ranked emails without any SMTP/API verification.
Uses website harvesting + HubSpot KB + emailformat.com + search to find the
company's real format, and returns addresses published online verbatim.

Output columns: email, email_status, email_source, confidence
  found-on-site     address published on the web (High)
  pattern-confirmed format proven by evidence (Medium)
  pattern-ddg / best-guess  weaker evidence (Low) - run through a verifier
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from emailer import csv_runner

if __name__ == "__main__":
    csv_runner.main(False, "Usage: python _generate_emails.py <input.csv> [output.csv]")
