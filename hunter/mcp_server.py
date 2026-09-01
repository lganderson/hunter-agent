"""Minimal stdio MCP server for local Hunter data.

This intentionally avoids third-party dependencies. It implements the JSON-RPC
methods needed for MCP tool discovery and tool calls.
"""

import json
import subprocess
import sys

from . import actions as action_store
from . import app_state
from . import applications as application_store
from . import candidate_eligibility
from . import discovery as discovery_store
from . import posting_snapshots as posting_snapshot_store
from . import companies as company_store
from . import contacts as contact_store
from . import paths, repository, schema, settings as settings_store, sqlite_store, storage


SERVER_INFO = {"name": "hunter", "version": "0.1.0"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_LIST_LIMIT = 25
MAX_LIST_LIMIT = 100
DETAIL_LIST_LIMIT = 25
PREVIEW_CHARS = 260


def text_result(payload):
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, sort_keys=True),
            }
        ]
    }


def error_response(request_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def preview_text(value, max_chars=PREVIEW_CHARS):
    text = storage.clean(str(value or ""))
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def requested_limit(args, default=DEFAULT_LIST_LIMIT, maximum=MAX_LIST_LIMIT):
    try:
        return max(1, min(maximum, int(args.get("limit") or default)))
    except (TypeError, ValueError):
        return default


def compact_application(app, detail=False):
    fields = [
        "id",
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
        "next_action_id",
        "next_action",
        "next_action_date",
    ]
    if detail:
        fields.extend(
            [
                "contact",
                "resume_version",
                "cover_letter",
                "notes",
            ]
        )
    row = {field: app.get(field, "") for field in fields}
    row["requisition_ids"] = sorted(company_store.normalized_requisition_ids(app.get("source_url", "")))
    row["open_action_count"] = int(app.get("open_action_count", 0) or 0)
    row["next_action_warning"] = app.get("next_action_warning", "")
    if not detail:
        row["notes_preview"] = preview_text(app.get("notes", ""))
    return row


def compact_action(action, detail=False):
    if detail:
        return {field: action.get(field, "") for field in schema.ACTION_FIELDS}
    fields = [
        "id",
        "application_id",
        "company",
        "role",
        "type",
        "title",
        "status",
        "priority",
        "due_date",
        "created_date",
        "completed_date",
        "related_url",
    ]
    row = {field: action.get(field, "") for field in fields}
    row["description_preview"] = preview_text(action.get("description", ""))
    row["notes_preview"] = preview_text(action.get("notes", ""))
    return row


def compact_contact(contact):
    return {field: contact.get(field, "") for field in schema.CONTACT_FIELDS}


def compact_company(company, detail=False):
    row = {field: company.get(field, "") for field in schema.COMPANY_FIELDS}
    if not detail:
        notes = row.pop("notes", "")
        row["notes_preview"] = preview_text(notes)
    return row


def candidate_status_label(candidate):
    status = storage.clean(candidate.get("status", ""))
    return "Considering" if status == "pursued" else status.replace("-", " ").title()


def compact_company_candidate(candidate, detail=False):
    if detail:
        row = {field: candidate.get(field, "") for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        row["review_state"] = company_store.candidate_review_state(candidate)
        row["status_label"] = candidate_status_label(candidate)
        row["requisition_ids"] = sorted(company_store.normalized_requisition_ids(candidate.get("url", "")))
        return row
    fields = [
        "id",
        "company_id",
        "title",
        "url",
        "location",
        "work_mode",
        "source_platform",
        "scan_state",
        "normalization_warnings",
        "status",
        "last_seen_at",
        "fit_score",
        "fit_summary",
    ]
    row = {field: candidate.get(field, "") for field in fields}
    row["review_state"] = company_store.candidate_review_state(candidate)
    row["status_label"] = candidate_status_label(candidate)
    row["requisition_ids"] = sorted(company_store.normalized_requisition_ids(candidate.get("url", "")))
    row["notes_preview"] = preview_text(candidate.get("notes", ""))
    return row


def compact_discovery_candidate(candidate, detail=False):
    if detail:
        row = {field: candidate.get(field, "") for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        for field in [
            "review_state",
            "review_next_action",
            "requisition_ids",
            "matching_posting_ids",
            "recommendation_eligible",
        ]:
            row[field] = candidate.get(field, [] if field.endswith("_ids") else "")
        row["status_label"] = candidate_status_label(candidate)
        return row
    fields = [
        "id", "company_id", "company", "title", "canonical_url", "url", "location",
        "work_mode", "source_platform", "status", "processing_status", "freshness_status",
        "fit_score", "fit_summary", "recommendation_eligible", "lane_match", "detail_state",
        "review_state", "review_next_action", "requisition_ids", "matching_posting_ids",
    ]
    row = {field: candidate.get(field, "") for field in fields}
    row["status_label"] = candidate_status_label(candidate)
    row["notes_preview"] = preview_text(candidate.get("notes", ""))
    return row


def compact_discovery_search(search):
    return {
        "id": search.get("id", ""),
        "name": search.get("name", ""),
        "keywords": search.get("keywords", ""),
        "role_family_ids": search.get("role_family_ids", []),
        "lanes": search.get("lanes", []),
        "last_run_at": search.get("last_run_at", ""),
        "last_run_summary": search.get("last_run_summary", {}),
    }


def split_tags(value):
    return storage.split_tags(value)


def app_matches(app, args):
    search = storage.clean(args.get("search", "")).lower()
    if search:
        haystack = " ".join(
            [
                app.get("id", ""),
                app.get("company", ""),
                app.get("role", ""),
                app.get("location", ""),
                app.get("source", ""),
                app.get("source_url", ""),
                app.get("stage", ""),
                app.get("outcome", ""),
                app.get("tags", ""),
                app.get("next_action", ""),
                app.get("notes", ""),
            ]
        ).lower()
        if search not in haystack:
            return False
    for field in ["stage", "outcome", "priority", "company"]:
        value = storage.clean(args.get(field, ""))
        if value and app.get(field, "").lower() != value.lower():
            return False
    tag = storage.normalize_tags(args.get("tag", ""))
    if tag and tag not in split_tags(app.get("tags", "")):
        return False
    return True


def tool_list_postings(args):
    limit = requested_limit(args)
    apps = [compact_application(app) for app in app_state.read_applications() if app_matches(app, args)]
    apps.sort(key=lambda app: (app.get("next_action_date") or "9999-12-31", app.get("company", ""), app.get("role", "")))
    return text_result({"count": len(apps), "postings": apps[:limit]})


def tool_get_posting(args):
    wanted = storage.clean(args.get("id", "")).upper()
    if not wanted:
        raise ValueError("id is required.")
    app = next((item for item in app_state.read_applications() if item.get("id", "").upper() == wanted), None)
    if not app:
        raise ValueError(f"No posting found with id {wanted}.")
    snapshots = []
    for snapshot in repository.read_posting_snapshots(wanted):
        if not posting_snapshot_store.is_usable(snapshot):
            continue
        snapshots.append({
            **{field: snapshot.get(field, "") for field in schema.POSTING_SNAPSHOT_FIELDS if field != "source_html"},
            "source_html_char_count": len(snapshot.get("source_html", "")),
        })
    related_actions = [
        action
        for action in repository.read_actions()
        if action.get("application_id", "").upper() == wanted
    ]
    return text_result(
        {
            "posting": compact_application(app, detail=True),
            "posting_snapshots": snapshots,
            "actions": [compact_action(action, detail=True) for action in related_actions],
        }
    )


def tool_list_actions(args):
    application_id = storage.clean(args.get("application_id", "")).upper()
    status = storage.clean(args.get("status", "open")).lower()
    limit = requested_limit(args, default=50)
    rows = []
    for action in repository.read_actions():
        if application_id and action.get("application_id", "").upper() != application_id:
            continue
        action_status = action.get("status", "").lower()
        if status == "open" and action_status in schema.COMPLETED_ACTION_STATUSES:
            continue
        if status not in {"", "all", "open"} and action_status != status:
            continue
        rows.append(compact_action(action))
    rows.sort(key=lambda action: (action.get("due_date") or "9999-12-31", action.get("company", ""), action.get("title", "")))
    return text_result({"count": len(rows), "actions": rows[:limit]})


def tool_create_action(args):
    action = action_store.create_action(args.get("application_id", ""), args.get("values", {}))
    posting = action_store.sync_next_action(action.get("application_id", ""))
    return text_result(
        {
            "action": compact_action(action, detail=True),
            "posting": compact_application(posting) if posting else None,
        }
    )


def tool_update_action(args):
    action = action_store.update_action_status(args.get("id", ""), args.get("status", ""))
    posting = action_store.sync_next_action(action.get("application_id", ""))
    return text_result({"action": compact_action(action, detail=True), "posting": compact_application(posting) if posting else None})


def tool_update_action_fields(args):
    action = action_store.update_action_fields(args.get("id", ""), args.get("updates", {}))
    posting = action_store.sync_next_action(action.get("application_id", ""))
    return text_result({"action": compact_action(action, detail=True), "posting": compact_application(posting) if posting else None})


def tool_make_next_action(args):
    posting = action_store.make_next_action(args.get("id", ""))
    return text_result({"posting": compact_application(posting) if posting else None})


def tool_update_application(args):
    app = application_store.update_application(args.get("id", ""), args.get("updates", {}))
    return text_result({"posting": compact_application(app)})


def tool_ingest_posting(args):
    url = storage.clean(args.get("url", ""))
    if not url:
        raise ValueError("url is required.")
    command = [sys.executable, str(paths.ROOT / "scripts" / "ingest_postings.py")]
    if args.get("dry_run"):
        command.append("--dry-run")
    if args.get("use_ai_actions"):
        command.append("--use-ai-actions")
    command.append(url)
    # Keep cwd unset so Python can use posix_spawn on macOS. Launching this
    # tool from the threaded app server with cwd set forces fork/exec, which
    # can crash the child before exec when macOS frameworks have active
    # threads. The script path is absolute and resolves Hunter's root itself.
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or "ingest failed").strip())
    return text_result({"stdout": result.stdout.strip(), "stderr": result.stderr.strip()})


def tool_get_resume_text(args):
    del args
    return text_result(settings_store.resume_text_payload())


def tool_get_settings(args):
    del args
    return text_result(settings_store.settings_status())


def tool_update_settings(args):
    search_goals = args.get("search_goals") if "search_goals" in args else None
    fit_signals = args.get("fit_signals") if "fit_signals" in args else None
    if search_goals is None and fit_signals is None:
        raise ValueError("search_goals or fit_signals is required.")
    if fit_signals is not None:
        if not isinstance(fit_signals, dict):
            raise ValueError("fit_signals must be an object.")
        merged_signals = settings_store.read_fit_signals()
        merged_signals.update(
            {
                key: value
                for key, value in fit_signals.items()
                if value is not None
            }
        )
        fit_signals = merged_signals
    status = settings_store.save_settings(
        None,
        None,
        None,
        "",
        search_goals=search_goals,
        fit_signals=fit_signals,
    )
    return text_result(status)


def tool_list_contacts(args):
    search = storage.clean(args.get("search", "")).lower()
    limit = requested_limit(args, default=50)
    rows = []
    for contact in contact_store.list_contacts():
        haystack = " ".join(compact_contact(contact).values()).lower()
        if search and search not in haystack:
            continue
        linked_postings = [
            link["application_id"]
            for link in repository.read_application_contacts()
            if link["contact_id"] == contact.get("id")
        ]
        rows.append({**compact_contact(contact), "linked_postings": linked_postings})
    return text_result({"count": len(rows), "contacts": rows[:limit]})


def tool_upsert_contact(args):
    contact = contact_store.upsert_contact(args.get("id", ""), args.get("updates", {}))
    return text_result({"contact": compact_contact(contact)})


def tool_link_contact(args):
    link = contact_store.link_contact(args.get("application_id", ""), args.get("contact_id", ""))
    return text_result({"link": link})


def tool_unlink_contact(args):
    link = contact_store.unlink_contact(args.get("application_id", ""), args.get("contact_id", ""))
    return text_result({"link": link})


def tool_list_companies(args):
    search = storage.clean(args.get("search", "")).lower()
    interest_status = storage.clean(args.get("interest_status", "")).lower()
    tracking_status = storage.clean(args.get("tracking_status", "")).lower()
    limit = requested_limit(args, default=50)
    rows = []
    for company in company_store.list_companies():
        if interest_status and company.get("interest_status", "").lower() != interest_status:
            continue
        if tracking_status and company.get("tracking_status", "").lower() != tracking_status:
            continue
        haystack = " ".join(compact_company(company).values()).lower()
        if search and search not in haystack:
            continue
        rows.append(compact_company(company))
    return text_result({"count": len(rows), "companies": rows[:limit]})


def tool_get_company(args):
    company = company_store.get_company(args.get("id", ""))
    company_id = company.get("id", "").upper()
    posting_limit = requested_limit({"limit": args.get("posting_limit")}, default=DETAIL_LIST_LIMIT)
    candidate_limit = requested_limit({"limit": args.get("candidate_limit")}, default=DETAIL_LIST_LIMIT)
    linked_contacts = [
        link["contact_id"]
        for link in repository.read_company_contacts()
        if link.get("company_id", "").upper() == company_id
    ]
    postings = [
        compact_application(app)
        for app in app_state.read_applications()
        if app.get("company_id", "").upper() == company_id
    ]
    raw_candidates = [
        candidate
        for candidate in repository.read_company_posting_candidates()
        if candidate.get("company_id", "").upper() == company_id
    ]
    include_excluded = args.get("include_excluded_companies") is True
    eligible_candidates, excluded_candidates = candidate_eligibility.partition_candidates(
        raw_candidates,
        [company],
        include_excluded_companies=include_excluded,
    )
    candidates = [
        compact_company_candidate(candidate)
        for candidate in eligible_candidates
    ]
    return text_result(
        {
            "company": compact_company(company, detail=True),
            "linked_contacts": linked_contacts,
            "postings_count": len(postings),
            "candidate_count": len(candidates),
            "excluded_company_candidate_count": len(excluded_candidates),
            "postings": postings[:posting_limit],
            "candidates": candidates[:candidate_limit],
        }
    )


def tool_upsert_company(args):
    company = company_store.upsert_company(args.get("id", ""), args.get("updates", {}))
    return text_result({"company": compact_company(company)})


def tool_archive_company(args):
    company = company_store.archive_company(args.get("id", ""))
    return text_result({"company": compact_company(company)})


def tool_restore_company(args):
    company = company_store.restore_company(args.get("id", ""), args.get("interest_status", "neutral"))
    return text_result({"company": compact_company(company)})


def tool_research_company(args):
    result = company_store.research_company(args.get("id", ""))
    return text_result(
        {
            "company": compact_company(result["company"], detail=True),
            "applied_fields": result.get("applied_fields", []),
            "suggestions": result.get("suggestions", []),
            "source_url": result.get("source_url", ""),
        }
    )


def tool_track_company(args):
    company = company_store.track_company(args.get("id", ""))
    return text_result({"company": compact_company(company)})


def tool_untrack_company(args):
    company = company_store.untrack_company(args.get("id", ""))
    return text_result({"company": compact_company(company)})


def tool_resolve_company_metadata_suggestion(args):
    company = company_store.resolve_company_metadata_suggestion(
        args.get("id", ""),
        args.get("suggestion_id", ""),
        args.get("action", ""),
    )
    return text_result({"company": compact_company(company, detail=True)})


def tool_check_company_postings(args):
    result = company_store.check_company_postings(args.get("id", ""))
    candidates = [compact_company_candidate(row) for row in result["candidates"]]
    candidate_limit = requested_limit({"limit": args.get("candidate_limit")}, default=DETAIL_LIST_LIMIT)
    new_rows = [compact_company_candidate(row) for row in result["new"]]
    recommended_rows = [compact_company_candidate(row) for row in result["recommended"]]
    return text_result(
        {
            "company": compact_company(result["company"]),
            "new_count": len(new_rows),
            "recommended_count": len(recommended_rows),
            "candidate_count": len(candidates),
            "scan": result.get("scan", {}),
            "new": new_rows[:candidate_limit],
            "recommended": recommended_rows[:candidate_limit],
            "candidates": candidates[:candidate_limit],
        }
    )


def tool_get_company_candidate(args):
    wanted = storage.clean(args.get("id", "")).upper()
    if not wanted:
        raise ValueError("id is required.")
    candidate = next(
        (
            row
            for row in repository.read_company_posting_candidates()
            if row.get("id", "").upper() == wanted
        ),
        None,
    )
    if not candidate:
        raise ValueError(f"No company posting candidate found with id {wanted}.")
    company = company_store.get_company(candidate.get("company_id", ""))
    if args.get("include_excluded_companies") is not True:
        candidate_eligibility.require_candidate_eligible(candidate, [company])
    return text_result(
        {
            "candidate": {
                **compact_company_candidate(candidate, detail=True),
                "matching_posting_ids": company_store.matching_tracked_posting_ids(
                    candidate,
                    company=company,
                ),
            },
            "company": compact_company(company),
        }
    )


def tool_list_company_candidates(args):
    company_id = storage.clean(args.get("company_id", "")).upper()
    status = storage.clean(args.get("status", "all")).lower() or "all"
    if status != "all" and status not in schema.COMPANY_POSTING_CANDIDATE_STATUSES:
        raise ValueError(f"Unsupported company posting candidate status: {status}")
    search = storage.clean(args.get("search", "")).lower()
    tracking_status = storage.clean(args.get("tracking_status", "tracked")).lower() or "tracked"
    if tracking_status not in {"all", *schema.COMPANY_TRACKING_STATUSES}:
        raise ValueError(f"Unsupported company tracking status: {tracking_status}")
    try:
        minimum_fit_score = max(0, min(100, int(args.get("minimum_fit_score") or 0)))
    except (TypeError, ValueError):
        minimum_fit_score = 0
    limit = requested_limit(args, default=50)
    company = company_store.get_company(company_id) if company_id else None
    company_by_id = {
        row.get("id", "").upper(): row
        for row in company_store.list_companies()
    }
    application_rows = repository.read_applications()
    tracked_context_by_company_id = {
        candidate_company_id: company_store.tracked_posting_context(
            candidate_company,
            application_rows=application_rows,
        )
        for candidate_company_id, candidate_company in company_by_id.items()
    }
    scored_rows = []
    excluded_count = 0
    other_tracking_status_count = 0
    include_excluded = args.get("include_excluded_companies") is True
    for candidate in repository.read_company_posting_candidates():
        candidate_company_id = candidate.get("company_id", "").upper()
        if company_id and candidate_company_id != company_id:
            continue
        candidate_status = candidate.get("status", "new").lower()
        if status != "all" and candidate_status != status:
            continue
        candidate_company = company_by_id.get(candidate_company_id, {})
        if (
            tracking_status != "all"
            and candidate_company.get("tracking_status", "").lower() != tracking_status
        ):
            other_tracking_status_count += 1
            continue
        excluded = candidate_eligibility.company_is_excluded(candidate_company)
        if excluded:
            excluded_count += 1
            if not include_excluded:
                continue
        try:
            fit_score = int(candidate.get("fit_score") or 0)
        except (TypeError, ValueError):
            fit_score = 0
        if fit_score < minimum_fit_score:
            continue
        haystack = " ".join(
            [
                candidate.get("id", ""),
                candidate.get("title", ""),
                candidate.get("location", ""),
                candidate.get("fit_summary", ""),
                candidate_company.get("name", ""),
            ]
        ).lower()
        if search and search not in haystack:
            continue
        scored_rows.append(
            (
                fit_score,
                {
                    **compact_company_candidate(candidate),
                    "company": candidate_company.get("name", ""),
                    "matching_posting_ids": company_store.matching_tracked_posting_ids(
                        candidate,
                        company=candidate_company,
                        tracked=tracked_context_by_company_id.get(candidate_company_id),
                    ),
                    "recommended": (
                        not excluded
                        and
                        candidate_status == "new"
                        and company_store.candidate_review_state(candidate) == "ready"
                        and fit_score >= company_store.FIT_RECOMMENDATION_THRESHOLD
                    ),
                },
            )
        )
    scored_rows.sort(
        key=lambda item: (
            -item[0],
            item[1].get("company", ""),
            item[1].get("title", ""),
        )
    )
    rows = [candidate for _score, candidate in scored_rows]
    return text_result(
        {
            "company": compact_company(company) if company else None,
            "tracking_status": tracking_status,
            "count": len(rows),
            "excluded_company_candidate_count": excluded_count,
            "other_tracking_status_candidate_count": other_tracking_status_count,
            "candidates": rows[:limit],
        }
    )


def tool_get_discovery_candidate(args):
    candidate = discovery_store.get_candidate(
        args.get("id", ""),
        include_excluded_companies=args.get("include_excluded_companies") is True,
    )
    company = None
    if candidate.get("company_id"):
        company = company_store.get_company(candidate.get("company_id", ""))
    return text_result(
        {
            "candidate": compact_discovery_candidate(candidate, detail=True),
            "company": compact_company(company) if company else None,
        }
    )


def tool_list_discovery_candidates(args):
    status = storage.clean(args.get("status", "all")).lower() or "all"
    if status != "all" and status not in schema.DISCOVERY_CANDIDATE_STATUSES:
        raise ValueError(f"Unsupported Discovery candidate status: {status}")
    search = storage.clean(args.get("search", "")).lower()
    try:
        minimum_fit_score = max(0, min(100, int(args.get("minimum_fit_score") or 0)))
    except (TypeError, ValueError):
        minimum_fit_score = 0
    include_excluded = args.get("include_excluded_companies") is True
    _visible, excluded, _company_by_id = discovery_store.candidate_review_rows(
        include_excluded_companies=include_excluded
    )
    rows = []
    for candidate in discovery_store.list_candidates(
        include_excluded_companies=include_excluded
    ):
        if status != "all" and candidate.get("status", "").lower() != status:
            continue
        try:
            fit_score = int(candidate.get("fit_score") or 0)
        except (TypeError, ValueError):
            fit_score = 0
        if fit_score < minimum_fit_score:
            continue
        haystack = " ".join(
            [candidate.get("id", ""), candidate.get("company", ""), candidate.get("title", "")]
        ).lower()
        if search and search not in haystack:
            continue
        rows.append((fit_score, compact_discovery_candidate(candidate)))
    rows.sort(key=lambda item: (-item[0], item[1].get("company", ""), item[1].get("title", "")))
    limit = requested_limit(args, default=50)
    candidates = [candidate for _score, candidate in rows]
    return text_result(
        {
            "count": len(candidates),
            "excluded_company_candidate_count": len(excluded),
            "candidates": candidates[:limit],
        }
    )


def tool_list_discovery_searches(_args):
    searches = [compact_discovery_search(search) for search in discovery_store.list_searches()]
    return text_result({"count": len(searches), "searches": searches})


def tool_run_discovery_search(args):
    search_id = storage.clean(args.get("id", "")).upper()
    if not search_id:
        raise ValueError("id is required.")
    try:
        enrichment_limit = max(0, min(250, int(args.get("enrichment_limit", 100))))
    except (TypeError, ValueError) as exc:
        raise ValueError("enrichment_limit must be an integer.") from exc
    result = discovery_store.continue_discovery(
        search_id,
        enrichment_limit=enrichment_limit,
        use_browser_fallback=args.get("use_browser_fallback") is True,
    )
    return text_result(
        {
            "search": compact_discovery_search(result.get("search", {})),
            "new_count": result.get("new_count", 0),
            "updated_count": result.get("updated_count", 0),
            "associated_count": result.get("associated_count", 0),
            "duplicate_count": result.get("duplicate_count", 0),
            "evaluated_count": result.get("evaluated_count", 0),
            "known_count": result.get("known_count", 0),
            "screened_count": result.get("screened_count", 0),
            "needs_details_count": result.get("needs_details_count", 0),
            "enrichment": result.get("enrichment", {}),
            "sources": result.get("sources", []),
            "errors": result.get("errors", []),
            "captured": [compact_discovery_candidate(candidate) for candidate in result.get("captured", [])],
        }
    )


def _isolated_discovery_command(search_id, enrichment_limit, use_browser_fallback):
    command = [
        sys.executable,
        str(paths.ROOT / "scripts" / "run_discovery_search.py"),
        search_id,
        "--enrichment-limit",
        str(enrichment_limit),
    ]
    if use_browser_fallback:
        command.append("--use-browser-fallback")
    return command


def tool_run_discovery_searches(args):
    configured = {search["id"]: search for search in discovery_store.list_searches()}
    requested_ids = [
        storage.clean(value).upper()
        for value in (args.get("ids") or configured.keys())
        if storage.clean(value)
    ]
    if not requested_ids:
        raise ValueError("At least one configured Discovery search is required.")
    try:
        enrichment_limit = max(0, min(250, int(args.get("enrichment_limit", 100))))
        timeout_seconds = max(1, min(600, int(args.get("timeout_seconds", 180))))
        retry_count = max(0, min(2, int(args.get("retry_count", 1))))
    except (TypeError, ValueError) as exc:
        raise ValueError("enrichment_limit, timeout_seconds, and retry_count must be integers.") from exc

    results = []
    for search_id in dict.fromkeys(requested_ids):
        if search_id not in configured:
            results.append(
                {
                    "id": search_id,
                    "name": "",
                    "status": "failed",
                    "attempt_count": 0,
                    "errors": [f"No Discovery search found with id {search_id}."],
                }
            )
            continue
        result = None
        attempt_errors = []
        for attempt in range(1, retry_count + 2):
            try:
                completed = subprocess.run(
                    _isolated_discovery_command(
                        search_id,
                        enrichment_limit,
                        args.get("use_browser_fallback") is True,
                    ),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_seconds,
                )
                output_lines = [line for line in completed.stdout.splitlines() if line.strip()]
                payload = json.loads(output_lines[-1]) if output_lines else {}
                if not isinstance(payload, dict):
                    payload = {}
                if completed.returncode and not payload.get("errors"):
                    payload["errors"] = [
                        storage.clean(completed.stderr or completed.stdout) or "Search subprocess failed."
                    ]
                payload.setdefault("id", search_id)
                payload.setdefault("name", configured[search_id].get("name", ""))
                payload.setdefault("status", "failed" if completed.returncode else "completed")
                payload["attempt_count"] = attempt
                result = payload
            except subprocess.TimeoutExpired:
                attempt_errors.append(
                    f"Timed out after {timeout_seconds} seconds on attempt {attempt}."
                )
                result = {
                    "id": search_id,
                    "name": configured[search_id].get("name", ""),
                    "status": "timed-out",
                    "attempt_count": attempt,
                    "errors": list(attempt_errors),
                }
            except (OSError, json.JSONDecodeError) as exc:
                attempt_errors.append(storage.clean(str(exc)))
                result = {
                    "id": search_id,
                    "name": configured[search_id].get("name", ""),
                    "status": "failed",
                    "attempt_count": attempt,
                    "errors": list(attempt_errors),
                }
            if result.get("status") not in {"failed", "timed-out"}:
                break
        results.append(result)

    return text_result(
        {
            "search_count": len(results),
            "completed_count": sum(
                result.get("status") in {"completed", "completed-with-errors"}
                for result in results
            ),
            "failed_count": sum(
                result.get("status") in {"failed", "timed-out"}
                for result in results
            ),
            "results": results,
        }
    )


def tool_refresh_discovery_candidates(args):
    candidate_id = storage.clean(args.get("id", "")).upper()
    raw_limit = args.get("limit")
    try:
        limit = max(1, int(raw_limit)) if raw_limit is not None else 0
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer.") from exc
    result = discovery_store.enrich_candidate_backlog(
        candidate_id=candidate_id,
        limit=limit,
    )
    return text_result(result)


def tool_consider_discovery_candidate(args):
    result = discovery_store.pursue_candidate(args.get("id", ""))
    return text_result(
        {
            "candidate": compact_discovery_candidate(result["candidate"]),
            "posting": compact_application(result["posting"]),
            "created": result.get("created", False),
        }
    )


def tool_update_company_candidate(args):
    candidate = company_store.update_candidate_status(args.get("id", ""), args.get("status", ""))
    company = company_store.get_company(candidate.get("company_id", ""))
    return text_result(
        {
            "candidate": compact_company_candidate(candidate, detail=True),
            "company": compact_company(company),
        }
    )


def tool_link_company_contact(args):
    link = company_store.link_contact(args.get("company_id", ""), args.get("contact_id", ""))
    return text_result({"link": link})


def tool_unlink_company_contact(args):
    link = company_store.unlink_contact(args.get("company_id", ""), args.get("contact_id", ""))
    return text_result({"link": link})


def tool_ingest_company_candidate(args):
    result = company_store.pursue_candidate(args.get("id", ""))
    return text_result(
        {
            "candidate": compact_company_candidate(result["candidate"]),
            "posting": compact_application(result["posting"]) if result.get("posting") else None,
            "stdout": result.get("stdout", ""),
        }
    )


def tool_consider_company_candidate(args):
    return tool_ingest_company_candidate(args)


TOOLS = {
    "hunter_list_postings": {
        "description": "List tracked Hunter postings with optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
                "stage": {"type": "string"},
                "outcome": {"type": "string"},
                "priority": {"type": "string"},
                "company": {"type": "string"},
                "tag": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        "handler": tool_list_postings,
    },
    "hunter_get_posting": {
        "description": "Get one Hunter posting, its captured source snapshots, and related actions.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "handler": tool_get_posting,
    },
    "hunter_list_actions": {
        "description": "List Hunter actions, optionally filtered by posting id or status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "string"},
                "status": {"type": "string", "description": "open, all, or a concrete status such as done"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        "handler": tool_list_actions,
    },
    "hunter_create_action": {
        "description": "Create a concrete action for a tracked posting and refresh that posting's next-action summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "string"},
                "values": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "type": {"type": "string"},
                        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                        "due_date": {"type": "string"},
                        "related_url": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["title", "type"],
                    "additionalProperties": False,
                },
            },
            "required": ["application_id", "values"],
        },
        "handler": tool_create_action,
    },
    "hunter_update_action": {
        "description": "Update an action status, such as marking an action done or reopening it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "status": {"type": "string", "enum": sorted(schema.ACTION_STATUSES)},
            },
            "required": ["id", "status"],
        },
        "handler": tool_update_action,
    },
    "hunter_update_action_fields": {
        "description": "Update editable fields on a Hunter action, including its due date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "updates": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "type": {"type": "string"},
                        "priority": {"type": "string"},
                        "due_date": {"type": "string"},
                        "related_url": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["id", "updates"],
        },
        "handler": tool_update_action_fields,
    },
    "hunter_make_next_action": {
        "description": "Choose an open action as the next action for its linked posting.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "handler": tool_make_next_action,
    },
    "hunter_update_application": {
        "description": "Update editable tracking fields on a Hunter posting/application.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "updates": {
                    "type": "object",
                    "properties": {
                        "company": {"type": "string"},
                        "company_id": {"type": "string"},
                        "role": {"type": "string"},
                        "location": {"type": "string"},
                        "work_mode": {"type": "string"},
                        "source": {"type": "string"},
                        "source_url": {"type": "string"},
                        "compensation": {"type": "string"},
                        "stage": {"type": "string"},
                        "outcome": {"type": "string"},
                        "tags": {"type": "string"},
                        "priority": {"type": "string"},
                        "date_found": {"type": "string"},
                        "date_applied": {"type": "string"},
                        "contact": {"type": "string"},
                        "resume_version": {"type": "string"},
                        "cover_letter": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["id", "updates"],
        },
        "handler": tool_update_application,
    },
    "hunter_ingest_posting": {
        "description": "Ingest or refresh one posting URL through Hunter's existing ingestion script.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "use_ai_actions": {"type": "boolean"},
            },
            "required": ["url"],
        },
        "handler": tool_ingest_posting,
    },
    "hunter_get_resume_text": {
        "description": "Get the full locally extracted resume text when exact resume wording or resume-specific tailoring is required.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
        "handler": tool_get_resume_text,
    },
    "hunter_get_settings": {
        "description": "Get current local Hunter settings, including Search Goals, fit signals, resume status, and whether an API token is configured.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
        "handler": tool_get_settings,
    },
    "hunter_update_settings": {
        "description": "Update local Search Goals or fit signal settings. Omit fields that should stay unchanged.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search_goals": {
                    "type": "string",
                    "description": "Replacement Search Goals text to guide career searches and fit judgment.",
                },
                "fit_signals": {
                    "type": "object",
                    "description": "Partial fit signal updates. Missing fit signal groups are preserved.",
                    "properties": {
                        "role_terms": {"type": "string"},
                        "domain_terms": {"type": "string"},
                        "seniority_terms": {"type": "string"},
                        "search_terms": {"type": "string"},
                        "low_match_terms": {"type": "string"},
                        "exclusion_terms": {"type": "string"},
                        "strength_terms": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "handler": tool_update_settings,
    },
    "hunter_list_contacts": {
        "description": "List Hunter contacts and their linked posting ids.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        "handler": tool_list_contacts,
    },
    "hunter_upsert_contact": {
        "description": "Create or update a Hunter contact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Optional existing contact id."},
                "updates": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "company": {"type": "string"},
                        "role": {"type": "string"},
                        "email": {"type": "string"},
                        "linkedin": {"type": "string"},
                        "relationship": {"type": "string"},
                        "status": {"type": "string"},
                        "last_contacted": {"type": "string"},
                        "next_follow_up": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["updates"],
        },
        "handler": tool_upsert_contact,
    },
    "hunter_link_contact": {
        "description": "Associate a contact with a posting/application.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "string"},
                "contact_id": {"type": "string"},
            },
            "required": ["application_id", "contact_id"],
        },
        "handler": tool_link_contact,
    },
    "hunter_unlink_contact": {
        "description": "Remove an association between a contact and a posting/application.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "application_id": {"type": "string"},
                "contact_id": {"type": "string"},
            },
            "required": ["application_id", "contact_id"],
        },
        "handler": tool_unlink_contact,
    },
    "hunter_list_companies": {
        "description": "List local Hunter companies, including explicitly tracked companies and employers encountered through Discovery.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
                "interest_status": {"type": "string", "enum": sorted(schema.COMPANY_INTEREST_STATUSES)},
                "tracking_status": {"type": "string", "enum": sorted(schema.COMPANY_TRACKING_STATUSES)},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        "handler": tool_list_companies,
    },
    "hunter_get_company": {
        "description": "Get one Hunter company with counts plus capped associated postings and eligible posting candidates. Candidates from not-interested or archived companies are omitted unless explicitly included.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "posting_limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "candidate_limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "include_excluded_companies": {"type": "boolean", "default": False},
            },
            "required": ["id"],
        },
        "handler": tool_get_company,
    },
    "hunter_upsert_company": {
        "description": "Create or update a managed Hunter company.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Optional existing company id."},
                "updates": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "aliases": {"type": "string"},
                        "interest_status": {"type": "string", "enum": sorted(schema.COMPANY_INTEREST_STATUSES)},
                        "tracking_status": {"type": "string", "enum": sorted(schema.COMPANY_TRACKING_STATUSES)},
                        "website": {"type": "string"},
                        "careers_url": {"type": "string"},
                        "industry": {"type": "string"},
                        "company_size": {"type": "string"},
                        "company_profile_url": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["updates"],
        },
        "handler": tool_upsert_company,
    },
    "hunter_archive_company": {
        "description": "Archive a managed Hunter company without deleting its contacts, postings, candidates, or career source history.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "handler": tool_archive_company,
    },
    "hunter_restore_company": {
        "description": "Restore an archived managed Hunter company to neutral or interested.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "interest_status": {"type": "string", "enum": ["interested", "neutral"]},
            },
            "required": ["id"],
        },
        "handler": tool_restore_company,
    },
    "hunter_research_company": {
        "description": "Use the signed-in Hunter Chrome profile to research a company. Blank fields are filled automatically; conflicting source-backed values are saved as reviewable suggestions.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "handler": tool_research_company,
    },
    "hunter_track_company": {
        "description": "Promote a company encountered through Discovery into the explicitly tracked set used by Companies career-page workflows.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "handler": tool_track_company,
    },
    "hunter_untrack_company": {
        "description": "Move a tracked company back to Discovery while preserving its company record, linked roles, contacts, candidates, and research.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "handler": tool_untrack_company,
    },
    "hunter_resolve_company_metadata_suggestion": {
        "description": "Apply or dismiss one source-backed company metadata suggestion after review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "suggestion_id": {"type": "string"},
                "action": {"type": "string", "enum": ["apply", "dismiss"]},
            },
            "required": ["id", "suggestion_id", "action"],
        },
        "handler": tool_resolve_company_metadata_suggestion,
    },
    "hunter_check_company_postings": {
        "description": "Manually check a company's careers URL and record new posting candidates for review. Returns capped candidate summaries with counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "candidate_limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["id"],
        },
        "handler": tool_check_company_postings,
    },
    "hunter_get_company_candidate": {
        "description": "Get full detail for one eligible company posting candidate. Excluded companies require explicit opt-in.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "include_excluded_companies": {"type": "boolean", "default": False},
            },
            "required": ["id"],
        },
        "handler": tool_get_company_candidate,
    },
    "hunter_list_company_candidates": {
        "description": "List eligible posting candidates for explicitly tracked companies by default. Use tracking_status=all to audit discovered-company candidates too. Not-interested and archived companies are omitted by default and counted compactly.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "tracking_status": {
                    "type": "string",
                    "enum": ["tracked", "discovered", "all"],
                    "default": "tracked",
                },
                "status": {"type": "string", "enum": ["all", *sorted(schema.COMPANY_POSTING_CANDIDATE_STATUSES)]},
                "search": {"type": "string"},
                "minimum_fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "include_excluded_companies": {"type": "boolean", "default": False},
            },
        },
        "handler": tool_list_company_candidates,
    },
    "hunter_get_discovery_candidate": {
        "description": "Get full detail for one eligible Discovery candidate. Excluded companies require explicit opt-in.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "include_excluded_companies": {"type": "boolean", "default": False},
            },
            "required": ["id"],
        },
        "handler": tool_get_discovery_candidate,
    },
    "hunter_list_discovery_candidates": {
        "description": "List eligible Discovery candidates. Not-interested and archived companies are omitted by default and counted compactly.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["all", *sorted(schema.DISCOVERY_CANDIDATE_STATUSES)]},
                "search": {"type": "string"},
                "minimum_fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "include_excluded_companies": {"type": "boolean", "default": False},
            },
        },
        "handler": tool_list_discovery_candidates,
    },
    "hunter_list_discovery_searches": {
        "description": "List the configured local Discovery searches and their last-run summaries.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
        "handler": tool_list_discovery_searches,
    },
    "hunter_run_discovery_search": {
        "description": "Run one configured Discovery search with its saved role families, lanes, and exclusions. Captures eligible candidates and performs normal detail enrichment without changing review decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Configured Discovery search id."},
                "enrichment_limit": {"type": "integer", "minimum": 0, "maximum": 250, "default": 100},
                "use_browser_fallback": {"type": "boolean", "default": False},
            },
            "required": ["id"],
        },
        "handler": tool_run_discovery_search,
    },
    "hunter_run_discovery_searches": {
        "description": "Run saved Discovery searches independently. Every search gets its own subprocess timeout, retry budget, and completion result so one stuck search cannot block the rest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Search ids to run. Omit to run every configured search.",
                },
                "enrichment_limit": {"type": "integer", "minimum": 0, "maximum": 250, "default": 100},
                "use_browser_fallback": {"type": "boolean", "default": False},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600, "default": 180},
                "retry_count": {"type": "integer", "minimum": 0, "maximum": 2, "default": 1},
            },
        },
        "handler": tool_run_discovery_searches,
    },
    "hunter_refresh_discovery_candidates": {
        "description": "Check existing eligible Discovery candidates that need posting detail or freshness. Does not run saved searches. Omit limit to process the complete eligible backlog, or provide id for one candidate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Optional Discovery candidate id."},
                "limit": {"type": "integer", "minimum": 1},
            },
        },
        "handler": tool_refresh_discovery_candidates,
    },
    "hunter_consider_discovery_candidate": {
        "description": "Add one review-ready Discovery candidate to Postings in Considering.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Discovery candidate id."}},
            "required": ["id"],
        },
        "handler": tool_consider_discovery_candidate,
    },
    "hunter_update_company_candidate": {
        "description": "Update a company posting candidate's review status, such as ignoring it or returning it to new.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "status": {"type": "string", "enum": sorted(schema.COMPANY_POSTING_CANDIDATE_STATUSES)},
            },
            "required": ["id", "status"],
        },
        "handler": tool_update_company_candidate,
    },
    "hunter_link_company_contact": {
        "description": "Associate a contact with a managed company.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "contact_id": {"type": "string"},
            },
            "required": ["company_id", "contact_id"],
        },
        "handler": tool_link_company_contact,
    },
    "hunter_unlink_company_contact": {
        "description": "Remove an association between a contact and a managed company.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "contact_id": {"type": "string"},
            },
            "required": ["company_id", "contact_id"],
        },
        "handler": tool_unlink_company_contact,
    },
    "hunter_ingest_company_candidate": {
        "description": "Compatibility alias for adding a reviewed company posting candidate to Postings in Considering.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Company posting candidate id."}},
            "required": ["id"],
        },
        "handler": tool_ingest_company_candidate,
    },
    "hunter_consider_company_candidate": {
        "description": "Add one review-ready company posting candidate to Postings in Considering.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Company posting candidate id."}},
            "required": ["id"],
        },
        "handler": tool_consider_company_candidate,
    },
}


