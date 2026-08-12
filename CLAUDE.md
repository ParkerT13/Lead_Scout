# ContactPuller — Claude Context File

This file is for AI assistants picking up this project. Read it fully before touching any code.

---

## What This Is

A Windows desktop tool for O&G sales prospecting. It finds LinkedIn contacts at target companies, verifies they still work there, and generates best-guess email addresses — all without paid APIs or third-party subscriptions.

Built for Parker's SeisWare Mercury sales workflow. Designed to be shared with non-technical colleagues via GitHub + double-click batch files.

---

## Stack

- **Python 3.14** (Windows, pip-managed, no conda/venv enforced)
- **PySide6** — GUI framework (QMainWindow, QThread, Signal/Slot)
- **Playwright** — Chromium browser automation for LinkedIn login session
- **requests** — HTTP for domain validation and web scraping
- **Standard library** — csv, json, pathlib, re, smtplib, socket, time, random

No paid APIs. No cloud services. Everything runs locally.

---

## Architecture

```
ContactPuller/
├── main.py                        # Entry point — launches PySide6 app
├── ui/
│   └── main_window.py             # Main GUI: Contact Puller tab + email enrichment
├── scraper/
│   ├── worker.py                  # QThread: runs multi-engine search
│   ├── query_builder.py           # Builds LinkedIn search queries per company
│   ├── parser.py                  # Extracts name/title/company/URL from snippets
│   └── engines/
│       ├── duckduckgo.py          # DDG HTML scraper
│       ├── bing.py                # Bing HTML scraper
│       └── google.py             # Google HTML scraper
├── verifier/
│   ├── linkedin_checker.py        # DDG-based employment check (no login)
│   ├── browser_checker.py         # Playwright browser verification (logged-in LinkedIn)
│   └── worker.py                  # QThread wrapper for linkedin_checker
├── emailer/
│   ├── domain_finder.py           # Discovers company email domain (HTTP validation + DDG)
│   ├── pattern_detector.py        # Scrapes company website for email pattern
│   ├── generator.py               # Builds email candidates from name + domain + pattern
│   ├── smtp_verifier.py           # SMTP RCPT TO probe + MX lookup + catch-all detection
│   ├── domain_cache.py            # Persists domain/pattern discoveries to JSON
│   ├── bounce_tracker.py          # Persists hard-bounced addresses to JSON
│   └── worker.py                  # QThread: runs full email enrichment pipeline
├── output/
│   └── csv_writer.py              # Writes contacts to all_contacts.csv + per-company CSV
│
├── _verify_batch.py               # CLI: DDG-verify a CSV of contacts
├── _verify_linkedin_browser.py    # CLI: Browser-verify a CSV via Playwright
├── _generate_emails.py            # CLI: Generate emails without SMTP (pattern-based only)
├── _enrich_batch.py               # CLI: Full SMTP enrichment pipeline on a CSV
├── _import_bounces.py             # CLI: Import bounce CSV from ESP, update bounce tracker
│
├── install.bat                    # One-time setup: pip install + playwright install chromium
├── Run ContactPuller.bat          # Launches main.py
├── Verify LinkedIn.bat            # Drag-and-drop CSV → _verify_linkedin_browser.py
├── Generate Emails.bat            # Drag-and-drop CSV → _generate_emails.py
├── Import Bounces.bat             # Drag-and-drop bounce CSV → _import_bounces.py
│
├── requirements.txt
├── README.md                      # User-facing feature overview
└── SETUP.md                       # Non-technical setup guide (plain English)
```

---

## Key Design Decisions

### LinkedIn Scraping
- Queries are built as `"Title" "Company" site:linkedin.com/in` across DDG, Bing, Google
- Results are parsed from HTML snippets — no LinkedIn API, no Selenium
- LinkedIn blocks direct profile fetches with HTTP 999 — never attempt direct LinkedIn requests

