# Lead Scout — O&G Sales Intelligence

A Windows desktop tool for finding, verifying, and enriching LinkedIn contacts at oil and gas companies. Built for SeisWare Mercury prospecting in unconventional E&P.

No paid APIs. No cloud services. Everything runs locally.

---

## What It Does

**Step 1 — Pull Contacts**
Searches LinkedIn via DuckDuckGo, Bing, and Google for engineers, geoscientists, and executives at a target company. Filters out false positives (wrong company, students, board members, self-employed). Auto-saves results to CSV.

**Step 2 — Verify Employment**
Confirms each contact is still at the company using either:
- DDG search verification (~50% coverage, no login needed)
- Browser verification via a saved LinkedIn session (near 100% coverage, recommended)

**Step 3 — Enrich Emails**
Detects the company's email domain and format pattern (`first.last`, `flast`, etc.) by checking a 978-domain knowledge base, scraping the company website, and querying search engines. Generates ranked email candidates. SMTP-verifies when port 25 is available; falls back to pattern-based generation + external verifier when blocked.

**Step 4 — Export to CRM**
Exports contacts with emails to HubSpot, Salesforce, or a generic CSV with configurable field mapping.

**Step 5 — Import Bounces**
After a campaign, import your ESP's bounce list. Bounced addresses are permanently flagged and auto-skipped on all future runs.

---

## Email Format Coverage

Pattern detection pulls from four sources in order:
1. **Personal HubSpot KB** — built from your own CRM exports (most accurate)
2. **Shared KB** — 978 domains with confirmed patterns, committed to this repo (no real emails)
3. **emailformat.com** — crowdsourced format database
4. **Company website scraping** — looks for name+email pairs on team/contact pages
5. **DDG search** — extracts patterns from search snippets

Candidate generation order (by real O&G frequency, 2,978 contacts):

| Format | Example | Frequency |
|--------|---------|-----------|
| `first.last` | john.smith@co.com | 38.9% |
| `flast` | jsmith@co.com | 37.4% |
| `first_last` | john_smith@co.com | 10.9% |
| `first` | john@co.com | 7.4% |
| `firstlast` | johnsmith@co.com | 2.4% |
| `lastf` | smithj@co.com | 1.4% |
| `last` | smith@co.com | 0.7% |
| `f.last` | j.smith@co.com | 0.6% |

---

## Output Files

All output saves to `C:\Users\<you>\LeadScout_Output\`

| File | Contents |
|---|---|
| `all_contacts.csv` | Every contact ever pulled, appended in real time |
| `<Company>_contacts.csv` | Per-company contact file |
| `domain_cache.json` | Discovered email domains and patterns (persists across runs) |
| `bounces.json` | Hard-bounced email addresses (persists across runs) |
| `browser_session/` | Saved LinkedIn browser session |

---

## Who Gets Included / Excluded

**Included:** Petroleum engineers, reservoir engineers, drilling engineers, completions engineers, production engineers, geologists, geophysicists, geoscientists, petrophysicists, seismic interpreters, and leadership (CEO, COO, VP, Director, Manager, Principal, Staff).

**Excluded:** Board members, investors, independent consultants, freelancers, students, interns.

---

## Contact Fields

| Field | Description |
|---|---|
| `first_name` / `last_name` | Parsed from LinkedIn snippet |
| `title` | Job title |
| `company` | Target company searched |
| `location` | City/state if visible in snippet |
| `basin` | O&G basin detected from location |
| `linkedin_url` | Direct LinkedIn profile URL |
| `emp_status` | `current` / `stale` / `unknown` (after verification) |
| `date_pulled` | Date the contact was found |
| `email` | Best-guess or verified email address |
| `email_status` | `verified` / `catch-all-confirmed` / `catch-all-risky` / `unverified` / `bounced` |
| `confidence` | `High` / `Medium` / `Low` (for CRM import) |

---

## Setup

See **SETUP.md** for full installation and usage instructions.

**Quick start:**
1. Download this repo as a ZIP
2. Run `install.bat`
3. Double-click `LeadScout.pyw`

---

## Known Constraints

- **Port 25 blocked on most office networks** — use `Generate Emails.bat` + MillionVerifier/NeverBounce for external verification
- **LinkedIn bot detection** — direct LinkedIn profile fetches return HTTP 999; only search-engine snippets and Playwright browser method are used
- **Personal HubSpot KB** — `emailer/hubspot_knowledge.json` is gitignored (contains real emails); regenerate with `Build Knowledge Base.bat` from your HubSpot contacts export
