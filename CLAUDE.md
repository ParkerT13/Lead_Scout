# Lead Scout — Claude Context File

This file is for AI assistants picking up this project. Read it fully before touching any code.

---

## What This Is

**Lead Scout** is a Windows desktop tool for O&G sales prospecting. It finds LinkedIn contacts at target companies, verifies employment, generates best-guess email addresses, and exports to CRM — all without paid APIs or third-party subscriptions.

Built for Parker's SeisWare Mercury sales workflow. Designed to be shared with non-technical colleagues via GitHub + double-click batch files.

GitHub: https://github.com/ParkerT13/Lead_Scout

---

## Stack

- **Python 3.14** (Windows, pip-managed, no conda/venv enforced)
- **PySide6** — GUI framework (QMainWindow, QThread, Signal/Slot)
- **Playwright** — Chromium browser automation for LinkedIn login session
- **Pillow** — logo/icon generation
- **requests** — HTTP for domain validation and web scraping
- **Standard library** — csv, json, pathlib, re, smtplib, socket, time, random

No paid APIs. No cloud services. Everything runs locally.

---

## Architecture

```
Lead_Scout/
├── main.py                          # Entry point — launches PySide6 app
├── LeadScout.pyw                    # Silent launcher (no console window, double-click this)
├── ui/
│   └── main_window.py               # Full GUI: 3 tabs — Lead Scout, Email Enricher, CRM Export
├── scraper/
│   ├── worker.py                    # QThread: runs multi-engine LinkedIn search
│   ├── query_builder.py             # Builds LinkedIn search queries per company
│   ├── parser.py                    # Extracts name/title/company/URL from snippets
│   └── engines/
│       ├── duckduckgo.py            # DDG HTML scraper
│       ├── bing.py                  # Bing HTML scraper
│       └── google.py                # Google HTML scraper
├── verifier/
│   ├── linkedin_checker.py          # DDG-based employment check (no login)
│   ├── browser_checker.py           # Playwright browser verification (logged-in LinkedIn)
│   └── worker.py                    # QThread wrapper for linkedin_checker
├── emailer/
│   ├── domain_finder.py             # Discovers company email domain (HTTP validation + DDG)
│   ├── pattern_detector.py          # Scrapes company website + HubSpot KB for email pattern
│   ├── generator.py                 # Builds 8 email candidates from name + domain + pattern
│   ├── smtp_verifier.py             # SMTP RCPT TO probe + MX lookup + catch-all detection
│   ├── domain_cache.py              # Persists domain/pattern discoveries to JSON
│   ├── bounce_tracker.py            # Persists hard-bounced addresses to JSON
│   ├── hubspot_knowledge.json       # GITIGNORED — 941 domain→pattern mappings from CRM
│   └── worker.py                    # QThread: runs full email enrichment pipeline
├── output/
│   ├── csv_writer.py                # Writes contacts to all_contacts.csv + per-company CSV
│   └── basin_mapper.py              # Maps LinkedIn location strings to O&G basin names
│
├── _verify_batch.py                 # CLI: DDG-verify a CSV of contacts
├── _verify_linkedin_browser.py      # CLI: Browser-verify a CSV via Playwright (--login-only supported)
├── _generate_emails.py              # CLI: Generate emails without SMTP (pattern-based only)
├── _enrich_batch.py                 # CLI: Full SMTP enrichment pipeline on a CSV
├── _import_bounces.py               # CLI: Import bounce CSV from ESP, update bounce tracker
├── _build_kb.py                     # CLI: Build hubspot_knowledge.json from HubSpot CSV export
├── _confirm_pattern.py              # CLI: Manually confirm a domain pattern, write to cache + KB
│
├── generate_logo.py                 # Generates assets/logo.svg, .png (512px), .ico (6 sizes)
├── _create_shortcut.py              # Creates Lead Scout.lnk on Desktop via PowerShell
├── Create Desktop Shortcut.bat      # Runs generate_logo.py then _create_shortcut.py
├── install.bat                      # One-time setup: pip install + playwright install chromium
├── Run Lead Scout.bat               # Launches main.py with console (for debugging)
├── Verify LinkedIn.bat              # Drag-and-drop CSV → _verify_linkedin_browser.py
├── Generate Emails.bat              # Drag-and-drop CSV → _generate_emails.py
├── Import Bounces.bat               # Drag-and-drop bounce CSV → _import_bounces.py
├── Build Knowledge Base.bat         # Runs _build_kb.py interactively
├── Confirm Pattern.bat              # Runs _confirm_pattern.py interactively
│
├── assets/
│   ├── logo.svg                     # Scalable source (dark navy, orange crosshair, white person)
│   ├── logo.png                     # 512x512 PNG
│   └── logo.ico                     # Multi-size ICO (16/32/48/64/128/256px)
│
├── requirements.txt
├── README.md                        # User-facing feature overview
└── SETUP.md                         # Non-technical setup guide (plain English)
```

---

## Output Location

All data persists to `~/LeadScout_Output/`:
- `all_contacts.csv` — master contact list across all pulls
- `domain_cache.json` — company → domain + email pattern cache
- `bounces.json` — hard-bounced email addresses
- `browser_session/` — Playwright LinkedIn login session (Chromium profile)
- Per-company CSVs named by company

---

## Key Design Decisions

### GUI — Three Tabs
1. **Lead Scout tab** — pull contacts by company, title filter (64 keywords, 3 groups), live search/filter, seniority scoring, completeness indicator, cross-session duplicate detection
2. **Email Enricher tab** — enrich contacts with email addresses, 8-candidate preview panel with per-candidate bounce/status, right-click to override or remove from bounce list, MV/NeverBounce export+import, bounce importer
3. **CRM Export tab** — preview and export enriched contacts for HubSpot/Salesforce

