"""Zero-dependency CSV storage helpers for Hunter."""

import csv
import re
from datetime import date, datetime

from . import paths, schema


def today_iso():
    return date.today().isoformat()


def normalize_date(value):
    if not value:
        return ""
    value = value.strip()
    if value.lower() == "today":
        return today_iso()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise SystemExit(f"Invalid date '{value}'. Use YYYY-MM-DD or 'today'.")


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def clean(value):
    if value is None:
        return ""
    return str(value).replace("\n", " ").strip()


def split_tags(value):
    tags = []
    for raw_tag in re.split(r"[,;]", value or ""):
        tag = re.sub(r"[^a-z0-9]+", "-", raw_tag.lower()).strip("-")
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def normalize_tags(value):
    return ",".join(split_tags(value))


def merge_tags(existing, added):
    tags = split_tags(existing)
    for tag in split_tags(added):
        if tag not in tags:
            tags.append(tag)
    return ",".join(tags)


def remove_tags(existing, removed):
    remove_set = set(split_tags(removed))
    return ",".join(tag for tag in split_tags(existing) if tag not in remove_set)


def ensure_csv(path, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()


def ensure_workspace():
    for directory in paths.WORKSPACE_DIRS:
        (paths.ROOT / directory).mkdir(parents=True, exist_ok=True)

    ensure_csv(paths.APPLICATIONS, schema.APPLICATION_FIELDS)
    ensure_csv(paths.CONTACTS, schema.CONTACT_FIELDS)
    ensure_csv(paths.INTERVIEWS, schema.INTERVIEW_FIELDS)
    ensure_csv(paths.ACTIONS, schema.ACTION_FIELDS)


def read_rows(path, fields):
    ensure_csv(path, fields)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            normalized = {field: clean(row.get(field, "")) for field in fields}
            rows.append(normalized)
    return rows


def write_rows(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: clean(row.get(field, "")) for field in fields})


def read_applications():
    return read_rows(paths.APPLICATIONS, schema.APPLICATION_FIELDS)


def write_applications(rows):
    write_rows(paths.APPLICATIONS, schema.APPLICATION_FIELDS, rows)


def read_actions():
    return read_rows(paths.ACTIONS, schema.ACTION_FIELDS)


def write_actions(rows):
    write_rows(paths.ACTIONS, schema.ACTION_FIELDS, rows)
