"""SQLite storage backend for Hunter.

The database uses plain TEXT columns for the CSV-backed entities so import and
export stay lossless while the app gets local transactional persistence.
"""

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from . import paths, schema, storage


TABLES = {
    "applications": (paths.APPLICATIONS, schema.APPLICATION_FIELDS),
    "contacts": (paths.CONTACTS, schema.CONTACT_FIELDS),
    "interviews": (paths.INTERVIEWS, schema.INTERVIEW_FIELDS),
    "actions": (paths.ACTIONS, schema.ACTION_FIELDS),
}

EDITABLE_TABLE_FIELDS = {**{name: fields for name, (_, fields) in TABLES.items()},
                         "discovery_searches": schema.DISCOVERY_SEARCH_FIELDS}

RESOURCE_FIELDS = {
    "companies": schema.COMPANY_FIELDS,
    "company_posting_candidates": schema.COMPANY_POSTING_CANDIDATE_FIELDS,
    "discovery_candidates": schema.DISCOVERY_CANDIDATE_FIELDS,
}
RESOURCE_PRESERVED_FIELDS = {
    "discovery_candidates": frozenset({"description_text", "warnings", "notes"}),
}

SCHEMA_VERSION = 21
LEGACY_SCHEMA_VERSION = 20
BUSY_TIMEOUT_MS = 5_000
_initialization_lock = threading.RLock()
_initialized_database_identity = None

