#!/usr/bin/env python3
"""Small zero-dependency helper for Hunter tracker commands."""

import argparse
import csv
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORTS))

from hunter import paths as hunter_paths
from hunter import repository
from hunter import schema as hunter_schema


ROOT = hunter_paths.ROOT
DATA_DIR = hunter_paths.DATA_DIR
APPLICATIONS = hunter_paths.APPLICATIONS
CONTACTS = hunter_paths.CONTACTS
INTERVIEWS = hunter_paths.INTERVIEWS
ACTIONS = hunter_paths.ACTIONS

APPLICATION_FIELDS = hunter_schema.APPLICATION_FIELDS
CONTACT_FIELDS = hunter_schema.CONTACT_FIELDS
INTERVIEW_FIELDS = hunter_schema.INTERVIEW_FIELDS
ACTION_FIELDS = hunter_schema.ACTION_FIELDS

COMPLETED_ACTION_STATUSES = hunter_schema.COMPLETED_ACTION_STATUSES
DEFAULT_STAGE = hunter_schema.DEFAULT_STAGE
DEFAULT_OUTCOME = hunter_schema.DEFAULT_OUTCOME
DEFAULT_PRIORITY = hunter_schema.DEFAULT_PRIORITY


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
    for directory in hunter_paths.WORKSPACE_DIRS:
        (ROOT / directory).mkdir(parents=True, exist_ok=True)

    if repository.using_sqlite():
        return

    ensure_csv(APPLICATIONS, APPLICATION_FIELDS)
    ensure_csv(CONTACTS, CONTACT_FIELDS)
    ensure_csv(INTERVIEWS, INTERVIEW_FIELDS)
    ensure_csv(ACTIONS, ACTION_FIELDS)


def read_rows(path, fields):
    if path == APPLICATIONS and fields == APPLICATION_FIELDS and repository.using_sqlite():
        return repository.read_applications()
    if path == ACTIONS and fields == ACTION_FIELDS and repository.using_sqlite():
        return repository.read_actions()
    ensure_csv(path, fields)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            normalized = {field: clean(row.get(field, "")) for field in fields}
            rows.append(normalized)
    return rows


def write_rows(path, fields, rows):
    if path == APPLICATIONS and fields == APPLICATION_FIELDS and repository.using_sqlite():
        repository.write_applications(rows)
        return
    if path == ACTIONS and fields == ACTION_FIELDS and repository.using_sqlite():
        repository.write_actions(rows)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: clean(row.get(field, "")) for field in fields})


def next_application_id(rows):
    highest = 0
    for row in rows:
        match = re.fullmatch(r"A(\d+)", row.get("id", "").upper())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"A{highest + 1:04d}"


def find_application(rows, app_id):
    wanted = app_id.strip().upper()
    for row in rows:
        if row.get("id", "").upper() == wanted:
            return row
    raise SystemExit(f"No application found with id {app_id}.")


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def load_template(name):
    path = ROOT / "templates" / name
    if not path.exists():
        raise SystemExit(f"Missing template: {path}")
    return path.read_text(encoding="utf-8")


def render_template(template, row):
    output = template
    for field in APPLICATION_FIELDS:
        output = output.replace("{{" + field + "}}", row.get(field, ""))
    return output


def make_posting_note(row, force=False):
    filename = f"{row['id'].lower()}-{slugify(row['company'])}-{slugify(row['role'])}.md"
    relative_path = Path("postings") / filename

    if repository.using_sqlite():
        posting_file = row.get("posting_file") or str(relative_path)
        existing = repository.read_posting_note(row.get("id", ""))
        if existing and not force:
            row["posting_file"] = existing.get("path") or posting_file
            return row["posting_file"], False
        rendered = render_template(load_template("job-posting.md"), row)
        row["posting_file"] = posting_file
        repository.write_posting_note(row["id"], row["posting_file"], rendered)
        return row["posting_file"], True

    if row.get("posting_file"):
        existing = ROOT / row["posting_file"]
        if existing.exists() and not force:
            return row["posting_file"], False

    target = ROOT / relative_path
    if target.exists() and not force:
        row["posting_file"] = str(relative_path)
        return str(relative_path), False

    rendered = render_template(load_template("job-posting.md"), row)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    row["posting_file"] = str(relative_path)
    return str(relative_path), True


