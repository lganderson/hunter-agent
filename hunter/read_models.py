"""Compact, revisioned read models for the local Hunter HTTP API."""

import base64
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime

from . import actions as action_store
from . import app_state
from . import candidate_eligibility
from . import companies as company_store
from . import discovery as discovery_store
from . import repository, storage, suggestions, workflow


API_VERSION = 1
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100
LIST_DESCRIPTION_LIMIT = 500
COMPANY_FIT_SUMMARY_LIMIT = 150

APPLICATION_SHELL_FIELDS = (
    "id", "company_id", "company", "role", "location", "work_mode", "source",
    "source_url", "compensation", "stage", "outcome", "tags", "priority",
    "date_found", "date_applied", "next_action_id", "next_action",
    "next_action_date", "contact", "resume_version", "cover_letter", "posting_file",
)
ACTION_SHELL_FIELDS = (
    "id", "application_id", "company", "role", "type", "title", "status",
    "priority", "due_date",
)
CONTACT_SHELL_FIELDS = (
    "id", "name", "company", "role", "email", "linkedin", "relationship",
    "status", "last_contacted", "next_follow_up",
)
COMPANY_SHELL_FIELDS = (
    "id", "name", "aliases", "interest_status", "tracking_status", "website",
    "careers_url", "industry", "company_size", "company_location_fit",
    "company_location", "company_remote_policy", "last_checked_at", "last_check_status",
    "company_fit_score", "company_fit_summary", "company_evaluation_status",
    "company_evaluation_error", "company_discovery_source",
    "company_discovery_source_url", "company_discovery_query", "discovery_role_count",
    "recommended_discovery_role_count", "ignored_role_count", "pursued_role_count",
    "tracking_recommendation", "decision_recommendation",
    "company_metadata_suggestion_count",
)
DISCOVERY_RUN_SUMMARY_FIELDS = (
    "evaluated_count", "qualified_count", "known_count", "associated_count",
    "new_count", "updated_count", "lane_unmatched_count", "duplicate_count",
    "screened_count", "limited_count", "enriched_count", "errors",
)


class ReadModelError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _copy_fields(row, fields):
    return {field: row.get(field, "") for field in fields}


def _json_list_count(value):
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    return len(decoded) if isinstance(decoded, list) else 0


def _compact_discovery_search(search):
    summary = search.get("last_run_summary", {})
    if not isinstance(summary, dict):
        summary = {}
    return {
        "id": search.get("id", ""),
        "name": search.get("name", ""),
        "keywords": search.get("keywords", ""),
        "role_family_ids": list(search.get("role_family_ids", [])),
        "lanes": [dict(lane) for lane in search.get("lanes", [])],
        "excluded_terms": list(search.get("excluded_terms", [])),
        "created_at": search.get("created_at", ""),
        "updated_at": search.get("updated_at", ""),
        "last_opened_at": search.get("last_opened_at", ""),
        "last_run_at": search.get("last_run_at", ""),
        "last_run_summary": {
            field: summary[field]
            for field in DISCOVERY_RUN_SUMMARY_FIELDS
            if field in summary
        },
    }


def _fit_score(row):
    try:
        return max(0, int(row.get("fit_score", "") or 0))
    except (TypeError, ValueError):
        return 0


def _first(query, name, default=""):
    values = (query or {}).get(name) or []
    return storage.clean(values[0]) if values else default


def _first_with_alias(query, name, alias, default=""):
    if (query or {}).get(name):
        return _first(query, name, default)
    return _first(query, alias, default)


def _truthy_query(query, name):
    return _first(query, name).lower() in {"1", "true", "yes", "on"}


def _status_values(query):
    values = []
    for raw in (query or {}).get("status") or []:
        for value in str(raw).split(","):
            normalized = storage.clean(value).lower()
            if normalized and normalized not in values:
                values.append(normalized)
    return values


def _page_limit(query):
    raw = _first(query, "limit", str(DEFAULT_PAGE_LIMIT))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ReadModelError(400, "limit must be a positive integer.") from exc
    if value <= 0:
        raise ReadModelError(400, "limit must be a positive integer.")
    return min(value, MAX_PAGE_LIMIT)


def _minimum_fit(query):
    raw = _first_with_alias(query, "minimum_fit_score", "min_fit", "0")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ReadModelError(400, "minimum_fit_score must be an integer from 0 to 100.") from exc
    if value < 0 or value > 100:
        raise ReadModelError(400, "minimum_fit_score must be an integer from 0 to 100.")
    return value