### Employment Verification
- **DDG method** (`linkedin_checker.py`): searches `"Name" "Company" site:linkedin.com/in`, checks if the profile slug appears with company in the title. ~50% coverage. No login needed.
- **Browser method** (`browser_checker.py`): Playwright persistent context saves login to `~/ContactPuller_Output/browser_session/`. Checks og:title, headline, and experience section. Near 100% coverage.
- Returns: `current`, `stale`, or `unknown`

### Domain Discovery (`domain_finder.py`)
Priority order:
1. Check `domain_cache.json` first
2. HTTP HEAD validate guessed slugs (e.g. `vitalenergy.com`, `chordenergy.com`) — most reliable
3. DDG search with scoring: key words = 2pts, generic words = 1pt, exact slug match = +10pts
4. Return unvalidated guess as last resort

**Known pitfall**: DDG returns O&G media sites (hartenergy.com) and unrelated businesses (vital.audio) when scoring isn't strict. HTTP validation of the slug guess is what prevents this.

### Email Pattern Detection (`pattern_detector.py`)
- Scrapes 14 paths: `/contact`, `/about`, `/team`, `/leadership`, `/staff`, `/our-team`, `/management`, `/about/team`, `/about/leadership`, `/team-members`, `/people`, `/about-us`, `/company/team`, `/who-we-are`
- Returns `(pattern, source)` tuple where source is `'scraped'`, `'ddg'`, or `'none'`
- Pattern examples: `first.last`, `flast`, `firstlast`, `f.last`

### SMTP Verification (`smtp_verifier.py`)
- RCPT TO probe via port 25
- **Residential IP limitation**: Spamhaus PBL blocks port 25 from most home/office IPs. All SMTP probes return 550. Use `_generate_emails.py` instead and run through MillionVerifier/NeverBounce externally.
- Catch-all detection: sends probe to `catch-all-test-{uuid}@domain` — if accepted, domain accepts everything
- `catch-all-confirmed` = pattern was scraped from website (lower risk)
- `catch-all-risky` = pattern was guessed/DDG (high risk, needs external verification)

### Bounce Tracker (`bounce_tracker.py`)
- Persists to `~/ContactPuller_Output/bounces.json`
- `record_bounces(emails, campaign)` — called from `_import_bounces.py` after ESP export
- `is_bounced(email)` — called in `_generate_emails.py` and `emailer/worker.py` before generating

### Domain Cache (`domain_cache.py`)
- Persists to `~/ContactPuller_Output/domain_cache.json`
- Schema: `{ "Company Name": { "domain": "...", "pattern": "...", "pattern_source": "..." } }`
- Checked at start of every email enrichment run — each company only discovered once

---

## Output Fields

CSV columns (in order):
`name, title, company, location, basin, linkedin_url, emp_status, date_pulled, email, email_status, email_source`

`email_status` values:
- `pattern-confirmed` — pattern scraped from company website
- `pattern-ddg` — pattern found via DDG search
- `best-guess` — no pattern found, used first.last
- `verified` — SMTP confirmed deliverable
- `catch-all-confirmed` — catch-all domain, scraped pattern
- `catch-all-risky` — catch-all domain, guessed pattern
- `bounced` — previously hard-bounced, skipped
- `no domain` / `no MX` / `no candidates` / `error` — failure states

---

## Known Constraints

- **Spamhaus PBL**: Residential/office IPs cannot send SMTP probes. `_generate_emails.py` is the correct path for most users. Recommend MillionVerifier or NeverBounce for verification.
- **LinkedIn bot detection**: HTTP 999 on all direct profile fetches. Only browser (Playwright) method works reliably.
- **Windows cp1252 encoding**: All console output must be ASCII-only (no →, ✓, ✗). Use `->`, `[+]`, `[-]`, `[?]` instead.
- **Python 3.14**: This project runs on 3.14. No compatibility hacks needed for older versions.

---

## Colleagues Setup

Non-technical users follow `SETUP.md`. The flow is:
1. Download ZIP from GitHub
2. Run `install.bat`
3. Double-click `Run ContactPuller.bat`

GitHub repo: https://github.com/ParkerT13/ContactPuller
