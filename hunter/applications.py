"""Application/posting update operations shared by the local app surfaces."""

from . import actions, repository, schema, storage, workflow


EDITABLE_FIELDS = {
    "company_id",
    "company",
    "stage",
    "outcome",
    "tags",
    "priority",
    "date_applied",
    "contact",
    "resume_version",
    "cover_letter",
    "notes",
}


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
            elif field == "date_applied":
                row[field] = storage.normalize_date(value)
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