def ellipsize(value, width):
    text = value or ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "..."


def print_table(rows, fields):
    if not rows:
        print("No applications found.")
        return

    widths = {}
    for field in fields:
        max_value = max([len(field)] + [len(row.get(field, "")) for row in rows])
        widths[field] = min(max_value, 34)

    header = "  ".join(field.upper().ljust(widths[field]) for field in fields)
    print(header)
    print("  ".join("-" * widths[field] for field in fields))
    for row in rows:
        print("  ".join(ellipsize(row.get(field, ""), widths[field]).ljust(widths[field]) for field in fields))


def active_rows(rows):
    return [row for row in rows if row.get("stage", "").lower() != "closed"]


def sort_for_action(rows):
    def key(row):
        due = parse_date(row.get("next_action_date")) or date.max
        priority_rank = {"high": 0, "medium": 1, "low": 2}.get(row.get("priority", "").lower(), 3)
        return due, priority_rank, row.get("company", "").lower(), row.get("role", "").lower()

    return sorted(rows, key=key)


def cmd_init(_args):
    ensure_workspace()
    print(f"Initialized legacy CSV workspace at {ROOT}")


def cmd_add(args):
    ensure_workspace()
    rows = read_rows(APPLICATIONS, APPLICATION_FIELDS)
    row = {field: "" for field in APPLICATION_FIELDS}
    row.update(
        {
            "id": next_application_id(rows),
            "company": clean(args.company),
            "role": clean(args.role),
            "location": clean(args.location),
            "work_mode": clean(args.work_mode),
            "source": clean(args.source),
            "source_url": clean(args.url),
            "compensation": clean(args.compensation),
            "stage": clean(args.stage or DEFAULT_STAGE),
            "outcome": clean(args.outcome or DEFAULT_OUTCOME),
            "tags": normalize_tags(args.tags),
            "priority": clean(args.priority or DEFAULT_PRIORITY),
            "date_found": normalize_date(args.date_found or "today"),
            "date_applied": normalize_date(args.date_applied),
            "next_action": clean(args.next_action),
            "next_action_date": normalize_date(args.next_action_date),
            "contact": clean(args.contact),
            "resume_version": clean(args.resume_version),
            "cover_letter": clean(args.cover_letter),
            "notes": clean(args.notes),
        }
    )

    if args.make_note:
        path, created = make_posting_note(row)
        print(("Created" if created else "Reused") + f" posting note: {path}")

    rows.append(row)
    write_rows(APPLICATIONS, APPLICATION_FIELDS, rows)
    print(f"Added {row['id']}: {row['company']} - {row['role']}")


def cmd_list(args):
    ensure_workspace()
    rows = read_rows(APPLICATIONS, APPLICATION_FIELDS)
    if not args.all:
        rows = active_rows(rows)
    if args.stage:
        rows = [row for row in rows if row.get("stage", "").lower() == args.stage.lower()]
    if args.outcome:
        rows = [row for row in rows if row.get("outcome", "").lower() == args.outcome.lower()]
    if args.tag:
        tag = normalize_tags(args.tag)
        rows = [row for row in rows if tag and tag in split_tags(row.get("tags", ""))]
    if args.company:
        company = args.company.lower()
        rows = [row for row in rows if company in row.get("company", "").lower()]
    rows = sort_for_action(rows)
    if args.limit:
        rows = rows[: args.limit]
    print_table(rows, ["id", "company", "role", "stage", "outcome", "tags", "priority", "next_action_date", "next_action"])


