"""Runtime state serialization for the Hunter dashboard."""

from datetime import date, datetime

from . import actions as action_store
from . import discovery as discovery_store
from . import companies as company_store
from . import candidate_eligibility, repository, schema, storage, suggestions, workflow


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
        open_related_actions = action_store.open_actions(related_actions)
        normalized["open_action_count"] = len(open_related_actions)
        normalized["next_action_warning"] = ""
        if normalized.get("stage", "").lower() == "considering" and len(open_related_actions) != 1:
            normalized["next_action_warning"] = (
                "Considering requires exactly one open next action; "
                f"found {len(open_related_actions)}."
            )
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
        if candidate.get("recommendation_eligible"):
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


def enrich_company_candidates(candidate_rows, discovery_candidates, searches):
    company_by_id = {
        company.get("id", "").upper(): company
        for company in repository.read_companies()
    }
    application_rows = repository.read_applications()
    tracked_context_by_company_id = {
        company_id: company_store.tracked_posting_context(
            company,
            application_rows=application_rows,
        )
        for company_id, company in company_by_id.items()
    }
    discovery_entries_by_company_id = {}
    for discovery_candidate in discovery_candidates:
        if discovery_candidate.get("status") not in {"new", "pursued", "ignored", "duplicate"}:
            continue
        discovery_company_id = discovery_candidate.get("company_id", "").upper()
        identity_keys = set()
        for url in discovery_store.candidate_source_urls(discovery_candidate):
            identity_keys.update(company_store.posting_identity_keys(url))
        discovery_entries_by_company_id.setdefault(discovery_company_id, []).append(
            {
                "id": discovery_candidate.get("id", ""),
                "requisition_ids": set(discovery_candidate.get("requisition_ids", [])),
                "identity_keys": identity_keys,
            }
        )
    enriched = []
    for candidate in candidate_rows:
        payload = dict(candidate)
        payload["review_state"] = company_store.candidate_review_state(candidate)
        payload["requisition_ids"] = sorted(
            company_store.normalized_requisition_ids(candidate.get("url", ""))
        )
        candidate_company_id = candidate.get("company_id", "").upper()
        company = company_by_id.get(candidate_company_id)
        payload["matching_posting_ids"] = (
            company_store.matching_tracked_posting_ids(
                candidate,
                company=company,
                tracked=tracked_context_by_company_id.get(candidate_company_id),
            )
            if company
            else []
        )
        payload["lane_match"] = discovery_store.candidate_lane_match(
            {
                **candidate,
                "description_text": candidate.get("description_excerpt", ""),
            },
            searches,
        )
        candidate_requisitions = set(payload["requisition_ids"])
        candidate_keys = company_store.posting_identity_keys(candidate.get("url", ""))
        payload["discovery_candidate_id"] = ""
        for discovery_entry in discovery_entries_by_company_id.get(candidate_company_id, []):
            discovery_requisitions = discovery_entry["requisition_ids"]
            if candidate_requisitions and discovery_requisitions:
                matched = bool(candidate_requisitions & discovery_requisitions)
            else:
                matched = bool(candidate_keys & discovery_entry["identity_keys"])
            if matched:
                payload["discovery_candidate_id"] = discovery_entry["id"]
                break
        enriched.append(payload)
    return enriched


def build_payload(include_excluded_companies=False):
    company_rows = repository.read_companies()
    _discovery_rows, excluded_discovery_rows, _company_by_id = discovery_store.candidate_review_rows(
        include_excluded_companies=include_excluded_companies
    )
    discovery_candidates = discovery_store.list_candidates(
        include_excluded_companies=include_excluded_companies
    )
    discovery_searches = discovery_store.list_searches()
    company_candidate_rows, excluded_company_candidate_rows = candidate_eligibility.partition_candidates(
        repository.read_company_posting_candidates(),
        company_rows,
        include_excluded_companies=include_excluded_companies,
    )
    company_candidates = enrich_company_candidates(
        company_candidate_rows,
        discovery_candidates,
        discovery_searches,
    )
    companies = enrich_companies(
        company_rows,
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
        "candidate_review_audit": {
            "excluded_company_candidate_count": (
                len(excluded_discovery_rows) + len(excluded_company_candidate_rows)
            ),
            "discovery_excluded_company_candidate_count": len(excluded_discovery_rows),
            "tracked_company_excluded_company_candidate_count": len(excluded_company_candidate_rows),
        },
        "discovery_searches": discovery_searches,
        "discovery_candidates": discovery_candidates,
        "discovery_preference_suggestions": discovery_store.preference_suggestions(discovery_candidates),
        "dismissed_suggestion_ids": sorted(suggestions.dismissed_ids()),
    }
