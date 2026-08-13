# Lead Scout — Setup Guide

---

## Before You Start

You need **Python** installed on your computer. To check:

1. Click the Windows Start button, type **cmd**, open **Command Prompt**
2. Type `python --version` and press Enter
3. If you see `Python 3.12.x` or higher — you're good, skip to Step 1
4. If you get an error — download Python from **python.org/downloads**
   - On the install screen, check **"Add Python to PATH"** before clicking Install

---

## First-Time Setup (Do This Once)

1. Download the **Lead_Scout** folder from GitHub and put it somewhere on your computer (Desktop is fine)
2. Open the folder
3. Double-click **install.bat**
4. A black window runs automatically — wait for it to say "Setup complete!"
5. Close the window

That's it. You only do this once.

---

## Opening the App

Double-click **LeadScout.pyw** — no console window, just the app.

Or double-click **Run Lead Scout.bat** if you want to see the log output (useful for troubleshooting).

---

## First Launch

On first launch a setup wizard walks you through three things:

1. **Output folder** — where your contacts and emails save (default: `C:\Users\YourName\LeadScout_Output\`)
2. **LinkedIn session** — click "Log in to LinkedIn now" and sign in once; the session is saved permanently so you won't be asked again
3. **Email knowledge base** — a shared base of 978 company email formats is included automatically; if you have a HubSpot contacts export you can build a personal one with `Build Knowledge Base.bat`

---

## How to Pull Contacts

1. In the **Lead Scout** tab, type a company name (e.g. `Pioneer Natural Resources`)
2. Click **Add to Queue** — add as many companies as you want
3. Click the green **Start** button
4. Contacts appear in the table as they're found
5. When done, click **Export CSV** or move straight to email enrichment

> The tool searches at a deliberate pace to avoid blocks. A batch of 5 companies typically takes 45–90 minutes. Start it and walk away.

---

## How to Generate Emails

1. After pulling contacts, switch to the **Email Enricher** tab
2. Click **Load from Lead Scout** to pull in the contacts you just found
3. Click **Start Enrichment**
4. The tool detects the company's email format and generates best-guess addresses

**Email statuses:**
- `verified` — SMTP confirmed, the mailbox accepted the probe (green)
- `catch-all-confirmed` — domain accepts all mail but format is reliably sourced (orange)
- `catch-all-risky` — domain accepts all mail and format was guessed (red)
- `unverified` — port 25 is blocked on your network; email is pattern-based but not SMTP-tested

> **If most emails show "unverified":** Your office network blocks outbound port 25 (very common). The app automatically routes through NeverBounce and Reoon if credentials are configured — ask Parker for the `credentials.json` file and drop it in your Lead Scout folder. That's it.
>
> If you don't have that file yet, use **Generate Emails.bat** to export contacts and verify externally.

---

## How to Verify Employment

Before emailing, confirm each contact is still at the company:

1. In the Lead Scout tab, select contacts and click **Verify**
   - Or drag your contacts CSV onto **Verify LinkedIn.bat**
2. The tool checks each LinkedIn profile automatically

**Employment statuses:**
- `current` — confirmed still at the company
- `stale` — moved to a different company — remove before emailing
- `unknown` — couldn't confirm either way — keep them, it doesn't mean they left

> **First time only:** If you skipped the LinkedIn login during setup, a browser window opens asking you to log in. Do it once — the session is saved.

---

## How to Export to CRM

1. Switch to the **CRM Export** tab
2. Choose your CRM format (HubSpot, Salesforce, or Generic CSV)
3. Select which fields to include
4. Click **Export**

---

## How to Import Bounces After a Campaign

After sending, export the bounce list from HubSpot, Mailchimp, Instantly, etc. and:

1. Double-click **Import Bounces.bat**
2. Drag your bounce CSV into the window and press Enter
3. Type a campaign name (optional) and press Enter

Bounced addresses are saved permanently. The tool skips them on every future run automatically.

---

## Improving Email Accuracy Over Time

If you know a company's email format from a real contact (e.g. someone replied), confirm it permanently:

1. Double-click **Confirm Pattern.bat**
2. Enter: company name, the confirmed email, first name, last name

This updates the domain cache and knowledge base — every future pull for that company uses the confirmed format.

---

## Where Are My Files?

Everything saves to: `C:\Users\YourName\LeadScout_Output\`

| File | What it is |
|---|---|
| `all_contacts.csv` | Every contact ever pulled — running master list |
| `CompanyName_contacts.csv` | Contacts from a specific company |
| `domain_cache.json` | Email domains and patterns the tool has learned — don't delete |
| `bounces.json` | All bounce history — don't delete |
| `browser_session/` | Saved LinkedIn login — don't delete |

---

## Something Not Working?

**App won't open after double-clicking LeadScout.pyw**
Run `install.bat` again. If still broken, try `Run Lead Scout.bat` — it shows error messages.

**LinkedIn window opens but closes immediately**
Your saved session expired. Delete `LeadScout_Output\browser_session\` then click the LinkedIn button in the app toolbar to log in again.

**Finding zero contacts for a company**
Some smaller companies aren't well-indexed. This isn't a bug — try a slightly different company name variant.

**All emails say "unverified"**
Your network blocks outbound port 25. Use `Generate Emails.bat` + MillionVerifier/NeverBounce. See the Email Enricher section above.

**Any other issue**
Contact Parker.
