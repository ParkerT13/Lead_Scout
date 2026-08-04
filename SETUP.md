# Contact Puller — Setup Guide

---

## Before You Start

You need **Python** installed on your computer. If you're not sure whether you have it:

1. Click the Windows Start button and type **cmd**, then open **Command Prompt**
2. Type `python --version` and press Enter
3. If you see a number like `Python 3.12.x` or higher — you're good, skip to Step 1 below
4. If you get an error — download and install Python from **python.org/downloads**
   - On the install screen, make sure to check the box that says **"Add Python to PATH"** before clicking Install

---

## First-Time Setup (Do This Once)

1. Copy the **ContactPuller** folder somewhere on your computer (Desktop is fine)
2. Open the folder
3. Double-click **install.bat**
4. A black window will appear and run automatically — wait for it to say "Setup complete!"
5. Close the window

That's it. You only do this once.

---

## Opening the App

Double-click **Run ContactPuller.bat**

The Contact Puller window will open.

---

## How to Pull Contacts

1. Open the app
2. In the **Contact Puller** tab, type a company name in the box at the top (e.g. `Pioneer Natural Resources`)
3. Click **Add Company** — repeat for as many companies as you want
4. Click the green **Start** button
5. Contacts will appear in the table as they're found — this runs automatically
6. When it finishes, click **Export CSV** to save the results

> The tool searches at a deliberate pace to avoid getting blocked. A batch of 5 companies typically takes 45-90 minutes. Start it and walk away.

---

## How to Verify Contacts Are Still There

After exporting a contacts CSV, you can confirm each person is still at that company:

1. Double-click **Verify LinkedIn.bat**
2. Drag and drop your contacts CSV file into the black window
3. Press Enter
4. A Chrome browser window will open
   - **First time only:** Log into LinkedIn when it asks. It saves your login — you won't be asked again.
5. The tool visits each profile automatically and updates an **Emp. Status** column in your CSV
   - **current** = confirmed still at the company
   - **stale** = moved to a different company — remove these before emailing
   - **unknown** = couldn't confirm either way — keep them, it doesn't mean they left

---

## How to Import Bounces After a Campaign

After sending emails, export the bounce list from your email tool (HubSpot, Instantly, Mailchimp, etc.) and:

1. Double-click **Import Bounces.bat**
2. Drag and drop the bounce CSV into the window and press Enter
3. Type the campaign name (optional) and press Enter

Bounced addresses are saved permanently. The tool will automatically skip them on every future run — you never email the same bad address twice.

---

## Where Are My Files?

Everything saves automatically to:

**C:\Users\YourName\ContactPuller_Output\\**

| File | What it is |
|---|---|
| `all_contacts.csv` | Every contact ever found — running master list |
| `CompanyName_contacts.csv` | Contacts from a specific company |
| `domain_cache.json` | Email domain info the tool has learned — don't delete this |
| `bounces.json` | All bounce history — don't delete this |

---

## Something Not Working?

**App won't open after double-clicking Run ContactPuller.bat**
Run `install.bat` again. If it still doesn't work, contact Parker.

**LinkedIn window opens but closes immediately**
Your saved login expired. Delete the `ContactPuller_Output\browser_session` folder and run `Verify LinkedIn.bat` again — it will ask you to log in once more.

**Finding zero contacts for a company**
Some smaller companies have very few LinkedIn members or their profiles aren't indexed by search engines. This isn't a bug — the tool logs these to `failed_companies.txt` in the output folder.

**Any other issue**
Contact Parker.