### Email Format Candidates (ordered by real-world O&G frequency)
Based on analysis of 941 domains / 2,978 contacts from HubSpot CRM data:

| Format | Example | O&G Frequency |
|--------|---------|---------------|
| `flast` | jsmith@co.com | 42% |
| `first.last` | john.smith@co.com | 31% |
| `first` | john@co.com | 20% |
| `firstlast` | johnsmith@co.com | 6% |
| `f.last` | j.smith@co.com | 1.4% |
| `first.l` | john.s@co.com | 0.5% |
| `first_last` | john_smith@co.com | rare |
| `last` | smith@co.com | rare |

`last.first` and `lfirst` were dropped — zero occurrences in O&G contact data.

### HubSpot Knowledge Base (`emailer/hubspot_knowledge.json`)
- 941 domain → pattern mappings derived from real HubSpot CRM contacts
- Schema: `{ "domain": { "pattern": "flast", "confidence": 0.95, "sample_count": 38, "samples": [...] } }`
- **Checked first** in pattern detection chain — instant, no network calls
- GITIGNORED (contains real email addresses) — regenerate with `_build_kb.py` from your HubSpot CSV export
- To build: `python _build_kb.py` (requires `Hubspot-Contacts.csv` in project root or Downloads)

### Pattern Detection Order (`pattern_detector.py`)
1. HubSpot KB (instant, 941 domains covered)
2. emailformat.com scrape
3. Company website scraping (14 paths)
4. DDG search
5. Returns `(pattern, source)` — source affects confidence and catch-all labeling

### LinkedIn Scraping
- Queries built as `"Title" "Company" site:linkedin.com/in` across DDG, Bing, Google
- Results parsed from HTML snippets — no LinkedIn API, no Selenium
- LinkedIn blocks direct profile fetches with HTTP 999 — never attempt direct LinkedIn requests

### Employment Verification
- **DDG method** (`linkedin_checker.py`): ~50% coverage, no login needed
- **Browser method** (`browser_checker.py`): Playwright persistent context, near 100% coverage
- Session saved to `~/LeadScout_Output/browser_session/`
- Returns: `current`, `stale`, or `unknown`
- Re-login via in-app button (LinkedIn session indicator in toolbar) or `--login-only` flag

### SMTP Verification (`smtp_verifier.py`)
- RCPT TO probe via port 25
- **Residential IP limitation**: Spamhaus PBL blocks port 25. Use `_generate_emails.py` + MillionVerifier/NeverBounce externally.
- Catch-all detection: probe to `catch-all-test-{uuid}@domain`
- `catch-all-confirmed` = scraped pattern (lower risk)
- `catch-all-risky` = guessed pattern (needs external verification)

### Bounce Tracker (`bounce_tracker.py`)
- Persists to `~/LeadScout_Output/bounces.json`
- `record_bounces(emails, campaign)` — import from ESP CSV
- `remove_bounce(email)` — clear false positives (available via right-click in email preview)
- `is_bounced(email)` — checked before generating each email

### Domain Cache (`domain_cache.py`)
- Persists to `~/LeadScout_Output/domain_cache.json`
- Schema: `{ "Company Name": { "domain": "...", "pattern": "...", "pattern_source": "..." } }`
- In-app editor available (Settings → Edit Domain Cache) with KB confidence column

---

## Session Persistence
- On close: saves contacts + enriched list + queue to `session.json` in output dir
- On open: offers to restore previous session
- Cross-session duplicate detection: loads all LinkedIn URLs from `all_contacts.csv` at startup

---

## Title Filter (64 keywords, 3 groups — all checked by default)
- **Geoscience**: geologist, geophysicist, geoscientist, seismic, petrophysicist, stratigraph, etc.
- **Engineering**: reservoir engineer, drilling engineer, completion, production, petroleum engineer, etc.
- **Leadership & Technical**: VP, director, manager, chief geoscientist, technical lead, etc.
- Configured via checkbox dialog in Lead Scout tab — state persists in `settings.json`

---

## Output Fields

CSV columns: `name, title, company, location, basin, linkedin_url, emp_status, date_pulled, email, email_status, email_source`

`email_status` values:
- `pattern-confirmed` — pattern scraped from company website
- `pattern-ddg` — pattern found via DDG
- `hubspot` — pattern from HubSpot KB
- `best-guess` — no pattern found, used flast (most common)
- `verified` — SMTP confirmed
- `catch-all-confirmed` / `catch-all-risky`
- `bounced` — skipped (in bounce tracker)
- `manual-override` — user selected from candidate preview
- `no domain` / `no MX` / `no candidates` / `error`

---

## Known Constraints

- **Spamhaus PBL**: Port 25 blocked on residential/office IPs. Use pattern-based generation + external verifier.
- **LinkedIn bot detection**: HTTP 999 on direct fetches. Playwright browser method only.
- **Windows cp1252**: Console output must be ASCII-only. Use `->`, `[+]`, `[-]`, `[?]`.
- **Python 3.14**: No compatibility shims needed.
- **hubspot_knowledge.json**: Gitignored. Must regenerate locally from HubSpot CSV export.

---

## Colleagues Setup

1. Download ZIP from https://github.com/ParkerT13/Lead_Scout
2. Run `install.bat`
3. Double-click `LeadScout.pyw` (no console) or `Run Lead Scout.bat` (with console for debugging)
4. Run `Create Desktop Shortcut.bat` to add to Desktop
