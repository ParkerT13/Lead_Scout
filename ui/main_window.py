import csv
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QLineEdit, QPushButton, QLabel, QProgressBar, QFileDialog,
    QGroupBox, QHeaderView, QMenu, QMessageBox, QAbstractItemView,
    QApplication, QTabWidget, QCheckBox, QRadioButton, QButtonGroup,
    QDialog, QTextEdit, QScrollArea, QFormLayout, QSpinBox,
    QComboBox, QFrame, QSizePolicy, QDialogButtonBox, QPlainTextEdit,
    QStackedWidget,
)
from PySide6.QtCore import Qt, Slot, QUrl, QTimer, QObject, Signal
from PySide6.QtGui import QColor, QFont, QDesktopServices, QShortcut, QKeySequence, QAction, QIcon

from scraper.worker import SearchWorker
from emailer.worker import EmailWorker, DROP_STATUSES
from emailer.generator import generate_candidates
from emailer.domain_cache import all_entries as cache_all, get as cache_get, put as cache_put
from emailer.smtp_verifier import port25_available
from verifier.worker import VerifyWorker
from output.csv_writer import append_contact, export_contacts, get_output_dir
from output.basin_mapper import get_basin

logger = logging.getLogger(__name__)

# ── Column definitions ───────────────────────────────────────────────────────

COLUMNS = ["First Name", "Last Name", "Title", "Company", "Location",
           "Basin", "LinkedIn URL", "Emp. Status", "Source", "Date", "Priority"]
FIELDS  = ["first_name", "last_name", "title", "company", "location",
           "basin", "linkedin_url", "emp_status", "source", "date_pulled", "priority"]

_EMAIL_COLS   = ["First Name", "Last Name", "Title", "Company",
                 "Email", "Status", "Confidence", "Source", "LinkedIn URL"]
_EMAIL_FIELDS = ["first_name", "last_name", "title", "company",
                 "email", "email_status", "confidence", "email_source", "linkedin_url"]

# ── CRM field mapping presets ────────────────────────────────────────────────

_CRM_PRESETS = {
    "HubSpot": {
        "first_name":   "First Name",
        "last_name":    "Last Name",
        "title":        "Job Title",
        "company":      "Company Name",
        "email":        "Email",
        "location":     "City",
        "basin":        "Basin",
        "linkedin_url": "LinkedIn Profile URL",
        "date_pulled":  "Create Date",
        "confidence":   "Lead Confidence",
        "emp_status":   "Employment Status",
    },
    "Salesforce": {
        "first_name":   "FirstName",
        "last_name":    "LastName",
        "title":        "Title",
        "company":      "Company",
        "email":        "Email",
        "location":     "MailingCity",
        "basin":        "Basin__c",
        "linkedin_url": "LinkedIn__c",
        "date_pulled":  "LeadSource_Date__c",
        "confidence":   "Lead_Confidence__c",
        "emp_status":   "Employment_Status__c",
    },
    "Generic CSV": {
        "first_name":   "first_name",
        "last_name":    "last_name",
        "title":        "title",
        "company":      "company",
        "email":        "email",
        "location":     "location",
        "basin":        "basin",
        "linkedin_url": "linkedin_url",
        "date_pulled":  "date_pulled",
        "confidence":   "confidence",
        "emp_status":   "emp_status",
    },
}

_ALL_EXPORT_FIELDS = [
    ("first_name",   "First Name"),
    ("last_name",    "Last Name"),
    ("title",        "Title / Role"),
    ("company",      "Company"),
    ("email",        "Email"),
    ("confidence",   "Confidence"),
    ("email_status", "Email Status"),
    ("location",     "Location"),
    ("basin",        "Basin"),
    ("linkedin_url", "LinkedIn URL"),
    ("emp_status",   "Emp. Status"),
    ("date_pulled",  "Date Pulled"),
]

# ── Status / color constants ─────────────────────────────────────────────────

_EMP_COLORS = {
    "current": "#2E7D32",
    "stale":   "#C62828",
    "unknown": "#888888",
}

_EMAIL_COLORS = {
    "verified":              "#2E7D32",
    "catch-all-confirmed":   "#E65100",
    "catch-all-risky":       "#C62828",
    "unverified":            "#888888",
    "bounced":               "#C62828",
}

_QUEUE_STATUS = {
    "pending": ("⏳", "#888888"),
    "running": ("🔵", "#1565C0"),
    "done":    ("✅", "#2E7D32"),
    "failed":  ("❌", "#C62828"),
}

_SETTINGS_FILE = get_output_dir() / "settings.json"
_SESSION_FILE  = get_output_dir() / "session.json"

_DEFAULT_SETTINGS = {
    "dark_mode":        True,
    "output_dir":       str(get_output_dir()),
    "search_delay":     2,
    "default_format":   "HubSpot",
    "title_filter_enabled": {},   # keyword -> bool (True = checked)
    "title_filter_custom":  "",   # comma-separated extra keywords
    "title_exclude":        "intern, student, professor",
    "nb_api_key":       "",       # NeverBounce API key (used first while credits remain)
    "reoon_api_key":    "",       # Reoon API key (automatic fallback when NeverBounce credits gone)
}

# Comprehensive O&G title keyword groups — each keyword does substring matching
_TITLE_FILTER_GROUPS = {
    "Geoscience": [
        "geologist",
        "geophysicist",
        "geoscientist",
        "petrophysicist",
        "seismic",
        "geosteering",
        "stratigraphic",
        "structural geology",
        "formation evaluation",
        "rock physics",
        "geomodel",
        "earth scientist",
        "basin analyst",
        "microseismic",
        "interpretation",
        "subsurface analyst",
        "g&g",
    ],
    "Engineering": [
        "petroleum engineer",
        "reservoir engineer",
        "completions engineer",
        "completion engineer",
        "drilling engineer",
        "production engineer",
        "well engineer",
        "subsurface engineer",
        "facilities engineer",
        "stimulation engineer",
        "frac engineer",
        "waterflood engineer",
        "artificial lift engineer",
        "surveillance engineer",
        "simulation engineer",
        "operations engineer",
        "wellbore engineer",
        "field development",
        "engineering manager",
        "subsurface manager",
        "asset manager",
        "asset team lead",
    ],
    "Leadership & Technical": [
        "vp geoscience",
        "vp exploration",
        "vp engineering",
        "vp operations",
        "vp subsurface",
        "vp reservoir",
        "vice president",
        "chief geoscientist",
        "chief engineer",
        "chief geophysicist",
        "director of geoscience",
        "director of engineering",
        "director of exploration",
        "exploration director",
        "technical director",
        "head of geoscience",
        "head of engineering",
        "principal geoscientist",
        "principal geophysicist",
        "principal engineer",
        "staff geoscientist",
        "staff geophysicist",
        "staff engineer",
        "technical manager",
        "subsurface lead",
    ],
}

# Seniority tiers for contact priority scoring
_SENIORITY_TIERS = [
    (1, ["chief ", "ceo", "cto", "coo", "cfo", "c-suite", "president", "founder", "owner", "managing partner", "managing director"]),
    (2, ["executive vice president", "evp", "senior vice president", "svp", "vice president", " vp "]),
    (3, ["director", "head of", "principal"]),
    (4, ["manager", "team lead", "lead ", "senior ", "sr. ", "sr "]),
    (5, ["engineer", "analyst", "associate", "specialist", "coordinator", "consultant", "geologist", "geophysicist"]),
]


def _confidence(status: str) -> str:
    if status in ("verified",):
        return "High"
    if status in ("catch-all-confirmed", "pattern-confirmed", "emailformat", "hubspot"):
        return "Medium"
    if status in ("catch-all-risky", "best-guess", "unknown", "pattern-ddg"):
        return "Low"
    return ""


def _seniority(title: str) -> int:
    """Return seniority rank 1 (C-suite) to 5 (IC). 9 = unranked."""
    t = " " + title.lower() + " "
    for rank, keywords in _SENIORITY_TIERS:
        if any(k in t for k in keywords):
            return rank
    return 9


def _priority_label(title: str) -> str:
    r = _seniority(title)
    return {
        1: "1 - C-Suite / Exec",
        2: "2 - VP",
        3: "3 - Director",
        4: "4 - Manager / Lead",
        5: "5 - Individual",
    }.get(r, "")


_COMPLETENESS_FIELDS = [
    "first_name", "last_name", "title", "company",
    "location", "basin", "linkedin_url", "email",
]

def _completeness(contact: dict) -> int:
    """Return 0-100 completeness score for a contact."""
    filled = sum(1 for f in _COMPLETENESS_FIELDS if str(contact.get(f, "")).strip())
    return int(filled / len(_COMPLETENESS_FIELDS) * 100)


# ── Qt logging bridge ─────────────────────────────────────────────────────────

class _LogEmitter(QObject):
    log_record = Signal(str, str)   # (formatted_message, level_name)

class _QtLogHandler(logging.Handler):
    def __init__(self, emitter: _LogEmitter):
        super().__init__()
        self._emitter = emitter
        self.setFormatter(logging.Formatter("%(asctime)s  %(name)-20s  %(message)s",
                                            datefmt="%H:%M:%S"))
    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self._emitter.log_record.emit(msg, record.levelname)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Main window