def _cursor_fingerprint(pool, filters):
    encoded = json.dumps({"pool": pool, "filters": filters}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _encode_cursor(pool, revision, offset, fingerprint):
    value = json.dumps(
        {"v": 1, "pool": pool, "revision": revision, "offset": offset, "fingerprint": fingerprint},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_cursor(value, pool, revision, fingerprint):
    if not value:
        return 0
    if len(value) > 1024:
        raise ReadModelError(400, "cursor is invalid.")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode((value + padding).encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
        offset = int(payload["offset"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadModelError(400, "cursor is invalid.") from exc
    if payload.get("v") != 1 or payload.get("pool") != pool or payload.get("fingerprint") != fingerprint:
        raise ReadModelError(400, "cursor does not match these filters.")
    if payload.get("revision") != revision:
        raise ReadModelError(409, "Candidate data changed; reload the first page.")
    if offset < 0:
        raise ReadModelError(400, "cursor is invalid.")
    return offset


def _company_summary(company):
    if not company:
        return None
    return {
        "id": company.get("id", ""),
        "name": company.get("name", ""),
        "interest_status": company.get("interest_status", ""),
        "tracking_status": company.get("tracking_status", ""),
        "industry": company.get("industry", ""),
        "company_size": company.get("company_size", ""),
        "website": company.get("website", ""),
        "careers_url": company.get("careers_url", ""),
    }


def _candidate_identity(candidate):
    urls = company_store.candidate_source_urls(candidate)
    requisitions = set()
    identity_keys = set()
    for url in urls:
        requisitions.update(company_store.normalized_requisition_ids(url))
        identity_keys.update(company_store.posting_identity_keys(url))
    return requisitions, identity_keys


def _cross_pool_identity_matches(left, right):
    left_requisitions, left_keys = left
    right_requisitions, right_keys = right
    if left_requisitions and right_requisitions:
        return bool(left_requisitions & right_requisitions)
    return bool(left_keys & right_keys)


def _company_merge_suggestions(company_rows):
    groups = {}
    for company in company_rows:
        key = company_store.company_merge_key(company.get("name", ""))
        if key:
            groups.setdefault(key, []).append(company)
    result = []
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        ordered = sorted(
            rows,
            key=lambda company: (
                company.get("tracking_status") == "tracked",
                bool(company.get("industry")) + bool(company.get("company_size")),
                -len(company.get("name", "")),
            ),
            reverse=True,
        )
        keep = ordered[0]
        result.extend(
            {
                "id": f"{keep.get('id', '')}:{duplicate.get('id', '')}",
                "keep_company_id": keep.get("id", ""),
                "keep_company_name": keep.get("name", ""),
                "merge_company_id": duplicate.get("id", ""),
                "merge_company_name": duplicate.get("name", ""),
                "reason": "These company names differ only by a legal suffix or punctuation.",
                "match_key": key,
            }
            for duplicate in ordered[1:]
        )
    return result


def _discovery_preference_suggestions(candidate_rows, search_rows):
    searches_by_id = {
        search.get("id", ""): search
        for search in search_rows
        if search.get("id", "")
    }
    fallback_search_id = next(iter(searches_by_id), "") if len(searches_by_id) == 1 else ""
    ignored_by_search = {}
    for candidate in candidate_rows:
        if candidate.get("status") != "ignored":
            continue
        if candidate.get("ignore_reason") not in {"", "wrong-role", "level", "other"}:
            continue
        search_ids = discovery_store.candidate_search_ids(candidate)
        if not search_ids and fallback_search_id:
            search_ids = [fallback_search_id]
        for search_id in search_ids:
            if search_id in searches_by_id:
                ignored_by_search.setdefault(search_id, []).append(candidate)
    stop_words = {
        "and", "for", "the", "technical", "technology", "program", "programme",
        "manager", "management", "senior", "staff", "principal", "lead", "director",
        "remote", "hybrid", "onsite", "role", "jobs", "system", "systems",
        "product", "engineering", "software",
    }
    result = []
    for search_id, ignored in ignored_by_search.items():
        search = searches_by_id[search_id]
        excluded_terms = {
            storage.clean(term).lower()
            for term in search.get("excluded_terms", [])
            if storage.clean(term)
        }
        terms = {}
        for candidate in ignored:
            title = storage.clean(candidate.get("title", ""))
            for term in {
                token
                for token in re.findall(r"[a-z][a-z0-9+#.-]{2,}", title.lower())
                if token not in stop_words and token not in excluded_terms
            }:
                item = terms.setdefault(term, {"candidate_ids": [], "samples": []})
                item["candidate_ids"].append(candidate.get("id", ""))
                if title and title not in item["samples"]:
                    item["samples"].append(title)
        result.extend(
            {
                "id": f"exclude:{search_id}:{term}",
                "search_id": search_id,
                "search_name": search.get("name", ""),
                "term": term,
                "ignored_count": len(item["candidate_ids"]),
                "sample_titles": item["samples"][:3],
                "reason": f"{len(item['candidate_ids'])} ignored roles contain “{term}”.",
            }
            for term, item in terms.items()
            if len(item["candidate_ids"]) >= 2
        )
    return sorted(
        result,
        key=lambda item: (-item["ignored_count"], item["search_name"].lower(), item["term"]),
    )[:10]


@dataclass
class CandidateReadContext:
    revision: int
    stable_revision: bool
    companies: list
    applications: list
    searches: list
    company_candidates: list
    discovery_candidates: list
    company_by_id: dict
    search_by_id: dict
    excluded_company_candidate_ids: set
    excluded_discovery_candidate_ids: set
    ignored_discovery_candidate_ids: set

    @classmethod
    def from_rows(
        cls,
        *,
        companies,
        applications,
        searches,
        company_candidates,
        discovery_candidates,
        revision=0,
        stable_revision=True,
    ):
        company_rows = [dict(row) for row in companies]
        application_rows = [dict(row) for row in applications]
        search_rows = [dict(row) for row in searches]
        company_by_id = candidate_eligibility.companies_by_id(company_rows)
        search_by_id = {
            storage.clean(row.get("id", "")).upper(): row
            for row in search_rows
            if storage.clean(row.get("id", ""))
        }
        tracked_context_by_company_id = {
            company_id: company_store.tracked_posting_context(company, application_rows=application_rows)
            for company_id, company in company_by_id.items()
        }

        collapsed_discovery = discovery_store.canonicalize_candidate_rows(
            [dict(row) for row in discovery_candidates]
        )
        resolved_discovery = [
            discovery_store.resolved_candidate_for_review(row, company_rows)
            for row in collapsed_discovery
        ]
        ignored_discovery_ids = {
            row.get("id", "")
            for row in resolved_discovery
            if discovery_store.ignored_discovery_source(row.get("canonical_url") or row.get("url", ""))
        }
        excluded_discovery_ids = {
            row.get("id", "")
            for row in resolved_discovery
            if candidate_eligibility.candidate_is_excluded(row, company_by_id)
        }
        for row in resolved_discovery:
            company_id = storage.clean(row.get("company_id", "")).upper()
            company = company_by_id.get(company_id)
            tracked = tracked_context_by_company_id.get(company_id)
            row["matching_posting_ids"] = (
                company_store.matching_tracked_posting_ids(
                    row,
                    company=company,
                    tracked=tracked,
                )
                if company and tracked and tracked.get("postings")
                else []
            )

        discovery_by_company_id = {}
        discovery_identity_by_object_id = {}
        for row in resolved_discovery:
            discovery_by_company_id.setdefault(storage.clean(row.get("company_id", "")).upper(), []).append(row)
            discovery_identity_by_object_id[id(row)] = _candidate_identity(row)

        enriched_company = []
        excluded_company_ids = set()
        for candidate in company_candidates:
            row = dict(candidate)
            company_id = storage.clean(row.get("company_id", "")).upper()
            company = company_by_id.get(company_id)
            if candidate_eligibility.company_is_excluded(company):
                excluded_company_ids.add(row.get("id", ""))
            row["review_state"] = company_store.candidate_review_state(row)
            row_identity = _candidate_identity(row)
            row["requisition_ids"] = sorted(row_identity[0])
            tracked = tracked_context_by_company_id.get(company_id)
            row["matching_posting_ids"] = (
                company_store.matching_tracked_posting_ids(
                    row,
                    company=company,
                    tracked=tracked,
                )
                if company and tracked and tracked.get("postings")
                else []
            )
            matches = [
                discovery_candidate
                for discovery_candidate in discovery_by_company_id.get(company_id, [])
                if _cross_pool_identity_matches(
                    row_identity,
                    discovery_identity_by_object_id[id(discovery_candidate)],
                )
            ]
            row["discovery_candidate_id"] = matches[0].get("id", "") if matches else ""
            enriched_company.append(row)

        enriched_company, resolved_discovery = app_state.canonicalize_candidate_visibility(
            enriched_company,
            resolved_discovery,
        )
        return cls(
            revision=revision,
            stable_revision=stable_revision,
            companies=company_rows,
            applications=application_rows,
            searches=search_rows,
            company_candidates=enriched_company,
            discovery_candidates=resolved_discovery,
            company_by_id=company_by_id,
            search_by_id=search_by_id,
            excluded_company_candidate_ids=excluded_company_ids,
            excluded_discovery_candidate_ids=excluded_discovery_ids,
            ignored_discovery_candidate_ids=ignored_discovery_ids,
        )

    @classmethod
    def read(cls):
        global _candidate_context_cache

        revision = repository.data_revision()
        cached = _candidate_context_cache
        if cached is not None and cached.revision == revision:
            return cached
        # The frontend loads the shell before route-specific candidate pages.
        # Serialize the first construction so concurrent local requests reuse
        # one revision-consistent context instead of rebuilding both pools.
        with _candidate_context_lock:
            revision = repository.data_revision()
            cached = _candidate_context_cache
            if cached is not None and cached.revision == revision:
                return cached
            for _attempt in range(2):
                before = repository.data_revision()
                context = cls.from_rows(
                    companies=repository.read_companies(),
                    applications=repository.read_applications(),
                    searches=discovery_store.list_searches(),
                    company_candidates=repository.read_company_posting_candidates(),
                    discovery_candidates=repository.read_discovery_candidates(),
                    revision=before,
                )
                after = repository.data_revision()
                if before == after:
                    _candidate_context_cache = context
                    return context
        raise ReadModelError(409, "Candidate data changed while loading; retry the request.")


_candidate_context_cache = None
_candidate_context_lock = threading.Lock()


def _query_filters(query, pool):
    search_id = _first(query, "search_id").upper() if pool == "discovery" else ""
    return {
        "search": _first_with_alias(query, "search", "q").lower(),
        "status": _status_values(query),
        "minimum_fit_score": _minimum_fit(query),
        "tracking_status": _first_with_alias(query, "tracking_status", "tracking").lower(),
        "company_id": _first(query, "company_id").upper(),
        "include_excluded_companies": _truthy_query(query, "include_excluded_companies"),
        "search_id": search_id,
    }


def _candidate_matches_filters(row, company, filters):
    if (
        filters["status"]
        and storage.clean(row.get("canonical_status") or row.get("status", "")).lower()
        not in filters["status"]
    ):
        return False
    if _fit_score(row) < filters["minimum_fit_score"]:
        return False
    if (
        filters["tracking_status"]
        and storage.clean((company or {}).get("tracking_status", "")).lower()
        != filters["tracking_status"]
    ):
        return False
    if filters["company_id"] and storage.clean(row.get("company_id", "")).upper() != filters["company_id"]:
        return False
    if filters["search"]:
        text = " ".join(
            [
                row.get("title", ""),
                row.get("location", ""),
                row.get("work_mode", ""),
                row.get("source_platform", ""),
                (company or {}).get("name", ""),
            ]
        ).lower()
        if filters["search"] not in text:
            return False
    return True


def _facet_values(rows, context):
    statuses = {}
    tracking = {}
    companies = {}
    for row in rows:
        company = context.company_by_id.get(storage.clean(row.get("company_id", "")).upper())
        status = storage.clean(row.get("canonical_status") or row.get("status", "")).lower() or "unknown"
        statuses[status] = statuses.get(status, 0) + 1
        tracking_status = storage.clean((company or {}).get("tracking_status", "")).lower() or "unknown"
        tracking[tracking_status] = tracking.get(tracking_status, 0) + 1
        company_id = storage.clean(row.get("company_id", "")).upper()
        if company_id:
            item = companies.setdefault(
                company_id,
                {"value": company_id, "label": (company or {}).get("name", company_id), "count": 0},
            )
            item["count"] += 1
    return {
        "statuses": [{"value": key, "count": statuses[key]} for key in sorted(statuses)],
        "tracking": [{"value": key, "count": tracking[key]} for key in sorted(tracking)],
        "companies": sorted(companies.values(), key=lambda item: (-item["count"], item["label"].lower())),
    }


def _company_list_item(row, context):
    company = context.company_by_id.get(storage.clean(row.get("company_id", "")).upper())
    lane_match = discovery_store.candidate_lane_match(
        {**row, "description_text": row.get("description_excerpt", "")},
        context.searches,
    )
    return {
        "id": row.get("id", ""),
        "company_id": row.get("company_id", ""),
        "company": _company_summary(company),
        "title": row.get("title", ""),
        "url": row.get("url", ""),
        "location": row.get("location", ""),
        "work_mode": row.get("work_mode", ""),
        "category": row.get("category", ""),
        "source_platform": row.get("source_platform", ""),
        "source_job_id": row.get("source_job_id", ""),
        "scan_state": row.get("scan_state", ""),
        "last_verified_at": row.get("last_verified_at", ""),
        "status": row.get("status", ""),
        "canonical_status": row.get("canonical_status", ""),
        "first_seen_at": row.get("first_seen_at", ""),
        "last_seen_at": row.get("last_seen_at", ""),
        "fit_score": row.get("fit_score", ""),
        "fit_summary": storage.clean(row.get("fit_summary", ""))[:LIST_DESCRIPTION_LIMIT],
        "fit_checked_at": row.get("fit_checked_at", ""),
        "review_state": row.get("review_state", ""),
        "matching_posting_ids": row.get("matching_posting_ids", []),
        "discovery_candidate_id": row.get("discovery_candidate_id", ""),
        "lane_match": lane_match,
        "description_excerpt": storage.clean(row.get("description_excerpt", ""))[:LIST_DESCRIPTION_LIMIT],
        "description_truncated": len(storage.clean(row.get("description_excerpt", ""))) > LIST_DESCRIPTION_LIMIT,
    }


def _discovery_list_item(row, context):
    company_id = storage.clean(row.get("company_id", "")).upper()
    company = context.company_by_id.get(company_id)
    role_family = discovery_store.candidate_role_family(row)
    source_trust = discovery_store.candidate_source_trust(row, company)
    description = storage.clean(row.get("description_excerpt", ""))
    if not description:
        description = storage.clean(row.get("description_text", ""))
    return {
        "id": row.get("id", ""),
        "search_ids": discovery_store.candidate_search_ids(row),
        "company_id": row.get("company_id", ""),
        "company": _company_summary(company),
        "title": row.get("title", ""),
        "url": row.get("url", ""),
        "canonical_url": row.get("canonical_url", ""),
        "location": row.get("location", ""),
        "work_mode": row.get("work_mode", ""),
        "source_platform": row.get("source_platform", ""),
        "captured_at": row.get("captured_at", ""),
        "last_seen_at": row.get("last_seen_at", ""),
        "status": row.get("status", ""),
        "canonical_status": row.get("canonical_status", ""),
        "processing_status": row.get("processing_status", ""),
        "qualification_status": row.get("qualification_status", ""),
        "qualification_reason": row.get("qualification_reason", ""),
        "detail_last_error": storage.clean(row.get("detail_last_error", ""))[:LIST_DESCRIPTION_LIMIT],
        "detail_next_action": discovery_store.candidate_detail_next_action(row),
        "review_next_action": discovery_store.candidate_review_next_action(row),
        "fit_score": row.get("fit_score", ""),
        "fit_summary": storage.clean(row.get("fit_summary", ""))[:LIST_DESCRIPTION_LIMIT],
        "fit_checked_at": row.get("fit_checked_at", ""),
        "freshness_status": row.get("freshness_status", ""),
        "freshness_checked_at": row.get("freshness_checked_at", ""),
        "detail_state": discovery_store.candidate_detail_state(row),
        "review_state": discovery_store.candidate_review_state(row),
        "recommendation_eligible": discovery_store.recommendation_eligible(row, company),
        "source_trust": source_trust["id"],
        "source_trust_label": source_trust["label"],
        "source_confidence": discovery_store.candidate_source_confidence(row, company),
        "lane_match": discovery_store.candidate_lane_match(row, context.searches),
        "role_family_id": role_family["id"] if role_family else "",
        "role_family": role_family["label"] if role_family else "Saved keyword match",
        "matching_posting_ids": row.get("matching_posting_ids", []),
        "company_candidate_id": row.get("company_candidate_id", ""),
        "description_excerpt": description[:LIST_DESCRIPTION_LIMIT],
        "description_truncated": len(description) > LIST_DESCRIPTION_LIMIT,
    }


def candidate_page(pool, query=None, context=None):
    if pool not in {"company", "discovery"}:
        raise ReadModelError(404, "Unknown candidate pool.")
    context = context or CandidateReadContext.read()
    filters = _query_filters(query, pool)
    limit = _page_limit(query)
    # Saved-search selection configures acquisition and response context only. It
    # must never bind a cursor for the one global Discovery review queue.
    cursor_filters = {key: value for key, value in filters.items() if key != "search_id"}
    fingerprint = _cursor_fingerprint(pool, cursor_filters)
    offset = _decode_cursor(_first(query, "cursor"), pool, context.revision, fingerprint)
    if pool == "company":
        all_rows = context.company_candidates
        excluded_ids = context.excluded_company_candidate_ids
        ignored_ids = set()
        projector = _company_list_item
    else:
        all_rows = context.discovery_candidates
        excluded_ids = context.excluded_discovery_candidate_ids
        ignored_ids = context.ignored_discovery_candidate_ids
        projector = _discovery_list_item

    eligible_rows = [
        row
        for row in all_rows
        if row.get("id", "") not in ignored_ids
        if filters["include_excluded_companies"] or row.get("id", "") not in excluded_ids
    ]
    canonical_rows = [row for row in eligible_rows if row.get("is_canonical", True)]
    filtered_rows = [
        row
        for row in canonical_rows
        if _candidate_matches_filters(
            row,
            context.company_by_id.get(storage.clean(row.get("company_id", "")).upper()),
            filters,
        )
    ]
    filtered_rows.sort(key=lambda row: (-_fit_score(row), row.get("title", "").lower(), row.get("id", "")))
    selected = filtered_rows[offset : offset + limit]
    next_offset = offset + len(selected)
    has_more = next_offset < len(filtered_rows)
    search = context.search_by_id.get(filters["search_id"])
    return {
        "api_version": API_VERSION,
        "pool": pool,
        "revision": context.revision,
        "items": [projector(row, context) for row in selected],
        "counts": {
            "source": len(all_rows),
            "eligible": len(eligible_rows),
            "canonical": len(canonical_rows),
            "filtered": len(filtered_rows),
            "returned": len(selected),
            "excluded_companies": len(excluded_ids),
            "ignored_sources": len(ignored_ids),
        },
        "facets": _facet_values(canonical_rows, context),
        "page": {
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "next_cursor": _encode_cursor(pool, context.revision, next_offset, fingerprint) if has_more else "",
        },
        "audit": {
            "stable_revision": context.stable_revision,
            "filters": filters,
            "canonical_hidden_count": len(eligible_rows) - len(canonical_rows),
            "search_context": (
                {
                    "id": filters["search_id"],
                    "name": (search or {}).get("name", ""),
                    "affects_rows": False,
                }
                if pool == "discovery" and filters["search_id"]
                else None
            ),
        },
    }


def company_candidate_page(query=None, context=None):
    return candidate_page("company", query, context)


def discovery_candidate_page(query=None, context=None):
    return candidate_page("discovery", query, context)


def candidate_detail(pool, query=None, context=None):
    if pool not in {"company", "discovery"}:
        raise ReadModelError(404, "Unknown candidate pool.")
    context = context or CandidateReadContext.read()
    candidate_id = _first(query, "id").upper()
    if not candidate_id:
        raise ReadModelError(400, "Candidate id is required.")
    include_excluded = _truthy_query(query, "include_excluded_companies")
    rows = context.company_candidates if pool == "company" else context.discovery_candidates
    row = next((item for item in rows if storage.clean(item.get("id", "")).upper() == candidate_id), None)
    if row is None:
        raise ReadModelError(404, f"No {pool} candidate found with id {candidate_id}.")
    excluded_ids = (
        context.excluded_company_candidate_ids
        if pool == "company"
        else context.excluded_discovery_candidate_ids
    )
    excluded = row.get("id", "") in excluded_ids
    if excluded and not include_excluded:
        raise ReadModelError(404, f"No {pool} candidate found with id {candidate_id}.")
    company_id = storage.clean(row.get("company_id", "")).upper()
    company = context.company_by_id.get(company_id)
    if pool == "discovery":
        item = discovery_store.candidate_payload(
            row,
            context.company_by_id,
            context.searches,
            {
                company_id: company_store.tracked_posting_context(
                    company,
                    application_rows=context.applications,
                )
            } if company else {},
        )
    else:
        item = dict(row)
        item["lane_match"] = discovery_store.candidate_lane_match(
            {**row, "description_text": row.get("description_excerpt", "")},
            context.searches,
        )
        item["source_urls"] = company_store.candidate_source_urls(row)
    item["company"] = _company_summary(company)
    return {
        "api_version": API_VERSION,
        "pool": pool,
        "revision": context.revision,
        "item": item,
        "audit": {
            "stable_revision": context.stable_revision,
            "excluded_company": excluded,
            "includes_full_description": pool == "discovery",
            "includes_notes": True,
        },
    }


def company_candidate_detail(query=None, context=None):
    return candidate_detail("company", query, context)


def discovery_candidate_detail(query=None, context=None):
    return candidate_detail("discovery", query, context)


def _read_candidate_detail(pool, query=None):
    """Read one detail without constructing the full multi-thousand-row list model."""
    candidate_id = _first(query, "id").upper()
    if not candidate_id:
        raise ReadModelError(400, "Candidate id is required.")
    for _attempt in range(2):
        before = repository.data_revision()
        companies = repository.read_companies()
        applications = repository.read_applications()
        searches = discovery_store.list_searches()
        if pool == "company":
            candidate = repository.read_company_posting_candidate(candidate_id)
            company_candidates = [candidate] if candidate else []
            # The smaller opposite pool supplies cross-pool provenance for this one role.
            candidate_company_id = storage.clean((candidate or {}).get("company_id", "")).upper()
            discovery_candidates = [
                row
                for row in repository.read_discovery_candidates()
                if not storage.clean(row.get("company_id", ""))
                or storage.clean(row.get("company_id", "")).upper() == candidate_company_id
            ] if candidate else []
        else:
            candidate = repository.read_discovery_candidate(candidate_id)
            company_candidates = []
            discovery_candidates = [candidate] if candidate else []
        if candidate is None:
            raise ReadModelError(404, f"No {pool} candidate found with id {candidate_id}.")
        context = CandidateReadContext.from_rows(
            companies=companies,
            applications=applications,
            searches=searches,
            company_candidates=company_candidates,
            discovery_candidates=discovery_candidates,
            revision=before,
        )
        result = candidate_detail(pool, query, context)
        after = repository.data_revision()
        if before == after:
            return result
    raise ReadModelError(409, "Candidate data changed while loading; retry the request.")


def build_company_candidate_detail(query=None):
    return _read_candidate_detail("company", query)


def build_discovery_candidate_detail(query=None):
    return _read_candidate_detail("discovery", query)


def _entity_rows(resource):
    if resource == "application":
        return repository.read_applications(), None
    if resource == "action":
        return repository.read_actions(), None
    if resource == "contact":
        return repository.read_contacts(), None
    if resource == "company":
        return repository.read_companies(), repository.read_company_career_sources()
    raise ReadModelError(404, "Unknown detail resource.")


def _read_entity_detail(resource, query=None):
    raw_id = _first(query, "id")
    if not raw_id:
        raise ReadModelError(400, f"{resource.title()} id is required.")
    wanted_id = raw_id.upper()
    for _attempt in range(2):
        before = repository.data_revision()
        rows, career_sources = _entity_rows(resource)
        item = next(
            (
                dict(row)
                for row in rows
                if storage.clean(row.get("id", "")).upper() == wanted_id
            ),
            None,
        )
        if item is not None and resource == "company":
            item["company_career_source"] = next(
                (
                    dict(source)
                    for source in career_sources
                    if storage.clean(source.get("company_id", "")).upper() == wanted_id
                ),
                None,
            )
        after = repository.data_revision()
        if before != after:
            continue
        if item is None:
            raise ReadModelError(404, f"No {resource} found with id {wanted_id}.")
        return {
            "api_version": API_VERSION,
            "resource": resource,
            "revision": before,
            "item": item,
            "audit": {
                "stable_revision": True,
                "includes_omitted_fields": True,
            },
        }
    raise ReadModelError(409, "Hunter data changed while loading; retry the request.")


def build_application_detail(query=None):
    return _read_entity_detail("application", query)


def build_action_detail(query=None):
    return _read_entity_detail("action", query)


def build_contact_detail(query=None):
    return _read_entity_detail("contact", query)


def build_company_detail(query=None):
    return _read_entity_detail("company", query)


def _application_shell_rows(application_rows, action_rows):
    actions_by_application = {}
    for action in action_rows:
        actions_by_application.setdefault(storage.clean(action.get("application_id", "")).upper(), []).append(action)
    projected = []
    for application in application_rows:
        row = _copy_fields(application, APPLICATION_SHELL_FIELDS)
        related = actions_by_application.get(storage.clean(row.get("id", "")).upper(), [])
        open_related = action_store.open_actions(related)
        row["open_action_count"] = len(open_related)
        row["next_action_warning"] = ""
        if row.get("stage", "").lower() == "considering" and len(open_related) != 1:
            row["next_action_warning"] = (
                "Considering requires exactly one open next action; "
                f"found {len(open_related)}."
            )
        if row.get("stage", "").lower() == "closed":
            row["next_action_id"] = ""
            row["next_action"] = ""
            row["next_action_date"] = ""
        else:
            next_action = action_store.select_next_action(row, related)
            row["next_action_id"] = next_action.get("id", "") if next_action else ""
            row["next_action"] = next_action.get("title", "") if next_action else ""
            row["next_action_date"] = next_action.get("due_date", "") if next_action else ""
        row["tag_list"] = storage.split_tags(row.get("tags", ""))
        projected.append(row)
    return app_state.enrich_rows(projected)


def app_shell_from_rows(
    *,
    revision,
    applications,
    actions,
    workflow_payload,
    contacts,
    application_contacts,
    companies,
    company_contacts,
    company_career_sources,
    discovery_searches,
    company_candidates,
    discovery_candidates,
    dismissed_suggestion_ids,
    stable_revision=True,
    candidate_context=None,
):
    company_rows = [dict(row) for row in companies]
    candidate_context = candidate_context or CandidateReadContext.from_rows(
        companies=company_rows,
        applications=applications,
        searches=discovery_searches,
        company_candidates=company_candidates,
        discovery_candidates=discovery_candidates,
        revision=revision,
        stable_revision=stable_revision,
    )
    visible_by_pool = {
        "company": [
            row
            for row in candidate_context.company_candidates
            if row.get("is_canonical", True)
            and row.get("id", "") not in candidate_context.excluded_company_candidate_ids
        ],
        "discovery": [
            row
            for row in candidate_context.discovery_candidates
            if row.get("is_canonical", True)
            and row.get("id", "") not in candidate_context.excluded_discovery_candidate_ids
            and row.get("id", "") not in candidate_context.ignored_discovery_candidate_ids
        ],
    }
    eligible_company_candidates = [
        row
        for row in candidate_context.company_candidates
        if row.get("id", "") not in candidate_context.excluded_company_candidate_ids
    ]
    eligible_discovery_candidates = [
        {
            **row,
            "recommendation_eligible": discovery_store.recommendation_eligible(
                row,
                candidate_context.company_by_id.get(
                    storage.clean(row.get("company_id", "")).upper()
                ),
            ),
        }
        for row in candidate_context.discovery_candidates
        if row.get("id", "") not in candidate_context.excluded_discovery_candidate_ids
        and row.get("id", "") not in candidate_context.ignored_discovery_candidate_ids
    ]
    enriched_company_rows = app_state.enrich_companies(
        company_rows,
        eligible_discovery_candidates,
        eligible_company_candidates,
    )
    compact_companies = []
    for company in enriched_company_rows:
        row = {
            field: company.get(field, "")
            for field in COMPANY_SHELL_FIELDS
        }
        row["company_metadata_suggestion_count"] = _json_list_count(
            company.get("company_metadata_suggestions_json", "")
        )
        fit_summary = storage.clean(company.get("company_fit_summary", ""))
        row["company_fit_summary"] = fit_summary[:COMPANY_FIT_SUMMARY_LIMIT]
        compact_companies.append(row)
    action_rows = app_state.enrich_actions([_copy_fields(row, ACTION_SHELL_FIELDS) for row in actions])
    return {
        "api_version": API_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generated_date": date.today().isoformat(),
        "revision": revision,
        "applications": _application_shell_rows(applications, actions),
        "actions": action_rows,
        "workflow": workflow_payload,
        "contacts": [_copy_fields(row, CONTACT_SHELL_FIELDS) for row in contacts],
        "application_contacts": [dict(row) for row in application_contacts],
        # Companies dominate shell volume because the same field names repeat
        # hundreds of times. A small row-table envelope preserves the complete
        # list contract while keeping the local JSON response under budget.
        "companies": {
            "fields": list(COMPANY_SHELL_FIELDS),
            "rows": [
                [company.get(field, "") for field in COMPANY_SHELL_FIELDS]
                for company in compact_companies
            ],
        },
        "company_merge_suggestions": _company_merge_suggestions(company_rows),
        "company_contacts": [dict(row) for row in company_contacts],
        "company_career_sources": [
            {key: value for key, value in row.items() if key not in {"evidence", "notes"}}
            for row in company_career_sources
        ],
        "discovery_searches": [
            _compact_discovery_search(row)
            for row in discovery_searches
        ],
        "discovery_preference_suggestions": _discovery_preference_suggestions(
            visible_by_pool["discovery"],
            discovery_searches,
        ),
        "dismissed_suggestion_ids": sorted(dismissed_suggestion_ids),
        "candidate_counts": {
            "company": len(visible_by_pool["company"]),
            "discovery": len(visible_by_pool["discovery"]),
        },
        "candidate_review_audit": {
            "excluded_company_candidate_count": (
                len(candidate_context.excluded_company_candidate_ids)
                + len(candidate_context.excluded_discovery_candidate_ids)
            ),
            "discovery_excluded_company_candidate_count": len(
                candidate_context.excluded_discovery_candidate_ids
            ),
            "tracked_company_excluded_company_candidate_count": len(
                candidate_context.excluded_company_candidate_ids
            ),
        },
        "audit": {
            "stable_revision": stable_revision,
            "omitted_large_fields": ["candidate pools", "notes", "company evidence", "career-source evidence"],
        },
    }


def build_app_shell(_query=None):
    for _attempt in range(2):
        before = repository.data_revision()
        candidate_context = CandidateReadContext.read()
        if candidate_context.revision != before:
            continue
        shell = app_shell_from_rows(
            revision=before,
            applications=candidate_context.applications,
            actions=repository.read_actions(),
            workflow_payload=workflow.read_workflow(),
            contacts=repository.read_contacts(),
            application_contacts=repository.read_application_contacts(),
            companies=candidate_context.companies,
            company_contacts=repository.read_company_contacts(),
            company_career_sources=repository.read_company_career_sources(),
            discovery_searches=candidate_context.searches,
            company_candidates=candidate_context.company_candidates,
            discovery_candidates=candidate_context.discovery_candidates,
            dismissed_suggestion_ids=suggestions.dismissed_ids(),
            candidate_context=candidate_context,
        )
        after = repository.data_revision()
        if before == after:
            return shell
    raise ReadModelError(409, "Hunter data changed while loading; retry the request.")


READ_MODEL_GET_ROUTES = {
    "/api/app-shell": build_app_shell,
    "/api/applications/detail": build_application_detail,
    "/api/actions/detail": build_action_detail,
    "/api/contacts/detail": build_contact_detail,
    "/api/companies/detail": build_company_detail,
    "/api/candidates/company": company_candidate_page,
    "/api/candidates/discovery": discovery_candidate_page,
    "/api/candidates/company/detail": build_company_candidate_detail,
    "/api/candidates/discovery/detail": build_discovery_candidate_detail,
}
