"""Headless GUI test of the one-click flow. Search is canned; email enrichment is real (network)."""
import os, sys, tempfile, time
from pathlib import Path
scratch = tempfile.mkdtemp(prefix="ls_gui2_")
os.environ.update(USERPROFILE=scratch, HOME=scratch, QT_QPA_PLATFORM="offscreen",
                  LEADSCOUT_OUTPUT_DIR=str(Path(scratch) / "LeadScout_Output"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import faulthandler; faulthandler.dump_traceback_later(120, repeat=True)

from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication([])
dialogs = []
for _n in ("question", "information", "warning", "critical"):
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: QMessageBox.Ok))
QMessageBox.exec = lambda self: dialogs.append(self.text()) or 0
QMessageBox.clickedButton = lambda self: None

from ui.main_window import MainWindow
MainWindow._show_onboarding = lambda self, *a, **k: None
import scraper.worker as sw

def fake_process(self, company):
    for n, f, l in (("Frank Sattler", "Frank", "Sattler"), ("Jos\u00e9 Mu\u00f1oz", "Jos\u00e9", "Mu\u00f1oz")):
        self.contact_found.emit({"name": n, "first_name": f, "last_name": l, "title": "Geologist",
            "company": company, "location": "Houston", "basin": "", "source": "t", "date_pulled": "2026-10-08",
            "linkedin_url": f"https://www.linkedin.com/in/{f.lower()}"})
    return 2
sw.SearchWorker._process_company = fake_process
sw._COMPANY_DELAY = (0, 0)

w = MainWindow()
w._settings["nb_api_key"] = ""; w._settings["reoon_api_key"] = ""   # never spend real API credits in tests
# 1) clicking with nothing typed must not hang or disable the button
w._btn_pipeline.click()
assert w._btn_pipeline.isEnabled() and not w._pipeline_mode
# 2) type a company and click once
w._input.setText("Ring Energy")
w._btn_pipeline.click()
t0 = time.time()
while (w._pipeline_mode or not w._btn_pipeline.isEnabled()) and time.time() - t0 < 200:
    app.processEvents(); time.sleep(0.05)
assert w._btn_pipeline.isEnabled() and not w._pipeline_mode, "pipeline never finished"
print("contacts:", len(w._contacts), "email_contacts:", len(w._email_contacts), "enriched:", [(c["name"], c["email"], c["email_status"]) for c in w._email_enriched])
print("dialog:", dialogs)
assert dialogs and "ready" in dialogs[-1]
assert len(w._email_enriched) == 2 and all(c["email"] for c in w._email_enriched), "enrichment produced nothing"
out = sorted(Path(scratch, "LeadScout_Output").glob("pipeline_*.csv"))
assert out, "no pipeline csv"
print(out[-1].read_text(encoding="utf-8"))
print("GUI PIPELINE OK")
