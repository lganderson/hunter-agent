"""Runtime state serialization for the Hunter dashboard."""

from datetime import date, datetime

from . import actions as action_store
from . import paths, repository, schema, storage, workflow


COMPLETED_ACTION_STATUSES = schema.COMPLETED_ACTION_STATUSES


def read_applications():
    rows = repository.read_applications()
    all_actions = repository.read_actions()
    for normalized in rows:
        related_actions = [
            action
            for action in all_actions
            if action.get("application_id", "").upper() == normalized.get("id", "").upper()
        ]
        if normalized.get("stage", "").lower() == "closed":
            normalized["next_action_id"] = ""
            normalized["next_action"] = ""
            normalized["next_action_date"] = ""
        else:
            next_action = action_store.select_next_action(normalized, related_actions)
            normalized["next_action_id"] = next_action.get("id", "") if next_action else ""
            normalized["next_action"] = next_action.get("title", "") if next_action else ""
            normalized["next_action_date"] = next_action.get("due_date", "") if next_action else ""
        normalized["tags"] = normalized.get("tags", "")
        normalized["tag_list"] = storage.split_tags(normalized["tags"])
        posting_file = normalized.get("posting_file", "")
        stored_note = repository.read_posting_note(normalized.get("id", ""))
        note_path = paths.ROOT / posting_file if posting_file else None
        file_exists = bool(note_path and note_path.exists())
        if stored_note:
            normalized["posting_markdown"] = stored_note.get("content", "")
            normalized["posting_file"] = stored_note.get("path") or posting_file
            normalized["posting_file_exists"] = True
        else:
            normalized["posting_markdown"] = note_path.read_text(encoding="utf-8") if file_exists else ""
            normalized["posting_file_exists"] = file_exists
    return rows


def read_actions():
    return repository.read_actions()


def enrich_rows(rows):
    today = date.today()
    for row in rows:
        due = storage.parse_date(row.get("next_action_date", ""))
        row["is_closed"] = row.get("stage", "").lower() == "closed"
        row["is_active"] = not row["is_closed"]
        row["is_overdue"] = bool(due and due < today and row["is_active"])
        row["is_due_soon"] = bool(due and 0 <= (due - today).days <= 7 and row["is_active"])
        row["days_until_next_action"] = (due - today).days if due else None
        row["sort_due"] = due.isoformat() if due else "9999-12-31"
    return rows


def enrich_actions(actions):
    today = date.today()
    for action in actions:
        status = action.get("status", "").lower()
        due = storage.parse_date(action.get("due_date", ""))
        action["is_complete"] = status in COMPLETED_ACTION_STATUSES
        action["is_open"] = not action["is_complete"]
        action["is_overdue"] = bool(due and due < today and action["is_open"])
        action["is_due_soon"] = bool(due and 0 <= (due - today).days <= 7 and action["is_open"])
        action["days_until_due"] = (due - today).days if due else None
        action["sort_due"] = due.isoformat() if due else "9999-12-31"
    return actions


def build_payload():
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generated_date": date.today().isoformat(),
        "applications": enrich_rows(read_applications()),
        "actions": enrich_actions(read_actions()),
        "workflow": workflow.read_workflow(),
        "contacts": repository.read_contacts(),
        "application_contacts": repository.read_application_contacts(),
        "companies": repository.read_companies(),
        "company_contacts": repository.read_company_contacts(),
        "company_career_sources": repository.read_company_career_sources(),
        "company_posting_candidates": repository.read_company_posting_candidates(),
    }
