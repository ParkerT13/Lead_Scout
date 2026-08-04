import time
import random
import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from scraper.query_builder import build_queries, generate_variants
from scraper.parser import parse_result, is_valid_contact
from scraper.engines import duckduckgo, bing, google

logger = logging.getLogger(__name__)

# After this many consecutive zero-result queries, switch preferred engine
_DDG_ZERO_THRESHOLD = 3
# Seconds between individual queries
_QUERY_DELAY = (3.5, 7.5)
# Seconds between companies
_COMPANY_DELAY = (15, 25)
# Seconds between engine fallback attempts within a single query
_FALLBACK_DELAY = (3, 6)


class SearchWorker(QThread):
    """
    Processes a list of companies sequentially.
    Emits signals thread-safely so the GUI can update in real time.
    """
    contact_found    = Signal(dict)
    company_started  = Signal(str)
    company_progress = Signal(str, int, int)   # company, current_query, total_queries
    company_finished = Signal(str, int)         # company, contacts_found
    company_failed   = Signal(str)
    status_update    = Signal(str)
    all_done         = Signal()

    def __init__(self, companies: list[str], output_dir: Path):
        super().__init__()
        self._companies  = companies
        self._output_dir = output_dir
        self._stop       = False

    # ── Main loop ────────────────────────────────────────────────────────

    def run(self):
        for idx, company in enumerate(self._companies):
            if self._stop:
                break

            self.company_started.emit(company)
            count = self._process_company(company)

            if count == 0:
                self.company_failed.emit(company)
                self._log_failed(company)
            else:
                self.company_finished.emit(company, count)

            # Cool-down before starting next company
            if not self._stop and idx < len(self._companies) - 1:
                delay = random.uniform(*_COMPANY_DELAY)
                self.status_update.emit(f"Cooling down {delay:.0f}s before next company...")
                time.sleep(delay)

        self.all_done.emit()

    def stop(self):
        self._stop = True

    # ── Per-company processing ───────────────────────────────────────────

    def _process_company(self, company: str) -> int:
        queries      = build_queries(company)
        seen_urls: set[str] = set()
        total        = 0
        ddg_zeros    = 0
        prefer_bing  = False  # DDG (via Yahoo fallback) is primary; Bing is currently CAPTCHA-blocked

        variant_lowers = {v.lower() for v in generate_variants(company)}
        # First significant word of each variant (≥4 chars) for truncation-tolerant
        # " at <word>..." matching.  Used only for title-specific queries.
        first_words = {v.split()[0] for v in variant_lowers if len(v.split()[0]) >= 4}

        for i, query in enumerate(queries):
            if self._stop:
                break

            self.company_progress.emit(company, i + 1, len(queries))
            engine_label = "Bing" if prefer_bing else "DDG"
            self.status_update.emit(
                f"[{company}]  Query {i+1}/{len(queries)}  |  Engine: {engine_label}  |  Found: {total}"
            )

            results = self._search(query, prefer_bing)

            if not results:
                ddg_zeros += 1
                if not prefer_bing and ddg_zeros >= _DDG_ZERO_THRESHOLD:
                    prefer_bing = True
                    self.status_update.emit(
                        f"[{company}]  DDG dry for {_DDG_ZERO_THRESHOLD} queries — switching to Bing."
                    )
            else:
                ddg_zeros = 0

            for r in results:
                href = r.get("href", "")
                if "linkedin.com/in/" not in href:
                    continue
                url = href.split("?")[0].rstrip("/")
                if url in seen_urls:
                    continue

                result_title = r.get("title", "")
                result_lower = result_title.lower()

                # Company name must be linked to this person as current employer.
                # Full match: "Texas Standard Oil" anywhere in title.
                # Truncation match: " at Texas" when Yahoo truncates " at Texas..."
                # This prevents false positives where someone just mentions the company
                # in past experience, while handling Yahoo's title truncation.
                full_match = any(v in result_lower for v in variant_lowers)
                trunc_match = any(f" at {fw}" in result_lower for fw in first_words)
                if not full_match and not trunc_match:
                    continue

                seen_urls.add(url)

                contact = parse_result(result_title, url, r.get("body", ""))
                contact["company"]     = company
                contact["source"]      = r.get("_engine", "unknown")
                contact["date_pulled"] = datetime.now().strftime("%Y-%m-%d")

                if is_valid_contact(contact, company):
                    self.contact_found.emit(contact)
                    total += 1

            time.sleep(random.uniform(*_QUERY_DELAY))

        # If title-specific queries found nothing, try a broad company search
        if total == 0 and not self._stop:
            self.status_update.emit(f"[{company}]  No title matches — running broad fallback search...")
            total += self._broad_fallback(company, seen_urls)

        return total

    # ── Broad fallback ───────────────────────────────────────────────────

    _BROAD_KEYWORDS = {
        # Technical
        "engineer", "geoscient", "geophys", "geolog", "petrophys",
        "reservoir", "completions", "petroleum", "subsurface", "seismic",
        # Executive / leadership (now included per user request)
        "ceo", "coo", "cfo", "cto", "president", "director",
        "vice president", "vp", "executive", "officer", "founder", "owner",
        # General professional roles
        "manager", "supervisor", "lead", "principal", "staff", "senior",
        "analyst", "specialist", "technician", "landman",
        "asset", "production", "operations", "exploration", "commercial",
    }

    def _broad_fallback(self, company: str, seen_urls: set) -> int:
        """
        Last-resort search: site:linkedin.com/in "Company" with no title
        constraint.

        STRICT MODE — two additional guards beyond the normal is_valid_contact:
          1. The company name must appear in the search result TITLE string
             (the title shows current employer, so if the company isn't there
             the person doesn't currently work there).
          2. The extracted job title must contain a role keyword.

        This prevents name-collision false positives (e.g. "Cabral Energy"
        matching anyone named Cabral who works in energy).
        """
        variants = generate_variants(company)
        company_lower = company.lower()
        # Also accept the stripped variant (e.g. "Franklin Mountain" for "Franklin Mountain Energy")
        variant_lowers = {v.lower() for v in variants}
        count = 0

        for variant in variants[:2]:
            if self._stop:
                break
            query = f'site:linkedin.com/in "{variant}"'
            self.status_update.emit(f"[{company}]  Broad: {query}")
            results = self._search(query, False)

            for r in results:
                href = r.get("href", "")
                if "linkedin.com/in/" not in href:
                    continue
                url = href.split("?")[0].rstrip("/")
                if url in seen_urls:
                    continue

                result_title = r.get("title", "")
                result_lower = result_title.lower()

                # Guard 1: company name must appear in the result title
                # (confirms this is their current/listed employer, not just a mention)
                if not any(v in result_lower for v in variant_lowers):
                    continue

                seen_urls.add(url)

                contact = parse_result(result_title, url, r.get("body", ""))
                contact["company"]     = company
                contact["source"]      = r.get("_engine", "unknown") + " (broad)"
                contact["date_pulled"] = datetime.now().strftime("%Y-%m-%d")

                # Guard 2: if a title was extracted, it must look like a
                # professional role, NOT random noise.
                # Allow: blank title (employee, role not in snippet),
                #        title that IS the company name (is_valid_contact handles it),
                #        title with at least one role keyword.
                title_lower = contact.get("title", "").lower()
                title_is_company = any(v in title_lower for v in variant_lowers)
                has_keyword = any(kw in title_lower for kw in self._BROAD_KEYWORDS)
                if title_lower and not title_is_company and not has_keyword:
                    continue

                if is_valid_contact(contact, company):
                    self.contact_found.emit(contact)
                    count += 1

            time.sleep(random.uniform(*_QUERY_DELAY))

        return count

    # ── Engine dispatch ──────────────────────────────────────────────────

    def _search(self, query: str, prefer_bing: bool) -> list[dict]:
        """
        Try engines in order. Tags each result with its source engine.
        Falls back through all three engines before giving up on a query.
        """
        order = (
            [("Bing", bing.search), ("DDG", duckduckgo.search), ("Google", google.search)]
            if prefer_bing
            else [("DDG", duckduckgo.search), ("Bing", bing.search), ("Google", google.search)]
        )

        for engine_name, search_fn in order:
            if self._stop:
                return []
            try:
                results = search_fn(query)
                if results:
                    for r in results:
                        r["_engine"] = engine_name
                    return results
            except Exception as e:
                logger.warning(f"{engine_name} raised: {e}")
            time.sleep(random.uniform(*_FALLBACK_DELAY))

        return []

    # ── Failure logging ──────────────────────────────────────────────────

    def _log_failed(self, company: str):
        log_path = self._output_dir / "failed_companies.txt"
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                f.write(f"{ts} | {company}\n")
        except Exception as e:
            logger.warning(f"Could not write failed log: {e}")
