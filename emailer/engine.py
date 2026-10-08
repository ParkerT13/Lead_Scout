"""
Shared email-enrichment engine used by the GUI worker and every CLI script.

One code path, so improvements (website harvesting, pattern learning, bounce
filtering, richer statuses) reach every entry point.

Per company:
  1. Find + sanity-check the domain, confirm MX
  2. Detect the email format: cache -> HubSpot KB -> website harvest -> emailformat
     -> search snippets (published name+email pairs are treated as proof)
  3. Contacts whose address is published online -> 'found-on-site' (High)
  4. Everything else: generate ranked candidates and verify them
       port 25 open   -> SMTP RCPT TO
       API keys       -> NeverBounce / Reoon (and LEARN the format from the
                          first verified hit so later contacts need fewer credits)
       neither        -> evidence-ranked guess, labelled honestly
                          (pattern-confirmed / pattern-ddg / best-guess)

Statuses:
  verified, found-on-site, catch-all-confirmed, catch-all-risky, pattern-confirmed,
  pattern-ddg, best-guess, bounced, unknown, no domain, no MX, no candidates
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, Iterator

from emailer import site_harvester as sh
from emailer.api_verifier import verify_via_api
from emailer.bounce_tracker import is_bounced
from emailer.domain_cache import (get as cache_get, put as cache_put,
                                  get_harvest, put_harvest)
from emailer.domain_finder import find_domain
from emailer.email_verifier import (check_catch_all, get_mx_records,
                                    verify_email as smtp_verify)
from emailer.generator import generate_candidates
from emailer.pattern_detector import detect_pattern_ex, get_kb_entry, get_kb_pattern2
from emailer.smtp_verifier import get_mx

logger = logging.getLogger(__name__)

# Pattern sources that are backed by evidence rather than a guess
RELIABLE_SOURCES = {"hubspot", "scraped", "emailformat", "smtp-verified",
                    "api-verified", "manual"}

# Statuses meaning the address is undeliverable / unusable
DROP_STATUSES = {"bounced", "no domain", "no MX", "no candidates", "error",
                 "invalid_syntax", "disposable"}

CONFIDENCE = {
    "verified":            "High",
    "found-on-site":       "High",
    "catch-all-confirmed": "Medium",
    "pattern-confirmed":   "Medium",
    "catch-all-risky":     "Low",
    "pattern-ddg":         "Low",
    "best-guess":          "Low",
    "unverified":          "Low",
}

_LABELS = {"hubspot": "HubSpot KB", "scraped": "from website", "emailformat": "emailformat.com",
           "ddg": "via search", "smtp-verified": "SMTP confirmed",
           "api-verified": "API confirmed", "manual": "manual"}


def name_parts(contact: dict) -> tuple[str, str, str]:
    first = contact.get("first_name") or ""
    last = contact.get("last_name") or ""
    if not first or not last:
        raw = (contact.get("name") or "").split(",")[0].strip()
        parts = raw.split()
        first = parts[0] if parts else ""
        last = parts[-1] if len(parts) > 1 else ""
    return first, last, contact.get("name") or f"{first} {last}".strip()


def local_to_pattern(local: str, first: str, last: str) -> str | None:
    hits = sh._patterns_for(local.lower(), first, last)
    return hits[0] if hits else None


class Enricher:
    def __init__(
        self,
        nb_key: str = "",
        reoon_key: str = "",
        port25_ok: bool = False,
        say: Callable[[str, str], None] | None = None,      # (company, message)
        detail: Callable[[str], None] | None = None,        # per-contact chatter
        should_stop: Callable[[], bool] | None = None,
        contact_delay: tuple[float, float] = (1.5, 3.0),
        probe_delay: float = 0.8,
    ):
        self.nb_key = (nb_key or "").strip()
        self.reoon_key = (reoon_key or "").strip()
        self.port25_ok = port25_ok
        self._say = say or (lambda c, m: None)
        self._detail = detail or (lambda m: None)
        self._stop = should_stop or (lambda: False)
        self.contact_delay = contact_delay
        self.probe_delay = probe_delay
        self._smtp_conclusive = 0
        self._smtp_inconclusive = 0

    @property
    def has_api(self) -> bool:
        return bool(self.nb_key or self.reoon_key)

    def _pause(self):
        time.sleep(random.uniform(*self.contact_delay))

    # ── Company ──────────────────────────────────────────────────────────

    def enrich_company(self, company: str, contacts: list[dict]) -> Iterator[dict]:
        say = lambda m: self._say(company, m)

        say("Finding domain...")
        domain = find_domain(company)
        if not domain:
            say("No domain found")
            for c in contacts:
                yield {**c, "email": "", "email_status": "no domain", "email_source": "", "confidence": ""}
            return

        say(f"Checking MX for {domain}...")
        if not get_mx(domain):
            say(f"No MX record for {domain}")
            for c in contacts:
                yield {**c, "email": "", "email_status": "no MX", "email_source": domain, "confidence": ""}
            return

        pattern, pattern_source, pattern2, findings = self._detect(company, domain, say)

        # Format probe over SMTP (only meaningful with port 25 and no evidence yet)
        if self.port25_ok and pattern_source not in RELIABLE_SOURCES:
            probed = self._probe_format(company, domain, contacts, say)
            if probed:
                pattern, pattern_source = probed, "smtp-verified"
                say(f"Pattern confirmed via SMTP: {pattern}")
                cache_put(company, domain, pattern, pattern_source, pattern2)

        state = {"pattern": pattern, "source": pattern_source, "pattern2": pattern2}
        for contact in contacts:
            if self._stop():
                break
            yield self._enrich_contact(company, domain, contact, findings, state)

    def _detect(self, company, domain, say):
        say(f"Detecting email format for {domain}...")
        cached = cache_get(company)
        same = bool(cached and cached.get("domain") == domain)
        rows = get_harvest(company) if same else None
        findings = sh.findings_from_json(rows) if rows is not None else None

        if same and cached.get("pattern") is not None:
            pattern = cached["pattern"]
            source = cached.get("pattern_source", "cache")
            pattern2 = cached.get("pattern2")
            # A cache entry written by a weaker detector must not outrank real CRM
            # data; keep both formats so each gets tried (split-format companies).
            kb = get_kb_entry(domain)
            if kb and kb.get("pattern") and kb["pattern"] != pattern and source != "manual":
                if source != "hubspot":
                    pattern2 = pattern2 if pattern2 not in (None, kb["pattern"]) else pattern
                pattern, source = kb["pattern"], "hubspot"
                cache_put(company, domain, pattern, source, pattern2)
            if findings is None:
                try:
                    findings = sh.harvest(domain)
                except Exception as e:                       # network must never kill a run
                    logger.warning("Harvest failed for %s: %s", domain, e)
                    findings = []
        else:
            det = detect_pattern_ex(domain, findings)
            pattern, source = det.pattern, det.source
            pattern2 = det.pattern2 or get_kb_pattern2(domain)
            findings = det.findings
            cache_put(company, domain, pattern, source, pattern2)

        if rows is None:
            put_harvest(company, sh.findings_to_json(findings))

        if findings:
            named = sum(1 for f in findings if f.name)
            say(f"Found {len(findings)} published address(es) ({named} with names)")
        if pattern:
            p2 = f" + {pattern2}" if pattern2 else ""
            say(f"Format: {pattern}{p2} ({_LABELS.get(source, source)})")
        else:
            say("Format unknown - will rank by O&G frequency")
        return pattern, source, pattern2, findings

    # ── Contact ──────────────────────────────────────────────────────────

    def _enrich_contact(self, company, domain, contact, findings, state) -> dict:
        first, last, name = name_parts(contact)
        base = {**contact}

        site_email = sh.find_contact_email(findings, first, last, domain)
        if site_email and not is_bounced(site_email):
            self._detail(f"[{company}] {name} -> {site_email} [found online]")
            return {**base, "email": site_email, "email_status": "found-on-site",
                    "email_source": f"{domain} (published online)", "confidence": "High"}

        candidates = generate_candidates(first, last, domain, state["pattern"], state["pattern2"])
        live = [c for c in candidates if not is_bounced(c)]
        if not candidates:
            return {**base, "email": "", "email_status": "no candidates",
                    "email_source": domain, "confidence": ""}
        if not live:
            return {**base, "email": candidates[0], "email_status": "bounced",
                    "email_source": domain, "confidence": ""}

        email, status = live[0], "unknown"
        self._detail(f"[{company}] {name} - {len(live)} candidate(s)")

        if self.port25_ok:
            email, status = self._verify_smtp(live)
            self._track_smtp(status, company)
        if status == "unknown" and self.has_api:
            email, status = self._verify_api(live, state, first, last, company, domain)
        if status == "unknown":                 # nothing could verify it
            email, status = live[0], "unverified"

        if status == "catch-all-risky" and state["source"] in RELIABLE_SOURCES:
            status = "catch-all-confirmed"

        if status == "unverified":
            if state["source"] in RELIABLE_SOURCES:
                status = "pattern-confirmed"
            elif state["pattern"]:
                status = "pattern-ddg"
            else:
                status = "best-guess"

        source = domain + (f" ({status})" if status.startswith("catch-all") else "")
        return {**base, "email": email, "email_status": status, "email_source": source,
                "confidence": CONFIDENCE.get(status, "")}

    def _track_smtp(self, status: str, company: str):
        """Port 25 may be nominally open yet every probe inconclusive (PBL-listed IP,
        greylisting). After 3 inconclusive results with no success, stop wasting time."""
        if status == "unknown":
            self._smtp_inconclusive += 1
        else:
            self._smtp_conclusive += 1
        if self._smtp_conclusive == 0 and self._smtp_inconclusive >= 3 and self.port25_ok:
            self.port25_ok = False
            self._say(company, "SMTP probes inconclusive (port 25 filtered) - skipping SMTP")

    def _verify_smtp(self, candidates: list[str]) -> tuple[str, str]:
        email, status = candidates[0], "unknown"
        for cand in candidates:
            r = smtp_verify(cand)
            self._detail(f"  -> {cand} [{r['status']}]")
            if r["status"] == "verified":
                return cand, "verified"
            if r["status"] == "catch-all-risky":
                return email, "catch-all-risky"
            if r["status"] == "bounced" and status == "unknown":
                status = "bounced"
        self._pause()
        return email, status

    def _verify_api(self, candidates, state, first, last, company, domain) -> tuple[str, str]:
        reliable = state["source"] in RELIABLE_SOURCES
        email, status = candidates[0], "unknown"
        for cand in candidates[: 2 if reliable else 3]:
            r = verify_via_api(cand, self.nb_key, self.reoon_key)
            self._detail(f"  -> {cand} [{r['status']}] ({r.get('detail', 'API')})")
            time.sleep(0.4)
            if r["status"] == "verified":
                self._learn(cand, first, last, state, company, domain)
                return cand, "verified"
            if r["status"] == "catch-all-risky":
                return email, "catch-all-risky"
            if r["status"] == "bounced" and status == "unknown":
                status = "bounced"
            if r["status"] == "unknown":
                break                      # API unavailable - stop burning calls
        return email, status

    def _learn(self, email, first, last, state, company, domain):
        """A verified address reveals the company format; remember it."""
        if state["source"] in ("smtp-verified", "api-verified", "manual", "hubspot", "scraped"):
            return
        pat = local_to_pattern(email.split("@")[0], first, last)
        if pat:
            state["pattern"], state["source"] = pat, "api-verified"
            cache_put(company, domain, pat, "api-verified", state["pattern2"])
            self._say(company, f"Format learned from verified address: {pat}")

    # ── SMTP format probe ────────────────────────────────────────────────

    def _probe_format(self, company, domain, contacts, say) -> str | None:
        mx_records = get_mx_records(domain)
        if not mx_records:
            return None
        if check_catch_all(domain, mx_records[0][1]):
            say(f"{domain} is catch-all - format probe skipped")
            return None
        for c in contacts:
            first, last, _ = name_parts(c)
            if first and last and len(last) > 1:
                break
        else:
            return None
        say(f"Probing formats: {first} {last} @ {domain}")
        unknown = 0
        for cand in generate_candidates(first, last, domain, pattern=None):
            status = smtp_verify(cand)["status"]
            if status == "verified":
                return local_to_pattern(cand.split("@")[0], first, last)
            unknown = unknown + 1 if status == "unknown" else 0
            if unknown >= 2:                      # server isn't answering probes; give up fast
                for _ in range(3):
                    self._track_smtp("unknown", company)
                return None
            time.sleep(self.probe_delay)
        return None
