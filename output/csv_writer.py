import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FIELDS = [
    "first_name",
    "last_name",
    "title",
    "company",
    "location",
    "basin",
    "linkedin_url",
    "emp_status",
    "date_pulled",
    "email",
    "email_status",
    "email_source",
]


def get_output_dir() -> Path:
    path = Path.home() / "LeadScout_Output"
    path.mkdir(exist_ok=True)
    return path


def append_contact(contact: dict, company_name: str):
    """Write one contact to the per-company CSV and the master CSV."""
    per_company = get_output_dir() / f"{_safe_filename(company_name)}_contacts.csv"
    master      = get_output_dir() / "all_contacts.csv"
    for path in (per_company, master):
        _append_row(path, contact)


def export_contacts(contacts: list[dict], filepath: str | Path):
    """Write a full contact list to a user-chosen file."""
    path = Path(filepath)
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows([{k: c.get(k, "") for k in FIELDS} for c in contacts])
    except Exception as e:
        logger.error(f"Export failed: {e}")


def _append_row(path: Path, contact: dict):
    write_header = not path.exists()
    try:
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow({k: contact.get(k, "") for k in FIELDS})
    except Exception as e:
        logger.error(f"Failed to write row to {path}: {e}")


def _safe_filename(name: str) -> str:
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "-")
    return name.replace(" ", "_")
