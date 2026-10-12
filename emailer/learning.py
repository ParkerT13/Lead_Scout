"""
Learn from real send results: which email FORMAT works at which company.

Bounce lists tell us a specific address failed. This module goes one step further and works out
which format that address used (flast, first.last, ...), counts failures and successes per
domain, and then:
  * pushes failing formats to the back of the candidate list for that domain, and
  * once a domain has enough evidence, corrects the cached company format (source
    "outcome-learned" - real send results outrank every inferred source except manual).

Outcome data lives in ~/LeadScout_Output/format_outcomes.json (honours LEADSCOUT_OUTPUT_DIR).
Name lookup comes from the contact CSVs Lead Scout already wrote, so a plain bounce export (just
email addresses) is enough.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()

# Evidence thresholds for changing a company's cached format
MIN_BAD = 2          # at least this many failures with this format
MIN_GOOD = 2         # delivered addresses needed to call a format "confirmed by outcomes"

# Fallback order when a format is ruled out (real-world O&G frequency)
_FALLBACK_ORDER = ["flast", "first.last", "first_last", "first", "firstlast", "lastf", "last", "f.last"]


def _dir() -> Path:
    return Path(os.environ.get("LEADSCOUT_OUTPUT_DIR") or (Path.home() / "LeadScout_Output"))


def _file() -> Path:
    return _dir() / "format_outcomes.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8")) if _file().exists() else {}
    except Exception as e:
        logger.warning("Could not load format outcomes: %s", e)
        return {}


def _save(data: dict):
    try:
        _dir().mkdir(parents=True, exist_ok=True)
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_file())
    except Exception as e:
        logger.warning("Could not save format outcomes: %s", e)


def name_index(extra_dirs: list[Path] | None = None) -> dict[str, tuple[str, str]]:
    """{email: (first, last)} from every contact CSV Lead Scout wrote that has an email column."""
    index: dict[str, tuple[str, str]] = {}
    folders = [_dir()] + list(extra_dirs or [])
    for folder in folders:
        for path in folder.glob("*.csv"):
            try:
                with open(path, newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    cols = {c.lower(): c for c in (reader.fieldnames or [])}
                    if "email" not in cols:
                        continue
                    for row in reader:
                        email = (row.get(cols["email"]) or "").strip().lower()
                        first = row.get(cols.get("first_name", ""), "") or ""
                        last = row.get(cols.get("last_name", ""), "") or ""
                        if not (first and last) and cols.get("name"):
                            parts = (row.get(cols["name"]) or "").split(",")[0].split()
                            first, last = (parts[0], parts[-1]) if len(parts) > 1 else ("", "")
                        if email and first and last:
                            index.setdefault(email, (first, last))
            except Exception:
                continue
    return index


def format_of(email: str, names: dict[str, tuple[str, str]]) -> str | None:
    """The format an address used, if we know whose address it is."""
    from emailer.engine import local_to_pattern
    email = email.lower().strip()
    if email not in names or "@" not in email:
        return None
    first, last = names[email]
    return local_to_pattern(email.split("@")[0], first, last)


def record_outcomes(bounced: list[str], delivered: list[str] | None = None,
                    names: dict[str, tuple[str, str]] | None = None) -> dict:
    """Count format successes/failures per domain. Returns a summary of what was learned."""
    names = names if names is not None else name_index()
    summary = {"bounced_known": 0, "bounced_unknown": 0, "delivered_known": 0}
    with _LOCK:
        data = _load()
        for kind, emails in (("bad", bounced), ("good", delivered or [])):
            for email in emails:
                email = email.lower().strip()
                pat = format_of(email, names)
                if not pat:
                    if kind == "bad":
                        summary["bounced_unknown"] += 1
                    continue
                dom = email.rsplit("@", 1)[-1]
                slot = data.setdefault(dom, {"bad": {}, "good": {}})
                slot[kind][pat] = slot[kind].get(pat, 0) + 1
                summary["bounced_known" if kind == "bad" else "delivered_known"] += 1
        _save(data)
    return summary


def bad_patterns(domain: str) -> set[str]:
    """Formats that have failed at this domain more than they have worked."""
    slot = _load().get(domain.lower(), {})
    bad, good = slot.get("bad", {}), slot.get("good", {})
    return {p for p, n in bad.items() if n >= MIN_BAD and n > good.get(p, 0)}


def apply_to_cache() -> list[str]:
    """Correct cached company formats that real send results contradict. Returns change notes."""
    from emailer.domain_cache import all_entries, put
    notes: list[str] = []
    outcomes = _load()
    for company, entry in all_entries().items():
        dom = entry.get("domain", "")
        slot = outcomes.get(dom.lower())
        if not slot or entry.get("pattern_source") == "manual":
            continue
        pattern = entry.get("pattern")
        bad, good = slot.get("bad", {}), slot.get("good", {})
        ruled_out = bad_patterns(dom)
        # 1) confirmed by deliveries: a format with enough successes and no more failures than successes
        best_good = max(good, key=good.get, default=None)
        if best_good and good[best_good] >= MIN_GOOD and best_good not in ruled_out and best_good != pattern:
            put(company, dom, best_good, "outcome-verified", pattern if pattern not in ruled_out else None)
            notes.append(f"{company}: {pattern} -> {best_good} (confirmed by {good[best_good]} delivered)")
            continue
        # 2) current format is ruled out: fall back to the most likely format that has not failed
        if pattern in ruled_out:
            nxt = next((p for p in _FALLBACK_ORDER if p not in ruled_out and p != pattern), None)
            if nxt:
                put(company, dom, nxt, "outcome-guess", None)
                notes.append(f"{company}: {pattern} -> {nxt} ({bad[pattern]} bounces with {pattern})")
    return notes


def read_emails(path: Path) -> list[str]:
    """Addresses from a CSV (any column with 'email' or 'address' in its name) or a plain text file."""
    import re
    rx = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    emails: list[str] = []
    if Path(path).suffix.lower() == ".csv":
        reader = csv.DictReader(text.splitlines())
        col = next((c for c in (reader.fieldnames or []) if "email" in c.lower() or "address" in c.lower()), None)
        for row in reader:
            val = (row.get(col) if col else next(iter(row.values()), "")) or ""
            m = rx.search(val)
            if m:
                emails.append(m.group(0).lower())
    else:
        for line in text.splitlines():
            m = rx.search(line)
            if m:
                emails.append(m.group(0).lower())
    return emails


def learn_from_files(bounce_file: Path, sent_file: Path | None = None) -> dict:
    """One call for the CLI and the UI: record outcomes, correct cached formats, report."""
    bounced = read_emails(bounce_file)
    sent = read_emails(sent_file) if sent_file else []
    bset = set(bounced)
    delivered = [e for e in sent if e not in bset]
    summary = record_outcomes(bounced, delivered, name_index())
    summary["bounced"] = len(bounced)
    summary["delivered"] = len(delivered)
    summary["changes"] = apply_to_cache()
    return summary
