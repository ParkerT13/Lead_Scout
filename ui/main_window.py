import logging
import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QLineEdit, QPushButton, QLabel, QProgressBar, QFileDialog,
    QGroupBox, QHeaderView, QMenu, QMessageBox, QAbstractItemView,
    QApplication, QTabWidget,
)
from PySide6.QtCore import Qt, Slot, QUrl
from PySide6.QtGui import QColor, QFont, QDesktopServices

from scraper.worker import SearchWorker
from emailer.worker import EmailWorker
from verifier.worker import VerifyWorker
from output.csv_writer import append_contact, export_contacts, get_output_dir

logger = logging.getLogger(__name__)

COLUMNS = ["First Name", "Last Name", "Title", "Company", "Location", "Basin", "LinkedIn URL", "Emp. Status", "Source", "Date"]
FIELDS  = ["first_name", "last_name", "title", "company", "location", "basin", "linkedin_url", "emp_status", "source", "date_pulled"]

_EMP_STATUS_COLORS = {
    "current": "#2E7D32",   # green
    "stale":   "#C62828",   # red
    "unknown": "#888888",   # gray
}

_STATUS = {
    "pending": ("⏳", "#888888"),
    "running": ("🔵", "#1565C0"),
    "done":    ("✅", "#2E7D32"),
    "failed":  ("❌", "#C62828"),
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Contact Puller — O&G LinkedIn Scraper")
        self.setMinimumSize(1300, 740)

        self._contacts:       list[dict]       = []
        self._company_status: dict[str, str]   = {}
        self._worker:         SearchWorker | None = None
        self._verify_worker:  VerifyWorker | None = None
        self._pipeline_mode:  bool = False

        self._build_ui()
        self._apply_style()

    # ── UI Construction ──────────────────────────────────────────────────

    def _build_ui(self):
        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        root = QVBoxLayout(root_widget)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_puller_tab(), "Contact Puller")
        self._tabs.addTab(self._build_email_tab(), "Email Enricher")
        root.addWidget(self._tabs)

    def _build_puller_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)
        root.addLayout(self._build_toolbar())
        root.addWidget(self._build_splitter(), stretch=1)
        root.addLayout(self._build_bottom_bar())
        return tab

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Enter company name (e.g. EOG Resources)…")
        self._input.setMinimumHeight(38)
        self._input.returnPressed.connect(self._add_company)
        row.addWidget(self._input, stretch=3)

        for text, slot in [
            ("Add Company",    self._add_company),
            ("Load from File", self._load_from_file),
        ]:
            btn = QPushButton(text)
            btn.setMinimumHeight(38)
            btn.clicked.connect(slot)
            row.addWidget(btn)

        self._btn_start = QPushButton("▶  Start")
        self._btn_start.setObjectName("btn_start")
        self._btn_start.setMinimumHeight(38)
        self._btn_start.clicked.connect(self._start_search)
        row.addWidget(self._btn_start)

        self._btn_stop = QPushButton("■  Stop")
        self._btn_stop.setObjectName("btn_stop")
        self._btn_stop.setMinimumHeight(38)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_search)
        row.addWidget(self._btn_stop)

        self._btn_verify = QPushButton("Verify Employment")
        self._btn_verify.setObjectName("btn_verify")
        self._btn_verify.setMinimumHeight(38)
        self._btn_verify.setToolTip(
            "Check each contact's LinkedIn profile to confirm they still work at the target company.\n"
            "Uses the public og:title tag — no login required."
        )
        self._btn_verify.clicked.connect(self._toggle_verify)
        row.addWidget(self._btn_verify)

        self._btn_pipeline = QPushButton("Run Full Pipeline")
        self._btn_pipeline.setObjectName("btn_pipeline")
        self._btn_pipeline.setMinimumHeight(38)
        self._btn_pipeline.setToolTip(
            "Pull contacts, find email domains, probe format variations, generate and verify all emails.\n"
            "Automatically removes bounced addresses and exports viable contacts."
        )
        self._btn_pipeline.clicked.connect(self._pipeline_start)
        row.addWidget(self._btn_pipeline)

        btn_export = QPushButton("Export CSV")
        btn_export.setMinimumHeight(38)
        btn_export.clicked.connect(self._export_csv)
        row.addWidget(btn_export)

        return row

    def _build_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Horizontal)

        # ── Left: company queue ──────────────────────────────────────────
        left_box = QGroupBox("Company Queue")
        left_layout = QVBoxLayout(left_box)
        left_layout.setSpacing(6)

        self._queue = QListWidget()
        self._queue.setMinimumWidth(240)
        self._queue.setSpacing(2)
        left_layout.addWidget(self._queue)

        btn_clear = QPushButton("Clear Pending")
        btn_clear.clicked.connect(self._clear_pending)
        left_layout.addWidget(btn_clear)

        splitter.addWidget(left_box)

        # ── Right: results table ─────────────────────────────────────────
        right_box = QGroupBox("Results")
        right_layout = QVBoxLayout(right_box)
        right_layout.setSpacing(6)

        self._count_label = QLabel("0 contacts found")
        font = QFont()
        font.setBold(True)
        self._count_label.setFont(font)
        right_layout.addWidget(self._count_label)

        self._table = QTableWidget()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        # Give URL column a bit more room (now index 6 with first_name + last_name)
        hh.setSectionResizeMode(6, QHeaderView.Interactive)
        self._table.setColumnWidth(6, 280)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.cellDoubleClicked.connect(self._cell_double_clicked)
        right_layout.addWidget(self._table)

        splitter.addWidget(right_box)
        splitter.setSizes([265, 1035])
        return splitter

    def _build_bottom_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setMaximumHeight(16)
        row.addWidget(self._progress, stretch=1)

        self._status = QLabel("Ready.")
        row.addWidget(self._status, stretch=3)

        out_label = QLabel(f"Output: {get_output_dir()}")
        out_label.setStyleSheet("color: #777; font-size: 11px;")
        row.addWidget(out_label, stretch=2)

        return row

    # ── Email Enricher Tab ───────────────────────────────────────────────

    def _build_email_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        # Top toolbar
        top = QHBoxLayout()
        btn_use = QPushButton("Use Contacts from Puller")
        btn_use.setMinimumHeight(36)
        btn_use.setToolTip("Load contacts already found in the Contact Puller tab")
        btn_use.clicked.connect(self._email_load_from_puller)
        top.addWidget(btn_use)

        btn_load = QPushButton("Load from CSV")
        btn_load.setMinimumHeight(36)
        btn_load.clicked.connect(self._email_load_from_csv)
        top.addWidget(btn_load)

        top.addStretch()

        self._email_btn_start = QPushButton("▶  Start Enrichment")
        self._email_btn_start.setObjectName("btn_start")
        self._email_btn_start.setMinimumHeight(36)
        self._email_btn_start.clicked.connect(self._email_start)
        top.addWidget(self._email_btn_start)

        self._email_btn_stop = QPushButton("■  Stop")
        self._email_btn_stop.setObjectName("btn_stop")
        self._email_btn_stop.setMinimumHeight(36)
        self._email_btn_stop.setEnabled(False)
        self._email_btn_stop.clicked.connect(self._email_stop)
        top.addWidget(self._email_btn_stop)

        btn_export = QPushButton("Export Enriched CSV")
        btn_export.setMinimumHeight(36)
        btn_export.clicked.connect(self._email_export)
        top.addWidget(btn_export)

        root.addLayout(top)

        # Company status panel + results table
        splitter = QSplitter(Qt.Horizontal)

        left_box = QGroupBox("Companies")
        left_layout = QVBoxLayout(left_box)
        self._email_company_list = QListWidget()
        self._email_company_list.setMinimumWidth(240)
        left_layout.addWidget(self._email_company_list)
        splitter.addWidget(left_box)

        right_box = QGroupBox("Enriched Contacts")
        right_layout = QVBoxLayout(right_box)

        self._email_count_label = QLabel("0 contacts loaded")
        font = QFont(); font.setBold(True)
        self._email_count_label.setFont(font)
        right_layout.addWidget(self._email_count_label)

        _EMAIL_COLS = ["First Name", "Last Name", "Title", "Company", "Email", "Status", "Source", "LinkedIn URL"]
        self._email_table = QTableWidget()
        self._email_table.setColumnCount(len(_EMAIL_COLS))
        self._email_table.setHorizontalHeaderLabels(_EMAIL_COLS)
        self._email_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._email_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._email_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._email_table.setAlternatingRowColors(True)
        self._email_table.setSortingEnabled(True)
        self._email_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._email_table.customContextMenuRequested.connect(self._email_context_menu)
        right_layout.addWidget(self._email_table)

        splitter.addWidget(right_box)
        splitter.setSizes([265, 1035])
        root.addWidget(splitter, stretch=1)

        # Bottom bar
        bottom = QHBoxLayout()
        self._email_progress = QProgressBar()
        self._email_progress.setVisible(False)
        self._email_progress.setMaximumHeight(16)
        bottom.addWidget(self._email_progress, stretch=1)

        self._email_status = QLabel("Load contacts to begin.")
        bottom.addWidget(self._email_status, stretch=4)
        root.addLayout(bottom)

        # Internal state
        self._email_contacts: list[dict] = []
        self._email_worker: EmailWorker | None = None
        self._email_enriched: list[dict] = []

        return tab

    # ── Email tab: load contacts ─────────────────────────────────────────

    def _email_load_from_puller(self):
        if not self._contacts:
            self._email_status.setText("No contacts in Contact Puller yet. Run a search first.")
            return
        self._email_contacts = [dict(c) for c in self._contacts]
        self._populate_email_table(self._email_contacts)
        self._email_status.setText(f"{len(self._email_contacts)} contacts loaded from Puller.")

    def _email_load_from_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Contacts CSV", str(get_output_dir()), "CSV Files (*.csv)"
        )
        if not path:
            return
        contacts = []
        try:
            with open(path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    contacts.append({k.lower().replace(" ", "_"): v for k, v in row.items()})
            self._email_contacts = contacts
            self._populate_email_table(contacts)
            self._email_status.setText(f"{len(contacts)} contacts loaded from {Path(path).name}")
        except Exception as e:
            QMessageBox.warning(self, "Load Error", str(e))

    def _populate_email_table(self, contacts: list[dict]):
        self._email_table.setSortingEnabled(False)
        self._email_table.setRowCount(0)
        self._email_company_list.clear()

        companies_seen: set[str] = set()
        for c in contacts:
            row = self._email_table.rowCount()
            self._email_table.insertRow(row)
            for col, key in enumerate(["first_name", "last_name", "title", "company", "email",
                                        "email_status", "email_source", "linkedin_url"]):
                val = str(c.get(key, ""))
                item = QTableWidgetItem(val)
                if key == "linkedin_url":
                    item.setForeground(QColor("#1565C0"))
                self._email_table.setItem(row, col, item)

            company = c.get("company", "")
            if company and company not in companies_seen:
                companies_seen.add(company)
                li = QListWidgetItem(f"⏳  {company}")
                li.setData(Qt.UserRole, company)
                self._email_company_list.addItem(li)

        self._email_table.setSortingEnabled(True)
        self._email_count_label.setText(f"{len(contacts)} contacts loaded")

    # ── Email tab: enrichment worker ─────────────────────────────────────

    def _email_start(self):
        if not self._email_contacts:
            self._email_status.setText("Load contacts first.")
            return

        self._email_enriched = []
        self._email_btn_start.setEnabled(False)
        self._email_btn_stop.setEnabled(True)
        self._email_progress.setVisible(True)
        self._email_progress.setRange(0, len(self._email_contacts))
        self._email_progress.setValue(0)

        self._email_worker = EmailWorker(self._email_contacts)
        self._email_worker.contact_enriched.connect(self._on_email_enriched)
        self._email_worker.company_started.connect(self._on_email_company_started)
        self._email_worker.company_status.connect(self._on_email_company_status)
        self._email_worker.company_done.connect(self._on_email_company_done)
        self._email_worker.status_update.connect(self._on_email_status)
        self._email_worker.port25_blocked.connect(self._on_port25_blocked)
        self._email_worker.all_done.connect(self._on_email_all_done)
        self._email_worker.start()

    def _email_stop(self):
        if self._email_worker:
            self._email_worker.stop()
        self._email_btn_stop.setEnabled(False)
        self._email_status.setText("Stopping after current contact...")

    @Slot(dict)
    def _on_email_enriched(self, contact: dict):
        self._email_enriched.append(contact)
        self._email_progress.setValue(len(self._email_enriched))

        # Find row by LinkedIn URL and update in place
        url = contact.get("linkedin_url", "")
        for row in range(self._email_table.rowCount()):
            url_item = self._email_table.item(row, 7)
            if url_item and url_item.text() == url:
                self._email_table.setSortingEnabled(False)
                email      = contact.get("email", "")
                status     = contact.get("email_status", "")
                source     = contact.get("email_source", "")

                self._email_table.setItem(row, 4, QTableWidgetItem(email))

                status_item = QTableWidgetItem(status)
                if status == "verified":
                    status_item.setForeground(QColor("#2E7D32"))
                elif status == "catch-all-confirmed":
                    status_item.setForeground(QColor("#E65100"))   # amber — send cautiously
                elif status == "catch-all-risky":
                    status_item.setForeground(QColor("#C62828"))   # red — do not send
                elif status == "invalid":
                    status_item.setForeground(QColor("#C62828"))
                else:
                    status_item.setForeground(QColor("#888"))
                self._email_table.setItem(row, 5, status_item)
                self._email_table.setItem(row, 6, QTableWidgetItem(source))
                self._email_table.setSortingEnabled(True)
                break

    @Slot(str, int)
    def _on_email_company_started(self, company: str, count: int):
        self._email_status.setText(f"Processing {company} ({count} contacts)...")
        self._set_email_company_icon(company, "🔵")

    @Slot(str, str)
    def _on_email_company_status(self, company: str, msg: str):
        self._email_status.setText(f"[{company}] {msg}")

    @Slot(str, int, int)
    def _on_email_company_done(self, company: str, verified: int, total: int):
        icon = "✅" if verified > 0 else "⚠️"
        self._set_email_company_icon(company, icon, suffix=f" ({verified}/{total} verified)")

    @Slot(str)
    def _on_email_status(self, msg: str):
        self._email_status.setText(msg)

    @Slot()
    def _on_port25_blocked(self):
        QMessageBox.warning(
            self, "Port 25 Blocked",
            "Outbound port 25 appears to be blocked by your ISP or network.\n\n"
            "SMTP verification may fail for all addresses. Options:\n"
            "  • Use a VPN with a clean IP\n"
            "  • Run on a cloud VM (AWS/GCP allow port 25 on some instance types)\n\n"
            "The tool will continue and mark results as 'error' where blocked."
        )

    @Slot()
    def _on_email_all_done(self):
        self._email_btn_start.setEnabled(True)
        self._email_btn_stop.setEnabled(False)
        self._email_progress.setVisible(False)
        verified   = sum(1 for c in self._email_enriched if c.get("email_status") == "verified")
        confirmed  = sum(1 for c in self._email_enriched if c.get("email_status") == "catch-all-confirmed")
        risky      = sum(1 for c in self._email_enriched if c.get("email_status") == "catch-all-risky")
        other      = len(self._email_enriched) - verified - confirmed - risky
        self._email_status.setText(
            f"Done. {verified} verified | "
            f"{confirmed} catch-all (pattern confirmed) | "
            f"{risky} catch-all (risky) | "
            f"{other} unresolved."
        )
        if self._pipeline_mode:
            self._pipeline_mode = False
            self._btn_pipeline.setEnabled(True)
            self._pipeline_finish()

    def _pipeline_finish(self):
        from emailer.worker import DROP_STATUSES
        viable  = [c for c in self._email_enriched
                   if c.get("email") and c.get("email_status", "") not in DROP_STATUSES]
        dropped = len(self._email_enriched) - len(viable)

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = get_output_dir() / f"pipeline_{ts}.csv"
        fields   = ["first_name", "last_name", "title", "company", "email",
                    "email_status", "email_source", "location", "basin",
                    "linkedin_url", "date_pulled"]
        try:
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows([{k: c.get(k, "") for k in fields} for c in viable])
            self._email_status.setText(
                f"Pipeline complete. {len(viable)} viable contacts saved to {out_path.name}  |  "
                f"{dropped} dropped (bounced / invalid)."
            )
        except Exception as e:
            self._email_status.setText(f"Pipeline complete but export failed: {e}")

    def _set_email_company_icon(self, company: str, icon: str, suffix: str = ""):
        for i in range(self._email_company_list.count()):
            item = self._email_company_list.item(i)
            if item.data(Qt.UserRole) == company:
                item.setText(f"{icon}  {company}{suffix}")
                return

    def _email_context_menu(self, pos):
        row = self._email_table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        email_item = self._email_table.item(row, 4)
        url_item   = self._email_table.item(row, 7)
        if email_item and email_item.text():
            menu.addAction("Copy Email").triggered.connect(
                lambda: QApplication.clipboard().setText(email_item.text())
            )
        if url_item and url_item.text():
            menu.addAction("Open LinkedIn").triggered.connect(
                lambda: QDesktopServices.openUrl(QUrl(url_item.text()))
            )
        menu.addSeparator()
        menu.addAction("Copy Row").triggered.connect(lambda: self._email_copy_row(row))
        menu.exec(self._email_table.viewport().mapToGlobal(pos))

    def _email_copy_row(self, row: int):
        values = [
            (self._email_table.item(row, c) or QTableWidgetItem("")).text()
            for c in range(self._email_table.columnCount())
        ]
        QApplication.clipboard().setText("\t".join(values))

    def _email_export(self):
        if not self._email_enriched:
            if not self._email_contacts:
                self._email_status.setText("No contacts to export.")
                return
            data = self._email_contacts
        else:
            data = self._email_enriched

        default = str(get_output_dir() / "contacts_enriched.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export Enriched CSV", default, "CSV Files (*.csv)")
        if not path:
            return

        fields = ["first_name", "last_name", "title", "company", "email", "email_status",
                  "email_source", "location", "basin", "linkedin_url", "source", "date_pulled"]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows([{k: c.get(k, "") for k in fields} for c in data])
            self._email_status.setText(f"Exported {len(data)} contacts to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget          { background: #f4f4f4; }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ccc;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                border-radius: 4px;
                padding: 4px 14px;
                background: #e0e0e0;
                border: 1px solid #bbb;
            }
            QPushButton:hover   { background: #d4d4d4; }
            QPushButton:pressed { background: #c8c8c8; }
            QPushButton#btn_start {
                background: #388E3C;
                color: white;
                font-weight: bold;
                border: none;
            }
            QPushButton#btn_start:hover   { background: #2E7D32; }
            QPushButton#btn_start:disabled { background: #aaa; color: #ddd; }
            QPushButton#btn_stop {
                background: #D32F2F;
                color: white;
                font-weight: bold;
                border: none;
            }
            QPushButton#btn_stop:hover    { background: #C62828; }
            QPushButton#btn_stop:disabled { background: #bbb; color: #888; border: none; }
            QPushButton#btn_verify {
                background: #1565C0;
                color: white;
                font-weight: bold;
                border: none;
            }
            QPushButton#btn_verify:hover   { background: #0D47A1; }
            QPushButton#btn_verify:disabled { background: #aaa; color: #ddd; border: none; }
            QPushButton#btn_pipeline {
                background: #6A1B9A;
                color: white;
                font-weight: bold;
                border: none;
            }
            QPushButton#btn_pipeline:hover   { background: #4A148C; }
            QPushButton#btn_pipeline:disabled { background: #aaa; color: #ddd; border: none; }
            QTableWidget {
                gridline-color: #ddd;
                selection-background-color: #BBDEFB;
                selection-color: #000;
            }
            QHeaderView::section {
                background: #ececec;
                font-weight: bold;
                padding: 5px;
                border: none;
                border-right: 1px solid #ddd;
            }
            QListWidget::item         { padding: 5px 4px; }
            QListWidget::item:selected { background: #BBDEFB; color: #000; }
        """)

    # ── Queue management ─────────────────────────────────────────────────

    def _add_company(self):
        name = self._input.text().strip()
        if not name or self._in_queue(name):
            return
        self._enqueue(name)
        self._input.clear()

    def _enqueue(self, name: str):
        icon, color = _STATUS["pending"]
        item = QListWidgetItem(f"{icon}  {name}")
        item.setData(Qt.UserRole, name)
        item.setForeground(QColor(color))
        self._queue.addItem(item)
        self._company_status[name] = "pending"

    def _in_queue(self, name: str) -> bool:
        for i in range(self._queue.count()):
            if self._queue.item(i).data(Qt.UserRole).lower() == name.lower():
                return True
        return False

    def _load_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Companies", "",
            "Text/CSV Files (*.txt *.csv);;All Files (*)"
        )
        if not path:
            return
        added = 0
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    name = line.strip().strip(',"\' ')
                    if name and not self._in_queue(name):
                        self._enqueue(name)
                        added += 1
            self._status.setText(f"Loaded {added} companies from file.")
        except Exception as e:
            QMessageBox.warning(self, "Load Error", str(e))

    def _clear_pending(self):
        if self._worker and self._worker.isRunning():
            QMessageBox.warning(self, "Running", "Stop the search before clearing the queue.")
            return
        to_remove = []
        for i in range(self._queue.count()):
            item = self._queue.item(i)
            company = item.data(Qt.UserRole)
            if self._company_status.get(company) == "pending":
                to_remove.append(i)
        for i in reversed(to_remove):
            self._queue.takeItem(i)

    # ── Search control ───────────────────────────────────────────────────

    def _pipeline_start(self):
        """Start the full pull → email → filter → export pipeline."""
        self._pipeline_mode = True
        self._btn_pipeline.setEnabled(False)
        self._start_search()

    def _start_search(self):
        pending = [
            self._queue.item(i).data(Qt.UserRole)
            for i in range(self._queue.count())
            if self._company_status.get(self._queue.item(i).data(Qt.UserRole)) == "pending"
        ]
        if not pending:
            self._status.setText("No pending companies in queue.")
            return

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # Indeterminate spinner

        self._worker = SearchWorker(pending, get_output_dir())
        self._worker.contact_found.connect(self._on_contact)
        self._worker.company_started.connect(self._on_started)
        self._worker.company_progress.connect(self._on_progress)
        self._worker.company_finished.connect(self._on_finished)
        self._worker.company_failed.connect(self._on_failed)
        self._worker.status_update.connect(self._on_status)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

    def _stop_search(self):
        if self._worker:
            self._worker.stop()
        self._btn_stop.setEnabled(False)
        self._status.setText("Stopping after current query completes…")

    # ── Verify employment ────────────────────────────────────────────────

    def _toggle_verify(self):
        if self._verify_worker and self._verify_worker.isRunning():
            self._verify_worker.stop()
            self._btn_verify.setText("Verify Employment")
            self._status.setText("Stopping verification...")
            return

        if not self._contacts:
            self._status.setText("No contacts to verify. Run a search first.")
            return

        self._btn_verify.setText("■  Stop Verify")
        self._progress.setVisible(True)
        self._progress.setRange(0, len(self._contacts))
        self._progress.setValue(0)

        self._verify_worker = VerifyWorker(self._contacts)
        self._verify_worker.contact_verified.connect(self._on_verify_contact)
        self._verify_worker.progress.connect(self._on_verify_progress)
        self._verify_worker.status_update.connect(self._on_status)
        self._verify_worker.all_done.connect(self._on_verify_done)
        self._verify_worker.start()

    @Slot(str, str)
    def _on_verify_contact(self, linkedin_url: str, emp_status: str):
        # Find the contact in self._contacts and update emp_status
        for c in self._contacts:
            if c.get("linkedin_url", "") == linkedin_url:
                c["emp_status"] = emp_status
                break

        # Find matching row in table and update the Emp. Status cell (index 6)
        emp_col = FIELDS.index("emp_status")
        url_col = FIELDS.index("linkedin_url")
        self._table.setSortingEnabled(False)
        for row in range(self._table.rowCount()):
            url_item = self._table.item(row, url_col)
            if url_item and url_item.text() == linkedin_url:
                status_item = QTableWidgetItem(emp_status)
                color = _EMP_STATUS_COLORS.get(emp_status)
                if color:
                    status_item.setForeground(QColor(color))
                self._table.setItem(row, emp_col, status_item)
                break
        self._table.setSortingEnabled(True)

    @Slot(int, int)
    def _on_verify_progress(self, current: int, total: int):
        self._progress.setValue(current)

    @Slot()
    def _on_verify_done(self):
        self._btn_verify.setText("Verify Employment")
        self._progress.setVisible(False)
        current = sum(1 for c in self._contacts if c.get("emp_status") == "current")
        stale   = sum(1 for c in self._contacts if c.get("emp_status") == "stale")
        unknown = sum(1 for c in self._contacts if c.get("emp_status") == "unknown")
        self._status.setText(
            f"Verification done: {current} current, {stale} stale, {unknown} unknown."
        )

    # ── Worker signal handlers ───────────────────────────────────────────

    @Slot(dict)
    def _on_contact(self, contact: dict):
        self._contacts.append(contact)

        # Temporarily disable sorting so row insertions are stable
        self._table.setSortingEnabled(False)
        row = self._table.rowCount()
        self._table.insertRow(row)

        for col, key in enumerate(FIELDS):
            val  = str(contact.get(key, ""))
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if key == "linkedin_url":
                item.setForeground(QColor("#1565C0"))
                item.setToolTip("Double-click to open in browser  |  Right-click for options")
            self._table.setItem(row, col, item)

        self._table.setSortingEnabled(True)
        self._count_label.setText(f"{len(self._contacts)} contacts found")

        # Auto-save immediately
        append_contact(contact, contact.get("company", "unknown"))

    @Slot(str)
    def _on_started(self, company: str):
        self._set_status(company, "running")

    @Slot(str, int, int)
    def _on_progress(self, company: str, current: int, total: int):
        self._progress.setRange(0, total)
        self._progress.setValue(current)

    @Slot(str, int)
    def _on_finished(self, company: str, count: int):
        self._set_status(company, "done", suffix=f" ({count})")

    @Slot(str)
    def _on_failed(self, company: str):
        self._set_status(company, "failed", suffix=" (0 — logged)")

    @Slot(str)
    def _on_status(self, msg: str):
        self._status.setText(msg)

    @Slot()
    def _on_all_done(self):
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._progress.setVisible(False)
        out = get_output_dir()
        self._status.setText(
            f"Done. {len(self._contacts)} contacts found. Auto-saved to {out}"
        )
        if self._pipeline_mode and self._contacts:
            self._status.setText(
                f"{len(self._contacts)} contacts pulled. Starting email pipeline..."
            )
            self._tabs.setCurrentIndex(1)      # switch to Email Enricher tab
            self._email_load_from_puller()
            self._email_start()

    def _set_status(self, company: str, status: str, suffix: str = ""):
        icon, color = _STATUS[status]
        for i in range(self._queue.count()):
            item = self._queue.item(i)
            if item.data(Qt.UserRole) == company:
                item.setText(f"{icon}  {company}{suffix}")
                item.setForeground(QColor(color))
                self._company_status[company] = status
                return

    # ── Table interactions ───────────────────────────────────────────────

    @Slot(int, int)
    def _cell_double_clicked(self, row: int, col: int):
        if FIELDS[col] == "linkedin_url":
            item = self._table.item(row, col)
            if item and item.text():
                QDesktopServices.openUrl(QUrl(item.text()))

    def _context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0:
            return

        url_item  = self._table.item(row, FIELDS.index("linkedin_url"))
        menu      = QMenu(self)

        if url_item and url_item.text():
            url = url_item.text()
            menu.addAction("Open LinkedIn in Browser").triggered.connect(
                lambda: QDesktopServices.openUrl(QUrl(url))
            )
            menu.addAction("Copy LinkedIn URL").triggered.connect(
                lambda: QApplication.clipboard().setText(url)
            )
            menu.addSeparator()

        menu.addAction("Copy Row as TSV").triggered.connect(lambda: self._copy_row(row))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_row(self, row: int):
        values = [
            (self._table.item(row, c) or QTableWidgetItem("")).text()
            for c in range(len(FIELDS))
        ]
        QApplication.clipboard().setText("\t".join(values))

    # ── Export ───────────────────────────────────────────────────────────

    def _export_csv(self):
        if not self._contacts:
            self._status.setText("No contacts to export yet.")
            return
        default = str(get_output_dir() / "contacts_export.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", default, "CSV Files (*.csv)"
        )
        if path:
            export_contacts(self._contacts, path)
            self._status.setText(f"Exported {len(self._contacts)} contacts to {path}")