LEGACY_CLOSED_STATUSES = {"rejected", "withdrawn", "archived", "offer_declined", "declined", "accepted"}
LEGACY_STAGE_MAP = {
    "closed-posting": "closed",
    "research": schema.DEFAULT_STAGE,
    **schema.WORKFLOW_STAGE_ALIASES,
}
LEGACY_STATUS_STAGE_MAP = {
    "applied": "applied",
    "interviewing": "interviewing",
    "offer": "offer",
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
    connection = sqlite3.connect(
        paths.SQLITE_DB,
        timeout=BUSY_TIMEOUT_MS / 1_000,
        factory=ClosingConnection,
    )
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return connection


def _database_identity():
    try:
        stat = paths.SQLITE_DB.stat()
    except FileNotFoundError:
        return (str(paths.SQLITE_DB.resolve()), None, None)
    return (str(paths.SQLITE_DB.resolve()), stat.st_dev, stat.st_ino)


def ensure_initialized():
    """Initialize the active database once per process and database file.

    The public ``initialize`` function intentionally remains a forceable,
    idempotent migration entry point for CLI startup, tests, and repair flows.
    Routine reads and writes use this cached guard instead of rerunning every
    schema migration for every repository call.
    """
    identity = _database_identity()
    if _initialized_database_identity == identity and identity[1] is not None:
        return
    initialize()


@contextmanager
def read_transaction():
    """Yield one consistent, short-lived SQLite read snapshot."""
    ensure_initialized()
    connection = connect()
    try:
        connection.execute("BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _increment_data_revision(connection):
    connection.execute(
        "INSERT INTO meta(key, value) VALUES('data_revision', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
    )


@contextmanager
def write_transaction():
    """Yield a serialized write transaction and advance its data revision once."""
    ensure_initialized()
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        _increment_data_revision(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def quote_identifier(value):
    if value not in EDITABLE_TABLE_FIELDS:
        raise ValueError(f"Unknown table: {value}")
    return f'"{value}"'


def quote_field(value, fields):
    if value not in fields:
        raise ValueError(f"Unknown field: {value}")
    return f'"{value}"'


def quote_resource(value):
    if value not in RESOURCE_FIELDS:
        raise ValueError(f"Unknown resource table: {value}")
    return f'"{value}"'


def _resource_value(table, field, value):
    if field in RESOURCE_PRESERVED_FIELDS.get(table, ()):
        return value or ""
    cleaned = storage.clean(value)
    if field == "status" and table in {
        "company_posting_candidates",
        "discovery_candidates",
    }:
        return schema.CANDIDATE_STATUS_ALIASES.get(cleaned.lower(), cleaned)
    return cleaned


def _resource_row(table, row):
    if row is None:
        return None
    return {
        field: _resource_value(table, field, row[field])
        for field in RESOURCE_FIELDS[table]
    }


def _update_resource_fields(table, fields, resource_id, updates):
    ensure_initialized()
    wanted = storage.clean(resource_id).upper()
    if not wanted:
        raise ValueError(f"{table} id is required.")
    cleaned_updates = {
        field: _resource_value(table, field, value)
        for field, value in (updates or {}).items()
        if field in fields and field != "id"
    }
    if not cleaned_updates:
        raise ValueError(f"No editable {table} fields were provided.")
    assignments = ", ".join(f'"{field}" = ?' for field in cleaned_updates)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with write_transaction() as connection:
        cursor = connection.execute(
            f"UPDATE {quote_resource(table)} SET {assignments} WHERE id = ?",
            [*cleaned_updates.values(), wanted],
        )
        if cursor.rowcount != 1:
            raise ValueError(f"No {table} row found with id {resource_id}.")
        row = connection.execute(
            f"SELECT {quoted_fields} FROM {quote_resource(table)} WHERE id = ?",
            (wanted,),
        ).fetchone()
    return _resource_row(table, row)


def _bulk_update_resource_fields(table, fields, updates_by_id):
    ensure_initialized()
    normalized = []
    for resource_id, updates in (updates_by_id or {}).items():
        wanted = storage.clean(resource_id).upper()
        cleaned_updates = {
            field: _resource_value(table, field, value)
            for field, value in (updates or {}).items()
            if field in fields and field != "id"
        }
        if wanted and cleaned_updates:
            normalized.append((wanted, cleaned_updates))
    if not normalized:
        raise ValueError(f"No {table} updates were provided.")
    ids = [resource_id for resource_id, _updates in normalized]
    placeholders = ", ".join("?" for _ in ids)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with write_transaction() as connection:
        found = {
            row["id"]
            for row in connection.execute(
                f"SELECT id FROM {quote_resource(table)} WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        }
        missing = set(ids) - found
        if missing:
            raise ValueError(
                f"No {table} row found with id " + ", ".join(sorted(missing)) + "."
            )
        for resource_id, cleaned_updates in normalized:
            assignments = ", ".join(f'"{field}" = ?' for field in cleaned_updates)
            connection.execute(
                f"UPDATE {quote_resource(table)} SET {assignments} WHERE id = ?",
                [*cleaned_updates.values(), resource_id],
            )
        rows = connection.execute(
            f"SELECT {quoted_fields} FROM {quote_resource(table)} "
            f"WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    by_id = {row["id"]: _resource_row(table, row) for row in rows}
    return [by_id[resource_id] for resource_id in ids]


def _compare_and_update_resource_fields(table, fields, resource_id, expected, updates):
    ensure_initialized()
    wanted = storage.clean(resource_id).upper()
    if not wanted:
        raise ValueError(f"{table} id is required.")
    cleaned_expected = {
        field: _resource_value(table, field, value)
        for field, value in (expected or {}).items()
        if field in fields and field != "id"
    }
    cleaned_updates = {
        field: _resource_value(table, field, value)
        for field, value in (updates or {}).items()
        if field in fields and field != "id"
    }
    if not cleaned_expected:
        raise ValueError(f"No expected {table} fields were provided.")
    if not cleaned_updates:
        raise ValueError(f"No editable {table} fields were provided.")
    assignments = ", ".join(f'"{field}" = ?' for field in cleaned_updates)
    conditions = " AND ".join(f'"{field}" = ?' for field in cleaned_expected)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with write_transaction() as connection:
        cursor = connection.execute(
            f"UPDATE {quote_resource(table)} SET {assignments} "
            f"WHERE id = ? AND {conditions}",
            [
                *cleaned_updates.values(),
                wanted,
                *cleaned_expected.values(),
            ],
        )
        row = connection.execute(
            f"SELECT {quoted_fields} FROM {quote_resource(table)} WHERE id = ?",
            (wanted,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No {table} row found with id {resource_id}.")
    return {"updated": cursor.rowcount == 1, "row": _resource_row(table, row)}


def _next_available_resource_id(connection, table, prefix, requested_id):
    requested = storage.clean(requested_id).upper()
    if requested:
        existing = connection.execute(
            f"SELECT 1 FROM {quote_resource(table)} WHERE id = ?",
            (requested,),
        ).fetchone()
        if not existing:
            return requested
    highest = 0
    for row in connection.execute(
        f"SELECT id FROM {quote_resource(table)} WHERE id LIKE ?",
        (f"{prefix}%",),
    ).fetchall():
        value = storage.clean(row["id"]).upper()
        suffix = value[len(prefix) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:04d}"


def _insert_resource_rows(table, fields, prefix, rows):
    ensure_initialized()
    if not rows:
        return []
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join("?" for _ in fields)
    inserted_ids = []
    with write_transaction() as connection:
        for source in rows:
            row = dict(source)
            if table == "company_posting_candidates":
                company_id = storage.clean(row.get("company_id", "")).upper()
                exact_url = storage.clean(row.get("url", ""))
                existing = (
                    connection.execute(
                        "SELECT id FROM company_posting_candidates "
                        "WHERE upper(company_id) = ? AND url = ?",
                        (company_id, exact_url),
                    ).fetchone()
                    if company_id and exact_url
                    else None
                )
                if existing is not None:
                    inserted_ids.append(existing["id"])
                    continue
            row["id"] = _next_available_resource_id(
                connection,
                table,
                prefix,
                row.get("id", ""),
            )
            values = [_resource_value(table, field, row.get(field, "")) for field in fields]
            connection.execute(
                f"INSERT INTO {quote_resource(table)} ({quoted_fields}) "
                f"VALUES ({placeholders})",
                values,
            )
            inserted_ids.append(row["id"])
        id_placeholders = ", ".join("?" for _ in inserted_ids)
        saved = connection.execute(
            f"SELECT {quoted_fields} FROM {quote_resource(table)} "
            f"WHERE id IN ({id_placeholders})",
            inserted_ids,
        ).fetchall()
    by_id = {row["id"]: _resource_row(table, row) for row in saved}
    return [by_id[resource_id] for resource_id in inserted_ids]


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


def discovery_candidates_table_sql(table="discovery_candidates", if_not_exists=True):
    qualifier = "IF NOT EXISTS " if if_not_exists else ""
    return (
        f"CREATE TABLE {qualifier}{table} ("
        "id TEXT PRIMARY KEY, "
        "search_id TEXT NOT NULL DEFAULT '', "
        "search_ids_json TEXT NOT NULL DEFAULT '', "
        "company_id TEXT NOT NULL DEFAULT '', "
        "title TEXT NOT NULL DEFAULT '', "
        "url TEXT NOT NULL DEFAULT '', "
        "canonical_url TEXT NOT NULL DEFAULT '', "
        "location TEXT NOT NULL DEFAULT '', "
        "work_mode TEXT NOT NULL DEFAULT '', "
        "source_platform TEXT NOT NULL DEFAULT '', "
        "captured_at TEXT NOT NULL DEFAULT '', "
        "last_seen_at TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'new', "
        "processing_status TEXT NOT NULL DEFAULT 'needs-details', "
        "qualification_status TEXT NOT NULL DEFAULT '', "
        "qualification_reason TEXT NOT NULL DEFAULT '', "
        "detail_attempt_count TEXT NOT NULL DEFAULT '', "
        "detail_last_attempt_at TEXT NOT NULL DEFAULT '', "
        "detail_last_error TEXT NOT NULL DEFAULT '', "
        "fit_score TEXT NOT NULL DEFAULT '', "
        "fit_summary TEXT NOT NULL DEFAULT '', "
        "fit_checked_at TEXT NOT NULL DEFAULT '', "
        "description_text TEXT NOT NULL DEFAULT '', "
        "description_excerpt TEXT NOT NULL DEFAULT '', "
        "warnings TEXT NOT NULL DEFAULT '', "
        "source_urls_json TEXT NOT NULL DEFAULT '', "
        "acquisition_provenance_json TEXT NOT NULL DEFAULT '', "
        "freshness_status TEXT NOT NULL DEFAULT '', "
        "freshness_checked_at TEXT NOT NULL DEFAULT '', "
        "ingested_application_id TEXT NOT NULL DEFAULT '', "
        "ignore_reason TEXT NOT NULL DEFAULT '', "
        "ignore_reason_detail TEXT NOT NULL DEFAULT '', "
        "notes TEXT NOT NULL DEFAULT ''"
        ")"
    )


def migrate_discovery_candidates_schema(connection):
    definition = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'discovery_candidates'"
    ).fetchone()
    if not definition:
        return
    current_columns = set(table_columns(connection, "discovery_candidates"))
    target_columns = set(schema.DISCOVERY_CANDIDATE_FIELDS)
    compact_definition = (definition["sql"] or "").replace(" ", "")
    if (
        current_columns == target_columns
        and "UNIQUE(url)" not in compact_definition
        and "UNIQUE(search_id,url)" not in compact_definition
    ):
        return
    legacy_company_fields = {
        "company",
        "company_industry",
        "company_size",
        "company_profile_url",
        "company_metadata_source",
    }
    if legacy_company_fields.issubset(current_columns):
        legacy_rows = connection.execute(
            "SELECT company_id, company, company_industry, company_size, "
            "company_profile_url, company_metadata_source, canonical_url, url, "
            "last_seen_at, captured_at FROM discovery_candidates "
            "WHERE company_id <> ''"
        ).fetchall()
        for row in legacy_rows:
            source = (
                storage.clean(row["company_metadata_source"])
                or storage.clean(row["canonical_url"])
                or storage.clean(row["url"])
            )
            observed_at = storage.clean(row["last_seen_at"]) or storage.clean(row["captured_at"])
            connection.execute(
                "UPDATE companies SET "
                "name = CASE WHEN name = '' THEN ? ELSE name END, "
                "industry = CASE WHEN industry = '' THEN ? ELSE industry END, "
                "company_size = CASE WHEN company_size = '' THEN ? ELSE company_size END, "
                "company_profile_url = CASE WHEN company_profile_url = '' THEN ? ELSE company_profile_url END, "
                "company_metadata_source = CASE "
                "WHEN company_metadata_source = '' AND (? <> '' OR ? <> '' OR ? <> '') THEN ? "
                "ELSE company_metadata_source END, "
                "company_metadata_checked_at = CASE "
                "WHEN company_metadata_checked_at = '' AND (? <> '' OR ? <> '' OR ? <> '') THEN ? "
                "ELSE company_metadata_checked_at END "
                "WHERE upper(id) = upper(?)",
                (
                    storage.clean(row["company"]),
                    storage.clean(row["company_industry"]),
                    storage.clean(row["company_size"]),
                    storage.clean(row["company_profile_url"]),
                    storage.clean(row["company_industry"]),
                    storage.clean(row["company_size"]),
                    storage.clean(row["company_profile_url"]),
                    source,
                    storage.clean(row["company_industry"]),
                    storage.clean(row["company_size"]),
                    storage.clean(row["company_profile_url"]),
                    observed_at,
                    storage.clean(row["company_id"]),
                ),
            )
    fields = schema.DISCOVERY_CANDIDATE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    rows = connection.execute(f"SELECT {quoted_fields} FROM discovery_candidates").fetchall()
    connection.execute("DROP TABLE IF EXISTS discovery_candidates_new")
    connection.execute(discovery_candidates_table_sql("discovery_candidates_new", if_not_exists=False))
    if rows:
        placeholders = ", ".join("?" for _ in fields)
        connection.executemany(
            f"INSERT INTO discovery_candidates_new ({quoted_fields}) VALUES ({placeholders})",
            [[row[field] or "" for field in fields] for row in rows],
        )
    connection.execute("DROP TABLE discovery_candidates")
    connection.execute("ALTER TABLE discovery_candidates_new RENAME TO discovery_candidates")


def migrate_discovery_search_lanes(connection):
    rows = connection.execute(
        "SELECT id, location, remote_location, lanes_json FROM discovery_searches"
    ).fetchall()
    all_work_modes = ["on-site", "hybrid", "remote"]
    for row in rows:
        if storage.clean(row["lanes_json"]):
            continue
        lanes = []
        location = storage.clean(row["location"])
        remote_location = storage.clean(row["remote_location"])
        if location:
            lanes.append(
                {
                    "id": "primary",
                    "label": location,
                    "location": location,
                    "work_modes": all_work_modes,
                }
            )
        if remote_location:
            lanes.append(
                {
                    "id": "remote",
                    "label": f"{remote_location} remote",
                    "location": remote_location,
                    "work_modes": ["remote"],
                }
            )
        if lanes:
            connection.execute(
                "UPDATE discovery_searches SET lanes_json = ? WHERE id = ?",
                (json.dumps(lanes), row["id"]),
            )


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


def migrate_simplified_workflow(connection):
    for table in ("company_posting_candidates", "discovery_candidates"):
        connection.execute(
            f"UPDATE {table} SET status = 'pursued' WHERE lower(trim(status)) = 'ingested'"
        )
    migrated = connection.execute(
        "SELECT value FROM meta WHERE key = 'simplified_workflow_v1'"
    ).fetchone()
    if not migrated:
        for old_stage, new_stage in schema.WORKFLOW_STAGE_ALIASES.items():
            connection.execute(
                "UPDATE applications SET stage = ? WHERE lower(trim(stage)) = ?",
                (new_stage, old_stage),
            )
        for row in schema.DEFAULT_WORKFLOW_ACTION_TYPES:
            connection.execute(
                "UPDATE workflow_action_types SET label = ?, description = ?, default_priority = ?, "
                "default_due_days = ?, allowed_stages = ?, sort_order = ? WHERE id = ?",
                (
                    row["label"], row["description"], row["default_priority"],
                    row["default_due_days"], row["allowed_stages"], row["sort_order"], row["id"],
                ),
            )
        connection.execute(
            "INSERT INTO meta(key, value) VALUES('simplified_workflow_v1', ?)",
            (datetime.now().astimezone().isoformat(timespec="seconds"),),
        )

    stage_metadata_migrated = connection.execute(
        "SELECT value FROM meta WHERE key = 'simplified_workflow_stage_metadata_v1'"
    ).fetchone()
    if stage_metadata_migrated:
        return
    active_stage_ids = [row["id"] for row in schema.DEFAULT_WORKFLOW_STAGES]
    placeholders = ", ".join("?" for _ in active_stage_ids)
    connection.execute(
        f"UPDATE workflow_stages SET is_active = '' WHERE id NOT IN ({placeholders})",
        active_stage_ids,
    )
    for row in schema.DEFAULT_WORKFLOW_STAGES:
        connection.execute(
            "UPDATE workflow_stages SET label = ?, sort_order = ?, is_terminal = ?, is_active = '1' WHERE id = ?",
            (row["label"], row["sort_order"], row["is_terminal"], row["id"]),
        )
    connection.execute(
        "INSERT INTO meta(key, value) VALUES('simplified_workflow_stage_metadata_v1', ?)",
        (datetime.now().astimezone().isoformat(timespec="seconds"),),
    )


def schema_version(connection):
    row = connection.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def data_revision(connection=None):
    """Return the monotonic revision used to invalidate read-model caches."""
    if connection is not None:
        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'data_revision'"
        ).fetchone()
        return int(row["value"]) if row else 0
    ensure_initialized()
    with connect() as current:
        return data_revision(current)


def _migrate_to_21(connection):
    connection.execute(
        "INSERT INTO meta(key, value) VALUES('data_revision', '0') "
        "ON CONFLICT(key) DO NOTHING"
    )


SCHEMA_MIGRATIONS = {
    21: _migrate_to_21,
}


def apply_schema_migrations(connection):
    """Advance the initialized v20 schema through numbered migrations."""
    current = schema_version(connection)
    if current < LEGACY_SCHEMA_VERSION:
        # The legacy initializer above establishes the complete v20 schema for
        # both new databases and databases created before versions were tracked.
        current = LEGACY_SCHEMA_VERSION
        connection.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(current),),
        )
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Hunter database schema {current} is newer than supported schema {SCHEMA_VERSION}."
        )
    for target in range(current + 1, SCHEMA_VERSION + 1):
        migration = SCHEMA_MIGRATIONS.get(target)
        if migration is None:
            raise RuntimeError(f"Missing Hunter database migration for schema {target}.")
        migration(connection)
        connection.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (str(target),),
        )
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def initialize():
    global _initialized_database_identity
    for directory in paths.WORKSPACE_DIRS:
        (paths.ROOT / directory).mkdir(parents=True, exist_ok=True)
    with _initialization_lock, connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
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
            "capture_method TEXT NOT NULL DEFAULT 'fetch', "
            "capture_model TEXT NOT NULL DEFAULT '', "
            "sources_json TEXT NOT NULL DEFAULT '[]', "
            "content_hash TEXT NOT NULL, "
            "content_text TEXT NOT NULL DEFAULT '', "
            "source_html TEXT NOT NULL DEFAULT '', "
            "warnings TEXT NOT NULL DEFAULT '', "
            "UNIQUE(application_id, content_hash)"
            ")"
        )
        ensure_text_columns(connection, "posting_snapshots", schema.POSTING_SNAPSHOT_FIELDS[1:])
        connection.execute(
            "UPDATE posting_snapshots SET capture_method = 'fetch' WHERE capture_method = ''"
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
            "CREATE TABLE IF NOT EXISTS suggestion_dismissals ("
            "suggestion_id TEXT PRIMARY KEY, "
            "dismissed_at TEXT NOT NULL"
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
            "tracking_status TEXT NOT NULL DEFAULT 'tracked', "
            "discovered_at TEXT NOT NULL DEFAULT '', "
            "last_seen_at TEXT NOT NULL DEFAULT '', "
            "website TEXT NOT NULL DEFAULT '', "
            "careers_url TEXT NOT NULL DEFAULT '', "
            "industry TEXT NOT NULL DEFAULT '', "
            "company_size TEXT NOT NULL DEFAULT '', "
            "company_profile_url TEXT NOT NULL DEFAULT '', "
            "company_metadata_source TEXT NOT NULL DEFAULT '', "
            "company_metadata_checked_at TEXT NOT NULL DEFAULT '', "
            "company_metadata_suggestions_json TEXT NOT NULL DEFAULT '[]', "
            "company_research_status TEXT NOT NULL DEFAULT '', "
            "company_discovery_source TEXT NOT NULL DEFAULT '', "
            "company_discovery_source_url TEXT NOT NULL DEFAULT '', "
            "company_discovery_query TEXT NOT NULL DEFAULT '', "
            "company_discovery_evidence TEXT NOT NULL DEFAULT '', "
            "company_location_fit TEXT NOT NULL DEFAULT '', "
            "company_location TEXT NOT NULL DEFAULT '', "
            "company_remote_policy TEXT NOT NULL DEFAULT '', "
            "company_location_evidence TEXT NOT NULL DEFAULT '', "
            "company_location_checked_at TEXT NOT NULL DEFAULT '', "
            "company_fit_score TEXT NOT NULL DEFAULT '', "
            "company_fit_summary TEXT NOT NULL DEFAULT '', "
            "company_fit_checked_at TEXT NOT NULL DEFAULT '', "
            "company_evaluation_status TEXT NOT NULL DEFAULT '', "
            "company_evaluation_version TEXT NOT NULL DEFAULT '', "
            "company_evaluation_checked_at TEXT NOT NULL DEFAULT '', "
            "company_evaluation_error TEXT NOT NULL DEFAULT '', "
            "notes TEXT NOT NULL DEFAULT '', "
            "last_checked_at TEXT NOT NULL DEFAULT '', "
            "last_check_status TEXT NOT NULL DEFAULT ''"
            ")"
        )
        ensure_text_columns(connection, "companies", schema.COMPANY_FIELDS)
        connection.execute(
            "UPDATE companies SET tracking_status = 'tracked' "
            "WHERE trim(tracking_status) = ''"
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
            "CREATE TABLE IF NOT EXISTS discovery_searches ("
            "id TEXT PRIMARY KEY, "
            "name TEXT NOT NULL DEFAULT '', "
            "keywords TEXT NOT NULL DEFAULT '', "
            "location TEXT NOT NULL DEFAULT '', "
            "remote_location TEXT NOT NULL DEFAULT '', "
            "lanes_json TEXT NOT NULL DEFAULT '', "
            "role_family_ids_json TEXT NOT NULL DEFAULT '', "
            "excluded_terms_json TEXT NOT NULL DEFAULT '', "
            "created_at TEXT NOT NULL DEFAULT '', "
            "updated_at TEXT NOT NULL DEFAULT '', "
            "last_opened_at TEXT NOT NULL DEFAULT '', "
            "last_run_at TEXT NOT NULL DEFAULT '', "
            "last_run_summary_json TEXT NOT NULL DEFAULT ''"
            ")"
        )
        ensure_text_columns(connection, "discovery_searches", schema.DISCOVERY_SEARCH_FIELDS)
        migrate_discovery_search_lanes(connection)
        connection.execute(discovery_candidates_table_sql())
        ensure_text_columns(connection, "discovery_candidates", schema.DISCOVERY_CANDIDATE_FIELDS)
        migrate_discovery_candidates_schema(connection)
        migrate_simplified_workflow(connection)
        apply_schema_migrations(connection)
    _initialized_database_identity = _database_identity()


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


def read_suggestion_dismissals():
    ensure_initialized()
    with connect() as connection:
        rows = connection.execute(
            "SELECT suggestion_id, dismissed_at "
            "FROM suggestion_dismissals ORDER BY dismissed_at DESC, suggestion_id"
        ).fetchall()
    return [
        {
            "suggestion_id": storage.clean(row["suggestion_id"]),
            "dismissed_at": storage.clean(row["dismissed_at"]),
        }
        for row in rows
    ]


def dismiss_suggestion(suggestion_id, dismissed_at):
    ensure_initialized()
    with write_transaction() as connection:
        connection.execute(
            "INSERT INTO suggestion_dismissals(suggestion_id, dismissed_at) VALUES (?, ?) "
            "ON CONFLICT(suggestion_id) DO UPDATE SET dismissed_at=excluded.dismissed_at",
            (storage.clean(suggestion_id), storage.clean(dismissed_at)),
        )
    return {
        "suggestion_id": storage.clean(suggestion_id),
        "dismissed_at": storage.clean(dismissed_at),
    }


def restore_suggestion(suggestion_id):
    ensure_initialized()
    cleaned_id = storage.clean(suggestion_id)
    with write_transaction() as connection:
        cursor = connection.execute(
            "DELETE FROM suggestion_dismissals WHERE suggestion_id = ?",
            (cleaned_id,),
        )
    return {"suggestion_id": cleaned_id, "restored": cursor.rowcount > 0}


def count_rows(table):
    ensure_initialized()
    with connect() as connection:
        return connection.execute(f"SELECT COUNT(*) AS total FROM {quote_identifier(table)}").fetchone()["total"]


class TableSnapshot(list):
    """Editable rows plus their read baseline for conflict-aware runtime saves.

    Saves require this baseline; replacement is a separate import operation.
    A runtime read/save pair only writes changed fields and never replaces rows
    added by another request while the caller was working or fetching a URL.
    """

    def __init__(self, table, rows):
        super().__init__(rows)
        self.table = table
        self.original = {row["id"]: dict(row) for row in rows}


def read_table(table):
    ensure_initialized()
    fields = EDITABLE_TABLE_FIELDS[table]
    quoted_fields = ", ".join(quote_field(field, fields) for field in fields)
    with connect() as connection:
        rows = connection.execute(
            f"SELECT {quoted_fields} FROM {quote_identifier(table)} ORDER BY \"id\""
        ).fetchall()
    return TableSnapshot(table, [{field: storage.clean(row[field]) for field in fields} for row in rows])


def _sync_next_action(connection, application_id):
    from .actions import select_next_action

    row = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    if row is None:
        return None
    application = dict(row)
    related = [dict(row) for row in connection.execute(
        "SELECT * FROM actions WHERE application_id = ?", (application_id,)
    )]
    selected = select_next_action(application, related) if application.get("stage") != "closed" else None
    values = {
        "next_action_id": (selected or {}).get("id", ""),
        "next_action": (selected or {}).get("title", ""),
        "next_action_date": (selected or {}).get("due_date", ""),
    }
    connection.execute(
        "UPDATE applications SET next_action_id = ?, next_action = ?, next_action_date = ? WHERE id = ?",
        (*values.values(), application_id),
    )
    return {**application, **values}


def sync_next_action(application_id):
    with write_transaction() as connection:
        return _sync_next_action(connection, storage.clean(application_id).upper())


def _save_table_snapshot(table, rows):
    if rows.table != table:
        raise ValueError("Cannot save a snapshot into a different table.")
    fields = EDITABLE_TABLE_FIELDS[table]
    submitted = {row["id"]: row for row in rows}
    if len(submitted) != len(rows):
        raise ValueError("Duplicate row ids in the submitted changes.")
    prefix = {"applications": "A", "actions": "T", "contacts": "C", "interviews": "I", "discovery_searches": "DS"}[table]
    refreshed = []
    with write_transaction() as connection:
        current = {row["id"]: dict(row) for row in connection.execute(f"SELECT * FROM {quote_identifier(table)}")}
        affected_applications = set()
        for row_id, original in rows.original.items():
            row = submitted.get(row_id)
            changes = {
                field: storage.clean(row.get(field, ""))
                for field in fields if field != "id" and row is not None
                and storage.clean(row.get(field, "")) != original[field]
            }
            if row is not None and not changes:
                continue
            latest = current.get(row_id)
            compared_fields = changes if row is not None else original
            if latest is None or any(
                latest[field] != original[field]
                and (row is None or latest[field] != changes[field])
                for field in compared_fields
            ):
                raise ValueError(f"{row_id} changed while you were editing. Reload it and try again.")
            if row is None:
                connection.execute(f"DELETE FROM {quote_identifier(table)} WHERE id = ?", (row_id,))
            else:
                assignments = ", ".join(f'{quote_field(field, fields)} = ?' for field in changes)
                connection.execute(
                    f"UPDATE {quote_identifier(table)} SET {assignments} WHERE id = ?", (*changes.values(), row_id)
                )
            if table == "actions":
                affected_applications.add(latest["application_id"])
                if row is not None:
                    affected_applications.add(row.get("application_id", ""))
            elif table == "applications" and row is not None:
                identity = ({field: changes.get(field, latest[field]) for field in ("company", "role")}
                            if {"company_id", "company", "role"} & changes.keys() else {})
                if identity:
                    assignments = ", ".join(f'"{field}" = ?' for field in identity)
                    connection.execute(f"UPDATE actions SET {assignments} WHERE application_id = ?", (*identity.values(), row_id))
                if {"stage", "next_action_id"} & changes.keys():
                    affected_applications.add(row_id)
        # Allocate IDs under the write lock. Keep provisional IDs when available
        # for legacy callers, and update caller-owned rows only after commit.
        reserved = set(current) | set(submitted)
        highest = max((int(match.group(1)) for value in reserved
                       if (match := re.fullmatch(rf"{prefix}(\d+)", value))), default=0)
        assigned = {}
        for row_id, row in submitted.items():
            if row_id in rows.original:
                assigned[row_id] = row_id
                continue
            assigned_id = row_id
            if assigned_id in current:
                highest += 1
                assigned_id = f"{prefix}{highest:04d}"
            assigned[row_id] = assigned_id
            values = {field: storage.clean(row.get(field, "")) for field in fields}
            values["id"] = assigned_id
            quoted = ", ".join(quote_field(field, fields) for field in fields)
            placeholders = ", ".join("?" for _ in fields)
            connection.execute(f"INSERT INTO {quote_identifier(table)} ({quoted}) VALUES ({placeholders})", tuple(values.values()))
            if table == "actions":
                affected_applications.add(values["application_id"])
        for application_id in affected_applications:
            _sync_next_action(connection, application_id)
        for row_id, row in submitted.items():
            saved = connection.execute(f"SELECT * FROM {quote_identifier(table)} WHERE id = ?", (assigned[row_id],)).fetchone()
            if saved is not None:
                refreshed.append((row, {field: storage.clean(saved[field]) for field in fields}))
    for row, saved in refreshed:
        row.update(saved)
    rows.original = {row["id"]: dict(row) for row in rows}


def save_table_changes(table, rows):
    """Commit only changes relative to a read baseline; never replace a table."""
    if not isinstance(rows, TableSnapshot):
        raise ValueError("Saving changes requires the original read snapshot. Use replace_table_for_import only for imports.")
    ensure_initialized()
    _save_table_snapshot(table, rows)


def replace_table_for_import(table, rows):
    """Explicit, destructive replacement for imports and synthetic fixtures."""
    ensure_initialized()
    fields = EDITABLE_TABLE_FIELDS[table]
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(quote_field(field, fields) for field in fields)
    values = [[storage.clean(row.get(field, "")) for field in fields] for row in rows]
    with write_transaction() as connection:
        connection.execute(f"DELETE FROM {quote_identifier(table)}")
        if values:
            connection.executemany(
                f"INSERT INTO {quote_identifier(table)} ({quoted_fields}) VALUES ({placeholders})",
                values,
            )


def upsert_table(table, rows):
    ensure_initialized()
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
    with write_transaction() as connection:
        connection.executemany(
            f"INSERT INTO {quote_identifier(table)} ({quoted_fields}) VALUES ({placeholders}) "
            f"ON CONFLICT(\"id\") DO UPDATE SET {updates}",
            values,
        )


def import_from_csv(overwrite=False):
    ensure_initialized()
    imported = {}
    for table, (path, fields) in TABLES.items():
        rows = storage.read_rows(path, fields)
        if overwrite:
            replace_table_for_import(table, rows)
        else:
            if count_rows(table):
                raise ValueError(
                    f"SQLite table '{table}' already has data. Re-run with --overwrite to replace it."
                )
            upsert_table(table, rows)
        imported[table] = len(rows)
    return imported


def export_to_csv():
    ensure_initialized()
    exported = {}
    for table, (path, fields) in TABLES.items():
        rows = read_table(table)
        storage.write_rows(path, fields, rows)
        exported[table] = len(rows)
    return exported


def read_applications():
    return read_table("applications")


def save_applications_changes(rows):
    save_table_changes("applications", rows)


def replace_applications_for_import(rows):
    replace_table_for_import("applications", rows)


def delete_unmodified_discovery_application(application_id):
    ensure_initialized()
    wanted = storage.clean(application_id).upper()
    with write_transaction() as connection:
        application = connection.execute(
            "SELECT id, stage, source, posting_file FROM applications WHERE upper(id) = ?",
            (wanted,),
        ).fetchone()
        if not application:
            return False
        if storage.clean(application["stage"]) != schema.DEFAULT_STAGE:
            raise ValueError("This posting has moved beyond Considering and can no longer be undone here.")
        if not storage.clean(application["source"]).lower().startswith("discovery"):
            raise ValueError("Only postings created by Discovery can be undone here.")
        if storage.clean(application["posting_file"]):
            raise ValueError("This posting now has a saved posting file and can no longer be undone here.")
        related_checks = (
            ("actions", "application_id"),
            ("interviews", "application_id"),
            ("application_contacts", "application_id"),
            ("resume_versions", "application_id"),
        )
        for table, field in related_checks:
            related = connection.execute(
                f"SELECT 1 FROM {table} WHERE upper({field}) = ? LIMIT 1",
                (wanted,),
            ).fetchone()
            if related:
                raise ValueError("This posting has related activity and can no longer be undone here.")
        connection.execute("DELETE FROM posting_snapshots WHERE upper(application_id) = ?", (wanted,))
        connection.execute("DELETE FROM posting_notes WHERE upper(application_id) = ?", (wanted,))
        cursor = connection.execute("DELETE FROM applications WHERE upper(id) = ?", (wanted,))
        return cursor.rowcount > 0


def read_actions():
    return read_table("actions")


def save_actions_changes(rows):
    save_table_changes("actions", rows)


def replace_actions_for_import(rows):
    replace_table_for_import("actions", rows)


def read_contacts():
    return read_table("contacts")


def save_contacts_changes(rows):
    save_table_changes("contacts", rows)


def replace_contacts_for_import(rows):
    replace_table_for_import("contacts", rows)


def read_companies():
    ensure_initialized()
    fields = schema.COMPANY_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with connect() as connection:
        rows = connection.execute(f"SELECT {quoted_fields} FROM companies ORDER BY lower(name), id").fetchall()
    return [{field: storage.clean(row[field]) for field in fields} for row in rows]


def read_company(company_id):
    ensure_initialized()
    fields = schema.COMPANY_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with connect() as connection:
        row = connection.execute(
            f"SELECT {quoted_fields} FROM companies WHERE upper(id) = ?",
            (storage.clean(company_id).upper(),),
        ).fetchone()
    return {field: storage.clean(row[field]) for field in fields} if row else None


def replace_companies_for_import(rows):
    """Replace every company row for explicit import/demo compatibility only."""
    ensure_initialized()
    fields = schema.COMPANY_FIELDS
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    values = [
        [
            _resource_value("companies", field, row.get(field, ""))
            for field in fields
        ]
        for row in rows
    ]
    with write_transaction() as connection:
        connection.execute("DELETE FROM companies")
        if values:
            connection.executemany(
                f"INSERT INTO companies ({quoted_fields}) VALUES ({placeholders})",
                values,
            )


def write_companies(rows):
    """Compatibility alias for callers not yet migrated to row-level commands."""
    replace_companies_for_import(rows)


def upsert_companies(rows):
    ensure_initialized()
    if not rows:
        return []
    fields = schema.COMPANY_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(
        f'"{field}"=excluded."{field}"' for field in fields if field != "id"
    )
    values = [[storage.clean(row.get(field, "")) for field in fields] for row in rows]
    with write_transaction() as connection:
        connection.executemany(
            f"INSERT INTO companies ({quoted_fields}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            values,
        )
    return [read_company(row.get("id", "")) for row in rows]


def insert_companies(rows):
    return _insert_resource_rows(
        "companies",
        schema.COMPANY_FIELDS,
        "CO",
        rows,
    )


def update_company_fields(company_id, updates):
    return _update_resource_fields(
        "companies",
        schema.COMPANY_FIELDS,
        company_id,
        updates,
    )


def bulk_update_company_fields(updates_by_id):
    return _bulk_update_resource_fields(
        "companies",
        schema.COMPANY_FIELDS,
        updates_by_id,
    )


def delete_company(company_id):
    ensure_initialized()
    wanted = storage.clean(company_id).upper()
    if not wanted:
        raise ValueError("Company id is required.")
    with write_transaction() as connection:
        cursor = connection.execute(
            "DELETE FROM companies WHERE upper(id) = ?",
            (wanted,),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"No company found with id {company_id}.")
    return {"id": wanted, "deleted": True}


def _merge_company_references(connection, keep_id, merge_id, company_name):
    connection.execute(
        "UPDATE applications SET company_id = ?, company = ? WHERE upper(company_id) = ?",
        (keep_id, company_name, merge_id),
    )
    connection.execute(
        "UPDATE discovery_candidates SET company_id = ? WHERE upper(company_id) = ?",
        (keep_id, merge_id),
    )
    connection.execute(
        "DELETE FROM company_posting_candidates AS merged "
        "WHERE upper(merged.company_id) = ? AND EXISTS ("
        "SELECT 1 FROM company_posting_candidates AS kept "
        "WHERE upper(kept.company_id) = ? AND kept.url = merged.url"
        ")",
        (merge_id, keep_id),
    )
    connection.execute(
        "UPDATE company_posting_candidates SET company_id = ? WHERE upper(company_id) = ?",
        (keep_id, merge_id),
    )
    connection.execute(
        "INSERT INTO company_contacts(company_id, contact_id, created_at) "
        "SELECT ?, contact_id, created_at FROM company_contacts WHERE upper(company_id) = ? "
        "ON CONFLICT(company_id, contact_id) DO NOTHING",
        (keep_id, merge_id),
    )
    connection.execute(
        "DELETE FROM company_contacts WHERE upper(company_id) = ?",
        (merge_id,),
    )
    kept_source = connection.execute(
        "SELECT 1 FROM company_career_sources WHERE upper(company_id) = ?",
        (keep_id,),
    ).fetchone()
    if kept_source:
        connection.execute(
            "DELETE FROM company_career_sources WHERE upper(company_id) = ?",
            (merge_id,),
        )
    else:
        connection.execute(
            "UPDATE company_career_sources SET company_id = ? WHERE upper(company_id) = ?",
            (keep_id, merge_id),
        )
    connection.execute(
        "DELETE FROM company_career_scans AS merged "
        "WHERE upper(merged.company_id) = ? AND EXISTS ("
        "SELECT 1 FROM company_career_scans AS kept "
        "WHERE upper(kept.company_id) = ? AND kept.checked_at = merged.checked_at"
        ")",
        (merge_id, keep_id),
    )
    connection.execute(
        "UPDATE company_career_scans SET company_id = ? WHERE upper(company_id) = ?",
        (keep_id, merge_id),
    )


def merge_company_references(keep_company_id, merge_company_id, company_name):
    ensure_initialized()
    keep_id = storage.clean(keep_company_id).upper()
    merge_id = storage.clean(merge_company_id).upper()
    with write_transaction() as connection:
        _merge_company_references(
            connection,
            keep_id,
            merge_id,
            storage.clean(company_name),
        )


def merge_companies_atomic(keep_row, merge_company_id):
    """Update, relink, and delete a company duplicate in one transaction."""
    ensure_initialized()
    fields = schema.COMPANY_FIELDS
    keep_id = storage.clean((keep_row or {}).get("id", "")).upper()
    merge_id = storage.clean(merge_company_id).upper()
    if not keep_id or not merge_id or keep_id == merge_id:
        raise ValueError("Choose two different company records to merge.")
    editable_fields = [field for field in fields if field != "id"]
    assignments = ", ".join(f'"{field}" = ?' for field in editable_fields)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    values = [_resource_value("companies", field, keep_row.get(field, "")) for field in editable_fields]
    with write_transaction() as connection:
        found = {
            row["id"].upper()
            for row in connection.execute(
                "SELECT id FROM companies WHERE upper(id) IN (?, ?)",
                (keep_id, merge_id),
            ).fetchall()
        }
        if found != {keep_id, merge_id}:
            raise ValueError("One of the company records no longer exists.")
        connection.execute(
            f"UPDATE companies SET {assignments} WHERE upper(id) = ?",
            [*values, keep_id],
        )
        _merge_company_references(
            connection,
            keep_id,
            merge_id,
            storage.clean(keep_row.get("name", "")),
        )
        connection.execute("DELETE FROM companies WHERE upper(id) = ?", (merge_id,))
        saved = connection.execute(
            f"SELECT {quoted_fields} FROM companies WHERE upper(id) = ?",
            (keep_id,),
        ).fetchone()
    return _resource_row("companies", saved)


def read_application_contacts():
    ensure_initialized()
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
    ensure_initialized()
    link = {
        "application_id": storage.clean(application_id).upper(),
        "contact_id": storage.clean(contact_id).upper(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with write_transaction() as connection:
        connection.execute(
            "INSERT INTO application_contacts(application_id, contact_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(application_id, contact_id) DO NOTHING",
            (link["application_id"], link["contact_id"], link["created_at"]),
        )
    return link


def unlink_application_contact(application_id, contact_id):
    ensure_initialized()
    application_id = storage.clean(application_id).upper()
    contact_id = storage.clean(contact_id).upper()
    with write_transaction() as connection:
        connection.execute(
            "DELETE FROM application_contacts WHERE application_id = ? AND contact_id = ?",
            (application_id, contact_id),
        )
    return {"application_id": application_id, "contact_id": contact_id}


def read_company_contacts():
    ensure_initialized()
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
    ensure_initialized()
    link = {
        "company_id": storage.clean(company_id).upper(),
        "contact_id": storage.clean(contact_id).upper(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with write_transaction() as connection:
        connection.execute(
            "INSERT INTO company_contacts(company_id, contact_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(company_id, contact_id) DO NOTHING",
            (link["company_id"], link["contact_id"], link["created_at"]),
        )
    return link


def unlink_company_contact(company_id, contact_id):
    ensure_initialized()
    company_id = storage.clean(company_id).upper()
    contact_id = storage.clean(contact_id).upper()
    with write_transaction() as connection:
        connection.execute(
            "DELETE FROM company_contacts WHERE company_id = ? AND contact_id = ?",
            (company_id, contact_id),
        )
    return {"company_id": company_id, "contact_id": contact_id}


def read_company_career_sources():
    ensure_initialized()
    fields = schema.COMPANY_CAREER_SOURCE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with connect() as connection:
        rows = connection.execute(
            f"SELECT {quoted_fields} FROM company_career_sources ORDER BY company_id"
        ).fetchall()
    return [{field: storage.clean(row[field]) for field in fields} for row in rows]


def write_company_career_sources(rows):
    ensure_initialized()
    fields = schema.COMPANY_CAREER_SOURCE_FIELDS
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    values = [[storage.clean(row.get(field, "")) for field in fields] for row in rows]
    with write_transaction() as connection:
        connection.execute("DELETE FROM company_career_sources")
        if values:
            connection.executemany(
                f"INSERT INTO company_career_sources ({quoted_fields}) VALUES ({placeholders})",
                values,
            )


def upsert_company_career_source(row):
    ensure_initialized()
    fields = schema.COMPANY_CAREER_SOURCE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(
        f'"{field}"=excluded."{field}"' for field in fields if field != "company_id"
    )
    values = [storage.clean(row.get(field, "")) for field in fields]
    with write_transaction() as connection:
        connection.execute(
            f"INSERT INTO company_career_sources ({quoted_fields}) VALUES ({placeholders}) "
            f"ON CONFLICT(company_id) DO UPDATE SET {updates}",
            values,
        )
    return {
        field: storage.clean(row.get(field, ""))
        for field in fields
    }


def read_company_posting_candidates():
    ensure_initialized()
    fields = schema.COMPANY_POSTING_CANDIDATE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with connect() as connection:
        rows = connection.execute(
            f"SELECT {quoted_fields} FROM company_posting_candidates ORDER BY company_id, status, title, url"
        ).fetchall()
    return [{field: storage.clean(row[field]) for field in fields} for row in rows]


def read_company_posting_candidates_for_company(company_id):
    """Read the identity peers needed for one candidate's detail projection."""
    ensure_initialized()
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM company_posting_candidates WHERE company_id = ? ORDER BY status, title, url",
            (storage.clean(company_id).upper(),),
        ).fetchall()
    return [_resource_row("company_posting_candidates", row) for row in rows]


def read_company_posting_candidate(candidate_id):
    ensure_initialized()
    fields = schema.COMPANY_POSTING_CANDIDATE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with connect() as connection:
        row = connection.execute(
            f"SELECT {quoted_fields} FROM company_posting_candidates WHERE id = ?",
            (storage.clean(candidate_id).upper(),),
        ).fetchone()
    return _resource_row("company_posting_candidates", row)


def replace_company_posting_candidates_for_import(rows):
    """Replace all tracked-company candidates for import/demo compatibility only."""
    ensure_initialized()
    fields = schema.COMPANY_POSTING_CANDIDATE_FIELDS
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    values = [
        [
            _resource_value("company_posting_candidates", field, row.get(field, ""))
            for field in fields
        ]
        for row in rows
    ]
    with write_transaction() as connection:
        connection.execute("DELETE FROM company_posting_candidates")
        if values:
            connection.executemany(
                f"INSERT INTO company_posting_candidates ({quoted_fields}) VALUES ({placeholders})",
                values,
            )


def write_company_posting_candidates(rows):
    """Compatibility alias for callers not yet migrated to row-level commands."""
    replace_company_posting_candidates_for_import(rows)


def upsert_company_posting_candidates(rows):
    ensure_initialized()
    if not rows:
        return []
    fields = schema.COMPANY_POSTING_CANDIDATE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(
        f'"{field}"=excluded."{field}"' for field in fields if field != "id"
    )
    values = [
        [
            _resource_value("company_posting_candidates", field, row.get(field, ""))
            for field in fields
        ]
        for row in rows
    ]
    with write_transaction() as connection:
        connection.executemany(
            f"INSERT INTO company_posting_candidates ({quoted_fields}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            values,
        )
    return [read_company_posting_candidate(row.get("id", "")) for row in rows]


def insert_company_posting_candidates(rows):
    return _insert_resource_rows(
        "company_posting_candidates",
        schema.COMPANY_POSTING_CANDIDATE_FIELDS,
        "CP",
        rows,
    )


def update_company_posting_candidate_fields(candidate_id, updates):
    return _update_resource_fields(
        "company_posting_candidates",
        schema.COMPANY_POSTING_CANDIDATE_FIELDS,
        candidate_id,
        updates,
    )


def compare_and_update_company_posting_candidate_fields(
    candidate_id,
    expected,
    updates,
):
    return _compare_and_update_resource_fields(
        "company_posting_candidates",
        schema.COMPANY_POSTING_CANDIDATE_FIELDS,
        candidate_id,
        expected,
        updates,
    )


def bulk_update_company_posting_candidate_fields(updates_by_id):
    return _bulk_update_resource_fields(
        "company_posting_candidates",
        schema.COMPANY_POSTING_CANDIDATE_FIELDS,
        updates_by_id,
    )


def update_company_posting_candidate_statuses(candidate_ids, status):
    updates = {
        storage.clean(candidate_id).upper(): {"status": storage.clean(status)}
        for candidate_id in candidate_ids or []
        if storage.clean(candidate_id)
    }
    return bulk_update_company_posting_candidate_fields(updates)


def read_company_career_scans(company_id="", limit=200):
    ensure_initialized()
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
    ensure_initialized()
    fields = schema.COMPANY_CAREER_SCAN_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f'"{field}"=excluded."{field}"' for field in fields[2:])
    values = [storage.clean(row.get(field, "")) for field in fields]
    with write_transaction() as connection:
        connection.execute(
            f"INSERT INTO company_career_scans ({quoted_fields}) VALUES ({placeholders}) "
            f"ON CONFLICT(company_id, checked_at) DO UPDATE SET {updates}",
            values,
        )
    return {field: values[index] for index, field in enumerate(fields)}


def clear_company_career_scans():
    ensure_initialized()
    with write_transaction() as connection:
        connection.execute("DELETE FROM company_career_scans")


def read_discovery_searches():
    rows = read_table("discovery_searches")
    rows.sort(key=lambda row: (row["name"].lower(), row["id"]))
    return rows


def save_discovery_searches_changes(rows):
    save_table_changes("discovery_searches", rows)


def replace_discovery_searches_for_import(rows):
    replace_table_for_import("discovery_searches", rows)


def read_discovery_candidates():
    ensure_initialized()
    fields = schema.DISCOVERY_CANDIDATE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    preserved_fields = {"description_text", "warnings", "notes"}
    with connect() as connection:
        rows = connection.execute(
            f"SELECT {quoted_fields} FROM discovery_candidates "
            "ORDER BY captured_at DESC, lower(title), id"
        ).fetchall()
    return [
        {
            field: (row[field] or "") if field in preserved_fields else storage.clean(row[field])
            for field in fields
        }
        for row in rows
    ]


def read_discovery_candidates_snapshot():
    """Read candidates and their revision from one consistent snapshot."""
    fields = schema.DISCOVERY_CANDIDATE_FIELDS
    preserved_fields = RESOURCE_PRESERVED_FIELDS["discovery_candidates"]
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with read_transaction() as connection:
        revision = data_revision(connection)
        rows = connection.execute(
            f"SELECT {quoted_fields} FROM discovery_candidates "
            "ORDER BY captured_at DESC, lower(title), id"
        ).fetchall()
    return revision, [
        {
            field: (row[field] or "")
            if field in preserved_fields
            else storage.clean(row[field])
            for field in fields
        }
        for row in rows
    ]


def reconcile_discovery_candidates(expected_revision, rows):
    """Apply topology-changing canonicalization only to its exact snapshot."""
    ensure_initialized()
    fields = schema.DISCOVERY_CANDIDATE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(
        f'"{field}"=excluded."{field}"' for field in fields if field != "id"
    )
    values = [
        [
            _resource_value("discovery_candidates", field, row.get(field, ""))
            for field in fields
        ]
        for row in rows
    ]
    ids = [value[0] for value in values]
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if data_revision(connection) != int(expected_revision):
            connection.rollback()
            return False
        if ids:
            id_placeholders = ", ".join("?" for _ in ids)
            connection.execute(
                f"DELETE FROM discovery_candidates WHERE id NOT IN ({id_placeholders})",
                ids,
            )
            connection.executemany(
                f"INSERT INTO discovery_candidates ({quoted_fields}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                values,
            )
        else:
            connection.execute("DELETE FROM discovery_candidates")
        _increment_data_revision(connection)
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def read_discovery_candidate(candidate_id):
    ensure_initialized()
    fields = schema.DISCOVERY_CANDIDATE_FIELDS
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    with connect() as connection:
        row = connection.execute(
            f"SELECT {quoted_fields} FROM discovery_candidates WHERE id = ?",
            (storage.clean(candidate_id).upper(),),
        ).fetchone()
    return _resource_row("discovery_candidates", row)


def replace_discovery_candidates_for_import(rows):
    """Replace all Discovery candidates for import/demo compatibility only."""
    ensure_initialized()
    fields = schema.DISCOVERY_CANDIDATE_FIELDS
    preserved_fields = {"description_text", "warnings", "notes"}
    placeholders = ", ".join("?" for _ in fields)
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    values = [
        [
            (row.get(field, "") or "") if field in preserved_fields
            else _resource_value("discovery_candidates", field, row.get(field, ""))
            for field in fields
        ]
        for row in rows
    ]
    with write_transaction() as connection:
        connection.execute("DELETE FROM discovery_candidates")
        if values:
            connection.executemany(
                f"INSERT INTO discovery_candidates ({quoted_fields}) VALUES ({placeholders})",
                values,
            )


def write_discovery_candidates(rows):
    """Compatibility alias for callers not yet migrated to row-level commands."""
    replace_discovery_candidates_for_import(rows)


def upsert_discovery_candidates(rows):
    ensure_initialized()
    if not rows:
        return []
    fields = schema.DISCOVERY_CANDIDATE_FIELDS
    preserved_fields = RESOURCE_PRESERVED_FIELDS["discovery_candidates"]
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(
        f'"{field}"=excluded."{field}"' for field in fields if field != "id"
    )
    values = [
        [
            (row.get(field, "") or "") if field in preserved_fields
            else _resource_value("discovery_candidates", field, row.get(field, ""))
            for field in fields
        ]
        for row in rows
    ]
    with write_transaction() as connection:
        connection.executemany(
            f"INSERT INTO discovery_candidates ({quoted_fields}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            values,
        )
    return [read_discovery_candidate(row.get("id", "")) for row in rows]


def insert_discovery_candidates(rows):
    return _insert_resource_rows(
        "discovery_candidates",
        schema.DISCOVERY_CANDIDATE_FIELDS,
        "DC",
        rows,
    )


def update_discovery_candidate_fields(candidate_id, updates):
    return _update_resource_fields(
        "discovery_candidates",
        schema.DISCOVERY_CANDIDATE_FIELDS,
        candidate_id,
        updates,
    )


def compare_and_update_discovery_candidate_fields(candidate_id, expected, updates):
    return _compare_and_update_resource_fields(
        "discovery_candidates",
        schema.DISCOVERY_CANDIDATE_FIELDS,
        candidate_id,
        expected,
        updates,
    )


def bulk_update_discovery_candidate_fields(updates_by_id):
    return _bulk_update_resource_fields(
        "discovery_candidates",
        schema.DISCOVERY_CANDIDATE_FIELDS,
        updates_by_id,
    )


def update_discovery_candidate_statuses(candidate_ids, status, **status_fields):
    fields = {
        "status": storage.clean(status),
        **{
            key: value
            for key, value in status_fields.items()
            if key in {
                "ingested_application_id",
                "ignore_reason",
                "ignore_reason_detail",
                "freshness_status",
                "freshness_checked_at",
            }
        },
    }
    updates = {
        storage.clean(candidate_id).upper(): fields
        for candidate_id in candidate_ids or []
        if storage.clean(candidate_id)
    }
    return bulk_update_discovery_candidate_fields(updates)


def read_posting_note(application_id):
    ensure_initialized()
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
    ensure_initialized()
    note = {
        "application_id": storage.clean(application_id).upper(),
        "path": storage.clean(path),
        "content": content or "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with write_transaction() as connection:
        connection.execute(
            "INSERT INTO posting_notes(application_id, path, content, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(application_id) DO UPDATE SET "
            "path=excluded.path, content=excluded.content, updated_at=excluded.updated_at",
            (note["application_id"], note["path"], note["content"], note["updated_at"]),
        )
    return note


def import_posting_notes_from_files(overwrite=False):
    ensure_initialized()
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
    ensure_initialized()
    with connect() as connection:
        return connection.execute("SELECT COUNT(*) AS total FROM posting_notes").fetchone()["total"]


def read_posting_snapshots(application_id=""):
    ensure_initialized()
    params = []
    where = ""
    wanted = storage.clean(application_id).upper()
    if wanted:
        where = " WHERE upper(application_id) = ?"
        params.append(wanted)
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, application_id, source_url, final_url, captured_at, http_status, capture_method, "
            "capture_model, sources_json, "
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
    capture_method = storage.clean(values.get("capture_method")) or "fetch"
    fingerprint = (content_text if capture_method == "ai-web" else source_html) or content_text or "|".join(
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
        "capture_method": capture_method,
        "capture_model": storage.clean(values.get("capture_model")),
        "sources_json": values.get("sources_json", "[]") or "[]",
        "content_hash": content_hash,
        "content_text": content_text,
        "source_html": source_html,
        "warnings": values.get("warnings", "") or "",
    }
    with write_transaction() as connection:
        connection.execute(
            "INSERT INTO posting_snapshots("
            "application_id, source_url, final_url, captured_at, http_status, capture_method, capture_model, sources_json, content_hash, content_text, source_html, warnings"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(application_id, content_hash) DO NOTHING",
            tuple(row[field] for field in schema.POSTING_SNAPSHOT_FIELDS[1:]),
        )
        saved = connection.execute(
            "SELECT id, application_id, source_url, final_url, captured_at, http_status, capture_method, "
            "capture_model, sources_json, "
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
    ensure_initialized()
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
    ensure_initialized()
    fields = schema.RESUME_VERSION_FIELDS
    values = [
        (row.get(field, "") or "") if field in {"guidance", "changes_json", "warnings_json"}
        else storage.clean(row.get(field, ""))
        for field in fields
    ]
    quoted_fields = ", ".join(f'"{field}"' for field in fields)
    placeholders = ", ".join("?" for _ in fields)
    with write_transaction() as connection:
        connection.execute(
            f"INSERT INTO resume_versions ({quoted_fields}) VALUES ({placeholders})",
            values,
        )
    return {field: values[index] for index, field in enumerate(fields)}


def record_event(entity_type, entity_id, event_type, data):
    payload = json.dumps(data, sort_keys=True)
    created_at = datetime.now().isoformat(timespec="seconds")
    with write_transaction() as connection:
        connection.execute(
            "INSERT INTO events(entity_type, entity_id, event_type, created_at, data_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (entity_type, entity_id, event_type, created_at, payload),
        )


def update_action_status(action_id, status):
    ensure_initialized()
    status = normalize_action_status(status)
    if status not in schema.ACTION_STATUSES:
        raise ValueError(f"Unsupported action status: {status}")

    wanted = storage.clean(action_id).upper()
    completed_date = storage.today_iso() if status in schema.COMPLETED_ACTION_STATUSES else ""
    with write_transaction() as connection:
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
        _sync_next_action(connection, after["application_id"])
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
