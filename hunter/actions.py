"""Action storage operations shared by CLI, app server, and future UIs."""

import re

from . import paths, repository, schema, sqlite_store, storage, workflow


def action_key(action):
    return (
        action.get("application_id", "").upper(),
        normalize_action_type(action.get("type", "")),
        action.get("title", "").lower(),
    )


def normalize_action_type(value):
    cleaned = storage.clean(value).lower().replace("_", "-")
    return schema.ACTION_TYPE_ALIASES.get(cleaned, cleaned)


def normalize_action_status(value):
    cleaned = storage.clean(value).lower()
    return schema.ACTION_STATUS_ALIASES.get(cleaned, cleaned)


def next_action_id(rows):
    highest = 0
    for row in rows:
        match = re.fullmatch(r"T(\d+)", row.get("id", "").upper())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"T{highest + 1:04d}"


def open_actions(rows):
    return [
        row
        for row in rows
        if row.get("status", "").lower() not in schema.COMPLETED_ACTION_STATUSES
    ]


def sort_open_actions(rows):
    priority_rank = {"high": 0, "medium": 1, "low": 2}

    def key(row):
        due = storage.parse_date(row.get("due_date")) or storage.parse_date("9999-12-31")
        return (
            due,
            priority_rank.get(row.get("priority", "").lower(), 3),
            row.get("created_date", ""),
            row.get("id", ""),
        )

    return sorted(open_actions(rows), key=key)


def select_next_action(application, rows):
    open_rows = sort_open_actions(rows)
    selected_id = application.get("next_action_id", "").upper()
    if selected_id:
        selected = next((row for row in open_rows if row.get("id", "").upper() == selected_id), None)
        if selected:
            return selected
    return next(iter(open_rows), None)


def upsert_action(rows, action):
    action = {**action, "type": normalize_action_type(action.get("type", ""))}
    if repository.using_sqlite():
        workflow.validate_action_type(action.get("type", ""))
    existing = {action_key(row): row for row in open_actions(rows)}
    key = action_key(action)
    row = existing.get(key)
    created = row is None
    if created:
        row = {field: "" for field in schema.ACTION_FIELDS}
        row["id"] = next_action_id(rows)
        row["created_date"] = storage.today_iso()
        rows.append(row)

    for field in schema.ACTION_FIELDS:
        if field == "id":
            continue
        value = action.get(field)
        if value is not None:
            row[field] = storage.clean(value)
    return created, row


def update_action_status(action_id, status):
    status = normalize_action_status(status)
    if status not in schema.ACTION_STATUSES:
        raise ValueError(f"Unsupported action status: {status}")

    if repository.using_sqlite():
        row = sqlite_store.update_action_status(action_id, status)
        sync_next_action(row.get("application_id", ""))
        return row

    rows = storage.read_rows(paths.ACTIONS, schema.ACTION_FIELDS)
    wanted = storage.clean(action_id).upper()
    for row in rows:
        if row.get("id", "").upper() != wanted:
            continue
        row["status"] = status
        row["completed_date"] = storage.today_iso() if status in schema.COMPLETED_ACTION_STATUSES else ""
        storage.write_rows(paths.ACTIONS, schema.ACTION_FIELDS, rows)
        sync_next_action(row.get("application_id", ""))
        return row

    raise ValueError(f"No action found with id {action_id}.")


def update_action_fields(action_id, updates):
    wanted = storage.clean(action_id).upper()
    editable = {"title", "description", "type", "priority", "due_date", "related_url", "notes"}
    rows = repository.read_actions()
    for row in rows:
        if row.get("id", "").upper() != wanted:
            continue
        for field, value in (updates or {}).items():
            if field not in editable:
                continue
            if field == "type":
                row[field] = workflow.validate_action_type(value)
            elif field == "due_date":
                try:
                    row[field] = storage.normalize_date(value)
                except SystemExit as exc:
                    raise ValueError(str(exc)) from exc
            elif field == "priority":
                priority = storage.clean(value).lower()
                if priority and priority not in {"high", "medium", "low"}:
                    raise ValueError("Action priority must be high, medium, or low.")
                row[field] = priority
            else:
                row[field] = storage.clean(value)
        repository.write_actions(rows)
        sync_next_action(row.get("application_id", ""))
        return row
    raise ValueError(f"No action found with id {action_id}.")


def make_next_action(action_id):
    wanted = storage.clean(action_id).upper()
    action = None
    for row in repository.read_actions():
        if row.get("id", "").upper() == wanted:
            action = row
            break
    if action is None:
        raise ValueError(f"No action found with id {action_id}.")
    if action.get("status", "").lower() in schema.COMPLETED_ACTION_STATUSES:
        raise ValueError("Completed actions cannot be the next action.")

    application_id = action.get("application_id", "").upper()
    applications = repository.read_applications()
    for app in applications:
        if app.get("id", "").upper() == application_id:
            app["next_action_id"] = action.get("id", "")
            repository.write_applications(applications)
            return sync_next_action(application_id)
    raise ValueError(f"No application found with id {application_id}.")


def sync_next_action(application_id):
    wanted = storage.clean(application_id).upper()
    if not wanted:
        return None

    applications = repository.read_applications()
    target = None
    for row in applications:
        if row.get("id", "").upper() == wanted:
            target = row
            break
    if target is None:
        return None

    if target.get("stage", "").lower() == "closed":
        target["next_action_id"] = ""
        target["next_action"] = ""
        target["next_action_date"] = ""
    else:
        related_actions = [
            row
            for row in repository.read_actions()
            if row.get("application_id", "").upper() == wanted
        ]
        next_action = select_next_action(target, related_actions)
        target["next_action_id"] = next_action.get("id", "") if next_action else ""
        target["next_action"] = next_action.get("title", "") if next_action else ""
        target["next_action_date"] = next_action.get("due_date", "") if next_action else ""

    repository.write_applications(applications)
    return target
