"""
Free headless-browser rendering for JavaScript-built pages (team lists, IR widgets).

Uses the Playwright + Chromium that Lead Scout already installs for the LinkedIn session.
Never raises: if Playwright or the browser is missing, rendering simply returns nothing and
callers carry on with what the plain HTTP fetch found.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def render_pages(urls: list[str], timeout_ms: int = 15000, total_seconds: float = 75.0) -> list[tuple[str, str]]:
    """[(final_url, html)] for each URL that rendered. Skips pages once the time budget is spent."""
    import time
    if not urls:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        logger.info("Playwright unavailable: %s", e)
        return []
    out: list[tuple[str, str]] = []
    start = time.time()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
                page = ctx.new_page()
                # images/fonts/media are irrelevant for text and slow everything down
                page.route("**/*", lambda route: route.abort()
                           if route.request.resource_type in ("image", "media", "font") else route.continue_())
                for url in urls:
                    if time.time() - start > total_seconds:
                        break
                    try:
                        page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                        try:
                            page.wait_for_load_state("networkidle", timeout=6000)
                        except Exception:
                            pass
                        page.wait_for_timeout(800)
                        out.append((page.url, page.content()))
                    except Exception as e:
                        logger.debug("Render failed for %s: %s", url, e)
            finally:
                browser.close()
    except Exception as e:
        logger.info("Browser rendering unavailable: %s", e)
    return out
