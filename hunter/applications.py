"""Application/posting create and update operations shared by local app surfaces."""

import re

from . import actions, repository, schema, storage, workflow


EDITABLE_FIELDS = {
    "company_id",
    "company",
    "role",
    "location",
    "work_mode",
    "source",
    "source_url",
    "compensation",
    "stage",
    "outcome",
    "tags",
    "priority",
    "date_found",
    "date_applied",
    "contact",
    "resume_version",
    "cover_letter",
    "notes",
}


def next_application_id(rows):
    highest = 0
    for row in rows:
        match = re.fullmatch(r"A(\d+)", row.get("id", "").upper())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"A{highest + 1:04d}"


def normalize_date(value):
    try:
        return storage.normalize_date(value)
    except SystemExit as exc:
        raise ValueError(str(exc)) from exc


def create_application(values):
    values = values or {}
    role = storage.clean(values.get("role", ""))
    company = storage.clean(values.get("company", ""))
    company_id = storage.clean(values.get("company_id", "")).upper()
    companies = repository.read_companies()
    managed_company = next((item for item in companies if item.get("id", "").upper() == company_id), None)
    if company_id and managed_company is None:
        raise ValueError(f"No company found with id {company_id}.")
    if managed_company:
        company = storage.clean(managed_company.get("name", ""))
    if not role:
        raise ValueError("Role is required.")
    if not company:
        raise ValueError("Company is required.")

    rows = repository.read_applications()
    row = {field: "" for field in schema.APPLICATION_FIELDS}
    row.update({"id": next_application_id(rows), "company": company, "role": role})
    row["stage"] = workflow.validate_stage(values.get("stage") or schema.DEFAULT_STAGE)
    row["outcome"] = workflow.validate_outcome(row["stage"], values.get("outcome", ""))
    row["priority"] = storage.clean(values.get("priority") or schema.DEFAULT_PRIORITY).lower()
    if row["priority"] not in {"high", "medium", "low"}:
        raise ValueError("Posting priority must be high, medium, or low.")
    row["date_found"] = normalize_date(values.get("date_found") or "today")
    for field, value in values.items():
        if field not in EDITABLE_FIELDS or field in {"company", "role", "stage", "outcome", "priority", "date_found"}:
            continue
        if field == "tags":
            row[field] = storage.normalize_tags(value)
        elif field == "company_id":
            row[field] = company_id
        elif field == "date_applied":
            row[field] = normalize_date(value)
        else:
            row[field] = storage.clean(value)
    rows.append(row)
    repository.write_applications(rows)
    return row


def sync_related_action_identity(application):
    application_id = application.get("id", "").upper()
    if not application_id:
        return

    actions = repository.read_actions()
    changed = False
    for action in actions:
        if action.get("application_id", "").upper() != application_id:
            continue
        for field in ["company", "role"]:
            next_value = storage.clean(application.get(field, ""))
            if next_value and action.get(field, "") != next_value:
                action[field] = next_value
                changed = True
    if changed:
        repository.write_actions(actions)


def update_application(application_id, updates):
    wanted = storage.clean(application_id).upper()
    rows = repository.read_applications()
    for row in rows:
        if row.get("id", "").upper() != wanted:
            continue
        next_stage = row.get("stage", "")
        next_outcome = row.get("outcome", "")
        if "stage" in (updates or {}):
            next_stage = workflow.validate_stage(updates.get("stage", ""))
        if "outcome" in (updates or {}):
            next_outcome = updates.get("outcome", "")
        next_outcome = workflow.validate_outcome(next_stage, next_outcome)
        for field, value in (updates or {}).items():
            if field not in EDITABLE_FIELDS or field not in schema.APPLICATION_FIELDS:
                continue
            if field == "tags":
                row[field] = storage.normalize_tags(value)
            elif field == "company_id":
                company_id = storage.clean(value).upper()
                company = next((item for item in repository.read_companies() if item.get("id", "").upper() == company_id), None)
                if company_id and company is None:
                    raise ValueError(f"No company found with id {value}.")
                row[field] = company_id
                if company:
                    row["company"] = storage.clean(company.get("name", ""))
            elif field == "stage":
                row[field] = next_stage
            elif field == "outcome":
                row[field] = next_outcome
            elif field in {"date_found", "date_applied"}:
                row[field] = normalize_date(value)
            elif field == "priority":
                priority = storage.clean(value).lower()
                if priority and priority not in {"high", "medium", "low"}:
                    raise ValueError("Posting priority must be high, medium, or low.")
                row[field] = priority
            else:
                row[field] = storage.clean(value)
        row["outcome"] = next_outcome
        repository.write_applications(rows)
        if any(field in (updates or {}) for field in ["company_id", "company", "role"]):
            sync_related_action_identity(row)
        if row.get("stage", "") == "closed":
            actions.sync_next_action(row.get("id", ""))
        return row
    raise ValueError(f"No application found with id {application_id}.")