def cmd_due(args):
    ensure_workspace()
    rows = active_rows(read_rows(APPLICATIONS, APPLICATION_FIELDS))
    cutoff = date.today() + timedelta(days=args.within)
    due_rows = []
    for row in rows:
        due = parse_date(row.get("next_action_date"))
        if due and due <= cutoff:
            due_rows.append(row)
    print_table(sort_for_action(due_rows), ["id", "company", "role", "stage", "next_action_date", "next_action"])


def cmd_stats(_args):
    ensure_workspace()
    rows = read_rows(APPLICATIONS, APPLICATION_FIELDS)
    active = active_rows(rows)
    print(f"Total applications: {len(rows)}")
    print(f"Active applications: {len(active)}")
    print()

    for label, field in [("By stage", "stage"), ("By outcome", "outcome")]:
        print(label)
        counts = Counter(row.get(field) or "(blank)" for row in rows)
        if not counts:
            print("  none")
        for value, count in sorted(counts.items()):
            print(f"  {value}: {count}")
        print()

    print("By tag")
    tag_counts = Counter(tag for row in rows for tag in split_tags(row.get("tags", "")))
    if not tag_counts:
        print("  none")
    for value, count in sorted(tag_counts.items()):
        print(f"  {value}: {count}")
    print()

    due_today = []
    overdue = []
    today = date.today()
    for row in active:
        due = parse_date(row.get("next_action_date"))
        if due and due < today:
            overdue.append(row)
        elif due and due == today:
            due_today.append(row)
    print(f"Overdue next actions: {len(overdue)}")
    print(f"Due today: {len(due_today)}")


def cmd_update(args):
    ensure_workspace()
    rows = read_rows(APPLICATIONS, APPLICATION_FIELDS)
    row = find_application(rows, args.application_id)

    updates = {
        "company": args.company,
        "role": args.role,
        "location": args.location,
        "work_mode": args.work_mode,
        "source": args.source,
        "source_url": args.url,
        "compensation": args.compensation,
        "stage": args.stage,
        "outcome": args.outcome,
        "tags": normalize_tags(args.tags) if args.tags is not None else None,
        "priority": args.priority,
        "date_found": normalize_date(args.date_found) if args.date_found else None,
        "date_applied": normalize_date(args.date_applied) if args.date_applied else None,
        "next_action": args.next_action,
        "next_action_date": normalize_date(args.next_action_date) if args.next_action_date else None,
        "contact": args.contact,
        "resume_version": args.resume_version,
        "cover_letter": args.cover_letter,
        "posting_file": args.posting_file,
        "notes": args.notes,
    }

    for field, value in updates.items():
        if value is not None:
            row[field] = clean(value)

    if args.add_note:
        entry = f"{today_iso()}: {clean(args.add_note)}"
        row["notes"] = f"{row['notes']} | {entry}" if row.get("notes") else entry

    if args.add_tag:
        row["tags"] = merge_tags(row.get("tags", ""), args.add_tag)

    if args.remove_tag:
        row["tags"] = remove_tags(row.get("tags", ""), args.remove_tag)

    if args.make_note:
        path, created = make_posting_note(row, force=args.force)
        print(("Created" if created else "Reused") + f" posting note: {path}")

    write_rows(APPLICATIONS, APPLICATION_FIELDS, rows)
    print(f"Updated {row['id']}: {row['company']} - {row['role']}")


def cmd_make_note(args):
    ensure_workspace()
    rows = read_rows(APPLICATIONS, APPLICATION_FIELDS)
    row = find_application(rows, args.application_id)
    path, created = make_posting_note(row, force=args.force)
    write_rows(APPLICATIONS, APPLICATION_FIELDS, rows)
    print(("Created" if created else "Reused") + f" posting note: {path}")


