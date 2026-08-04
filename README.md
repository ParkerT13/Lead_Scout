# Contact Puller — O&G LinkedIn Scraper & Email Enricher

A desktop tool for finding, verifying, and enriching LinkedIn contacts at oil and gas companies. Built for Mercury/unconventional E&P prospecting.

---

## What It Does

**Step 1 — Pull Contacts**
Searches LinkedIn via DuckDuckGo, Bing, and Google for engineers, geoscientists, and executives currently employed at a target company. Filters out false positives (wrong company, students, board members, self-employed). Auto-saves results to CSV.

**Step 2 — Verify Employment**
Confirms each contact is still at the company using either:
- DDG search verification (quick, no login needed, ~50% coverage)
- Browser verification using your logged-in LinkedIn session (near 100% coverage, recommended)

**Step 3 — Enrich Emails**
Finds the company's email domain, detects the email format pattern (e.g. `first.last`, `flast`) by scraping the company website, then generates best-guess email addresses. Caches domain/pattern data so each company only needs to be discovered once.

**Step 4 — Import Bounces**
After a campaign, import your ESP's bounce list. Bounced addresses are permanently flagged and auto-skipped on future runs. Identifies domains where the pattern guess was wrong.

---

## Output Files

All output saves to `C:\Users\<you>\ContactPuller_Output\`

| File | Contents |
|---|---|
| `all_contacts.csv` | Every contact ever pulled, appended in real time |
| `<Company>_contacts.csv` | Per-company contact file |
| `failed_companies.txt` | Companies where zero contacts were found |
| `domain_cache.json` | Discovered email domains and patterns (persists across runs) |
| `bounces.json` | Hard-bounced email addresses (persists across runs) |
| `browser_session/` | Saved LinkedIn browser session (created on first verify run) |

---

## Contact Fields

| Field | Description |
|---|---|
| name | Full name |
| title | Job title extracted from LinkedIn snippet |
| company | Target company searched |
| location | City/state if visible in snippet |
| basin | O&G basin detected from location keywords |
| linkedin_url | Direct LinkedIn profile URL |
| emp_status | current / stale / unknown (after verification) |
| date_pulled | Date the contact was found |

---

## Who Gets Included / Excluded

**Included:** Engineers, geoscientists, executives (CEO, COO, CFO, VP, President, Director), managers, analysts, land professionals, operations staff.

**Excluded:** Board members, investors, independent consultants, freelancers, students, interns, drilling engineers (out of scope for Mercury ICP).