# ═══════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lead Scout — O&G Sales Intelligence")
        self.setMinimumSize(1350, 820)

        # App icon
        _icon_path = Path(__file__).parent.parent / "assets" / "logo.ico"
        if _icon_path.exists():
            self.setWindowIcon(QIcon(str(_icon_path)))

        # ── Core state ──────────────────────────────────────────────────────
        self._contacts:       list[dict] = []
        self._company_status: dict[str, str] = {}
        self._seen_urls:      set[str] = set()          # in-session + history dedup
        self._worker:         SearchWorker | None = None
        self._verify_worker:  VerifyWorker | None = None
        self._pipeline_mode:  bool = False
        self._port25_ok:      bool = True

        self._email_contacts: list[dict] = []
        self._email_worker:   EmailWorker | None = None
        self._email_enriched: list[dict] = []
        self._email_company_progress: dict[str, tuple[int,int]] = {}

        self._crm_contacts:   list[dict] = []
        self._settings:       dict = {}

        # ── Logging bridge ───────────────────────────────────────────────────
        self._log_emitter = _LogEmitter()
        self._log_handler = _QtLogHandler(self._log_emitter)
        logging.getLogger().addHandler(self._log_handler)
        logging.getLogger().setLevel(logging.DEBUG)

        self._load_settings()
        self._load_history_urls()       # cross-session dup detection
        self._build_ui()
        self._apply_style()
        self._register_shortcuts()
        self._check_linkedin_session()  # set indicator state

        # Wire log signal after UI is built
        self._log_emitter.log_record.connect(self._append_log)

        first_run = not _SETTINGS_FILE.exists()
        self._session_offer_restore()
        if first_run:
            QTimer.singleShot(400, self._show_onboarding)

    # ── Settings ────────────────────────────────────────────────────────────

    def _load_settings(self):
        try:
            if _SETTINGS_FILE.exists():
                self._settings = json.loads(_SETTINGS_FILE.read_text())
        except Exception:
            pass
        for k, v in _DEFAULT_SETTINGS.items():
            self._settings.setdefault(k, v)
        # credentials.json in app folder — shared by Parker, overrides per-machine settings
        # for API keys so colleagues don't need their own accounts
        _creds_path = next(
            (p for p in (
                Path(__file__).parent.parent / "credentials.json",
                Path(__file__).parent.parent / "credentials.json.example",
            ) if p.exists()),
            None,
        )
        if _creds_path:
            try:
                creds = json.loads(_creds_path.read_text(encoding="utf-8"))
                for key in ("nb_api_key", "reoon_api_key"):
                    if creds.get(key):
                        self._settings[key] = creds[key]
            except Exception:
                pass
        # Migrate old title_include plain text → new structure (one-time)
        if "title_include" in self._settings:
            del self._settings["title_include"]

    def _save_settings(self):
        try:
            _SETTINGS_FILE.write_text(json.dumps(self._settings, indent=2))
        except Exception:
            pass

    def _load_history_urls(self):
        """Pre-populate seen_urls from all_contacts.csv so we skip historical dupes."""
        master = get_output_dir() / "all_contacts.csv"
        if not master.exists():
            return
        try:
            with open(master, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    url = row.get("linkedin_url", "").strip()
                    if url:
                        self._seen_urls.add(url)
            logger.info("Loaded %d historical LinkedIn URLs for dup detection", len(self._seen_urls))
        except Exception as e:
            logger.warning("Could not load history URLs: %s", e)

    # ── UI Construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        root = QVBoxLayout(root_widget)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_puller_tab(),  "1 · Lead Scout")
        self._tabs.addTab(self._build_email_tab(),   "2 · Email Enricher")
        self._tabs.addTab(self._build_export_tab(),  "3 · CRM Export")
        root.addWidget(self._tabs)

        # ── Logging panel (collapsible) ──────────────────────────────────────
        self._log_panel = QPlainTextEdit()
        self._log_panel.setReadOnly(True)
        self._log_panel.setMaximumHeight(160)
        self._log_panel.setMinimumHeight(160)
        self._log_panel.setVisible(False)
        self._log_panel.setObjectName("log_panel")
        root.addWidget(self._log_panel)

        self._build_menu_bar()

        # Persistent status bar with log toggle
        sb = self.statusBar()
        sb.showMessage("Ready.")
        sb.setStyleSheet("QStatusBar { font-size: 12px; }")
        self._btn_log_toggle = QPushButton("Show Logs")
        self._btn_log_toggle.setMaximumHeight(20)
        self._btn_log_toggle.setFlat(True)
        self._btn_log_toggle.setStyleSheet("font-size: 10px; color: #888;")
        self._btn_log_toggle.clicked.connect(self._toggle_log_panel)
        sb.addPermanentWidget(self._btn_log_toggle)

    def _build_menu_bar(self):
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("File")
        act_settings = QAction("Settings…", self)
        act_settings.triggered.connect(self._show_settings_dialog)
        file_menu.addAction(act_settings)

        self._act_dark = QAction("Dark Mode", self, checkable=True)
        self._act_dark.setChecked(self._settings.get("dark_mode", True))
        self._act_dark.triggered.connect(self._toggle_dark_mode)
        file_menu.addAction(self._act_dark)

        file_menu.addSeparator()
        act_import = QAction("Import Previous Session…", self)
        act_import.triggered.connect(self._session_import)
        file_menu.addAction(act_import)

        act_export = QAction("Export CSV\tCtrl+S", self)
        act_export.triggered.connect(self._export_csv)
        file_menu.addAction(act_export)

        file_menu.addSeparator()
        act_folder = QAction("Open Output Folder", self)
        act_folder.triggered.connect(self._open_output_folder)
        file_menu.addAction(act_folder)

        # Tools menu
        tools_menu = menu_bar.addMenu("Tools")
        act_cache = QAction("Domain Cache Editor…", self)
        act_cache.triggered.connect(self._show_domain_cache_editor)
        tools_menu.addAction(act_cache)

        tools_menu.addSeparator()
        act_check = QAction("Check Domains (CLI)…", self)
        act_check.triggered.connect(lambda: QMessageBox.information(
            self, "Check Domains",
            "Run Check Domains.bat from the project folder\n"
            "to manually review and correct domain/pattern assignments."
        ))
        tools_menu.addAction(act_check)

    # ── Lead Scout tab ───────────────────────────────────────────────────

    def _build_puller_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)
        root.addLayout(self._build_toolbar())
        root.addLayout(self._build_filter_row())
        root.addLayout(self._build_search_bar())
        root.addWidget(self._build_splitter(), stretch=1)
        root.addLayout(self._build_bottom_bar())
        return tab

    def _build_search_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        lbl = QLabel("Search results:")
        lbl.setStyleSheet("font-size: 11px; color: #aaa;")
        row.addWidget(lbl)

        self._search_bar = QLineEdit()
        self._search_bar.setPlaceholderText("Filter by name, title, company, location…")
        self._search_bar.setMaximumHeight(28)
        self._search_bar.textChanged.connect(self._apply_table_filter)
        row.addWidget(self._search_bar, stretch=2)

        self._search_company = QComboBox()
        self._search_company.setMaximumHeight(28)
        self._search_company.addItem("All Companies")
        self._search_company.currentTextChanged.connect(self._apply_table_filter)
        row.addWidget(self._search_company)

        btn_clear = QPushButton("Clear")
        btn_clear.setMaximumHeight(28)
        btn_clear.setMaximumWidth(60)
        btn_clear.clicked.connect(lambda: (self._search_bar.clear(),
                                           self._search_company.setCurrentIndex(0)))
        row.addWidget(btn_clear)

        self._completeness_label = QLabel("")
        self._completeness_label.setStyleSheet("color: #888; font-size: 11px;")
        row.addWidget(self._completeness_label)

        return row

    def _apply_table_filter(self):
        text    = self._search_bar.text().lower().strip()
        company = self._search_company.currentText()
        show_all_companies = (company == "All Companies")

        for row in range(self._table.rowCount()):
            match_text = True
            match_co   = True
            if text:
                row_text = " ".join(
                    (self._table.item(row, c) or QTableWidgetItem("")).text()
                    for c in range(self._table.columnCount())
                ).lower()
                match_text = text in row_text
            if not show_all_companies:
                co_item = self._table.item(row, FIELDS.index("company"))
                match_co = co_item and co_item.text() == company
            self._table.setRowHidden(row, not (match_text and match_co))

    def _update_completeness_label(self):
        if not self._contacts:
            self._completeness_label.setText("")
            return
        avg = sum(_completeness(c) for c in self._contacts) // len(self._contacts)
        color = "#4ADE80" if avg >= 80 else "#FBBF24" if avg >= 50 else "#F87171"
        self._completeness_label.setText(f"Avg completeness: <b style='color:{color}'>{avg}%</b>")
        self._completeness_label.setTextFormat(Qt.RichText)

    def _build_filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        lbl = QLabel("Title Filter:")
        lbl.setStyleSheet("font-weight: bold;")
        row.addWidget(lbl)

        self._filter_summary = QLabel()
        self._filter_summary.setStyleSheet("color: #aaa; font-size: 11px;")
        self._filter_summary.setWordWrap(False)
        self._update_filter_summary()
        row.addWidget(self._filter_summary, stretch=1)

        btn_configure = QPushButton("Configure Titles…")
        btn_configure.setMaximumHeight(30)
        btn_configure.clicked.connect(self._show_title_filter_dialog)
        row.addWidget(btn_configure)

        btn_folder = QPushButton("Open Output Folder")
        btn_folder.setMaximumHeight(30)
        btn_folder.clicked.connect(self._open_output_folder)
        row.addWidget(btn_folder)

        return row

    def _get_active_include_keywords(self) -> list[str]:
        """Return list of active include keywords (checked defaults + custom)."""
        enabled = self._settings.get("title_filter_enabled", {})
        keywords = []
        for group_keywords in _TITLE_FILTER_GROUPS.values():
            for kw in group_keywords:
                # Default to True if not explicitly set
                if enabled.get(kw, True):
                    keywords.append(kw)
        custom = self._settings.get("title_filter_custom", "")
        if custom:
            keywords += [t.strip().lower() for t in custom.split(",") if t.strip()]
        return keywords

    def _update_filter_summary(self):
        keywords = self._get_active_include_keywords()
        exclude  = self._settings.get("title_exclude", "")
        if not keywords:
            self._filter_summary.setText("No filter active — pulling all titles")
            return
        # Show count + sample
        sample = ", ".join(keywords[:6])
        suffix = f" +{len(keywords)-6} more" if len(keywords) > 6 else ""
        exc_note = f"  |  excludes: {exclude}" if exclude else ""
        self._filter_summary.setText(f"{len(keywords)} keywords active: {sample}{suffix}{exc_note}")

    def _show_title_filter_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Configure Title Filter")
        dlg.setMinimumSize(620, 580)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        info = QLabel(
            "Check the title keywords to include. Contacts whose title contains "
            "ANY checked keyword will be kept. Uncheck all to pull every title."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(10)

        enabled = self._settings.get("title_filter_enabled", {})
        self._title_checkboxes: dict[str, QCheckBox] = {}

        for group_name, keywords in _TITLE_FILTER_GROUPS.items():
            grp_box = QGroupBox(group_name)
            grp_layout = QVBoxLayout(grp_box)
            grp_layout.setSpacing(3)

            # Select All / None row
            btn_row = QHBoxLayout()
            btn_all  = QPushButton("All")
            btn_none = QPushButton("None")
            btn_all.setMaximumWidth(50)
            btn_none.setMaximumWidth(50)
            btn_all.setMaximumHeight(22)
            btn_none.setMaximumHeight(22)
            btn_row.addWidget(btn_all)
            btn_row.addWidget(btn_none)
            btn_row.addStretch()
            grp_layout.addLayout(btn_row)

            group_cbs = []
            for kw in keywords:
                cb = QCheckBox(kw)
                cb.setChecked(enabled.get(kw, True))
                self._title_checkboxes[kw] = cb
                grp_layout.addWidget(cb)
                group_cbs.append(cb)

            # Wire All/None buttons for this group
            btn_all.clicked.connect(lambda _, cbs=group_cbs: [c.setChecked(True) for c in cbs])
            btn_none.clicked.connect(lambda _, cbs=group_cbs: [c.setChecked(False) for c in cbs])

            inner_layout.addWidget(grp_box)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        # Custom keywords
        custom_box = QGroupBox("Custom Keywords (comma-separated)")
        custom_layout = QVBoxLayout(custom_box)
        custom_edit = QLineEdit()
        custom_edit.setPlaceholderText("e.g. landman, data scientist, technical advisor")
        custom_edit.setText(self._settings.get("title_filter_custom", ""))
        custom_layout.addWidget(custom_edit)
        layout.addWidget(custom_box)

        # Exclude keywords
        excl_box = QGroupBox("Exclude Keywords (comma-separated)")
        excl_layout = QVBoxLayout(excl_box)
        excl_edit = QLineEdit()
        excl_edit.setPlaceholderText("e.g. intern, student, professor")
        excl_edit.setText(self._settings.get("title_exclude", ""))
        excl_layout.addWidget(excl_edit)
        layout.addWidget(excl_box)

        # Global Select All / None
        global_row = QHBoxLayout()
        btn_global_all  = QPushButton("Select All Defaults")
        btn_global_none = QPushButton("Deselect All Defaults")
        btn_global_all.clicked.connect(lambda: [cb.setChecked(True) for cb in self._title_checkboxes.values()])
        btn_global_none.clicked.connect(lambda: [cb.setChecked(False) for cb in self._title_checkboxes.values()])
        global_row.addWidget(btn_global_all)
        global_row.addWidget(btn_global_none)
        global_row.addStretch()
        layout.addLayout(global_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() != QDialog.Accepted:
            return

        # Save state
        self._settings["title_filter_enabled"] = {
            kw: cb.isChecked() for kw, cb in self._title_checkboxes.items()
        }
        self._settings["title_filter_custom"] = custom_edit.text().strip()
        self._settings["title_exclude"]        = excl_edit.text().strip()
        self._save_settings()
        self._update_filter_summary()

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
            ("Paste List",     self._paste_companies),
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
            "Check each contact's LinkedIn profile to confirm they still work at the target company."
        )
        self._btn_verify.clicked.connect(self._toggle_verify)
        row.addWidget(self._btn_verify)

        self._btn_pipeline = QPushButton("Run Full Pipeline")
        self._btn_pipeline.setObjectName("btn_pipeline")
        self._btn_pipeline.setMinimumHeight(38)
        self._btn_pipeline.setToolTip(
            "Pull contacts → detect email patterns → generate and verify all emails → export viable contacts."
        )
        self._btn_pipeline.clicked.connect(self._pipeline_start)
        row.addWidget(self._btn_pipeline)

        btn_export = QPushButton("Export CSV")
        btn_export.setMinimumHeight(38)
        btn_export.clicked.connect(self._export_csv)
        row.addWidget(btn_export)

        # LinkedIn session health indicator
        row.addSpacing(12)
        self._li_status_label = QLabel()
        self._li_status_label.setStyleSheet("font-size: 11px;")
        self._li_status_label.setToolTip(
            "LinkedIn browser session status.\n"
            "Active = Playwright session found (employment verify will work).\n"
            "Login Required = run 'Verify Employment' once to log in."
        )
        row.addWidget(self._li_status_label)

        self._li_login_btn = QPushButton("LinkedIn Login")
        self._li_login_btn.setMaximumHeight(30)
        self._li_login_btn.setToolTip("Open browser to log into LinkedIn and save the session.")
        self._li_login_btn.clicked.connect(self._linkedin_relogin)
        row.addWidget(self._li_login_btn)

        return row

    def _build_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Horizontal)

        # Left: company queue
        left_box = QGroupBox("Company Queue")
        left_layout = QVBoxLayout(left_box)
        left_layout.setSpacing(6)

        self._queue = QListWidget()
        self._queue.setMinimumWidth(240)
        self._queue.setSpacing(2)
        self._queue.setContextMenuPolicy(Qt.CustomContextMenu)
        self._queue.customContextMenuRequested.connect(self._queue_context_menu)
        left_layout.addWidget(self._queue)

        btn_clear = QPushButton("Clear Pending")
        btn_clear.clicked.connect(self._clear_pending)
        left_layout.addWidget(btn_clear)

        splitter.addWidget(left_box)

        # Right: results table
        right_box = QGroupBox("Results")
        right_layout = QVBoxLayout(right_box)
        right_layout.setSpacing(6)

        hdr = QHBoxLayout()
        self._count_label = QLabel("0 contacts found")
        font = QFont(); font.setBold(True)
        self._count_label.setFont(font)
        hdr.addWidget(self._count_label)
        self._dup_label = QLabel("")
        self._dup_label.setStyleSheet("color: #E65100; font-size: 11px;")
        hdr.addWidget(self._dup_label)
        hdr.addStretch()
        right_layout.addLayout(hdr)

        self._table = QTableWidget()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(FIELDS.index("linkedin_url"), QHeaderView.Interactive)
        self._table.setColumnWidth(FIELDS.index("linkedin_url"), 280)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.cellDoubleClicked.connect(self._cell_double_clicked)
        right_layout.addWidget(self._table)

        splitter.addWidget(right_box)
        splitter.setSizes([265, 1085])
        return splitter

    def _build_bottom_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setMaximumHeight(16)
        row.addWidget(self._progress, stretch=1)
        self._puller_status = QLabel("Ready.")
        row.addWidget(self._puller_status, stretch=5)
        return row

    # ── Email Enricher tab ───────────────────────────────────────────────────

    def _build_email_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        top = QHBoxLayout()
        for text, slot in [
            ("Use Contacts from Puller", self._email_load_from_puller),
            ("Load from CSV",            self._email_load_from_csv),
        ]:
            btn = QPushButton(text)
            btn.setMinimumHeight(36)
            btn.clicked.connect(slot)
            top.addWidget(btn)

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

        self._email_btn_retry = QPushButton("Retry Failed")
        self._email_btn_retry.setObjectName("btn_retry")
        self._email_btn_retry.setMinimumHeight(36)
        self._email_btn_retry.setToolTip("Re-run enrichment on contacts where email lookup failed.")
        self._email_btn_retry.setEnabled(False)
        self._email_btn_retry.clicked.connect(self._email_retry_failed)
        top.addWidget(self._email_btn_retry)

        btn_export = QPushButton("Export Enriched CSV")
        btn_export.setMinimumHeight(36)
        btn_export.clicked.connect(self._email_export)
        top.addWidget(btn_export)

        btn_mv_export = QPushButton("Export for Verification…")
        btn_mv_export.setMinimumHeight(36)
        btn_mv_export.setToolTip(
            "Export email list in MillionVerifier / NeverBounce format.\n"
            "Upload to either service, then import results back."
        )
        btn_mv_export.clicked.connect(self._mv_export)
        top.addWidget(btn_mv_export)

        btn_mv_import = QPushButton("Import Verification Results…")
        btn_mv_import.setMinimumHeight(36)
        btn_mv_import.setToolTip(
            "Import results from MillionVerifier or NeverBounce.\n"
            "Updates email_status for each contact."
        )
        btn_mv_import.clicked.connect(self._mv_import)
        top.addWidget(btn_mv_import)

        btn_bounce = QPushButton("Import Bounces…")
        btn_bounce.setMinimumHeight(36)
        btn_bounce.setToolTip(
            "Import a bounce list from your ESP (Mailchimp, Outreach, etc.).\n"
            "Marks those emails in the bounce tracker so they are skipped in future runs."
        )
        btn_bounce.clicked.connect(self._import_bounces_dialog)
        top.addWidget(btn_bounce)

        root.addLayout(top)

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

        self._email_table = QTableWidget()
        self._email_table.setColumnCount(len(_EMAIL_COLS))
        self._email_table.setHorizontalHeaderLabels(_EMAIL_COLS)
        hh2 = self._email_table.horizontalHeader()
        hh2.setSectionResizeMode(QHeaderView.Stretch)
        hh2.setSectionResizeMode(_EMAIL_FIELDS.index("linkedin_url"), QHeaderView.Interactive)
        self._email_table.setColumnWidth(_EMAIL_FIELDS.index("linkedin_url"), 250)
        self._email_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._email_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._email_table.setAlternatingRowColors(True)
        self._email_table.setSortingEnabled(True)
        self._email_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._email_table.customContextMenuRequested.connect(self._email_context_menu)
        self._email_table.selectionModel().selectionChanged.connect(self._on_email_row_selected)
        right_layout.addWidget(self._email_table)

        # Email preview panel
        preview_box = QGroupBox("Email Candidates Preview")
        preview_box.setFixedHeight(160)
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setSpacing(4)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        self._email_preview_label = QLabel("Select a contact to see all email format candidates.")
        self._email_preview_label.setStyleSheet("color: #888; font-size: 11px;")
        self._email_preview_label.setWordWrap(True)
        preview_layout.addWidget(self._email_preview_label)
        self._email_preview_table = QTableWidget()
        self._email_preview_table.setColumnCount(3)
        self._email_preview_table.setHorizontalHeaderLabels(["Format", "Email Candidate", "Status"])
        self._email_preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._email_preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._email_preview_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._email_preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._email_preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._email_preview_table.setMaximumHeight(140)
        self._email_preview_table.setVisible(False)
        self._email_preview_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._email_preview_table.customContextMenuRequested.connect(self._preview_context_menu)
        preview_layout.addWidget(self._email_preview_table)
        right_layout.addWidget(preview_box)

        splitter.addWidget(right_box)
        splitter.setSizes([265, 1085])
        root.addWidget(splitter, stretch=1)

        # Port 25 warning banner (hidden until blocked)
        self._port25_banner = QLabel(
            "  PORT 25 BLOCKED — SMTP verification is unavailable on this network. "
            "Emails will still be generated using pattern detection, but cannot be SMTP-confirmed. "
            "Run 'Generate Emails.bat' to export contacts, then verify externally via MillionVerifier or NeverBounce (both free tiers available)."
        )
        self._port25_banner.setStyleSheet(
            "background: #7F1D1D; color: #FCA5A5; padding: 6px 10px; "
            "font-size: 11px; border-radius: 4px;"
        )
        self._port25_banner.setWordWrap(True)
        self._port25_banner.setVisible(False)
        root.addWidget(self._port25_banner)

        bottom = QHBoxLayout()
        self._email_progress = QProgressBar()
        self._email_progress.setVisible(False)
        self._email_progress.setMaximumHeight(16)
        bottom.addWidget(self._email_progress, stretch=1)
        self._email_status = QLabel("Load contacts to begin.")
        bottom.addWidget(self._email_status, stretch=5)
        root.addLayout(bottom)

        return tab

    # ── CRM Export tab ───────────────────────────────────────────────────────

    def _build_export_tab(self) -> QWidget:
        tab = QWidget()
        outer = QHBoxLayout(tab)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(12)

        # ── Left: options panel (scrollable) ────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(380)
        scroll.setFrameShape(QFrame.NoFrame)

        opts = QWidget()
        opts_layout = QVBoxLayout(opts)
        opts_layout.setSpacing(12)
        opts_layout.setContentsMargins(4, 4, 4, 4)

        # Campaign name
        camp_box = QGroupBox("Campaign")
        camp_layout = QFormLayout(camp_box)
        self._crm_campaign = QLineEdit()
        self._crm_campaign.setPlaceholderText("e.g. Q3 Permian Outreach")
        camp_layout.addRow("Name:", self._crm_campaign)
        opts_layout.addWidget(camp_box)

        # Source
        src_box = QGroupBox("Source")
        src_layout = QVBoxLayout(src_box)
        self._crm_src_enricher = QRadioButton("From Email Enricher (current session)")
        self._crm_src_enricher.setChecked(True)
        self._crm_src_csv = QRadioButton("Load from CSV file…")
        self._crm_src_enricher.toggled.connect(self._crm_update_preview)
        self._crm_src_csv.toggled.connect(self._crm_maybe_load_csv)
        src_layout.addWidget(self._crm_src_enricher)
        src_layout.addWidget(self._crm_src_csv)
        self._crm_src_label = QLabel("0 contacts in session")
        self._crm_src_label.setStyleSheet("color: #666; font-size: 11px; padding-left: 4px;")
        src_layout.addWidget(self._crm_src_label)
        opts_layout.addWidget(src_box)

        # CRM format preset
        fmt_box = QGroupBox("CRM Format")
        fmt_layout = QVBoxLayout(fmt_box)
        self._crm_format_group = QButtonGroup()
        for i, preset in enumerate(_CRM_PRESETS):
            rb = QRadioButton(preset)
            if preset == self._settings.get("default_format", "HubSpot"):
                rb.setChecked(True)
            rb.toggled.connect(self._crm_update_preview)
            self._crm_format_group.addButton(rb, i)
            fmt_layout.addWidget(rb)
        opts_layout.addWidget(fmt_box)

        # Column selector
        col_box = QGroupBox("Columns to Export")
        col_layout = QVBoxLayout(col_box)
        self._crm_col_checks: dict[str, QCheckBox] = {}
        default_on = {"first_name", "last_name", "title", "company", "email",
                      "confidence", "location", "basin", "linkedin_url"}
        for field, label in _ALL_EXPORT_FIELDS:
            cb = QCheckBox(label)
            cb.setChecked(field in default_on)
            cb.toggled.connect(self._crm_update_preview)
            self._crm_col_checks[field] = cb
            col_layout.addWidget(cb)
        opts_layout.addWidget(col_box)

        # Status filters
        filter_box = QGroupBox("Include Email Status")
        filter_layout = QVBoxLayout(filter_box)
        self._crm_status_checks: dict[str, QCheckBox] = {}
        statuses = [
            ("verified",            "Verified (SMTP confirmed)",    True),
            ("catch-all-confirmed", "Catch-all confirmed",          True),
            ("catch-all-risky",     "Catch-all risky",              False),
            ("unknown",             "Unknown (port 25 blocked)",    False),
            ("best-guess",          "Best-guess (no SMTP)",         True),
            ("pattern-confirmed",   "Pattern confirmed (scraped)",  True),
            ("pattern-ddg",         "Pattern via DDG",              True),
            ("_drop",               "Show dropped (no domain/MX/error)", False),
        ]
        for key, label, default in statuses:
            cb = QCheckBox(label)
            cb.setChecked(default)
            cb.toggled.connect(self._crm_update_preview)
            self._crm_status_checks[key] = cb
            filter_layout.addWidget(cb)
        opts_layout.addWidget(filter_box)

        # Options
        opt_box = QGroupBox("Options")
        opt_layout = QVBoxLayout(opt_box)
        self._crm_dedup = QCheckBox("Remove duplicate emails")
        self._crm_dedup.setChecked(True)
        self._crm_dedup.toggled.connect(self._crm_update_preview)
        self._crm_require_email = QCheckBox("Exclude contacts without email")
        self._crm_require_email.setChecked(True)
        self._crm_require_email.toggled.connect(self._crm_update_preview)
        opt_layout.addWidget(self._crm_dedup)
        opt_layout.addWidget(self._crm_require_email)
        opts_layout.addWidget(opt_box)

        opts_layout.addStretch()
        scroll.setWidget(opts)
        outer.addWidget(scroll)

        # ── Right: preview + export ──────────────────────────────────────────
        right = QVBoxLayout()

        preview_box = QGroupBox("Export Preview")
        preview_layout = QVBoxLayout(preview_box)

        self._crm_preview_label = QLabel("0 contacts will be exported")
        font = QFont(); font.setBold(True); font.setPointSize(13)
        self._crm_preview_label.setFont(font)
        preview_layout.addWidget(self._crm_preview_label)

        self._crm_preview_table = QTableWidget()
        self._crm_preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._crm_preview_table.setAlternatingRowColors(True)
        self._crm_preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        preview_layout.addWidget(self._crm_preview_table)

        right.addWidget(preview_box, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._crm_refresh_btn = QPushButton("Refresh Preview")
        self._crm_refresh_btn.setMinimumHeight(38)
        self._crm_refresh_btn.clicked.connect(self._crm_refresh_preview)
        btn_row.addWidget(self._crm_refresh_btn)

        self._crm_export_btn = QPushButton("Export to CRM CSV")
        self._crm_export_btn.setObjectName("btn_start")
        self._crm_export_btn.setMinimumHeight(38)
        self._crm_export_btn.clicked.connect(self._crm_export)
        btn_row.addWidget(self._crm_export_btn)

        right.addLayout(btn_row)

        self._crm_status_label = QLabel("")
        self._crm_status_label.setStyleSheet("color: #555; font-size: 11px;")
        right.addWidget(self._crm_status_label)

        outer.addLayout(right, stretch=1)
        return tab

    # ── CRM Export logic ─────────────────────────────────────────────────────

    def _crm_maybe_load_csv(self, checked: bool):
        if not checked:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Contacts CSV", str(get_output_dir()), "CSV Files (*.csv)"
        )
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
                self._crm_contacts = [{k.lower().replace(" ", "_"): v for k, v in r.items()} for r in rows]
                self._crm_src_label.setText(f"{len(self._crm_contacts)} contacts from {Path(path).name}")
                self._crm_update_preview()
            except Exception as e:
                QMessageBox.warning(self, "Load Error", str(e))
                self._crm_src_enricher.setChecked(True)
        else:
            self._crm_src_enricher.setChecked(True)

    def _crm_source_contacts(self) -> list[dict]:
        if self._crm_src_enricher.isChecked():
            base = self._email_enriched if self._email_enriched else self._email_contacts
            self._crm_src_label.setText(f"{len(base)} contacts in session")
            return base
        return self._crm_contacts

    def _crm_filtered_contacts(self) -> list[dict]:
        contacts = self._crm_source_contacts()
        allowed_statuses = {k for k, cb in self._crm_status_checks.items() if cb.isChecked()}
        show_dropped = "_drop" in allowed_statuses
        allowed_statuses.discard("_drop")

        result = []
        seen_emails: set[str] = set()
        for c in contacts:
            status = c.get("email_status", "")
            email  = c.get("email", "").strip().lower()

            # Status filter
            if status in DROP_STATUSES and not show_dropped:
                continue
            if status not in DROP_STATUSES and status not in allowed_statuses:
                continue

            # Require email
            if self._crm_require_email.isChecked() and not email:
                continue

            # Dedup by email
            if self._crm_dedup.isChecked() and email:
                if email in seen_emails:
                    continue
                seen_emails.add(email)

            result.append(c)
        return result

    def _crm_active_fields(self) -> list[str]:
        return [f for f, cb in self._crm_col_checks.items() if cb.isChecked()]

    def _crm_active_preset(self) -> str:
        for btn in self._crm_format_group.buttons():
            if btn.isChecked():
                return btn.text()
        return "Generic CSV"

    def _crm_update_preview(self):
        # Debounce — schedule a refresh in 200ms to avoid hammering on rapid changes
        if hasattr(self, "_crm_preview_timer"):
            self._crm_preview_timer.stop()
        self._crm_preview_timer = QTimer(self)
        self._crm_preview_timer.setSingleShot(True)
        self._crm_preview_timer.timeout.connect(self._crm_refresh_preview)
        self._crm_preview_timer.start(200)

    def _crm_refresh_preview(self):
        contacts = self._crm_filtered_contacts()
        fields   = self._crm_active_fields()
        preset   = self._crm_active_preset()
        mapping  = _CRM_PRESETS.get(preset, _CRM_PRESETS["Generic CSV"])
        headers  = [mapping.get(f, f) for f in fields]

        self._crm_preview_label.setText(f"{len(contacts)} contacts will be exported")

        # Show up to 50 rows in preview
        preview = contacts[:50]
        self._crm_preview_table.setRowCount(len(preview))
        self._crm_preview_table.setColumnCount(len(headers))
        self._crm_preview_table.setHorizontalHeaderLabels(headers)
        self._crm_preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        for row, c in enumerate(preview):
            for col, field in enumerate(fields):
                val = c.get(field, "") or ""
                if field == "confidence" and not val:
                    val = _confidence(c.get("email_status", ""))
                self._crm_preview_table.setItem(row, col, QTableWidgetItem(str(val)))

    def _crm_export(self):
        contacts = self._crm_filtered_contacts()
        if not contacts:
            QMessageBox.warning(self, "Nothing to Export", "No contacts match the current filters.")
            return

        fields   = self._crm_active_fields()
        preset   = self._crm_active_preset()
        mapping  = _CRM_PRESETS.get(preset, _CRM_PRESETS["Generic CSV"])
        campaign = self._crm_campaign.text().strip()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_campaign = campaign.replace(" ", "_").replace("/", "-") if campaign else "export"
        default_name = f"crm_{safe_campaign}_{ts}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CRM CSV", str(get_output_dir() / default_name), "CSV Files (*.csv)"
        )
        if not path:
            return

        headers = [mapping.get(f, f) for f in fields]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()
                for c in contacts:
                    row = {}
                    for field, header in zip(fields, headers):
                        val = c.get(field, "") or ""
                        if field == "confidence" and not val:
                            val = _confidence(c.get("email_status", ""))
                        row[header] = val
                    if campaign:
                        row["Campaign"] = campaign
                    writer.writerow(row)

            msg = f"Exported {len(contacts)} contacts to {Path(path).name}"
            self._crm_status_label.setText(msg)
            self.statusBar().showMessage(msg)
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    # ── Style ────────────────────────────────────────────────────────────────

    def _apply_style(self):
        dark = self._settings.get("dark_mode", False)
        if dark:
            self._apply_dark_style()
        else:
            self._apply_light_style()

    def _apply_light_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget          { background: #f4f4f4; color: #111; }
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
                background: #388E3C; color: white;
                font-weight: bold; border: none;
            }
            QPushButton#btn_start:hover   { background: #2E7D32; }
            QPushButton#btn_start:disabled { background: #aaa; color: #ddd; }
            QPushButton#btn_stop {
                background: #D32F2F; color: white;
                font-weight: bold; border: none;
            }
            QPushButton#btn_stop:hover    { background: #C62828; }
            QPushButton#btn_stop:disabled { background: #bbb; color: #888; border: none; }
            QPushButton#btn_verify {
                background: #1565C0; color: white;
                font-weight: bold; border: none;
            }
            QPushButton#btn_verify:hover   { background: #0D47A1; }
            QPushButton#btn_verify:disabled { background: #aaa; color: #ddd; border: none; }
            QPushButton#btn_pipeline {
                background: #6A1B9A; color: white;
                font-weight: bold; border: none;
            }
            QPushButton#btn_pipeline:hover   { background: #4A148C; }
            QPushButton#btn_pipeline:disabled { background: #aaa; color: #ddd; border: none; }
            QPushButton#btn_retry {
                background: #E65100; color: white;
                font-weight: bold; border: none;
            }
            QPushButton#btn_retry:hover   { background: #BF360C; }
            QPushButton#btn_retry:disabled { background: #bbb; color: #888; border: none; }
            QTableWidget {
                gridline-color: #ddd;
                selection-background-color: #F97316;
                selection-color: #fff;
            }
            QHeaderView::section {
                background: #ececec; font-weight: bold;
                padding: 5px; border: none;
                border-right: 1px solid #ddd;
            }
            QListWidget::item         { padding: 5px 4px; }
            QListWidget::item:selected { background: #F97316; color: #fff; }
            QScrollArea { border: none; }
        """)

    def _apply_dark_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget, QDialog { background: #1e1e1e; color: #e0e0e0; }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #444;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 12px;
                color: #e0e0e0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px; padding: 0 4px;
            }
            QPushButton {
                border-radius: 4px; padding: 4px 14px;
                background: #333; border: 1px solid #555; color: #e0e0e0;
            }
            QPushButton:hover   { background: #444; }
            QPushButton:pressed { background: #555; }
            QPushButton#btn_start {
                background: #2E7D32; color: white;
                font-weight: bold; border: none;
            }
            QPushButton#btn_start:hover   { background: #388E3C; }
            QPushButton#btn_start:disabled { background: #444; color: #777; }
            QPushButton#btn_stop {
                background: #C62828; color: white;
                font-weight: bold; border: none;
            }
            QPushButton#btn_stop:hover    { background: #D32F2F; }
            QPushButton#btn_stop:disabled { background: #444; color: #777; border: none; }
            QPushButton#btn_verify {
                background: #0D47A1; color: white;
                font-weight: bold; border: none;
            }
            QPushButton#btn_verify:hover   { background: #1565C0; }
            QPushButton#btn_verify:disabled { background: #444; color: #777; border: none; }
            QPushButton#btn_pipeline {
                background: #4A148C; color: white;
                font-weight: bold; border: none;
            }
            QPushButton#btn_pipeline:hover   { background: #6A1B9A; }
            QPushButton#btn_pipeline:disabled { background: #444; color: #777; border: none; }
            QPushButton#btn_retry {
                background: #BF360C; color: white;
                font-weight: bold; border: none;
            }
            QPushButton#btn_retry:hover   { background: #E65100; }
            QPushButton#btn_retry:disabled { background: #444; color: #777; border: none; }
            QTableWidget {
                background: #1a2a3a; gridline-color: #2a3f52;
                alternate-background-color: #1e3348;
                selection-background-color: #F97316;
                selection-color: #fff; color: #e0e0e0;
            }
            QTableWidget QTableCornerButton::section { background: #333; }
            QHeaderView::section {
                background: #2d2d2d; font-weight: bold; color: #ccc;
                padding: 5px; border: none;
                border-right: 1px solid #444;
            }
            QListWidget {
                background: #252526; color: #e0e0e0; border: 1px solid #444;
            }
            QListWidget::item         { padding: 5px 4px; }
            QListWidget::item:selected { background: #F97316; color: #fff; }
            QLineEdit, QTextEdit, QSpinBox, QComboBox {
                background: #2d2d2d; color: #e0e0e0;
                border: 1px solid #555; border-radius: 3px; padding: 3px;
            }
            QCheckBox, QRadioButton { color: #e0e0e0; }
            QTabWidget::pane { border: 1px solid #444; }
            QTabBar::tab {
                background: #2d2d2d; color: #ccc;
                padding: 8px 16px; border: 1px solid #444;
            }
            QTabBar::tab:selected { background: #1e1e1e; color: #fff; }
            QScrollArea { border: none; }
            QProgressBar {
                background: #333; border: 1px solid #555;
                text-align: center; color: #e0e0e0;
            }
            QProgressBar::chunk { background: #0D47A1; }
            QStatusBar { background: #252526; color: #999; }
            QMenuBar { background: #2d2d2d; color: #e0e0e0; }
            QMenuBar::item:selected { background: #3d3d3d; }
            QMenu { background: #2d2d2d; color: #e0e0e0; border: 1px solid #555; }
            QMenu::item:selected { background: #3d3d3d; }
        """)

    def _toggle_dark_mode(self, checked: bool):
        self._settings["dark_mode"] = checked
        self._save_settings()
        self._apply_style()

    # ── Settings dialog ──────────────────────────────────────────────────────

    def _show_settings_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.setMinimumWidth(420)
        layout = QFormLayout(dlg)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # Output folder
        out_row = QHBoxLayout()
        out_edit = QLineEdit(self._settings.get("output_dir", str(get_output_dir())))
        out_browse = QPushButton("Browse…")
        out_browse.clicked.connect(lambda: (
            p := QFileDialog.getExistingDirectory(dlg, "Output Folder"),
            out_edit.setText(p) if p else None,
        ))
        out_row.addWidget(out_edit, stretch=1)
        out_row.addWidget(out_browse)
        layout.addRow("Output Folder:", out_row)

        # Search delay
        delay_spin = QSpinBox()
        delay_spin.setRange(1, 10)
        delay_spin.setValue(self._settings.get("search_delay", 2))
        delay_spin.setSuffix(" s")
        layout.addRow("Search Delay:", delay_spin)

        # Default CRM format
        fmt_combo = QComboBox()
        fmt_combo.addItems(list(_CRM_PRESETS.keys()))
        cur = self._settings.get("default_format", "HubSpot")
        if cur in _CRM_PRESETS:
            fmt_combo.setCurrentText(cur)
        layout.addRow("Default CRM Format:", fmt_combo)

        # Dark mode
        dark_cb = QCheckBox()
        dark_cb.setChecked(self._settings.get("dark_mode", False))
        layout.addRow("Dark Mode:", dark_cb)

        # MillionVerifier API key
        api_lbl = QLabel(
            "Email verification APIs — used when port 25 is blocked.\n"
            "NeverBounce credits are used first; Reoon activates automatically when they run out.\n"
            "Keys here are overridden by credentials.json if present in the app folder."
        )
        api_lbl.setStyleSheet("color: #888; font-size: 10px;")
        api_lbl.setWordWrap(True)
        layout.addRow(api_lbl)

        nb_edit = QLineEdit(self._settings.get("nb_api_key", ""))
        nb_edit.setPlaceholderText("NeverBounce API key…")
        nb_edit.setEchoMode(QLineEdit.Password)
        nb_show = QCheckBox("Show")
        nb_show.toggled.connect(
            lambda checked: nb_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        )
        nb_row = QHBoxLayout()
        nb_row.addWidget(nb_edit, stretch=1)
        nb_row.addWidget(nb_show)
        layout.addRow("NeverBounce Key:", nb_row)

        reoon_edit = QLineEdit(self._settings.get("reoon_api_key", ""))
        reoon_edit.setPlaceholderText("Reoon API key (fallback)…")
        reoon_edit.setEchoMode(QLineEdit.Password)
        reoon_show = QCheckBox("Show")
        reoon_show.toggled.connect(
            lambda checked: reoon_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        )
        reoon_row = QHBoxLayout()
        reoon_row.addWidget(reoon_edit, stretch=1)
        reoon_row.addWidget(reoon_show)
        layout.addRow("Reoon Key (fallback):", reoon_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)

        if dlg.exec() == QDialog.Accepted:
            self._settings["output_dir"]     = out_edit.text().strip()
            self._settings["search_delay"]   = delay_spin.value()
            self._settings["default_format"] = fmt_combo.currentText()
            self._settings["dark_mode"]      = dark_cb.isChecked()
            self._settings["nb_api_key"]     = nb_edit.text().strip()
            self._settings["reoon_api_key"]  = reoon_edit.text().strip()
            self._act_dark.setChecked(dark_cb.isChecked())
            self._save_settings()
            self._apply_style()

    # ── Keyboard shortcuts ───────────────────────────────────────────────────

    def _register_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._shortcut_export)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._shortcut_run)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._add_company)

    def _shortcut_export(self):
        tab = self._tabs.currentIndex()
        if tab == 0:
            self._export_csv()
        elif tab == 1:
            self._email_export()
        elif tab == 2:
            self._crm_export()

    def _shortcut_run(self):
        tab = self._tabs.currentIndex()
        if tab == 0:
            self._start_search()
        elif tab == 1:
            self._email_start()

    # ── Queue management ─────────────────────────────────────────────────────

    def _add_company(self):
        name = self._input.text().strip()
        if not name or self._in_queue(name):
            return
        self._enqueue(name)
        self._input.clear()

    def _paste_companies(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Paste Company Names")
        dlg.setMinimumSize(400, 300)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Enter one company name per line:"))
        text_edit = QTextEdit()
        text_edit.setPlaceholderText("EOG Resources\nCoterra Energy\nChord Energy\n…")
        layout.addWidget(text_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        lines = text_edit.toPlainText().strip().splitlines()
        added = 0
        for line in lines:
            name = line.strip().strip(',"\'')
            if name and not self._in_queue(name):
                self._enqueue(name)
                added += 1
        if added:
            self.statusBar().showMessage(f"Added {added} companies to queue.")

    def _enqueue(self, name: str):
        icon, color = _QUEUE_STATUS["pending"]
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
            self, "Load Companies", "", "Text/CSV Files (*.txt *.csv);;All Files (*)"
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
            self.statusBar().showMessage(f"Loaded {added} companies from file.")
        except Exception as e:
            QMessageBox.warning(self, "Load Error", str(e))

    def _clear_pending(self):
        if self._worker and self._worker.isRunning():
            QMessageBox.warning(self, "Running", "Stop the search before clearing the queue.")
            return
        to_remove = [
            i for i in range(self._queue.count())
            if self._company_status.get(self._queue.item(i).data(Qt.UserRole)) == "pending"
        ]
        for i in reversed(to_remove):
            self._queue.takeItem(i)

    def _queue_context_menu(self, pos):
        item = self._queue.itemAt(pos)
        if not item:
            return
        company = item.data(Qt.UserRole)
        status  = self._company_status.get(company, "")
        menu    = QMenu(self)
        if status == "running":
            menu.addAction("Currently running — cannot remove").setEnabled(False)
        else:
            n = sum(1 for c in self._contacts if c.get("company", "") == company)
            lbl = f"Remove '{company}'"
            if n:
                lbl += f" and {n} contact{'s' if n != 1 else ''}"
            menu.addAction(lbl).triggered.connect(lambda: self._remove_company(company))
        menu.exec(self._queue.viewport().mapToGlobal(pos))

    def _remove_company(self, company: str):
        # Remove from queue
        for i in range(self._queue.count()):
            if self._queue.item(i).data(Qt.UserRole) == company:
                self._queue.takeItem(i)
                break
        self._company_status.pop(company, None)

        # Remove from contacts list
        removed = [c for c in self._contacts if c.get("company", "") == company]
        self._contacts = [c for c in self._contacts if c.get("company", "") != company]

        # Remove URLs from duplicate tracker
        for c in removed:
            self._seen_urls.discard(c.get("linkedin_url", ""))

        # Remove rows from table
        self._table.setSortingEnabled(False)
        rows = [
            row for row in range(self._table.rowCount())
            if (self._table.item(row, FIELDS.index("company")) or QTableWidgetItem("")).text() == company
        ]
        for row in reversed(rows):
            self._table.removeRow(row)
        self._table.setSortingEnabled(True)

        self._count_label.setText(f"{len(self._contacts)} contacts found")
        self.statusBar().showMessage(f"Removed '{company}' and {len(removed)} contacts from session.")

    # ── Search control ───────────────────────────────────────────────────────

    def _pipeline_start(self):
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
            self.statusBar().showMessage("No pending companies in queue.")
            return

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)

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
        self.statusBar().showMessage("Stopping after current query completes…")

    # ── Verify employment ────────────────────────────────────────────────────

    def _toggle_verify(self):
        if self._verify_worker and self._verify_worker.isRunning():
            self._verify_worker.stop()
            self._btn_verify.setText("Verify Employment")
            self.statusBar().showMessage("Stopping verification…")
            return
        if not self._contacts:
            self.statusBar().showMessage("No contacts to verify. Run a search first.")
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
        for c in self._contacts:
            if c.get("linkedin_url", "") == linkedin_url:
                c["emp_status"] = emp_status
                break
        emp_col = FIELDS.index("emp_status")
        url_col = FIELDS.index("linkedin_url")
        self._table.setSortingEnabled(False)
        for row in range(self._table.rowCount()):
            if (self._table.item(row, url_col) or QTableWidgetItem("")).text() == linkedin_url:
                status_item = QTableWidgetItem(emp_status)
                color = _EMP_COLORS.get(emp_status)
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
        msg = f"Verification done — {current} current, {stale} stale, {unknown} unknown."
        self._puller_status.setText(msg)
        self.statusBar().showMessage(msg)

    # ── Worker signal handlers ───────────────────────────────────────────────

    @Slot(dict)
    def _on_contact(self, contact: dict):
        # Duplicate detection
        url = contact.get("linkedin_url", "")
        if url and url in self._seen_urls:
            self._dup_count = getattr(self, "_dup_count", 0) + 1
            self._dup_label.setText(f"({self._dup_count} duplicate{'s' if self._dup_count != 1 else ''} skipped)")
            return
        if url:
            self._seen_urls.add(url)

        # Title filter
        title = contact.get("title", "").lower()
        inc_keywords = self._get_active_include_keywords()
        if inc_keywords and not any(kw in title for kw in inc_keywords):
            return
        exc = self._settings.get("title_exclude", "")
        if exc:
            exc_terms = [t.strip().lower() for t in exc.split(",") if t.strip()]
            if any(t in title for t in exc_terms):
                return

        # Auto-fill priority from title
        contact["priority"] = _priority_label(contact.get("title", ""))

        # Auto-fill basin if blank
        if not contact.get("basin"):
            contact["basin"] = get_basin(contact.get("location", ""))

        self._contacts.append(contact)

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
        append_contact(contact, contact.get("company", "unknown"))
        self._on_contact_added_update_ui(contact)

    @Slot(str)
    def _on_started(self, company: str):
        self._set_queue_status(company, "running")

    @Slot(str, int, int)
    def _on_progress(self, company: str, current: int, total: int):
        self._progress.setRange(0, total)
        self._progress.setValue(current)

    @Slot(str, int)
    def _on_finished(self, company: str, count: int):
        self._set_queue_status(company, "done", suffix=f" ({count})")

    @Slot(str)
    def _on_failed(self, company: str):
        self._set_queue_status(company, "failed", suffix=" (0 — logged)")

    @Slot(str)
    def _on_status(self, msg: str):
        self._puller_status.setText(msg)
        self.statusBar().showMessage(msg)

    @Slot()
    def _on_all_done(self):
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._progress.setVisible(False)
        msg = f"Done — {len(self._contacts)} contacts found. Auto-saved to {get_output_dir()}"
        self._puller_status.setText(msg)
        self.statusBar().showMessage(msg)
        if self._pipeline_mode and self._contacts:
            self.statusBar().showMessage(f"{len(self._contacts)} contacts pulled. Starting email pipeline…")
            self._tabs.setCurrentIndex(1)
            self._email_load_from_puller()
            self._email_start()

    def _set_queue_status(self, company: str, status: str, suffix: str = ""):
        icon, color = _QUEUE_STATUS[status]
        for i in range(self._queue.count()):
            item = self._queue.item(i)
            if item.data(Qt.UserRole) == company:
                item.setText(f"{icon}  {company}{suffix}")
                item.setForeground(QColor(color))
                self._company_status[company] = status
                return

    # ── Table interactions ───────────────────────────────────────────────────

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
        url_item = self._table.item(row, FIELDS.index("linkedin_url"))
        menu = QMenu(self)
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
        company_item = self._table.item(row, FIELDS.index("company"))
        if company_item and company_item.text():
            co = company_item.text()
            menu.addSeparator()
            menu.addAction(f"Remove all '{co}' contacts").triggered.connect(
                lambda: self._remove_company(co)
            )
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_row(self, row: int):
        values = [(self._table.item(row, c) or QTableWidgetItem("")).text()
                  for c in range(len(FIELDS))]
        QApplication.clipboard().setText("\t".join(values))

    def _export_csv(self):
        if not self._contacts:
            self.statusBar().showMessage("No contacts to export yet.")
            return
        default = str(get_output_dir() / "contacts_export.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", default, "CSV Files (*.csv)")
        if path:
            export_contacts(self._contacts, path)
            self.statusBar().showMessage(f"Exported {len(self._contacts)} contacts to {path}")

    # ── Email tab: load contacts ─────────────────────────────────────────────

    def _email_load_from_puller(self):
        if not self._contacts:
            self._email_status.setText("No contacts in Lead Scout yet.")
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
        try:
            with open(path, encoding="utf-8") as f:
                contacts = [{k.lower().replace(" ", "_"): v for k, v in row.items()}
                            for row in csv.DictReader(f)]
            self._email_contacts = contacts
            self._populate_email_table(contacts)
            self._email_status.setText(f"{len(contacts)} contacts loaded from {Path(path).name}")
        except Exception as e:
            QMessageBox.warning(self, "Load Error", str(e))

    def _populate_email_table(self, contacts: list[dict]):
        self._email_table.setSortingEnabled(False)
        self._email_table.setRowCount(0)
        self._email_company_list.clear()
        self._email_company_progress.clear()

        companies_seen: set[str] = set()
        for c in contacts:
            row = self._email_table.rowCount()
            self._email_table.insertRow(row)
            for col, key in enumerate(_EMAIL_FIELDS):
                val = str(c.get(key, ""))
                if key == "confidence" and not val:
                    val = _confidence(c.get("email_status", ""))
                item = QTableWidgetItem(val)
                if key == "linkedin_url":
                    item.setForeground(QColor("#1565C0"))
                self._email_table.setItem(row, col, item)

            company = c.get("company", "")
            if company and company not in companies_seen:
                companies_seen.add(company)
                n = sum(1 for x in contacts if x.get("company") == company)
                li = QListWidgetItem(f"⏳  {company}  (0/{n})")
                li.setData(Qt.UserRole, company)
                self._email_company_list.addItem(li)
                self._email_company_progress[company] = (0, n)

        self._email_table.setSortingEnabled(True)
        self._email_count_label.setText(f"{len(contacts)} contacts loaded")
        self._email_btn_retry.setEnabled(False)

    # ── Email tab: enrichment ────────────────────────────────────────────────

    def _email_start(self):
        if not self._email_contacts:
            self._email_status.setText("Load contacts first.")
            return

        # Skip already-enriched?
        already = [c for c in self._email_contacts if c.get("email", "").strip()]
        to_enrich = self._email_contacts
        if already:
            reply = QMessageBox.question(
                self, "Skip Enriched Contacts",
                f"{len(already)} contact(s) already have an email address.\n\n"
                f"Skip them and only process the {len(self._email_contacts) - len(already)} remaining?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.Yes:
                to_enrich = [c for c in self._email_contacts if not c.get("email", "").strip()]

        if not to_enrich:
            self._email_status.setText("All contacts already have emails.")
            return

        self._email_enriched = []
        self._email_btn_start.setEnabled(False)
        self._email_btn_stop.setEnabled(True)
        self._email_btn_retry.setEnabled(False)
        self._email_progress.setVisible(True)
        self._email_progress.setRange(0, len(to_enrich))
        self._email_progress.setValue(0)

        self._email_worker = EmailWorker(
            to_enrich,
            nb_api_key=self._settings.get("nb_api_key", ""),
            reoon_api_key=self._settings.get("reoon_api_key", ""),
        )
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
        self._email_status.setText("Stopping after current contact…")

    def _email_retry_failed(self):
        failed = [c for c in self._email_enriched if c.get("email_status", "") in DROP_STATUSES]
        if not failed:
            self._email_status.setText("No failed contacts to retry.")
            return
        reply = QMessageBox.question(
            self, "Retry Failed",
            f"Re-run enrichment on {len(failed)} failed contact(s)?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._email_contacts = failed
        self._populate_email_table(failed)
        self._email_start()

    @Slot(dict)
    def _on_email_enriched(self, contact: dict):
        # Add confidence
        status = contact.get("email_status", "")
        contact["confidence"] = _confidence(status)

        self._email_enriched.append(contact)
        self._email_progress.setValue(len(self._email_enriched))

        url_col = _EMAIL_FIELDS.index("linkedin_url")
        url = contact.get("linkedin_url", "")
        for row in range(self._email_table.rowCount()):
            url_item = self._email_table.item(row, url_col)
            if url_item and url_item.text() == url:
                self._email_table.setSortingEnabled(False)
                for col, key in enumerate(_EMAIL_FIELDS):
                    val = str(contact.get(key, "") or "")
                    if key == "confidence" and not val:
                        val = _confidence(contact.get("email_status", ""))
                    item = QTableWidgetItem(val)
                    if key == "email_status":
                        color = _EMAIL_COLORS.get(status)
                        if color:
                            item.setForeground(QColor(color))
                    elif key == "confidence":
                        item.setForeground(QColor(
                            "#2E7D32" if val == "High" else
                            "#E65100" if val == "Medium" else
                            "#888" if val == "Low" else "#bbb"
                        ))
                    elif key == "linkedin_url":
                        item.setForeground(QColor("#1565C0"))
                    self._email_table.setItem(row, col, item)
                self._email_table.setSortingEnabled(True)

                # Update per-company progress
                company = contact.get("company", "")
                if company in self._email_company_progress:
                    done, total = self._email_company_progress[company]
                    done += 1
                    self._email_company_progress[company] = (done, total)
                    self._set_email_company_icon(company, "🔵", f" ({done}/{total})")
                break

    @Slot(str, int)
    def _on_email_company_started(self, company: str, count: int):
        self._email_status.setText(f"Processing {company} ({count} contacts)…")
        self._set_email_company_icon(company, "🔵", f" (0/{count})")
        self._email_company_progress[company] = (0, count)

    @Slot(str, str)
    def _on_email_company_status(self, company: str, msg: str):
        self._email_status.setText(f"[{company}] {msg}")
        self.statusBar().showMessage(f"[{company}] {msg}")

    @Slot(str, int, int)
    def _on_email_company_done(self, company: str, verified: int, total: int):
        icon = "✅" if verified > 0 else "⚠️"
        self._set_email_company_icon(company, icon, f" ({verified}/{total} verified)")

    @Slot(str)
    def _on_email_status(self, msg: str):
        self._email_status.setText(msg)
        self.statusBar().showMessage(msg)

    @Slot()
    def _on_port25_blocked(self):
        self._port25_ok = False
        has_nb    = bool(self._settings.get("nb_api_key", "").strip())
        has_reoon = bool(self._settings.get("reoon_api_key", "").strip())
        if has_nb or has_reoon:
            verifier = "NeverBounce" + (" → Reoon fallback" if has_reoon else "")
            self._port25_banner.setText(
                f"  PORT 25 BLOCKED — Routing verification through {verifier}. "
                "Results will be fully verified (verified / catch-all / bounced)."
            )
            self._port25_banner.setStyleSheet(
                "background: #14532D; color: #86EFAC; padding: 6px 10px; "
                "font-size: 11px; border-radius: 4px;"
            )
        else:
            self._port25_banner.setText(
                "  PORT 25 BLOCKED — SMTP verification unavailable on this network. "
                "Emails generated from pattern detection only (status: unverified). "
                "Add a Reoon API key in Settings for full verification, "
                "or use Generate Emails.bat + Reoon/NeverBounce externally."
            )
            self._port25_banner.setStyleSheet(
                "background: #7F1D1D; color: #FCA5A5; padding: 6px 10px; "
                "font-size: 11px; border-radius: 4px;"
            )
        self._port25_banner.setVisible(True)

    @Slot()
    def _on_email_all_done(self):
        self._email_btn_start.setEnabled(True)
        self._email_btn_stop.setEnabled(False)
        self._email_progress.setVisible(False)
        self._email_btn_retry.setEnabled(
            any(c.get("email_status", "") in DROP_STATUSES for c in self._email_enriched)
        )
        verified  = sum(1 for c in self._email_enriched if c.get("email_status") == "verified")
        confirmed = sum(1 for c in self._email_enriched if c.get("email_status") == "catch-all-confirmed")
        risky     = sum(1 for c in self._email_enriched if c.get("email_status") == "catch-all-risky")
        other     = len(self._email_enriched) - verified - confirmed - risky
        msg = (f"Done — {verified} verified | {confirmed} catch-all confirmed | "
               f"{risky} risky | {other} unresolved")
        self._email_status.setText(msg)
        self.statusBar().showMessage(msg)
        if self._pipeline_mode:
            self._pipeline_mode = False
            self._btn_pipeline.setEnabled(True)
            self._pipeline_finish()

    def _pipeline_finish(self):
        viable  = [c for c in self._email_enriched
                   if c.get("email") and c.get("email_status", "") not in DROP_STATUSES]
        dropped = len(self._email_enriched) - len(viable)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = get_output_dir() / f"pipeline_{ts}.csv"
        fields = ["first_name", "last_name", "title", "company", "email",
                  "email_status", "confidence", "email_source", "location",
                  "basin", "linkedin_url", "date_pulled"]
        try:
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows([{k: c.get(k, "") for k in fields} for c in viable])
            msg = (f"Pipeline complete — {len(viable)} viable contacts saved to "
                   f"{out_path.name} | {dropped} dropped")
            self._email_status.setText(msg)
            self.statusBar().showMessage(msg)
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
        email_col = _EMAIL_FIELDS.index("email")
        url_col   = _EMAIL_FIELDS.index("linkedin_url")
        email_item = self._email_table.item(row, email_col)
        url_item   = self._email_table.item(row, url_col)
        menu = QMenu(self)
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
        values = [(self._email_table.item(row, c) or QTableWidgetItem("")).text()
                  for c in range(self._email_table.columnCount())]
        QApplication.clipboard().setText("\t".join(values))

    def _email_export(self):
        data = self._email_enriched if self._email_enriched else self._email_contacts
        if not data:
            self._email_status.setText("No contacts to export.")
            return
        default = str(get_output_dir() / "contacts_enriched.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export Enriched CSV", default, "CSV Files (*.csv)")
        if not path:
            return
        fields = ["first_name", "last_name", "title", "company", "email",
                  "email_status", "confidence", "email_source", "location",
                  "basin", "linkedin_url", "source", "date_pulled"]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows([{k: c.get(k, "") for k in fields} for c in data])
            msg = f"Exported {len(data)} contacts to {Path(path).name}"
            self._email_status.setText(msg)
            self.statusBar().showMessage(msg)
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    # ── Email preview panel ──────────────────────────────────────────────────

    def _on_email_row_selected(self):
        rows = self._email_table.selectionModel().selectedRows()
        if not rows:
            self._email_preview_label.setText("Select a contact to see all email format candidates.")
            self._email_preview_table.setVisible(False)
            return
        row = rows[0].row()
        fn_col  = _EMAIL_FIELDS.index("first_name")
        ln_col  = _EMAIL_FIELDS.index("last_name")
        co_col  = _EMAIL_FIELDS.index("company")
        em_col  = _EMAIL_FIELDS.index("email")
        fn = (self._email_table.item(row, fn_col) or QTableWidgetItem("")).text()
        ln = (self._email_table.item(row, ln_col) or QTableWidgetItem("")).text()
        co = (self._email_table.item(row, co_col) or QTableWidgetItem("")).text()
        known_email = (self._email_table.item(row, em_col) or QTableWidgetItem("")).text()

        # Look up domain from cache
        cached = cache_get(co) if co else None
        domain = cached.get("domain", "") if cached else ""
        pattern = cached.get("pattern") if cached else None

        if not domain or not fn or not ln:
            self._email_preview_label.setText(
                f"{fn} {ln} @ {co}  —  domain not yet discovered."
            )
            self._email_preview_table.setVisible(False)
            return

        candidates = generate_candidates(fn, ln, domain, pattern=None)  # all 8 formats
        self._email_preview_label.setText(
            f"{fn} {ln}  |  {co}  |  Domain: {domain}  |  Known pattern: {pattern or 'unknown'}"
            "  —  Right-click a row to use that address."
        )

        from emailer.bounce_tracker import is_bounced
        _FORMAT_NAMES = ["flast", "first.last", "first_last", "first", "firstlast", "lastf", "last", "f.last"]
        self._email_preview_table.setRowCount(len(candidates))
        for i, cand in enumerate(candidates):
            fmt_name = _FORMAT_NAMES[i] if i < len(_FORMAT_NAMES) else ""
            bounced  = is_bounced(cand)
            selected = (cand == known_email)

            if selected:
                status_text  = "selected"
                status_color = QColor("#4ADE80")
                row_color    = QColor("#4ADE80")
            elif bounced:
                status_text  = "bounced"
                status_color = QColor("#C62828")
                row_color    = QColor("#C62828")
            else:
                status_text  = ""
                status_color = None
                row_color    = None

            fmt_item    = QTableWidgetItem(fmt_name)
            em_item     = QTableWidgetItem(cand)
            status_item = QTableWidgetItem(status_text)

            for item in (fmt_item, em_item, status_item):
                if row_color:
                    item.setForeground(row_color)

            self._email_preview_table.setItem(i, 0, fmt_item)
            self._email_preview_table.setItem(i, 1, em_item)
            self._email_preview_table.setItem(i, 2, status_item)
        self._email_preview_table.setVisible(True)

    def _preview_context_menu(self, pos):
        """Right-click on a candidate row → use that email for the selected contact."""
        prev_rows = self._email_preview_table.selectionModel().selectedRows()
        if not prev_rows:
            return
        prev_row = prev_rows[0].row()
        cand_item = self._email_preview_table.item(prev_row, 1)
        if not cand_item:
            return
        chosen_email = cand_item.text()

        from emailer.bounce_tracker import is_bounced, remove_bounce
        bounced = is_bounced(chosen_email)

        menu = QMenu(self)
        act_use    = menu.addAction(f"Use  {chosen_email}")
        act_unbounce = None
        if bounced:
            act_unbounce = menu.addAction(f"Remove from bounce list & use  {chosen_email}")
            act_unbounce.setToolTip("Clears this address from the bounce tracker, then sets it as the contact's email.")

        chosen = menu.exec(self._email_preview_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_unbounce:
            remove_bounce(chosen_email)
        elif chosen != act_use:
            return

        # Apply to the currently selected row in the email table
        sel_rows = self._email_table.selectionModel().selectedRows()
        if not sel_rows:
            return
        tbl_row = sel_rows[0].row()
        em_col  = _EMAIL_FIELDS.index("email")
        st_col  = _EMAIL_FIELDS.index("email_status")

        em_item = QTableWidgetItem(chosen_email)
        em_item.setForeground(QColor(_EMAIL_COLORS.get("pattern-confirmed", "#e0e0e0")))
        self._email_table.setItem(tbl_row, em_col, em_item)
        self._email_table.setItem(tbl_row, st_col, QTableWidgetItem("manual-override"))

        # Update in-memory contact list
        name_col = _EMAIL_FIELDS.index("name") if "name" in _EMAIL_FIELDS else None
        if name_col is not None:
            name_val = (self._email_table.item(tbl_row, name_col) or QTableWidgetItem("")).text()
            for c in self._enriched:
                if c.get("name") == name_val:
                    c["email"] = chosen_email
                    c["email_status"] = "manual-override"
                    break

        # Refresh preview so the selected row updates
        self._on_email_row_selected()

    # ── Session persistence ──────────────────────────────────────────────────

    def _session_save(self):
        try:
            data = {
                "saved_at": datetime.now().isoformat(),
                "contacts": self._contacts,
                "enriched": self._email_enriched,
                "queue":    [self._queue.item(i).data(Qt.UserRole)
                             for i in range(self._queue.count())],
                "company_status": self._company_status,
            }
            _SESSION_FILE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning("Session save failed: %s", e)

    def _session_offer_restore(self):
        try:
            if not _SESSION_FILE.exists():
                return
            data = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
            contacts = data.get("contacts", [])
            enriched = data.get("enriched", [])
            if not contacts and not enriched:
                return
            saved_at = data.get("saved_at", "unknown")[:16].replace("T", " ")
            reply = QMessageBox.question(
                self, "Restore Previous Session",
                f"A session saved {saved_at} was found with "
                f"{len(contacts)} contacts and {len(enriched)} enriched contacts.\n\n"
                "Restore it?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            self._session_load_data(data)
        except Exception as e:
            logger.warning("Session restore failed: %s", e)

    def _session_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Session", str(get_output_dir()), "JSON Files (*.json);;CSV Files (*.csv)"
        )
        if not path:
            return
        try:
            if path.endswith(".json"):
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self._session_load_data(data)
            else:
                # CSV import — treat as contacts
                with open(path, encoding="utf-8") as f:
                    contacts = [{k.lower().replace(" ", "_"): v for k, v in r.items()}
                                for r in csv.DictReader(f)]
                self._session_load_data({"contacts": contacts, "enriched": [], "queue": [], "company_status": {}})
        except Exception as e:
            QMessageBox.warning(self, "Import Error", str(e))

    def _session_load_data(self, data: dict):
        contacts = data.get("contacts", [])
        enriched = data.get("enriched", [])
        queue    = data.get("queue", [])
        statuses = data.get("company_status", {})

        # Restore contacts table
        for c in contacts:
            if c.get("linkedin_url") and c["linkedin_url"] not in self._seen_urls:
                if c.get("linkedin_url"):
                    self._seen_urls.add(c["linkedin_url"])
                if not c.get("priority"):
                    c["priority"] = _priority_label(c.get("title", ""))
                self._contacts.append(c)
                self._table.setSortingEnabled(False)
                row = self._table.rowCount()
                self._table.insertRow(row)
                for col, key in enumerate(FIELDS):
                    val = str(c.get(key, ""))
                    item = QTableWidgetItem(val)
                    if key == "linkedin_url":
                        item.setForeground(QColor("#1565C0"))
                    self._table.setItem(row, col, item)
                self._table.setSortingEnabled(True)

        self._count_label.setText(f"{len(self._contacts)} contacts found")

        # Restore queue
        for co in queue:
            if not self._in_queue(co):
                status = statuses.get(co, "done")
                icon, color = _QUEUE_STATUS.get(status, _QUEUE_STATUS["done"])
                item = QListWidgetItem(f"{icon}  {co}")
                item.setData(Qt.UserRole, co)
                item.setForeground(QColor(color))
                self._queue.addItem(item)
                self._company_status[co] = status

        # Restore enriched contacts
        if enriched:
            self._email_enriched = enriched
            self._email_contacts = contacts if contacts else enriched
            self._populate_email_table(self._email_contacts)

        n = len(contacts)
        e = len(enriched)
        self.statusBar().showMessage(f"Session restored — {n} contacts, {e} enriched.")

    def closeEvent(self, event):
        if self._contacts or self._email_enriched:
            self._session_save()
        event.accept()

    # ── Domain cache editor ──────────────────────────────────────────────────

    def _show_domain_cache_editor(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Domain Cache Editor")
        dlg.setMinimumSize(800, 500)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        info = QLabel(
            "Edit domain and email pattern assignments. "
            "Changes take effect immediately for future email generation runs."
        )
        info.setStyleSheet("color: #888; font-size: 11px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Load KB for confidence display
        from emailer.pattern_detector import _load_kb
        kb = _load_kb()

        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["Company", "Domain", "Pattern", "Source", "KB Confidence", "KB Samples"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)

        entries = cache_all()
        table.setRowCount(len(entries))
        for row, (company, info_dict) in enumerate(sorted(entries.items())):
            domain = info_dict.get("domain", "")
            table.setItem(row, 0, QTableWidgetItem(company))
            table.setItem(row, 1, QTableWidgetItem(domain))
            pattern_item = QTableWidgetItem(info_dict.get("pattern") or "")
            table.setItem(row, 2, pattern_item)
            src_item = QTableWidgetItem(info_dict.get("pattern_source", ""))
            src_item.setFlags(src_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 3, src_item)

            # KB confidence
            kb_entry = kb.get(domain, {})
            if kb_entry:
                conf = kb_entry.get("confidence", 0)
                n    = kb_entry.get("sample_count", 0)
                conf_text = f"{int(conf*100)}%  ({n} contacts)"
                conf_item = QTableWidgetItem(conf_text)
                conf_item.setForeground(QColor(
                    "#4ADE80" if conf >= 0.9 else "#FBBF24" if conf >= 0.7 else "#F87171"
                ))
            else:
                conf_item = QTableWidgetItem("—  not in KB")
                conf_item.setForeground(QColor("#6B7280"))
            conf_item.setFlags(conf_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 4, conf_item)

            samples = ", ".join(kb_entry.get("samples", []))
            samp_item = QTableWidgetItem(samples)
            samp_item.setFlags(samp_item.flags() & ~Qt.ItemIsEditable)
            samp_item.setForeground(QColor("#6B7280"))
            table.setItem(row, 5, samp_item)

        # Only domain and pattern are editable
        for row in range(table.rowCount()):
            table.item(row, 0).setFlags(table.item(row, 0).flags() & ~Qt.ItemIsEditable)

        layout.addWidget(table, stretch=1)

        pattern_hint = QLabel(
            "Valid patterns: first.last  |  flast  |  firstlast  |  f.last  |  first.l  |  first  |  last.first  |  lfirst"
        )
        pattern_hint.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(pattern_hint)

        btn_row = QHBoxLayout()
        btn_delete = QPushButton("Delete Selected Row")
        btn_delete.clicked.connect(lambda: table.removeRow(table.currentRow()))
        btn_row.addWidget(btn_delete)
        btn_row.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.Accepted:
            return

        # Write back all rows
        new_cache = {}
        for row in range(table.rowCount()):
            company  = (table.item(row, 0) or QTableWidgetItem("")).text().strip()
            domain   = (table.item(row, 1) or QTableWidgetItem("")).text().strip()
            pattern  = (table.item(row, 2) or QTableWidgetItem("")).text().strip() or None
            src_item = table.item(row, 3)
            source   = src_item.text().strip() if src_item else "manual"
            if company and domain:
                new_cache[company] = {
                    "domain":         domain,
                    "pattern":        pattern,
                    "pattern_source": source if source else "manual",
                }

        from emailer.domain_cache import _CACHE_FILE
        try:
            _CACHE_FILE.write_text(json.dumps(new_cache, indent=2), encoding="utf-8")
            self.statusBar().showMessage(f"Domain cache saved — {len(new_cache)} entries.")
        except Exception as e:
            QMessageBox.warning(self, "Save Error", str(e))

    # ── Open output folder ───────────────────────────────────────────────────

    def _open_output_folder(self):
        folder = Path(self._settings.get("output_dir", str(get_output_dir())))
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(folder))
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    # ── Bounce importer ──────────────────────────────────────────────────────

    def _import_bounces_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Bounce List", str(get_output_dir()),
            "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return
        try:
            from emailer.bounce_tracker import record_bounces
            emails = []
            with open(path, encoding="utf-8") as f:
                # Try CSV first, fall back to one-per-line
                content = f.read()
            f_lines = content.splitlines()
            if "," in f_lines[0] if f_lines else False:
                import io
                reader = csv.DictReader(io.StringIO(content))
                # Accept any column named email, Email, EMAIL, address, etc.
                email_col = next(
                    (k for k in (reader.fieldnames or [])
                     if "email" in k.lower() or "address" in k.lower()), None
                )
                if email_col:
                    emails = [row[email_col].strip() for row in reader if row.get(email_col, "").strip()]
                else:
                    emails = [line.strip() for line in f_lines if "@" in line]
            else:
                emails = [line.strip() for line in f_lines if "@" in line]

            if not emails:
                QMessageBox.warning(self, "No Emails Found",
                                    "Could not find any email addresses in the file.\n"
                                    "Ensure the CSV has an 'email' or 'address' column.")
                return

            campaign, ok = QLineEdit.getText if False else ("", True)
            # Simple campaign name prompt
            dlg = QDialog(self)
            dlg.setWindowTitle("Campaign Name (optional)")
            dlg.setFixedSize(340, 110)
            lay = QVBoxLayout(dlg)
            edit = QLineEdit()
            edit.setPlaceholderText("e.g.  Q3 Permian Outreach")
            lay.addWidget(QLabel("Tag these bounces with a campaign name:"))
            lay.addWidget(edit)
            btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            lay.addWidget(btns)
            if dlg.exec() == QDialog.Accepted:
                campaign = edit.text().strip()

            record_bounces(emails, campaign)
            msg = f"Recorded {len(emails)} bounced addresses."
            if campaign:
                msg += f"  Campaign: {campaign}"
            self.statusBar().showMessage(msg)
            QMessageBox.information(self, "Bounces Imported", msg)
        except Exception as e:
            QMessageBox.warning(self, "Import Error", str(e))

    # ── MillionVerifier / NeverBounce export + import ─────────────────────────

    def _mv_export(self):
        """Export email list in MillionVerifier / NeverBounce input format."""
        data = self._email_enriched if self._email_enriched else self._email_contacts
        emails = [c.get("email", "").strip() for c in data if c.get("email", "").strip()]
        if not emails:
            QMessageBox.warning(self, "No Emails", "No email addresses to export.")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = str(get_output_dir() / f"verify_input_{ts}.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export for Verification", default, "CSV Files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["email"])
                for email in emails:
                    writer.writerow([email])
            self.statusBar().showMessage(
                f"Exported {len(emails)} emails to {Path(path).name}  "
                f"— upload to MillionVerifier or NeverBounce, then import results."
            )
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    def _mv_import(self):
        """Import MillionVerifier / NeverBounce result CSV and update statuses."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Verification Results", str(get_output_dir()),
            "CSV Files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            if not rows:
                QMessageBox.warning(self, "Empty File", "No rows found in the results file.")
                return

            headers = list(rows[0].keys())
            # Detect email + result columns flexibly
            email_col  = next((h for h in headers if "email" in h.lower()), None)
            result_col = next((h for h in headers if any(k in h.lower()
                               for k in ("result", "status", "verdict", "state"))), None)

            if not email_col or not result_col:
                QMessageBox.warning(self, "Column Not Found",
                                    f"Expected 'email' and 'result' columns.\nFound: {headers}")
                return

            # MillionVerifier: ok/invalid/unknown/catch-all/disposable
            # NeverBounce:     valid/invalid/disposable/unknown/catchall
            _MV_MAP = {
                "ok":          "verified",
                "valid":       "verified",
                "invalid":     "bounced",
                "unknown":     "unknown",
                "catch-all":   "catch-all-risky",
                "catchall":    "catch-all-risky",
                "disposable":  "bounced",
            }

            result_map = {row[email_col].strip().lower(): row[result_col].strip().lower()
                          for row in rows if row.get(email_col)}

            updated = 0
            all_data = self._email_enriched if self._email_enriched else self._email_contacts
            for c in all_data:
                email = c.get("email", "").strip().lower()
                if email in result_map:
                    new_status = _MV_MAP.get(result_map[email], result_map[email])
                    c["email_status"] = new_status
                    c["confidence"]   = _confidence(new_status)
                    updated += 1

            # Refresh email table
            if self._email_enriched:
                self._populate_email_table(self._email_enriched)
            msg = f"Updated {updated} contacts from verification results."
            self.statusBar().showMessage(msg)
            QMessageBox.information(self, "Results Imported", msg)

        except Exception as e:
            QMessageBox.warning(self, "Import Error", str(e))

    # ── Logging panel ─────────────────────────────────────────────────────────

    @Slot(str, str)
    def _append_log(self, message: str, level: str):
        colors = {
            "DEBUG":    "#6B7280",
            "INFO":     "#D1D5DB",
            "WARNING":  "#FBBF24",
            "ERROR":    "#F87171",
            "CRITICAL": "#EF4444",
        }
        color = colors.get(level, "#D1D5DB")
        self._log_panel.appendHtml(f'<span style="color:{color}; font-family:monospace; font-size:10px;">{message}</span>')
        # Auto-scroll to bottom
        sb = self._log_panel.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _toggle_log_panel(self):
        visible = not self._log_panel.isVisible()
        self._log_panel.setVisible(visible)
        self._btn_log_toggle.setText("Hide Logs" if visible else "Show Logs")

    # ── LinkedIn session indicator ────────────────────────────────────────────

    def _check_linkedin_session(self):
        session_dir = get_output_dir() / "browser_session" / "Default"
        has_session = session_dir.exists() and any(session_dir.iterdir())
        if has_session:
            self._li_status_label.setText("LinkedIn: Active")
            self._li_status_label.setStyleSheet("color: #4ADE80; font-size: 11px; font-weight: bold;")
            self._li_login_btn.setVisible(False)
        else:
            self._li_status_label.setText("LinkedIn: Login Required")
            self._li_status_label.setStyleSheet("color: #FBBF24; font-size: 11px; font-weight: bold;")
            self._li_login_btn.setVisible(True)

    def _linkedin_relogin(self):
        """Launch Playwright browser so user can log into LinkedIn and save session."""
        script = Path(__file__).parent.parent / "_verify_linkedin_browser.py"
        if not script.exists():
            QMessageBox.warning(self, "Not Found",
                                "Could not find _verify_linkedin_browser.py.\n"
                                "Run it manually from the project folder.")
            return
        import sys
        subprocess.Popen([sys.executable, str(script), "--login-only"],
                         cwd=str(script.parent))
        QMessageBox.information(self, "LinkedIn Login",
                                "A browser window is opening.\n\n"
                                "Log into LinkedIn, then close the browser.\n"
                                "The session will be saved automatically.")
        QTimer.singleShot(3000, self._check_linkedin_session)

    # ── Onboarding wizard ─────────────────────────────────────────────────────

    def _show_onboarding(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Welcome to Lead Scout")
        dlg.setMinimumSize(520, 600)
        dlg.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Logo / header
        icon_path = Path(__file__).parent.parent / "assets" / "logo.png"
        if icon_path.exists():
            logo_lbl = QLabel()
            pix = __import__("PySide6.QtGui", fromlist=["QPixmap"]).QPixmap(str(icon_path))
            logo_lbl.setPixmap(pix.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            logo_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(logo_lbl)

        title = QLabel("Welcome to Lead Scout")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            "O&G Sales Intelligence — find contacts, generate emails, export to your CRM.\n"
            "Let's get you set up in three quick steps."
        )
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(subtitle)

        layout.addWidget(self._make_separator())

        # Step 1: Output folder
        step1 = QGroupBox("Step 1 — Output Folder")
        s1l = QHBoxLayout(step1)
        self._ob_out_edit = QLineEdit(self._settings.get("output_dir", str(get_output_dir())))
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(lambda: (
            p := QFileDialog.getExistingDirectory(dlg, "Output Folder"),
            self._ob_out_edit.setText(p) if p else None,
        ))
        s1l.addWidget(self._ob_out_edit, stretch=1)
        s1l.addWidget(btn_browse)
        layout.addWidget(step1)

        # Step 2: LinkedIn session
        session_dir = get_output_dir() / "browser_session" / "Default"
        has_session = session_dir.exists() and any(session_dir.iterdir())
        step2 = QGroupBox("Step 2 — LinkedIn Session (Employment Verification)")
        s2l = QVBoxLayout(step2)
        if has_session:
            s2_lbl = QLabel("LinkedIn session detected — employment verification is active.")
            s2_lbl.setStyleSheet("color: #4ADE80; font-size: 11px;")
        else:
            s2_lbl = QLabel(
                "No LinkedIn session found. Without it, employment verification returns 'unknown' for ~50% of contacts.\n"
                "Click the button below or use the LinkedIn button in the toolbar to log in."
            )
            s2_lbl.setWordWrap(True)
            s2_lbl.setStyleSheet("color: #FBBF24; font-size: 11px;")
            btn_li = QPushButton("Log in to LinkedIn now")
            btn_li.clicked.connect(self._linkedin_relogin)
            s2l.addWidget(btn_li)
        s2l.addWidget(s2_lbl)
        layout.addWidget(step2)

        # Step 3: HubSpot KB
        kb_path = Path(__file__).parent.parent / "emailer" / "hubspot_knowledge.json"
        step3 = QGroupBox("Step 3 — Email Pattern Knowledge Base")
        s3l = QVBoxLayout(step3)
        if kb_path.exists():
            import json as _json
            try:
                kb_count = len(_json.loads(kb_path.read_text(encoding="utf-8")))
            except Exception:
                kb_count = 0
            s3_lbl = QLabel(f"Knowledge base active — {kb_count} company domains with confirmed patterns.")
            s3_lbl.setStyleSheet("color: #4ADE80; font-size: 11px;")
        else:
            s3_lbl = QLabel(
                "No personal knowledge base found. A shared base (978 domains) is included and will be used automatically.\n\n"
                "To build your own from your HubSpot contacts export, run 'Build Knowledge Base.bat'. "
                "Your personal KB always takes priority and improves email accuracy over time."
            )
            s3_lbl.setWordWrap(True)
            s3_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        s3l.addWidget(s3_lbl)
        layout.addWidget(step3)

        # Step 4: Title filter
        step4 = QGroupBox("Step 4 — Title Filter")
        s4l = QVBoxLayout(step4)
        kw_count = len(self._get_active_include_keywords())
        s4_lbl = QLabel(
            f"{kw_count} default O&G keywords are active (geologist, engineer, VP, director…).\n"
            "Click 'Configure Titles' in the Lead Scout tab to customize."
        )
        s4_lbl.setWordWrap(True)
        s4_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        s4l.addWidget(s4_lbl)
        layout.addWidget(step4)

        # Step 5: First company
        step3 = QGroupBox("Step 5 — Add Your First Company")
        s3l = QHBoxLayout(step3)
        self._ob_company_edit = QLineEdit()
        self._ob_company_edit.setPlaceholderText("e.g.  EOG Resources")
        s3l.addWidget(self._ob_company_edit, stretch=1)
        layout.addWidget(step3)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_skip = QPushButton("Skip")
        btn_skip.clicked.connect(dlg.reject)
        btn_start = QPushButton("Get Started →")
        btn_start.setObjectName("btn_start")
        btn_start.setMinimumHeight(38)
        btn_start.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_skip)
        btn_row.addStretch()
        btn_row.addWidget(btn_start)
        layout.addLayout(btn_row)

        if dlg.exec() == QDialog.Accepted:
            out = self._ob_out_edit.text().strip()
            if out:
                self._settings["output_dir"] = out
                self._save_settings()
            company = self._ob_company_edit.text().strip()
            if company and not self._in_queue(company):
                self._enqueue(company)
                self._input.setText(company)
                self._input.clear()

    @staticmethod
    def _make_separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    # ── Completeness + search updates on new contact ──────────────────────────

    def _on_contact_added_update_ui(self, contact: dict):
        """Called after a contact is added — update search company filter + completeness."""
        company = contact.get("company", "")
        if company:
            existing = [self._search_company.itemText(i)
                        for i in range(self._search_company.count())]
            if company not in existing:
                self._search_company.addItem(company)
        self._update_completeness_label()
