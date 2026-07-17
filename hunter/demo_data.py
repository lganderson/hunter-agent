"""Load committed fictional demo data into the local Hunter database."""

import json
from pathlib import Path

from . import chat_history, paths, repository, schema, sqlite_store, storage


DEMO_DATA_FILE = Path(__file__).resolve().parents[1] / "demo" / "hunter-demo-data.json"

COUNT_KEYS = [
    "applications",
    "actions",
    "contacts",
    "interviews",
    "companies",
    "company_contacts",
    "company_career_sources",
    "company_posting_candidates",
    "posting_notes",
]


def _clean_rows(rows, fields):
    return [{field: storage.clean(row.get(field, "")) for field in fields} for row in rows]


def _existing_counts():
    return {
        "applications": len(repository.read_applications()),
        "actions": len(repository.read_actions()),
        "contacts": len(repository.read_contacts()),
        "interviews": len(sqlite_store.read_table("interviews")),
        "companies": len(repository.read_companies()),
        "company_contacts": len(repository.read_company_contacts()),
        "company_career_sources": len(repository.read_company_career_sources()),
        "company_posting_candidates": len(repository.read_company_posting_candidates()),
        "posting_notes": _posting_note_count(),
    }


def _posting_note_count():
    with sqlite_store.connect() as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM posting_notes").fetchone()
    return int(row["total"])


def _clear_related_tables():
    chat_history.clear_messages()
    repository.clear_company_career_scans()
    with sqlite_store.connect() as connection:
        connection.execute("DELETE FROM application_contacts")
        connection.execute("DELETE FROM company_contacts")
        connection.execute("DELETE FROM posting_notes")
    repository.write_company_career_sources([])
    repository.write_company_posting_candidates([])
    repository.write_companies([])
    repository.write_applications([])
    repository.write_actions([])
    repository.write_contacts([])
    sqlite_store.write_table("interviews", [])


def _replace_company_contacts(rows):
    with sqlite_store.connect() as connection:
        connection.execute("DELETE FROM company_contacts")
        for row in rows:
            connection.execute(
                "INSERT INTO company_contacts(company_id, contact_id, created_at) VALUES (?, ?, ?)",
                (
                    storage.clean(row.get("company_id", "")).upper(),
                    storage.clean(row.get("contact_id", "")).upper(),
                    storage.clean(row.get("created_at", "")),
                ),
            )


def _replace_posting_notes(rows):
    with sqlite_store.connect() as connection:
        connection.execute("DELETE FROM posting_notes")
        for row in rows:
            connection.execute(
                "INSERT INTO posting_notes(application_id, path, content, updated_at) VALUES (?, ?, ?, ?)",
                (
                    storage.clean(row.get("application_id", "")).upper(),
                    storage.clean(row.get("path", "")),
                    row.get("content", "") or "",
                    storage.clean(row.get("updated_at", "")),
                ),
            )


def read_demo_data(path=DEMO_DATA_FILE):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def load_demo_data(overwrite=False, path=DEMO_DATA_FILE):
    sqlite_store.initialize()
    existing = _existing_counts()
    if not overwrite and any(existing.values()):
        occupied = ", ".join(f"{key}={count}" for key, count in existing.items() if count)
        raise ValueError(
            "Local Hunter data already exists. Re-run with --overwrite to replace it. "
            f"Existing rows: {occupied}"
        )

    payload = read_demo_data(path)
    _clear_related_tables()
    repository.write_companies(_clean_rows(payload.get("companies", []), schema.COMPANY_FIELDS))
    repository.write_contacts(_clean_rows(payload.get("contacts", []), schema.CONTACT_FIELDS))
    repository.write_applications(_clean_rows(payload.get("applications", []), schema.APPLICATION_FIELDS))
    repository.write_actions(_clean_rows(payload.get("actions", []), schema.ACTION_FIELDS))
    sqlite_store.write_table("interviews", _clean_rows(payload.get("interviews", []), schema.INTERVIEW_FIELDS))
    repository.write_company_career_sources(
        _clean_rows(payload.get("company_career_sources", []), schema.COMPANY_CAREER_SOURCE_FIELDS)
    )
    repository.write_company_posting_candidates(
        _clean_rows(payload.get("company_posting_candidates", []), schema.COMPANY_POSTING_CANDIDATE_FIELDS)
    )
    _replace_company_contacts(payload.get("company_contacts", []))
    _replace_posting_notes(payload.get("posting_notes", []))

    counts = _existing_counts()
    return {key: counts.get(key, 0) for key in COUNT_KEYS}


def print_counts(counts):
    for key in COUNT_KEYS:
        print(f"{key}: {counts.get(key, 0)}")
    print(f"Local database: {paths.SQLITE_DB}")
