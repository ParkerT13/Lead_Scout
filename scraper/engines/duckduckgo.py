import time
import random
import logging

logger = logging.getLogger(__name__)

try:
    from ddgs import DDGS
    try:
        from ddgs.exceptions import RatelimitException, TimeoutException
    except ImportError:
        RatelimitException = Exception
        TimeoutException = Exception
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False
    logger.warning("ddgs not installed. DDG engine disabled.")


def search(query: str, max_results: int = 10) -> list[dict]:
    """
    Search DuckDuckGo via the ddgs library.
    Returns list of {title, href, body}. Empty list on failure.
    Handles rate limits with exponential backoff.
    """
    if not HAS_DDGS:
        return []

    for attempt in range(2):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return results
        except RatelimitException:
            wait = 20 + random.uniform(0, 10)
            logger.warning(f"DDG rate limited. Waiting {wait:.0f}s (attempt {attempt + 1}/2)")
            time.sleep(wait)
        except Exception as e:
            # TimeoutException is handled internally by ddgs (falls back to Yahoo/Yandex).
            # If it does propagate here, retry once then give up.
            logger.warning(f"DDG error (attempt {attempt + 1}/2): {e}")
            if attempt == 0:
                time.sleep(3)
            else:
                break

    return []
