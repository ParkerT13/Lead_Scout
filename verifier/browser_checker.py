"""
LinkedIn employment verification using a real browser session via Playwright.

First run: opens a visible Chrome window so you can log into LinkedIn.
           Session is saved to ~/LeadScout_Output/browser_session/
Subsequent runs: reuses the saved session — no login needed.

Reads current employer directly from the logged-in profile page, which is
far more reliable than DDG search (near 100% coverage vs ~47%).

Returns:
    'current'  — company confirmed as current employer on the profile
    'stale'    — profile loaded but a different company shown as current
    'unknown'  — profile loaded but couldn't parse employer (e.g. no experience)
    'error'    — page failed to load or LinkedIn blocked the request
"""

import re
import time
import random
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_DIR = Path.home() / "LeadScout_Output" / "browser_session"
_DELAY       = (3.0, 7.0)   # seconds between profile visits

from scraper.query_builder import generate_variants


def _get_browser_context():
    """
    Return a Playwright persistent browser context using the saved session.
    Creates the session directory if it doesn't exist.
    """
    from playwright.sync_api import sync_playwright

    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    p = sync_playwright().start()

    context = p.chromium.launch_persistent_context(
        str(_SESSION_DIR),
        headless=False,          # visible window so user can log in on first run
        channel="chrome",        # use real Chrome if available, else Playwright Chromium
        args=["--disable-blink-features=AutomationControlled"],
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        slow_mo=200,
    )
    return p, context


def ensure_logged_in(context) -> bool:
    """
    Navigate to LinkedIn and check if we're logged in.
    If not, wait for the user to log in manually (up to 3 minutes).
    Returns True if logged in.
    """
    page = context.new_page()
    page.goto("https://www.linkedin.com/feed/", timeout=30000)
    time.sleep(2)

    # Check for feed elements that only appear when logged in
    if page.url.startswith("https://www.linkedin.com/feed"):
        logger.info("Already logged into LinkedIn.")
        page.close()
        return True

    # Not logged in — wait for manual login
    print("\nPlease log into LinkedIn in the browser window that just opened.")
    print("Waiting up to 3 minutes for login...")
    try:
        page.wait_for_url("**/feed/**", timeout=180000)
        print("Login detected. Session saved for future runs.")
        page.close()
        return True
    except Exception:
        print("Login timed out.")
        page.close()
        return False


def check_employment(linkedin_url: str, company: str, name: str = "") -> str:
    """
    Check a single LinkedIn profile using the browser.
    Intended for use in batch scripts. For bulk verification use verify_batch().
    """
    p, context = _get_browser_context()
    try:
        if not ensure_logged_in(context):
            return "error"
        result = _check_one(context, linkedin_url, company)
        return result
    finally:
        context.close()
        p.stop()


def verify_batch(contacts: list[dict]) -> list[dict]:
    """
    Verify a list of contacts in a single browser session.
    Each contact dict needs: linkedin_url, company, name.
    Returns the same list with emp_status added/updated.
    """
    p, context = _get_browser_context()
    try:
        if not ensure_logged_in(context):
            for c in contacts:
                c["emp_status"] = "error"
            return contacts

        total = len(contacts)
        for i, contact in enumerate(contacts):
            url     = contact.get("linkedin_url", "")
            company = contact.get("company", "")
            name    = contact.get("name", "Unknown")

            if not url or not company:
                contact["emp_status"] = "unknown"
                continue

            print(f"[{i+1}/{total}] {name} ({company})...", end=" ", flush=True)
            status = _check_one(context, url, company)
            contact["emp_status"] = status
            label = {"current": "[+] current", "stale": "[-] stale", "unknown": "[?] unknown", "error": "[!] error"}.get(status, status)
            print(label)

            if i < total - 1:
                time.sleep(random.uniform(*_DELAY))

    finally:
        context.close()
        p.stop()

    return contacts


def _check_one(context, linkedin_url: str, company: str) -> str:
    variants     = generate_variants(company)
    variant_lowers = {v.lower() for v in variants}
    first_words    = {v.split()[0].lower() for v in variant_lowers if len(v.split()[0]) >= 4}

    page = context.new_page()
    try:
        resp = page.goto(linkedin_url, timeout=20000, wait_until="domcontentloaded")

        if resp is None or resp.status >= 400:
            return "error"

        # Check for auth wall / login redirect
        if "authwall" in page.url or "login" in page.url:
            return "error"

        time.sleep(2)  # let dynamic content load

        # Strategy 1: read og:title from page source (works even on dynamic pages)
        og_title = page.evaluate("""
            () => {
                const m = document.querySelector('meta[property="og:title"]');
                return m ? m.getAttribute('content') : '';
            }
        """) or ""

        if og_title and "| linkedin" in og_title.lower():
            # Strip "| LinkedIn", find " at "
            clean = re.sub(r"\s*\|\s*linkedin\s*$", "", og_title, flags=re.IGNORECASE).strip().lower()
            at_idx = clean.rfind(" at ")
            if at_idx != -1:
                employer = clean[at_idx + 4:].strip()
                if any(v in employer for v in variant_lowers):
                    return "current"
                if any(f" at {fw}" in clean for fw in first_words):
                    return "current"
                if employer:
                    return "stale"

        # Strategy 2: look for current position in the profile's top section
        # LinkedIn renders the current role in the headline below the name
        headline = page.evaluate("""
            () => {
                const el = document.querySelector('.text-body-medium.break-words')
                    || document.querySelector('[data-generated-suggestion-target]')
                    || document.querySelector('.pv-text-details__left-panel .text-body-medium');
                return el ? el.innerText : '';
            }
        """) or ""

        headline_lower = headline.lower()
        if any(v in headline_lower for v in variant_lowers):
            return "current"
        if headline_lower and " at " in headline_lower:
            return "stale"

        # Strategy 3: scan the experience section for first (current) role
        experience_text = page.evaluate("""
            () => {
                const section = document.querySelector('#experience ~ div')
                    || document.querySelector('[id*="experience"]');
                return section ? section.innerText.substring(0, 500) : '';
            }
        """) or ""

        exp_lower = experience_text.lower()
        if any(v in exp_lower for v in variant_lowers):
            return "current"
        if exp_lower:
            return "stale"

        return "unknown"

    except Exception as e:
        logger.warning("Browser check failed for %s: %s", linkedin_url, e)
        return "error"
    finally:
        page.close()
