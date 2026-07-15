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


def compact_company_candidate(candidate, detail=False):
    if detail:
        return {field: candidate.get(field, "") for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
    fields = [
        "id",
        "company_id",
        "title",
        "url",
        "location",
        "status",
        "last_seen_at",
        "fit_score",
        "fit_summary",
    ]
    row = {field: candidate.get(field, "") for field in fields}
    row["notes_preview"] = preview_text(candidate.get("notes", ""))
    return row


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
    note = repository.read_posting_note(wanted)
    related_actions = [
        action
        for action in repository.read_actions()
        if action.get("application_id", "").upper() == wanted
    ]
    return text_result(
        {
            "posting": compact_application(app, detail=True),
            "posting_note": note or None,
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
    result = subprocess.run(command, cwd=paths.ROOT, capture_output=True, text=True, check=False)
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
    limit = requested_limit(args, default=50)
    rows = []
    for company in company_store.list_companies():
        if interest_status and company.get("interest_status", "").lower() != interest_status:
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
    candidates = [
        compact_company_candidate(candidate)
        for candidate in repository.read_company_posting_candidates()
        if candidate.get("company_id", "").upper() == company_id
    ]
    return text_result(
        {
            "company": compact_company(company, detail=True),
            "linked_contacts": linked_contacts,
            "postings_count": len(postings),
            "candidate_count": len(candidates),
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
    result = company_store.ingest_candidate(args.get("id", ""))
    return text_result(
        {
            "candidate": compact_company_candidate(result["candidate"]),
            "posting": compact_application(result["posting"]) if result.get("posting") else None,
            "stdout": result.get("stdout", ""),
        }
    )


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
        "description": "Get one Hunter posting, its SQLite-backed posting note, and related actions.",
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
        "description": "List managed Hunter companies with optional search and interest filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
                "interest_status": {"type": "string", "enum": sorted(schema.COMPANY_INTEREST_STATUSES)},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        "handler": tool_list_companies,
    },
    "hunter_get_company": {
        "description": "Get one Hunter company with counts plus capped associated postings and posting candidates. Use hunter_get_company_candidate for full candidate detail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "posting_limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "candidate_limit": {"type": "integer", "minimum": 1, "maximum": 100},
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
                        "website": {"type": "string"},
                        "careers_url": {"type": "string"},
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
        "description": "Get full detail for one company posting candidate.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "handler": tool_get_company_candidate,
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
        "description": "Ingest a reviewed company posting candidate through Hunter's existing posting ingest path.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Company posting candidate id."}},
            "required": ["id"],
        },
        "handler": tool_ingest_company_candidate,
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
