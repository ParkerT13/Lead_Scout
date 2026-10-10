"""CSV front-end for the enrichment engine (used by the _enrich_batch / _generate_emails scripts)."""

import csv
import json
import sys
import time
import random
from collections import defaultdict
from pathlib import Path

from emailer.engine import Enricher
from emailer.smtp_verifier import port25_available
from emailer.api_verifier import reset_session

_ROOT = Path(__file__).resolve().parent.parent
OUT_COLS = ("email", "email_status", "email_source", "confidence")


def load_api_keys() -> tuple[str, str]:
    for name in ("credentials.json", "credentials.json.example"):
        p = _ROOT / name
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                return d.get("nb_api_key", ""), d.get("reoon_api_key", "")
            except Exception:
                pass
    return "", ""


def run(input_path: Path, output_path: Path, verify: bool) -> list[dict]:
    """verify=False: evidence-ranked emails only (no SMTP / API calls)."""
    # Windows consoles are cp1252: a name like "Kim Dong-seung" in Korean must not crash the run
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    with input_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        contacts = list(reader)
    for col in OUT_COLS:
        if col not in fields:
            fields.append(col)

    port25_ok, nb, reoon = False, "", ""
    if verify:
        port25_ok = port25_available()
        nb, reoon = load_api_keys()
        reset_session()
        if not port25_ok and not (nb or reoon):
            print("NOTE: port 25 blocked and no API keys - emails will be evidence-ranked, not verified.\n")
        elif not port25_ok:
            print("Port 25 blocked - verifying through NeverBounce/Reoon API.\n")

    enricher = Enricher(nb_key=nb, reoon_key=reoon, port25_ok=port25_ok,
                        say=lambda c, m: print(f"  {m}"),
                        detail=lambda m: print(f"  {m}"))

    by_company = defaultdict(list)
    for c in contacts:
        by_company[c.get("company", "Unknown")].append(c)

    results: list[dict] = []
    names = list(by_company)
    prefetch = enricher.prefetch(names)
    for i, company in enumerate(names):
        group = by_company[company]
        prefetch.wait(company)
        print(f"--- {company.upper()} ({len(group)} contacts) ---")
        try:
            for r in enricher.enrich_company(company, group):
                results.append(r)
                print(f"  {r.get('name', '')} -> {r.get('email') or '-'} [{r['email_status']}]")
        except Exception as e:
            print(f"  [!] {company} failed: {e}")
            results.extend({**c, "email": "", "email_status": "error", "email_source": str(e)[:60],
                            "confidence": ""} for c in group)
        # write progress after every company so a crash never loses work
        _write(output_path, fields, results)
        if i < len(names) - 1 and (verify and (port25_ok or enricher.has_api)):
            time.sleep(random.uniform(3, 6))
        print()

    prefetch.close()
    summary: dict[str, int] = defaultdict(int)
    for r in results:
        summary[r["email_status"]] += 1
    print(f"Done. {output_path}")
    for k, v in sorted(summary.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<22}: {v}")
    return results


def _write(path: Path, fields: list[str], rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(verify: bool, usage: str):
    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.parent / (src.stem + "_enriched.csv")
    run(src, dst, verify)
