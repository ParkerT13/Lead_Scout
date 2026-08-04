import random
import logging
from urllib.parse import quote_plus, unquote

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests
    HAS_CURL_CFFI = False

_FALLBACK_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

_IMPERSONATIONS = ["chrome110", "chrome116", "chrome119", "chrome124", "edge99", "edge101"]


def search(query: str, max_results: int = 10) -> list[dict]:
    """
    Search Google as absolute last resort.
    Detects CAPTCHA and returns empty rather than garbage.
    """
    url = f"https://www.google.com/search?q={quote_plus(query)}&num=10&hl=en&gl=us"
    headers = {
        "User-Agent": random.choice(_FALLBACK_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/",
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
    }

    try:
        if HAS_CURL_CFFI:
            resp = cffi_requests.get(
                url, headers=headers,
                impersonate=random.choice(_IMPERSONATIONS),
                timeout=20,
            )
        else:
            resp = cffi_requests.get(url, headers=headers, timeout=20)

        text = resp.text
        if "captcha" in text.lower() or "unusual traffic" in text.lower():
            logger.warning("Google returned CAPTCHA page — skipping.")
            return []

        soup = BeautifulSoup(text, "lxml")
        results = []

        for div in soup.select("div.g"):
            h3 = div.select_one("h3")
            link = div.select_one("a[href]")
            snippet = (
                div.select_one("div.VwiC3b")
                or div.select_one("span.aCOpRe")
                or div.select_one("div[data-sncf]")
            )
            if not (h3 and link):
                continue

            href = link.get("href", "")
            if href.startswith("/url?q="):
                href = unquote(href.split("/url?q=")[1].split("&")[0])

            if not href.startswith("http"):
                continue

            results.append({
                "title": h3.get_text(strip=True),
                "href": href,
                "body": snippet.get_text(strip=True) if snippet else "",
            })

        return results[:max_results]

    except Exception as e:
        logger.warning(f"Google search error: {e}")
        return []