def initialize_result(params):
    sqlite_store.initialize()
    client_version = (params or {}).get("protocolVersion")
    return {
        "protocolVersion": client_version or DEFAULT_PROTOCOL_VERSION,
        "serverInfo": SERVER_INFO,
        "capabilities": {"tools": {}},
    }


def list_tools_result():
    tools = []
    for name, definition in TOOLS.items():
        tools.append(
            {
                "name": name,
                "description": definition["description"],
                "inputSchema": definition["inputSchema"],
            }
        )
    return {"tools": tools}


def call_tool_result(params):
    name = (params or {}).get("name", "")
    args = (params or {}).get("arguments") or {}
    return call_named_tool(name, args)


def call_named_tool(name, args):
    if name not in TOOLS:
        raise ValueError(f"Unknown tool: {name}")
    return TOOLS[name]["handler"](args)


def handle_request(message):
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if request_id is None:
        return None

    try:
        if method == "initialize":
            return response(request_id, initialize_result(params))
        if method == "tools/list":
            return response(request_id, list_tools_result())
        if method == "tools/call":
            return response(request_id, call_tool_result(params))
        if method in {"resources/list", "prompts/list"}:
            return response(request_id, {method.split("/")[0]: []})
        return error_response(request_id, -32601, f"Method not found: {method}")
    except Exception as exc:  # noqa: BLE001 - MCP should return JSON-RPC errors.
        return error_response(request_id, -32000, str(exc))


def serve(input_stream=None, output_stream=None):
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    for line in input_stream:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            result = error_response(None, -32700, f"Parse error: {exc}")
        else:
            result = handle_request(message)
        if result is None:
            continue
        output_stream.write(json.dumps(result, separators=(",", ":")) + "\n")
        output_stream.flush()


def main():
    serve()


if __name__ == "__main__":
    main()
