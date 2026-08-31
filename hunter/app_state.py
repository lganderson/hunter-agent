"""Runtime state serialization for the Hunter dashboard."""

from datetime import date, datetime

from . import actions as action_store
from . import discovery as discovery_store
from . import companies as company_store
from . import repository, schema, storage, suggestions, workflow


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


def enrich_companies(company_rows, discovery_candidates, company_candidates=None):
    counts = {}
    for candidate in discovery_candidates:
        company_id = candidate.get("company_id", "")
        if not company_id:
            continue
        summary = counts.setdefault(
            company_id,
            {"roles": 0, "recommended": 0, "ignored": 0, "pursued": 0},
        )
        summary["roles"] += 1
        status = candidate.get("status", "")
        if status == "ignored":
            summary["ignored"] += 1
        elif status == "pursued":
            summary["pursued"] += 1
        try:
            fit_score = int(candidate.get("fit_score", "") or 0)
        except (TypeError, ValueError):
            fit_score = 0
        if (
            candidate.get("status", "") == "new"
            and candidate.get("processing_status", "") == "ready"
            and fit_score >= 45
        ):
            summary["recommended"] += 1

    for candidate in company_candidates or []:
        company_id = candidate.get("company_id", "")
        if not company_id:
            continue
        summary = counts.setdefault(
            company_id,
            {"roles": 0, "recommended": 0, "ignored": 0, "pursued": 0},
        )
        status = candidate.get("status", "")
        if status == "ignored":
            summary["ignored"] += 1
        elif status == "pursued":
            summary["pursued"] += 1

    enriched = []
    for company in company_rows:
        row = dict(company)
        summary = counts.get(
            row.get("id", ""),
            {"roles": 0, "recommended": 0, "ignored": 0, "pursued": 0},
        )
        row["discovery_role_count"] = summary["roles"]
        row["recommended_discovery_role_count"] = summary["recommended"]
        row["ignored_role_count"] = summary["ignored"]
        row["pursued_role_count"] = summary["pursued"]
        row["tracking_recommendation"] = ""
        row["decision_recommendation"] = ""
        if row.get("tracking_status", "") == "discovered":
            if summary["recommended"] >= 2:
                row["tracking_recommendation"] = (
                    f"Hunter suggests tracking: {summary['recommended']} recommended "
                    "Discovery roles."
                )
            elif summary["recommended"] == 1:
                row["tracking_recommendation"] = (
                    "Worth reviewing: one recommended Discovery role."
                )
            elif summary["roles"]:
                row["tracking_recommendation"] = (
                    f"Discovered through {summary['roles']} role"
                    f"{'' if summary['roles'] == 1 else 's'}; keep passive until fit improves."
                )
        if (
            row.get("interest_status", "").lower() == "neutral"
            and summary["ignored"] >= 2
            and summary["pursued"] == 0
        ):
            row["decision_recommendation"] = (
                f"You ignored {summary['ignored']} roles from this company and have not kept any. "
                "If the company itself is not a fit, mark it Not interested to stop future roles."
            )
        enriched.append(row)
    return enriched


def build_payload():
    discovery_candidates = discovery_store.list_candidates()
    company_candidates = repository.read_company_posting_candidates()
    companies = enrich_companies(
        repository.read_companies(),
        discovery_candidates,
        company_candidates,
    )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generated_date": date.today().isoformat(),
        "applications": enrich_rows(read_applications()),
        "actions": enrich_actions(read_actions()),
        "workflow": workflow.read_workflow(),
        "contacts": repository.read_contacts(),
        "application_contacts": repository.read_application_contacts(),
        "companies": companies,
        "company_merge_suggestions": company_store.company_merge_suggestions(),
        "company_contacts": repository.read_company_contacts(),
        "company_career_sources": repository.read_company_career_sources(),
        "company_posting_candidates": company_candidates,
        "company_career_scans": repository.read_company_career_scans(limit=200),
        "discovery_searches": discovery_store.list_searches(),
        "discovery_candidates": discovery_candidates,
        "discovery_preference_suggestions": discovery_store.preference_suggestions(discovery_candidates),
        "dismissed_suggestion_ids": sorted(suggestions.dismissed_ids()),
    }
