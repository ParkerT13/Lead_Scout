"""Headless GUI smoke test (offscreen Qt, HOME redirected to a scratch dir). Uses the network."""
import os, sys, tempfile, time
from pathlib import Path
scratch = tempfile.mkdtemp(prefix="ls_gui_")
os.environ["USERPROFILE"] = scratch; os.environ["HOME"] = scratch
os.environ["LEADSCOUT_OUTPUT_DIR"] = str(Path(scratch) / "LeadScout_Output")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer
app = QApplication([])
# modal dialogs would block a headless run forever: auto-answer them
for _n in ("question", "information", "warning", "critical"):
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: QMessageBox.No if _n == "question" else QMessageBox.Ok))
import faulthandler; faulthandler.dump_traceback_later(60, repeat=True)
from ui.main_window import MainWindow
MainWindow._show_onboarding = lambda self, *a, **k: None   # first-run modal
import emailer.worker as ew

w = MainWindow()
contacts = [
    {"name": "Frank Sattler", "first_name": "Frank", "last_name": "Sattler", "title": "Senior Geologist",
     "company": "Ring Energy", "location": "Houston", "basin": "Permian",
     "linkedin_url": "https://www.linkedin.com/in/a", "email": ""},
    {"name": "Jose Munoz", "first_name": "Jos\u00e9", "last_name": "Mu\u00f1oz", "title": "Reservoir Engineer",
     "company": "Ring Energy", "location": "Midland", "basin": "Permian",
     "linkedin_url": "https://www.linkedin.com/in/b", "email": ""},
]
w._email_contacts = contacts
w._populate_email_table(contacts)
w._email_start()

done = {"v": False}
w._email_worker.all_done.connect(lambda: done.__setitem__("v", True))
t0 = time.time()
while not done["v"] and time.time() - t0 < 240:
    app.processEvents(); time.sleep(0.05)
assert done["v"], "worker did not finish"
rows = w._email_enriched
for r in rows:
    print(r["name"], r["email"], r["email_status"], r["confidence"])
assert len(rows) == 2 and all(r["email"] for r in rows)
assert "munoz" in rows[1]["email"] and "?" not in rows[1]["email"], rows[1]["email"]
# CRM tab filter must accept the new statuses
w._crm_contacts = rows
for k, cb in w._crm_status_checks.items():
    cb.setChecked(k not in ("_drop",))
flt = w._crm_filtered_contacts()
print("crm rows:", len(flt))
assert len(flt) == 2
print("GUI SMOKE OK")
