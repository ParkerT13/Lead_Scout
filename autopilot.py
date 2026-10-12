"""
Autopilot: find contacts AND emails for a list of companies, unattended, at zero cost.

    py -3 autopilot.py companies.txt            # one company per line
    py -3 autopilot.py --from-master            # every company already in all_contacts.csv
    py -3 autopilot.py companies.txt --no-linkedin --no-web

Free sources only: LinkedIn pages via free search engines, company team pages, SEC filings, web
mentions, and free email-format detection. Paid email verification (NeverBounce/Reoon) is NEVER
used unless you pass --paid-verify.

Built to be left alone:
  * no dialogs, prompts or clicks; all output goes to a log file as well as the console
  * checkpoints after every company - rerun the same command and it resumes where it stopped
    (crash, sleep, network drop, reboot)
  * waits out internet outages instead of recording them as "no domain"
  * one bad company never stops the run
Results: ~/LeadScout_Output/autopilot_<list-name>.csv (contacts + emails, grows as it runs),
all_contacts.csv (master list), and autopilot_state_<list-name>.json (resume state).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT_FIELDS = ["first_name", "last_name", "title", "company", "email", "email_status", "confidence",
              "email_source", "location", "basin", "linkedin_url", "source", "date_pulled"]


def read_companies(path: Path) -> list[str]:
    seen, out = set(), []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name = line.strip().strip(",").strip('"')
        if name and not name.startswith("#") and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def master_companies(out_dir: Path) -> list[str]:
    with open(out_dir / "all_contacts.csv", newline="", encoding="utf-8-sig") as f:
        names = {r["company"] for r in csv.DictReader(f) if r.get("company")}
    return sorted(names, key=str.lower)


def setup_logging(log_path: Path):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_path, encoding="utf-8")])
    for noisy in ("httpx", "httpcore", "primp", "ddgs", "urllib3", "hpack", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def discover_contacts(company: str, sources: dict) -> list[dict]:
    """All contacts for one company from every enabled free source (same code as the GUI)."""
    from PySide6.QtCore import QCoreApplication
    from scraper.worker import SearchWorker
    QCoreApplication.instance() or QCoreApplication([])
    found: list[dict] = []
    worker = SearchWorker([company], Path("."), sources=sources)
    worker.contact_found.connect(found.append)
    worker.status_update.connect(lambda m: logging.info(m))
    worker._process_company(company)           # synchronous: no thread needed in autopilot
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Unattended, zero-cost contact + email discovery")
    ap.add_argument("companies", nargs="?", help="text file, one company per line")
    ap.add_argument("--from-master", action="store_true", help="use the companies in all_contacts.csv")
    ap.add_argument("--no-linkedin", action="store_true")
    ap.add_argument("--no-team", action="store_true")
    ap.add_argument("--no-sec", action="store_true")
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--no-emails", action="store_true", help="contacts only")
    ap.add_argument("--paid-verify", action="store_true",
                    help="allow NeverBounce/Reoon (spends credits). Off by default.")
    ap.add_argument("--fresh", action="store_true", help="ignore the saved resume state")
    ap.add_argument("--max-companies", type=int, default=0)
    args = ap.parse_args(argv)

    from output.csv_writer import get_output_dir, append_contact
    out_dir = get_output_dir()
    if args.from_master:
        companies, tag = master_companies(out_dir), "master"
    elif args.companies:
        src = Path(args.companies)
        companies, tag = read_companies(src), src.stem
    else:
        ap.error("give a companies file or --from-master")
    if args.max_companies:
        companies = companies[:args.max_companies]

    setup_logging(out_dir / f"autopilot_{tag}.log")
    result_path = out_dir / f"autopilot_{tag}.csv"
    state_path = out_dir / f"autopilot_state_{tag}.json"
    done: set[str] = set()
    if state_path.exists() and not args.fresh:
        try:
            done = set(json.loads(state_path.read_text(encoding="utf-8")).get("done", []))
        except Exception:
            done = set()
    elif result_path.exists() and args.fresh:
        result_path.unlink()

    sources = {"linkedin": not args.no_linkedin, "team": not args.no_team,
               "sec": not args.no_sec, "web": not args.no_web}
    logging.info("Autopilot: %d companies (%d already done), sources=%s, emails=%s, paid verification=%s",
                 len(companies), len(done), [k for k, v in sources.items() if v],
                 not args.no_emails, args.paid_verify)

    enricher = None
    if not args.no_emails:
        from emailer.engine import Enricher
        from emailer.csv_runner import load_api_keys
        nb, reoon = load_api_keys() if args.paid_verify else ("", "")
        enricher = Enricher(nb_key=nb, reoon_key=reoon, port25_ok=False,
                            say=lambda c, m: logging.info("[%s] %s", c, m),
                            detail=lambda m: logging.debug(m))

    write_header = not result_path.exists()
    total_contacts = total_emails = 0
    t0 = time.time()
    for i, company in enumerate(companies, 1):
        if company.lower() in done:
            continue
        logging.info("=== (%d/%d) %s ===", i, len(companies), company)
        try:
            from emailer.smtp_verifier import network_up
            waited = 0
            while not network_up() and waited < 3600:          # wait out outages, up to an hour
                logging.info("No internet - waiting...")
                time.sleep(30)
                waited += 30
            contacts = discover_contacts(company, sources)
            rows = list(enricher.enrich_company(company, contacts)) if (enricher and contacts) else contacts
        except Exception:
            logging.exception("Company failed (will be retried on the next run): %s", company)
            continue

        for r in rows:
            try:
                append_contact(r, company)
            except Exception:
                logging.exception("Could not save a row for %s", company)
        with open(result_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=OUT_FIELDS, extrasaction="ignore")
            if write_header:
                w.writeheader()
                write_header = False
            w.writerows(rows)
        total_contacts += len(rows)
        total_emails += sum(1 for r in rows if r.get("email") and r.get("email_status") not in ("bounced", "error"))
        done.add(company.lower())
        state_path.write_text(json.dumps({"done": sorted(done)}), encoding="utf-8")
        logging.info("%s: %d contacts, %d with email | totals: %d contacts, %d emails, %.0f min elapsed",
                     company, len(rows), sum(1 for r in rows if r.get("email")), total_contacts,
                     total_emails, (time.time() - t0) / 60)

    logging.info("Autopilot finished: %d contacts, %d with an email -> %s", total_contacts, total_emails, result_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
