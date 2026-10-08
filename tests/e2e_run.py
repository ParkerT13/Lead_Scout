"""End-to-end smoke run: SearchWorker -> EmailWorker (headless). Not a unit test; uses the network.
Usage: py -3 tests/e2e_run.py "Company Name" [max_queries]"""
import os, sys, tempfile, time
from pathlib import Path
os.environ.setdefault("LEADSCOUT_OUTPUT_DIR", tempfile.mkdtemp(prefix="leadscout_e2e_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication
import scraper.worker as sw
import emailer.worker as ew
from emailer import engine

company = sys.argv[1]
maxq = int(sys.argv[2]) if len(sys.argv) > 2 else 6
sw._QUERY_DELAY = (1.0, 2.0); sw._COMPANY_DELAY = (0, 0); sw._FALLBACK_DELAY = (1, 2)
sw._DRY_STREAK_STOP = 3
engine_delay = (0.0, 0.0)

app = QCoreApplication([])
contacts = []
w = sw.SearchWorker([company], Path(os.environ["LEADSCOUT_OUTPUT_DIR"]))
w.contact_found.connect(contacts.append)
w.status_update.connect(lambda m: None)
# cap queries for the smoke run
import scraper.query_builder as qb
orig = qb.build_query_plan
sw.build_query_plan = lambda c: [g[:maxq] for g in orig(c)][:1]
w.run()
print(f"\nSEARCH: {len(contacts)} contacts")
for c in contacts[:15]:
    print(f"  {c['name']:<24} | {c['title'][:40]:<40} | {c['location']}")

out = []
e = ew.EmailWorker(contacts)
e.contact_enriched.connect(out.append)
e.company_status.connect(lambda c, m: print("  [status]", m))
e.run()
print(f"\nEMAIL: {len(out)} rows")
for r in out:
    print(f"  {r['name']:<24} {r['email']:<38} {r['email_status']:<18} {r['confidence']}")
bad = [r for r in out if r["email"] and ("@" not in r["email"] or " " in r["email"])]
assert not bad, bad
assert len(out) == len(contacts)
print("E2E OK")