def build_parser():
    parser = argparse.ArgumentParser(description="Manage local Hunter tracker data.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a legacy CSV workspace.")
    init_parser.set_defaults(func=cmd_init)

    add_parser = subparsers.add_parser("add", help="Add an application or prospect.")
    add_parser.add_argument("--company", required=True)
    add_parser.add_argument("--role", required=True)
    add_parser.add_argument("--location", default="")
    add_parser.add_argument("--work-mode", default="")
    add_parser.add_argument("--source", default="")
    add_parser.add_argument("--url", default="")
    add_parser.add_argument("--compensation", default="")
    add_parser.add_argument("--stage", default=DEFAULT_STAGE)
    add_parser.add_argument("--outcome", default=DEFAULT_OUTCOME)
    add_parser.add_argument("--tags", default="", help="Comma-separated tags such as no-reply,first-interview.")
    add_parser.add_argument("--priority", default=DEFAULT_PRIORITY)
    add_parser.add_argument("--date-found", default="today")
    add_parser.add_argument("--date-applied", default="")
    add_parser.add_argument("--next-action", default="")
    add_parser.add_argument("--next-action-date", default="")
    add_parser.add_argument("--contact", default="")
    add_parser.add_argument("--resume-version", default="")
    add_parser.add_argument("--cover-letter", default="")
    add_parser.add_argument("--notes", default="")
    add_parser.add_argument("--make-note", action="store_true")
    add_parser.set_defaults(func=cmd_add)

    list_parser = subparsers.add_parser("list", help="List active applications.")
    list_parser.add_argument("--all", action="store_true", help="Include closed applications.")
    list_parser.add_argument("--stage", default="")
    list_parser.add_argument("--outcome", default="")
    list_parser.add_argument("--tag", default="")
    list_parser.add_argument("--company", default="")
    list_parser.add_argument("--limit", type=int, default=0)
    list_parser.set_defaults(func=cmd_list)

    due_parser = subparsers.add_parser("due", help="List applications with due next actions.")
    due_parser.add_argument("--within", type=int, default=7, help="Days from today to include.")
    due_parser.set_defaults(func=cmd_due)

    stats_parser = subparsers.add_parser("stats", help="Show pipeline counts.")
    stats_parser.set_defaults(func=cmd_stats)

    update_parser = subparsers.add_parser("update", help="Update an existing application.")
    update_parser.add_argument("application_id")
    update_parser.add_argument("--company")
    update_parser.add_argument("--role")
    update_parser.add_argument("--location")
    update_parser.add_argument("--work-mode")
    update_parser.add_argument("--source")
    update_parser.add_argument("--url")
    update_parser.add_argument("--compensation")
    update_parser.add_argument("--stage")
    update_parser.add_argument("--outcome")
    update_parser.add_argument("--tags", help="Replace the full comma-separated tag list.")
    update_parser.add_argument("--add-tag", help="Add one or more comma-separated tags.")
    update_parser.add_argument("--remove-tag", help="Remove one or more comma-separated tags.")
    update_parser.add_argument("--priority")
    update_parser.add_argument("--date-found")
    update_parser.add_argument("--date-applied")
    update_parser.add_argument("--next-action")
    update_parser.add_argument("--next-action-date")
    update_parser.add_argument("--contact")
    update_parser.add_argument("--resume-version")
    update_parser.add_argument("--cover-letter")
    update_parser.add_argument("--posting-file")
    update_parser.add_argument("--notes")
    update_parser.add_argument("--add-note")
    update_parser.add_argument("--make-note", action="store_true")
    update_parser.add_argument("--force", action="store_true", help="Overwrite generated posting note.")
    update_parser.set_defaults(func=cmd_update)

    note_parser = subparsers.add_parser("make-note", help="Create or reuse a posting note.")
    note_parser.add_argument("application_id")
    note_parser.add_argument("--force", action="store_true", help="Overwrite generated posting note.")
    note_parser.set_defaults(func=cmd_make_note)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
