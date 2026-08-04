import random
import logging
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests
    HAS_CURL_CFFI = False
    logger.warning("curl-cffi not installed — Bing using standard requests (lower anti-bot resistance).")

_FALLBACK_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

try:
    from fake_useragent import UserAgent
    _ua_gen = UserAgent(browsers=["chrome", "firefox", "edge"])
    def _random_ua() -> str:
        try:
            return _ua_gen.random
        except Exception:
            return random.choice(_FALLBACK_UAS)
except Exception:
    def _random_ua() -> str:
        return random.choice(_FALLBACK_UAS)

_IMPERSONATIONS = ["chrome110", "chrome116", "chrome119", "chrome124", "edge99", "edge101"]


def search(query: str, max_results: int = 10) -> list[dict]:
    """Search Bing with TLS fingerprint impersonation via curl-cffi."""
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count=10&setlang=en&cc=US"
    headers = {
        "User-Agent": _random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.bing.com/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }

    try:
        if HAS_CURL_CFFI:
            resp = cffi_requests.get(
                url, headers=headers,
                impersonate=random.choice(_IMPERSONATIONS),
                timeout=15,
            )
        else:
            resp = cffi_requests.get(url, headers=headers, timeout=15)

        # Bing sometimes returns a CAPTCHA challenge as a 200 response
        if "solve the challenge" in resp.text.lower() or "captcha" in resp.text.lower():
            logger.warning("Bing returned CAPTCHA challenge — skipping")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        results = []

        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            snippet_el = (
                li.select_one(".b_caption p")
                or li.select_one(".b_algoSlug")
                or li.select_one("p")
            )
            if a and a.get("href"):
                results.append({
                    "title": a.get_text(strip=True),
                    "href": a["href"],
                    "body": snippet_el.get_text(strip=True) if snippet_el else "",
                })

        return results[:max_results]

    except Exception as e:
        logger.warning(f"Bing search error: {e}")
        return []
