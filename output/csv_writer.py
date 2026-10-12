import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FIELDS = [
    "first_name",
    "last_name",
    "title",
    "company",
    "fit",
    "discipline",
    "location",
    "basin",
    "linkedin_url",
    "emp_status",
    "date_pulled",
    "source",
    "email",
    "email_status",
    "email_source",
]


# The column order that was written underneath old-format headers (the cause of the shifted rows).
# Frozen on purpose: it must never change when FIELDS gains columns, or repairs would misread rows.
_SHIFTED_LAYOUT = ["first_name", "last_name", "title", "company", "location", "basin", "linkedin_url",
                   "emp_status", "date_pulled", "email", "email_status", "email_source"]


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


def _split_name(name: str) -> tuple[str, str]:
    from scraper.parser import _extract_name
    parts = _extract_name(name or "").split()
    return (parts[0] if parts else ""), (parts[-1] if len(parts) > 1 else "")


def migrate_file(path: Path) -> int:
    """
    Bring an existing contacts CSV to the current FIELDS layout. Files created by older
    versions had the header  name,title,company,...,source,date_pulled ; appending new-layout
    rows underneath it shifted every column (first name under "name", last name under "title",
    job title under "company"...). Both kinds of row are repaired here.
    Returns the number of rows that were column-shifted. A backup copy is kept next to the file.
    """
    if not path.exists() or path.stat().st_size == 0:
        return 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        raw = list(csv.reader(f))
    if not raw or raw[0] == FIELDS:
        return 0
    header, body = raw[0], raw[1:]
    fixed, shifted = [], 0
    for row in body:
        if not any(cell.strip() for cell in row):
            continue
        old = dict(zip(header, row))
        looks_shifted = len(row) > len(header) or str(old.get("source", "")).startswith("http")
        if looks_shifted:
            shifted += 1
            new = dict(zip(_SHIFTED_LAYOUT, row))     # written positionally under the old header
        else:
            first, last = _split_name(old.get("name", "")) if "name" in old else ("", "")
            new = {**{k: old.get(k, "") for k in FIELDS}}
            new["first_name"] = new["first_name"] or first
            new["last_name"] = new["last_name"] or last
        fixed.append({k: new.get(k, "") or "" for k in FIELDS})

    import shutil, time
    backup_dir = path.parent / "backup_pre_migration"
    backup_dir.mkdir(exist_ok=True)
    shutil.copy2(path, backup_dir / f"{path.stem}.{time.strftime('%Y%m%d_%H%M%S')}{path.suffix}")
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(fixed)
    tmp.replace(path)
    logger.info("Migrated %s to current layout (%d rows, %d were column-shifted)", path.name, len(fixed), shifted)
    return shifted


def _append_row(path: Path, contact: dict):
    try:
        migrate_file(path)
    except Exception as e:
        logger.error(f"Could not migrate {path}: {e}")
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
