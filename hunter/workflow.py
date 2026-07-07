"""Workflow stage and action-type definitions for Hunter."""

import re

from . import schema, sqlite_store, storage


def slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", storage.clean(value).lower()).strip("-")


def truthy(value):
    return "1" if storage.clean(value).lower() in {"1", "true", "yes", "on"} else ""


def normalize_int(value, default="0"):
    cleaned = storage.clean(value)
    if not cleaned:
        return default
    try:
        return str(int(cleaned))
    except ValueError as exc:
        raise ValueError(f"Expected an integer value, got '{cleaned}'.") from exc


def normalize_allowed_stages(value):
    if isinstance(value, list):
        raw = ",".join(storage.clean(item) for item in value)
    else:
        raw = storage.clean(value)
    return ",".join(slugify(item) for item in raw.split(",") if slugify(item))


def normalize_stage(row):
    stage_id = slugify(row.get("id") or row.get("label"))
    if not stage_id:
        raise ValueError("Stage id or label is required.")
    return {
        "id": stage_id,
        "label": storage.clean(row.get("label")) or stage_id.replace("-", " ").title(),
        "sort_order": normalize_int(row.get("sort_order"), "0"),
        "is_terminal": truthy(row.get("is_terminal")),
        "is_active": "1" if row.get("is_active") is None else truthy(row.get("is_active")),
    }


def normalize_action_type(row):
    action_type_id = slugify(row.get("id") or row.get("label"))
    if not action_type_id:
        raise ValueError("Action type id or label is required.")
    priority = storage.clean(row.get("default_priority") or schema.DEFAULT_PRIORITY).lower()
    if priority not in {"high", "medium", "low"}:
        raise ValueError("Action type default_priority must be high, medium, or low.")
    return {
        "id": schema.ACTION_TYPE_ALIASES.get(action_type_id, action_type_id),
        "label": storage.clean(row.get("label")) or action_type_id.replace("-", " ").title(),
        "description": storage.clean(row.get("description")),
        "default_priority": priority,
        "default_due_days": normalize_int(row.get("default_due_days"), "1"),
        "allowed_stages": normalize_allowed_stages(row.get("allowed_stages", "")),
        "sort_order": normalize_int(row.get("sort_order"), "0"),
        "is_active": "1" if row.get("is_active") is None else truthy(row.get("is_active")),
    }


def read_workflow():
    sqlite_store.initialize()
    with sqlite_store.connect() as connection:
        stages = connection.execute(
            "SELECT id, label, sort_order, is_terminal, is_active "
            "FROM workflow_stages ORDER BY CAST(sort_order AS INTEGER), label"
        ).fetchall()
        action_types = connection.execute(
            "SELECT id, label, description, default_priority, default_due_days, allowed_stages, sort_order, is_active "
            "FROM workflow_action_types ORDER BY CAST(sort_order AS INTEGER), label"
        ).fetchall()
    return {
        "stages": [{field: storage.clean(row[field]) for field in schema.WORKFLOW_STAGE_FIELDS} for row in stages],
        "action_types": [
            {field: storage.clean(row[field]) for field in schema.WORKFLOW_ACTION_TYPE_FIELDS}
            for row in action_types
        ],
        "outcomes": sorted(schema.TERMINAL_OUTCOMES),
    }


def active_stage_ids():
    workflow = read_workflow()
    return {row["id"] for row in workflow["stages"] if row.get("is_active") == "1"}


def active_action_type_ids():
    workflow = read_workflow()
    return {row["id"] for row in workflow["action_types"] if row.get("is_active") == "1"}


def action_type_by_id(action_type_id):
    wanted = schema.ACTION_TYPE_ALIASES.get(slugify(action_type_id), slugify(action_type_id))
    for row in read_workflow()["action_types"]:
        if row["id"] == wanted:
            return row
    return None


def validate_stage(stage, allow_inactive=False):
    stage_id = slugify(stage)
    if not stage_id:
        stage_id = schema.DEFAULT_STAGE
    for row in read_workflow()["stages"]:
        if row["id"] == stage_id and (allow_inactive or row.get("is_active") == "1"):
            return stage_id
    raise ValueError(f"Unsupported posting stage: {stage}")


def validate_outcome(stage, outcome):
    outcome = slugify(outcome)
    if stage != "closed":
        return ""
    if not outcome:
        raise ValueError("Closed postings require an outcome.")
    if outcome not in schema.TERMINAL_OUTCOMES:
        raise ValueError(f"Unsupported posting outcome: {outcome}")
    return outcome


def validate_action_type(action_type_id, allow_inactive=False):
    normalized = schema.ACTION_TYPE_ALIASES.get(slugify(action_type_id), slugify(action_type_id))
    for row in read_workflow()["action_types"]:
        if row["id"] == normalized and (allow_inactive or row.get("is_active") == "1"):
            return normalized
    raise ValueError(f"Unsupported action type: {action_type_id}")


def upsert_stage(payload):
    row = normalize_stage(payload or {})
    sqlite_store.initialize()
    with sqlite_store.connect() as connection:
        connection.execute(
            "INSERT INTO workflow_stages(id, label, sort_order, is_terminal, is_active) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "label=excluded.label, sort_order=excluded.sort_order, "
            "is_terminal=excluded.is_terminal, is_active=excluded.is_active",
            tuple(row[field] for field in schema.WORKFLOW_STAGE_FIELDS),
        )
    return row


def archive_stage(stage_id):
    stage_id = validate_stage(stage_id, allow_inactive=True)
    if stage_id == "closed":
        raise ValueError("The closed stage cannot be archived.")
    with sqlite_store.connect() as connection:
        connection.execute("UPDATE workflow_stages SET is_active = '' WHERE id = ?", (stage_id,))
    return {"id": stage_id, "is_active": ""}


def upsert_action_type(payload):
    row = normalize_action_type(payload or {})
    allowed = set(filter(None, row["allowed_stages"].split(",")))
    known = {stage["id"] for stage in read_workflow()["stages"]}
    unknown = sorted(allowed - known)
    if unknown:
        raise ValueError(f"Unknown allowed stage for action type: {unknown[0]}")
    sqlite_store.initialize()
    with sqlite_store.connect() as connection:
        connection.execute(
            "INSERT INTO workflow_action_types("
            "id, label, description, default_priority, default_due_days, allowed_stages, sort_order, is_active"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "label=excluded.label, description=excluded.description, "
            "default_priority=excluded.default_priority, default_due_days=excluded.default_due_days, "
            "allowed_stages=excluded.allowed_stages, sort_order=excluded.sort_order, is_active=excluded.is_active",
            tuple(row[field] for field in schema.WORKFLOW_ACTION_TYPE_FIELDS),
        )
    return row


def archive_action_type(action_type_id):
    action_type_id = validate_action_type(action_type_id, allow_inactive=True)
    with sqlite_store.connect() as connection:
        connection.execute("UPDATE workflow_action_types SET is_active = '' WHERE id = ?", (action_type_id,))
    return {"id": action_type_id, "is_active": ""}
