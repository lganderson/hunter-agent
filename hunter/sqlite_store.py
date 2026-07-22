"""SQLite storage backend for Hunter.

The database uses plain TEXT columns for the CSV-backed entities so import and
export stay lossless while the app gets local transactional persistence.
"""

import hashlib
import json
import sqlite3
from datetime import datetime

from . import paths, schema, storage


TABLES = {
    "applications": (paths.APPLICATIONS, schema.APPLICATION_FIELDS),
    "contacts": (paths.CONTACTS, schema.CONTACT_FIELDS),
    "interviews": (paths.INTERVIEWS, schema.INTERVIEW_FIELDS),
    "actions": (paths.ACTIONS, schema.ACTION_FIELDS),
}

LEGACY_CLOSED_STATUSES = {"rejected", "withdrawn", "archived", "offer_declined", "declined", "accepted"}
LEGACY_STAGE_MAP = {
    "closed-posting": "closed",
    "research": schema.DEFAULT_STAGE,
}
LEGACY_STATUS_STAGE_MAP = {
    "applied": "application-submitted",
    "interviewing": "recruiter-screen",
    "offer": "offer-review",
    "prospect": schema.DEFAULT_STAGE,
    "saved": schema.DEFAULT_STAGE,
}
LEGACY_STATUS_OUTCOME_MAP = {
    "offer_declined": "declined",
}


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def connect():
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(paths.SQLITE_DB, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    return connection


def quote_identifier(value):
    if value not in TABLES:
        raise ValueError(f"Unknown table: {value}")
    return f'"{value}"'


def quote_field(value, fields):
    if value not in fields:
        raise ValueError(f"Unknown field: {value}")
    return f'"{value}"'


def create_table_sql(table, fields):
    columns = []
    for field in fields:
        if field == "id":
            columns.append('"id" TEXT PRIMARY KEY')
        else:
            columns.append(f'"{field}" TEXT NOT NULL DEFAULT ""')
    return f"CREATE TABLE IF NOT EXISTS {quote_identifier(table)} ({', '.join(columns)})"


def table_columns(connection, table):
    return [row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()]


def normalize_legacy_application(row):
    normalized = {}
    legacy_status = storage.clean(row.get("status", "")).lower()
    legacy_stage = storage.clean(row.get("stage", "")).lower()
    stage = LEGACY_STAGE_MAP.get(legacy_stage, legacy_stage) or LEGACY_STATUS_STAGE_MAP.get(legacy_status, schema.DEFAULT_STAGE)
    outcome = storage.clean(row.get("outcome", "")).lower()

    if legacy_status in LEGACY_CLOSED_STATUSES:
        stage = "closed"
        outcome = LEGACY_STATUS_OUTCOME_MAP.get(legacy_status, legacy_status)
    elif legacy_stage == "closed-posting":
        stage = "closed"
        outcome = "closed-posting"

    if stage != "closed":
        outcome = ""
    elif outcome not in schema.TERMINAL_OUTCOMES:
        outcome = "archived"

    for field in schema.APPLICATION_FIELDS:
        if field == "stage":
            normalized[field] = stage
        elif field == "outcome":
            normalized[field] = outcome
        else:
            normalized[field] = storage.clean(row.get(field, ""))
    return normalized


def rebuild_table(connection, table, fields, rows):
    temporary = f"{table}_new"
    connection.execute(f"DROP TABLE IF EXISTS {temporary}")
    columns = []
    for field in fields:
        if field == "id":
            columns.append('"id" TEXT PRIMARY KEY')
        else:
            columns.append(f'"{field}" TEXT NOT NULL DEFAULT ""')
    connection.execute(f"CREATE TABLE {temporary} ({', '.join(columns)})")
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    values = [[storage.clean(row.get(field, "")) for field in fields] for row in rows]
    if values:
        connection.executemany(
            f"INSERT INTO {temporary} ({quoted_fields}) VALUES ({placeholders})",
            values,
        )
    connection.execute(f"DROP TABLE {table}")
    connection.execute(f"ALTER TABLE {temporary} RENAME TO {table}")


def migrate_applications_schema(connection):
    existing = table_columns(connection, "applications")
    if not existing:
        return
    if existing == schema.APPLICATION_FIELDS:
        return
    rows = [
        {column: storage.clean(row[column]) for column in existing}
        for row in connection.execute("SELECT * FROM applications ORDER BY id").fetchall()
    ]
    normalized = [normalize_legacy_application(row) for row in rows]
    rebuild_table(connection, "applications", schema.APPLICATION_FIELDS, normalized)


def normalize_action_type(value):
    cleaned = storage.clean(value).lower().replace("_", "-")
    return schema.ACTION_TYPE_ALIASES.get(cleaned, cleaned)


def normalize_action_status(value):
    cleaned = storage.clean(value).lower()
    return schema.ACTION_STATUS_ALIASES.get(cleaned, cleaned)


def migrate_actions_schema(connection):
    existing = table_columns(connection, "actions")
    if not existing:
        return
    rows = [
        {column: storage.clean(row[column]) for column in existing}
        for row in connection.execute("SELECT * FROM actions ORDER BY id").fetchall()
    ]
    normalized = []
    changed = existing != schema.ACTION_FIELDS
    for row in rows:
        next_row = {}
        for field in schema.ACTION_FIELDS:
            if field == "type":
                value = normalize_action_type(row.get("type", ""))
                changed = changed or value != row.get("type", "")
            elif field == "status":
                value = normalize_action_status(row.get("status", ""))
                changed = changed or value != row.get("status", "")
            else:
                value = storage.clean(row.get(field, ""))
            next_row[field] = value
        normalized.append(next_row)
    if changed:
        rebuild_table(connection, "actions", schema.ACTION_FIELDS, normalized)


def create_workflow_tables(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS workflow_stages ("
        "id TEXT PRIMARY KEY, "
        "label TEXT NOT NULL DEFAULT '', "
        "sort_order TEXT NOT NULL DEFAULT '', "
        "is_terminal TEXT NOT NULL DEFAULT '', "
        "is_active TEXT NOT NULL DEFAULT '1'"
        ")"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS workflow_action_types ("
        "id TEXT PRIMARY KEY, "
        "label TEXT NOT NULL DEFAULT '', "
        "description TEXT NOT NULL DEFAULT '', "
        "default_priority TEXT NOT NULL DEFAULT '', "
        "default_due_days TEXT NOT NULL DEFAULT '', "
        "allowed_stages TEXT NOT NULL DEFAULT '', "
        "sort_order TEXT NOT NULL DEFAULT '', "
        "is_active TEXT NOT NULL DEFAULT '1'"
        ")"
    )


def ensure_text_columns(connection, table, columns):
    existing = set(table_columns(connection, table))
    for column in columns:
        if column not in existing:
            connection.execute(f'ALTER TABLE {table} ADD COLUMN "{column}" TEXT NOT NULL DEFAULT ""')


def seed_workflow_defaults(connection):
    for row in schema.DEFAULT_WORKFLOW_STAGES:
        connection.execute(
            "INSERT INTO workflow_stages(id, label, sort_order, is_terminal, is_active) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
            tuple(row.get(field, "") for field in schema.WORKFLOW_STAGE_FIELDS),
        )
    for row in schema.DEFAULT_WORKFLOW_ACTION_TYPES:
        connection.execute(
            "INSERT INTO workflow_action_types("
            "id, label, description, default_priority, default_due_days, allowed_stages, sort_order, is_active"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
            tuple(row.get(field, "") for field in schema.WORKFLOW_ACTION_TYPE_FIELDS),
        )


def initialize():
    for directory in paths.WORKSPACE_DIRS:
        (paths.ROOT / directory).mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        for table, (_, fields) in TABLES.items():
            connection.execute(create_table_sql(table, fields))
        migrate_applications_schema(connection)
        migrate_actions_schema(connection)
        create_workflow_tables(connection)
        seed_workflow_defaults(connection)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS meta ("
            "key TEXT PRIMARY KEY, "
            "value TEXT NOT NULL"
            ")"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "entity_type TEXT NOT NULL, "
            "entity_id TEXT NOT NULL, "
            "event_type TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "data_json TEXT NOT NULL"
            ")"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS posting_notes ("
            "application_id TEXT PRIMARY KEY, "
            "path TEXT NOT NULL, "
            "content TEXT NOT NULL, "
            "updated_at TEXT NOT NULL"
            ")"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS posting_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "application_id TEXT NOT NULL, "
            "source_url TEXT NOT NULL DEFAULT '', "
            "final_url TEXT NOT NULL DEFAULT '', "
            "captured_at TEXT NOT NULL, "
            "http_status TEXT NOT NULL DEFAULT '', "
            "content_hash TEXT NOT NULL, "
            "content_text TEXT NOT NULL DEFAULT '', "
            "source_html TEXT NOT NULL DEFAULT '', "
            "warnings TEXT NOT NULL DEFAULT '', "
            "UNIQUE(application_id, content_hash)"
            ")"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS agent_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "role TEXT NOT NULL, "
            "content TEXT NOT NULL, "
            "tool_calls_json TEXT NOT NULL DEFAULT '[]', "
            "context_json TEXT NOT NULL DEFAULT '{}', "
            "created_at TEXT NOT NULL"
            ")"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS resume_versions ("
            "id TEXT PRIMARY KEY, "
            "application_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "guidance TEXT NOT NULL DEFAULT '', "
            "source_filename TEXT NOT NULL DEFAULT '', "
            "docx_path TEXT NOT NULL DEFAULT '', "
            "pdf_path TEXT NOT NULL DEFAULT '', "
            "changes_json TEXT NOT NULL DEFAULT '[]', "
            "warnings_json TEXT NOT NULL DEFAULT '[]'"
            ")"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS application_contacts ("
            "application_id TEXT NOT NULL, "
            "contact_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "PRIMARY KEY(application_id, contact_id)"
            ")"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS companies ("
            "id TEXT PRIMARY KEY, "
            "name TEXT NOT NULL DEFAULT '', "
            "aliases TEXT NOT NULL DEFAULT '', "
            "interest_status TEXT NOT NULL DEFAULT 'neutral', "
            "website TEXT NOT NULL DEFAULT '', "
            "careers_url TEXT NOT NULL DEFAULT '', "
            "notes TEXT NOT NULL DEFAULT '', "
            "last_checked_at TEXT NOT NULL DEFAULT '', "
            "last_check_status TEXT NOT NULL DEFAULT ''"
            ")"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS company_contacts ("
            "company_id TEXT NOT NULL, "
            "contact_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "PRIMARY KEY(company_id, contact_id)"
            ")"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS company_career_sources ("
            "company_id TEXT PRIMARY KEY, "
            "source_url TEXT NOT NULL DEFAULT '', "
            "platform_type TEXT NOT NULL DEFAULT '', "
            "config_json TEXT NOT NULL DEFAULT '', "
            "evidence TEXT NOT NULL DEFAULT '', "
            "discovered_at TEXT NOT NULL DEFAULT '', "
            "last_verified_at TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL DEFAULT '', "
            "notes TEXT NOT NULL DEFAULT ''"
            ")"
        )
        ensure_text_columns(connection, "company_career_sources", schema.COMPANY_CAREER_SOURCE_FIELDS)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS company_posting_candidates ("
            "id TEXT PRIMARY KEY, "
            "company_id TEXT NOT NULL, "
            "title TEXT NOT NULL DEFAULT '', "
            "url TEXT NOT NULL DEFAULT '', "
            "location TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL DEFAULT 'new', "
            "first_seen_at TEXT NOT NULL DEFAULT '', "
            "last_seen_at TEXT NOT NULL DEFAULT '', "
            "fit_score TEXT NOT NULL DEFAULT '', "
            "fit_summary TEXT NOT NULL DEFAULT '', "
            "fit_checked_at TEXT NOT NULL DEFAULT '', "
            "notes TEXT NOT NULL DEFAULT '', "
            "UNIQUE(company_id, url)"
            ")"
        )
        ensure_text_columns(connection, "company_posting_candidates", schema.COMPANY_POSTING_CANDIDATE_FIELDS)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS company_career_scans ("
            "company_id TEXT NOT NULL, "
            "checked_at TEXT NOT NULL, "
            "platform_type TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL DEFAULT '', "
            "requests_succeeded TEXT NOT NULL DEFAULT '', "
            "requests_failed TEXT NOT NULL DEFAULT '', "
            "extracted_count TEXT NOT NULL DEFAULT '', "
            "unique_candidate_count TEXT NOT NULL DEFAULT '', "
            "new_count TEXT NOT NULL DEFAULT '', "
            "recommended_count TEXT NOT NULL DEFAULT '', "
            "unavailable_count TEXT NOT NULL DEFAULT '', "
            "verification_count TEXT NOT NULL DEFAULT '', "
            "verification_skipped_count TEXT NOT NULL DEFAULT '', "
            "errors_json TEXT NOT NULL DEFAULT '[]', "
            "PRIMARY KEY(company_id, checked_at)"
            ")"
        )
        ensure_text_columns(connection, "company_career_scans", schema.COMPANY_CAREER_SCAN_FIELDS)
        connection.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', '7') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )


def is_initialized():
    if not paths.SQLITE_DB.exists():
        return False
    try:
        with connect() as connection:
            result = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='applications'"
            ).fetchone()
            return result is not None
    except sqlite3.DatabaseError:
        return False


def count_rows(table):
    with connect() as connection:
        return connection.execute(f"SELECT COUNT(*) AS total FROM {quote_identifier(table)}").fetchone()["total"]


def read_table(table):
    initialize()
    _, fields = TABLES[table]
    quoted_fields = ", ".join(quote_field(field, fields) for field in fields)
    with connect() as connection:
        rows = connection.execute(
            f"SELECT {quoted_fields} FROM {quote_identifier(table)} ORDER BY \"id\""
        ).fetchall()
    return [{field: storage.clean(row[field]) for field in fields} for row in rows]


def write_table(table, rows):
    initialize()
    _, fields = TABLES[table]
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(quote_field(field, fields) for field in fields)
    values = [[storage.clean(row.get(field, "")) for field in fields] for row in rows]
    with connect() as connection:
        connection.execute(f"DELETE FROM {quote_identifier(table)}")
        if values:
            connection.executemany(
                f"INSERT INTO {quote_identifier(table)} ({quoted_fields}) VALUES ({placeholders})",
                values,
            )


def upsert_table(table, rows):
    _, fields = TABLES[table]
    if not rows:
        return
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(quote_field(field, fields) for field in fields)
    updates = ", ".join(
        f'{quote_field(field, fields)}=excluded.{quote_field(field, fields)}'
        for field in fields
        if field != "id"
    )
    values = [[storage.clean(row.get(field, "")) for field in fields] for row in rows]
    with connect() as connection:
        connection.executemany(
            f"INSERT INTO {quote_identifier(table)} ({quoted_fields}) VALUES ({placeholders}) "
            f"ON CONFLICT(\"id\") DO UPDATE SET {updates}",
            values,
        )


def import_from_csv(overwrite=False):
    initialize()
    imported = {}
    for table, (path, fields) in TABLES.items():
        rows = storage.read_rows(path, fields)
        if overwrite:
            write_table(table, rows)
        else:
            if count_rows(table):
                raise ValueError(
                    f"SQLite table '{table}' already has data. Re-run with --overwrite to replace it."
                )
            upsert_table(table, rows)
        imported[table] = len(rows)
    return imported


def export_to_csv():
    initialize()
    exported = {}
    for table, (path, fields) in TABLES.items():
        rows = read_table(table)
        storage.write_rows(path, fields, rows)
        exported[table] = len(rows)
    return exported


def read_applications():
    return read_table("applications")


def write_applications(rows):
    write_table("applications", rows)


def read_actions():
    return read_table("actions")


def write_actions(rows):
    write_table("actions", rows)


def read_contacts():
    return read_table("contacts")


def write_contacts(rows):
    write_table("contacts", rows)


def read_companies():
    initialize()
    fields = schema.COMPANY_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with connect() as connection:
        rows = connection.execute(f"SELECT {quoted_fields} FROM companies ORDER BY lower(name), id").fetchall()
    return [{field: storage.clean(row[field]) for field in fields} for row in rows]


def write_companies(rows):
    initialize()
    fields = schema.COMPANY_FIELDS
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    values = [[storage.clean(row.get(field, "")) for field in fields] for row in rows]
    with connect() as connection:
        connection.execute("DELETE FROM companies")
        if values:
            connection.executemany(
                f"INSERT INTO companies ({quoted_fields}) VALUES ({placeholders})",
                values,
            )


def read_application_contacts():
    initialize()
    with connect() as connection:
        rows = connection.execute(
            "SELECT application_id, contact_id, created_at FROM application_contacts "
            "ORDER BY application_id, contact_id"
        ).fetchall()
    return [
        {
            "application_id": storage.clean(row["application_id"]),
            "contact_id": storage.clean(row["contact_id"]),
            "created_at": storage.clean(row["created_at"]),
        }
        for row in rows
    ]


def link_application_contact(application_id, contact_id):
    initialize()
    link = {
        "application_id": storage.clean(application_id).upper(),
        "contact_id": storage.clean(contact_id).upper(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO application_contacts(application_id, contact_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(application_id, contact_id) DO NOTHING",
            (link["application_id"], link["contact_id"], link["created_at"]),
        )
    return link


def unlink_application_contact(application_id, contact_id):
    initialize()
    application_id = storage.clean(application_id).upper()
    contact_id = storage.clean(contact_id).upper()
    with connect() as connection:
        connection.execute(
            "DELETE FROM application_contacts WHERE application_id = ? AND contact_id = ?",
            (application_id, contact_id),
        )
    return {"application_id": application_id, "contact_id": contact_id}


def read_company_contacts():
    initialize()
    with connect() as connection:
        rows = connection.execute(
            "SELECT company_id, contact_id, created_at FROM company_contacts "
            "ORDER BY company_id, contact_id"
        ).fetchall()
    return [
        {
            "company_id": storage.clean(row["company_id"]),
            "contact_id": storage.clean(row["contact_id"]),
            "created_at": storage.clean(row["created_at"]),
        }
        for row in rows
    ]


def link_company_contact(company_id, contact_id):
    initialize()
    link = {
        "company_id": storage.clean(company_id).upper(),
        "contact_id": storage.clean(contact_id).upper(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO company_contacts(company_id, contact_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(company_id, contact_id) DO NOTHING",
            (link["company_id"], link["contact_id"], link["created_at"]),
        )
    return link


def unlink_company_contact(company_id, contact_id):
    initialize()
    company_id = storage.clean(company_id).upper()
    contact_id = storage.clean(contact_id).upper()
    with connect() as connection:
        connection.execute(
            "DELETE FROM company_contacts WHERE company_id = ? AND contact_id = ?",
            (company_id, contact_id),
        )
    return {"company_id": company_id, "contact_id": contact_id}


def read_company_career_sources():
    initialize()
    fields = schema.COMPANY_CAREER_SOURCE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with connect() as connection:
        rows = connection.execute(
            f"SELECT {quoted_fields} FROM company_career_sources ORDER BY company_id"
        ).fetchall()
    return [{field: storage.clean(row[field]) for field in fields} for row in rows]


def write_company_career_sources(rows):
    initialize()
    fields = schema.COMPANY_CAREER_SOURCE_FIELDS
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    values = [[storage.clean(row.get(field, "")) for field in fields] for row in rows]
    with connect() as connection:
        connection.execute("DELETE FROM company_career_sources")
        if values:
            connection.executemany(
                f"INSERT INTO company_career_sources ({quoted_fields}) VALUES ({placeholders})",
                values,
            )


def read_company_posting_candidates():
    initialize()
    fields = schema.COMPANY_POSTING_CANDIDATE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with connect() as connection:
        rows = connection.execute(
            f"SELECT {quoted_fields} FROM company_posting_candidates ORDER BY company_id, status, title, url"
        ).fetchall()
    return [{field: storage.clean(row[field]) for field in fields} for row in rows]


def write_company_posting_candidates(rows):
    initialize()
    fields = schema.COMPANY_POSTING_CANDIDATE_FIELDS
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    values = [[storage.clean(row.get(field, "")) for field in fields] for row in rows]
    with connect() as connection:
        connection.execute("DELETE FROM company_posting_candidates")
        if values:
            connection.executemany(
                f"INSERT INTO company_posting_candidates ({quoted_fields}) VALUES ({placeholders})",
                values,
            )


def read_company_career_scans(company_id="", limit=200):
    initialize()
    fields = schema.COMPANY_CAREER_SCAN_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    query = f"SELECT {quoted_fields} FROM company_career_scans"
    params = []
    if storage.clean(company_id):
        query += " WHERE upper(company_id) = ?"
        params.append(storage.clean(company_id).upper())
    query += " ORDER BY checked_at DESC, company_id LIMIT ?"
    params.append(max(1, min(1000, int(limit or 200))))
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [{field: storage.clean(row[field]) for field in fields} for row in rows]


def write_company_career_scan(row):
    initialize()
    fields = schema.COMPANY_CAREER_SCAN_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f'"{field}"=excluded."{field}"' for field in fields[2:])
    values = [storage.clean(row.get(field, "")) for field in fields]
    with connect() as connection:
        connection.execute(
            f"INSERT INTO company_career_scans ({quoted_fields}) VALUES ({placeholders}) "
            f"ON CONFLICT(company_id, checked_at) DO UPDATE SET {updates}",
            values,
        )
    return {field: values[index] for index, field in enumerate(fields)}


def clear_company_career_scans():
    initialize()
    with connect() as connection:
        connection.execute("DELETE FROM company_career_scans")


def read_posting_note(application_id):
    initialize()
    with connect() as connection:
        row = connection.execute(
            "SELECT application_id, path, content, updated_at FROM posting_notes WHERE upper(application_id) = ?",
            (storage.clean(application_id).upper(),),
        ).fetchone()
    if not row:
        return None
    return {
        "application_id": storage.clean(row["application_id"]),
        "path": storage.clean(row["path"]),
        "content": row["content"] or "",
        "updated_at": storage.clean(row["updated_at"]),
    }


def write_posting_note(application_id, path, content):
    initialize()
    note = {
        "application_id": storage.clean(application_id).upper(),
        "path": storage.clean(path),
        "content": content or "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO posting_notes(application_id, path, content, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(application_id) DO UPDATE SET "
            "path=excluded.path, content=excluded.content, updated_at=excluded.updated_at",
            (note["application_id"], note["path"], note["content"], note["updated_at"]),
        )
    return note


def import_posting_notes_from_files(overwrite=False):
    initialize()
    imported = 0
    skipped = 0
    for app in read_applications():
        app_id = app.get("id", "")
        posting_file = app.get("posting_file", "")
        if not app_id or not posting_file:
            skipped += 1
            continue
        path = paths.ROOT / posting_file
        if not path.exists():
            skipped += 1
            continue
        if not overwrite and read_posting_note(app_id):
            skipped += 1
            continue
        write_posting_note(app_id, posting_file, path.read_text(encoding="utf-8"))
        imported += 1
    return {"imported": imported, "skipped": skipped}


def posting_note_count():
    initialize()
    with connect() as connection:
        return connection.execute("SELECT COUNT(*) AS total FROM posting_notes").fetchone()["total"]


def read_posting_snapshots(application_id=""):
    initialize()
    params = []
    where = ""
    wanted = storage.clean(application_id).upper()
    if wanted:
        where = " WHERE upper(application_id) = ?"
        params.append(wanted)
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, application_id, source_url, final_url, captured_at, http_status, "
            "content_hash, content_text, source_html, warnings FROM posting_snapshots"
            f"{where} ORDER BY captured_at DESC, id DESC",
            params,
        ).fetchall()
    preserved_fields = {"content_text", "source_html", "warnings"}
    return [
        {
            field: (row[field] or "") if field in preserved_fields else storage.clean(row[field])
            for field in schema.POSTING_SNAPSHOT_FIELDS
        }
        for row in rows
    ]


def write_posting_snapshot(application_id, values):
    application_id = storage.clean(application_id).upper()
    if not application_id:
        raise ValueError("Posting snapshot application id is required.")
    values = values or {}
    content_text = values.get("content_text", "") or ""
    source_html = values.get("source_html", "") or ""
    fingerprint = source_html or content_text or "|".join(
        storage.clean(values.get(field, ""))
        for field in ["source_url", "final_url", "http_status", "warnings"]
    )
    content_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    captured_at = storage.clean(values.get("captured_at")) or datetime.now().isoformat(timespec="seconds")
    row = {
        "application_id": application_id,
        "source_url": storage.clean(values.get("source_url")),
        "final_url": storage.clean(values.get("final_url")),
        "captured_at": captured_at,
        "http_status": storage.clean(values.get("http_status")),
        "content_hash": content_hash,
        "content_text": content_text,
        "source_html": source_html,
        "warnings": values.get("warnings", "") or "",
    }
    with connect() as connection:
        connection.execute(
            "INSERT INTO posting_snapshots("
            "application_id, source_url, final_url, captured_at, http_status, content_hash, content_text, source_html, warnings"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(application_id, content_hash) DO NOTHING",
            tuple(row[field] for field in schema.POSTING_SNAPSHOT_FIELDS[1:]),
        )
        saved = connection.execute(
            "SELECT id, application_id, source_url, final_url, captured_at, http_status, "
            "content_hash, content_text, source_html, warnings FROM posting_snapshots "
            "WHERE application_id = ? AND content_hash = ?",
            (application_id, content_hash),
        ).fetchone()
    preserved_fields = {"content_text", "source_html", "warnings"}
    return {
        field: (saved[field] or "") if field in preserved_fields else storage.clean(saved[field])
        for field in schema.POSTING_SNAPSHOT_FIELDS
    }


def read_resume_versions(application_id=""):
    initialize()
    params = []
    where = ""
    wanted = storage.clean(application_id).upper()
    if wanted:
        where = " WHERE upper(application_id) = ?"
        params.append(wanted)
    quoted_fields = ", ".join(f'"{field}"' for field in schema.RESUME_VERSION_FIELDS)
    with connect() as connection:
        rows = connection.execute(
            f"SELECT {quoted_fields} FROM resume_versions{where} ORDER BY created_at DESC, id DESC",
            params,
        ).fetchall()
    return [
        {
            field: (row[field] or "") if field in {"guidance", "changes_json", "warnings_json"}
            else storage.clean(row[field])
            for field in schema.RESUME_VERSION_FIELDS
        }
        for row in rows
    ]


def write_resume_version(row):
    initialize()
    fields = schema.RESUME_VERSION_FIELDS
    values = [
        (row.get(field, "") or "") if field in {"guidance", "changes_json", "warnings_json"}
        else storage.clean(row.get(field, ""))
        for field in fields
    ]
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join("?" for _ in fields)
    with connect() as connection:
        connection.execute(
            f"INSERT INTO resume_versions ({quoted_fields}) VALUES ({placeholders})",
            values,
        )
    return {field: values[index] for index, field in enumerate(fields)}


def record_event(entity_type, entity_id, event_type, data):
    payload = json.dumps(data, sort_keys=True)
    created_at = datetime.now().isoformat(timespec="seconds")
    with connect() as connection:
        connection.execute(
            "INSERT INTO events(entity_type, entity_id, event_type, created_at, data_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (entity_type, entity_id, event_type, created_at, payload),
        )


def update_action_status(action_id, status):
    initialize()
    status = normalize_action_status(status)
    if status not in schema.ACTION_STATUSES:
        raise ValueError(f"Unsupported action status: {status}")

    wanted = storage.clean(action_id).upper()
    completed_date = storage.today_iso() if status in schema.COMPLETED_ACTION_STATUSES else ""
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM actions WHERE upper(id) = ?",
            (wanted,),
        ).fetchone()
        if not row:
            raise ValueError(f"No action found with id {action_id}.")
        before = {field: storage.clean(row[field]) for field in schema.ACTION_FIELDS}
        connection.execute(
            "UPDATE actions SET status = ?, completed_date = ? WHERE upper(id) = ?",
            (status, completed_date, wanted),
        )
        after = {**before, "status": status, "completed_date": completed_date}
        connection.execute(
            "INSERT INTO events(entity_type, entity_id, event_type, created_at, data_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "action",
                after["id"],
                "status_changed",
                datetime.now().isoformat(timespec="seconds"),
                json.dumps({"before": before, "after": after}, sort_keys=True),
            ),
        )
    return after
