"""Company management operations for Hunter."""

import html
import hashlib
import json
import re
import ssl
import subprocess
import sys
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from .adapters import greenhouse as greenhouse_adapter, lever as lever_adapter
from .adapters.text import normalize_url, clean_html_text
from .adapters.greenhouse import (greenhouse_text_value, greenhouse_metadata_value, greenhouse_location, greenhouse_category, greenhouse_candidate_url)

from . import applications, candidate_eligibility, paths, repository, schema, settings as settings_store, storage


EDITABLE_FIELDS = {
    "name",
    "aliases",
    "interest_status",
    "tracking_status",
    "website",
    "careers_url",
    "industry",
    "company_size",
    "company_profile_url",
    "notes",
}
JOB_BOARD_HOST_MARKERS = {
    "ashbyhq.com",
    "greenhouse.io",
    "icims.com",
    "jobs.apple.com",
    "lever.co",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "workdayjobs.com",
}
JOB_LINK_PATTERN = re.compile(r"\b(job|jobs|career|careers|position|positions|opening|openings|req|role)\b", re.I)
JOB_URL_PATTERN = re.compile(
    r"(^|/)(job|jobs|job-detail|jobdetail|job-postings|positions|openings|req|requisition|details)(/|$)|[?&](gh_jid|jobid|job_id|reqid|req_id)=",
    re.I,
)
NON_JOB_TITLE_PATTERN = re.compile(
    r"\b(employee\s+login|login|sign\s+in|job\s+listings|all\s+jobs|us\s+jobs|global\s+jobs)\b",
    re.I,
)
NON_JOB_URL_PATTERN = re.compile(r"(^|/)login(?:/|$|\?)|[?&]loginOnly=1", re.I)
JOB_ID_QUERY_KEYS = {"gh_jid", "jobid", "job_id", "jobId".lower(), "reqid", "req_id", "req"}
GOOGLE_CAREERS_HOST = "www.google.com"
GOOGLE_CAREERS_RESULTS_PATH = "/about/careers/applications/jobs/results"
OPENAI_CAREERS_HOST = "openai.com"
OPENAI_ASHBY_BOARD_URL = "https://jobs.ashbyhq.com/openai"
AMAZON_JOBS_HOST = "amazon.jobs"
TARGET_CAREERS_HOST = "corporate.target.com"
TARGET_JOB_SEARCH_PATH = "/careers/job-search"
TARGET_JOB_API_PATH = "/api/jobsearch"
SHOPIFY_CAREERS_HOST = "www.shopify.com"
AVATURE_HOST_MARKER = "avature.net"
GREENHOUSE_BOARD_HOSTS = {"job-boards.greenhouse.io", "boards.greenhouse.io"}
GREENHOUSE_TOKEN_PROBE_LIMIT = 6
BRANDED_GREENHOUSE_JOB_PATTERN = re.compile(r"(/greenhouse/job/\d+|[?&]gh_jid=\d+)", re.I)
EIGHTFOLD_PCS_PATH = "/careers"
CAREERS_SEARCH_MAX_PAGES = 3
CAREERS_SEARCH_MAX_TERMS = 8
CAREERS_SEARCH_RESULT_LIMIT = 25
WORKDAY_CXS_RESULT_LIMIT = 20
CAREER_DISCOVERY_SCRIPT_LIMIT = 20
JIBE_API_LIMIT = 10
FIT_RECOMMENDATION_THRESHOLD = 45
RECOMMENDED_CANDIDATE_LIMIT = 25
CANDIDATE_DETAIL_VERIFY_LIMIT = 25
MIN_REVIEW_DESCRIPTION_CHARS = 500
CAREER_SEARCH_MODEL = "gpt-5.6-luna"
SEARCH_ROLE_TERM_MIN_WEIGHT = 20
SEARCH_SENIORITY_TERM_MIN_WEIGHT = 5
SEARCH_EXPANSION_BASE_LIMIT = 6
SEARCH_TERM_ROLE_WEIGHT = 34
UNAVAILABLE_DETAIL_STATUS_CODES = {404, 410}
NUMERIC_JOB_SLUG_PATTERN = re.compile(r"^(\d{8,})(?:[-_].*)?$", re.I)
SEARCH_SUFFIX_LEVEL_PATTERN = re.compile(r"^(?:[ivx]{2,6}|l\d+|[2-9])$", re.I)
UNAVAILABLE_DETAIL_PATTERNS = [
    re.compile(r"\b(job|position|posting)\s+(is\s+)?no longer available\b", re.I),
    re.compile(r"\b(this\s+)?(job|position|posting)\s+is\s+no longer open\b", re.I),
    re.compile(r"\b(this\s+)?(job|position|posting)\s+has been filled\b", re.I),
    re.compile(r"\bno longer accepting applications\b", re.I),
    re.compile(r"\bnot accepting applications\b", re.I),
    re.compile(r"\bjob not found\b", re.I),
    re.compile(r"\bpage not found\b", re.I),
]
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
DEFAULT_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7"


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def now_scan_iso():
    return datetime.now().isoformat(timespec="microseconds")


@lru_cache(maxsize=16_384)
def normalized_key(value):
    return re.sub(r"[^a-z0-9]+", " ", storage.clean(value).lower()).strip()


def normalized_text(value):
    return re.sub(r"[^a-z0-9+]+", " ", storage.clean(value).lower()).strip()


def split_aliases(value):
    aliases = []
    for raw_alias in re.split(r"[,;]", value or ""):
        alias = storage.clean(raw_alias)
        if alias and alias not in aliases:
            aliases.append(alias)
    return aliases


def normalize_aliases(value):
    return ", ".join(split_aliases(value))


def normalize_company_industry(value):
    if isinstance(value, list):
        values = [storage.clean(str(item or "")) for item in value]
        return ", ".join(dict.fromkeys(item for item in values if item))
    return storage.clean(str(value or ""))


def normalize_company_profile_url(value):
    normalized = normalize_url(str(value or ""))
    parsed = urlparse(normalized)
    if "linkedin.com" not in parsed.netloc.lower():
        return ""
    match = re.search(r"/company/([^/?#]+)", parsed.path, re.I)
    if not match:
        return ""
    return f"https://www.linkedin.com/company/{match.group(1)}"


def normalize_company_size(value):
    if isinstance(value, dict):
        minimum = storage.clean(str(value.get("minValue", "") or ""))
        maximum = storage.clean(str(value.get("maxValue", "") or ""))
        exact = storage.clean(str(value.get("value", "") or ""))
        if minimum and maximum:
            value = f"{minimum}–{maximum}"
        else:
            value = exact or minimum or maximum
    cleaned = storage.clean(str(value or ""))
    if not cleaned:
        return ""
    cleaned = re.sub(r"\s*(?:employees?|people)\s*$", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"\s*[-–]\s*", "–", cleaned)
    if not re.search(r"\d", cleaned):
        return ""
    return f"{cleaned} employees"


def structured_company_metadata(page_html, job=None):
    objects = json_ld_objects(page_html)
    hiring = job.get("hiringOrganization") if isinstance(job, dict) else {}
    organizations = [hiring] if isinstance(hiring, dict) else []
    hiring_name = normalized_key(hiring.get("name", "")) if isinstance(hiring, dict) else ""
    for item in objects:
        item_type = item.get("@type", "")
        item_types = item_type if isinstance(item_type, list) else [item_type]
        if not any(value in {"Organization", "Corporation", "LocalBusiness"} for value in item_types):
            continue
        item_name = normalized_key(item.get("name", ""))
        if hiring_name and item_name and item_name != hiring_name:
            continue
        organizations.append(item)

    industry = ""
    company_size = ""
    profile_url = ""
    for organization in organizations:
        if not industry:
            industry = normalize_company_industry(
                organization.get("industry")
                or organization.get("naics")
            )
        if not company_size:
            company_size = normalize_company_size(
                organization.get("numberOfEmployees")
                or organization.get("employees")
            )
        if not profile_url:
            same_as = organization.get("sameAs") or organization.get("url") or ""
            if isinstance(same_as, list):
                same_as = next((value for value in same_as if storage.clean(str(value or ""))), "")
            profile_url = normalize_company_profile_url(str(same_as or ""))
    return {
        "company_industry": industry,
        "company_size": company_size,
        "company_profile_url": profile_url,
    }


def company_keys(company):
    keys = {normalized_key(company.get("name", ""))}
    keys.update(normalized_key(alias) for alias in split_aliases(company.get("aliases", "")))
    return {key for key in keys if key}


def next_company_id(rows):
    highest = 0
    for row in rows:
        match = re.fullmatch(r"CO(\d+)", row.get("id", "").upper())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"CO{highest + 1:04d}"


def next_candidate_id(rows):
    highest = 0
    for row in rows:
        match = re.fullmatch(r"CP(\d+)", row.get("id", "").upper())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"CP{highest + 1:04d}"


def validate_interest_status(value):
    status = storage.clean(value).lower() or schema.DEFAULT_COMPANY_INTEREST_STATUS
    if status not in schema.COMPANY_INTEREST_STATUSES:
        raise ValueError(f"Unsupported company interest status: {status}")
    return status


def validate_tracking_status(value):
    status = storage.clean(value).lower() or schema.DEFAULT_COMPANY_TRACKING_STATUS
    if status not in schema.COMPANY_TRACKING_STATUSES:
        raise ValueError(f"Unsupported company tracking status: {status}")
    return status


def validate_candidate_status(value):
    status = storage.clean(value).lower() or "new"
    status = schema.CANDIDATE_STATUS_ALIASES.get(status, status)
    if status not in schema.COMPANY_POSTING_CANDIDATE_STATUSES:
        raise ValueError(f"Unsupported company posting candidate status: {status}")
    return status


def list_companies():
    return repository.read_companies()


def get_company(company_id):
    wanted = storage.clean(company_id).upper()
    company = next((row for row in repository.read_companies() if row.get("id", "").upper() == wanted), None)
    if not company:
        raise ValueError(f"No company found with id {company_id}.")
    return company


def upsert_company(company_id="", updates=None):
    rows = repository.read_companies()
    wanted = storage.clean(company_id).upper()
    row = None
    if wanted:
        row = next((item for item in rows if item.get("id", "").upper() == wanted), None)
        if row is None:
            raise ValueError(f"No company found with id {company_id}.")
    is_new = row is None
    if is_new:
        row = {field: "" for field in schema.COMPANY_FIELDS}
        row["id"] = next_company_id(rows)
        row["interest_status"] = schema.DEFAULT_COMPANY_INTEREST_STATUS
        row["tracking_status"] = schema.DEFAULT_COMPANY_TRACKING_STATUS
        row["company_metadata_suggestions_json"] = "[]"
        rows.append(row)
    original = dict(row)

    metadata_changed_fields = set()
    for field, value in (updates or {}).items():
        if field not in EDITABLE_FIELDS:
            continue
        if field == "aliases":
            row[field] = normalize_aliases(value)
        elif field == "interest_status":
            row[field] = validate_interest_status(value)
        elif field == "tracking_status":
            row[field] = validate_tracking_status(value)
        elif field == "industry":
            cleaned = normalize_company_industry(value)
            if cleaned != row.get(field, ""):
                metadata_changed_fields.add(field)
            row[field] = cleaned
        elif field == "company_size":
            cleaned = normalize_company_size(value)
            if cleaned != row.get(field, ""):
                metadata_changed_fields.add(field)
            row[field] = cleaned
        elif field == "company_profile_url":
            cleaned = normalize_company_profile_url(value)
            if cleaned != row.get(field, ""):
                metadata_changed_fields.add(field)
            row[field] = cleaned
        elif field == "website":
            cleaned = normalize_url(value)
            if cleaned != row.get(field, ""):
                metadata_changed_fields.add(field)
            row[field] = cleaned
        else:
            row[field] = storage.clean(value)

    if not row.get("name"):
        raise ValueError("Company name is required.")
    if metadata_changed_fields:
        row["company_metadata_source"] = "manual"
        row["company_metadata_checked_at"] = now_iso()
        row["company_metadata_suggestions_json"] = json.dumps(
            [
                item
                for item in company_metadata_suggestions(row)
                if item.get("field", "") not in metadata_changed_fields
            ],
            ensure_ascii=False,
            sort_keys=True,
        )

    if is_new:
        row = repository.insert_companies([row])[0]
    else:
        changed = {
            field: row.get(field, "")
            for field in schema.COMPANY_FIELDS
            if field != "id" and row.get(field, "") != original.get(field, "")
        }
        if changed:
            repository.update_company_fields(row.get("id", ""), changed)
    associate_matching_postings(row.get("id", ""))
    return get_company(row.get("id", ""))


def company_metadata_suggestions(company):
    try:
        suggestions = json.loads(company.get("company_metadata_suggestions_json", "") or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in suggestions if isinstance(item, dict)]


def company_metadata_value(field, value):
    if field == "industry":
        return normalize_company_industry(value)
    if field == "company_size":
        return normalize_company_size(value)
    if field in {"company_profile_url", "website"}:
        if field == "company_profile_url":
            return normalize_company_profile_url(value)
        return normalize_url(str(value or ""))
    return storage.clean(str(value or ""))


def company_metadata_suggestion(field, current, suggested, source_url, observed_at):
    identity = hashlib.sha256(
        f"{field}|{suggested}|{source_url}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "id": identity,
        "field": field,
        "current": current,
        "suggested": suggested,
        "source_url": source_url,
        "reason": "Hunter found a different source-backed value.",
        "observed_at": observed_at,
    }


def update_company_metadata(company_id, metadata=None, source_url="", checked_at=""):
    rows = repository.read_companies()
    wanted = storage.clean(company_id).upper()
    row = next((item for item in rows if item.get("id", "").upper() == wanted), None)
    if row is None:
        raise ValueError(f"No company found with id {company_id}.")
    original = dict(row)

    incoming = metadata or {}
    normalized = {
        "industry": company_metadata_value(
            "industry",
            incoming.get("company_industry") or incoming.get("industry"),
        ),
        "company_size": company_metadata_value("company_size", incoming.get("company_size")),
        "company_profile_url": company_metadata_value(
            "company_profile_url",
            incoming.get("company_profile_url", ""),
        ),
        "website": company_metadata_value("website", incoming.get("website", "")),
    }
    observed_at = storage.clean(checked_at) or now_iso()
    cleaned_source = storage.clean(source_url)
    normalized_source = "manual" if cleaned_source == "manual" else normalize_url(cleaned_source)
    suggestions = company_metadata_suggestions(row)
    suggestions_by_field = {item.get("field", ""): item for item in suggestions}
    changed = False
    for field, value in normalized.items():
        current = company_metadata_value(field, row.get(field, ""))
        if not value or value == current:
            continue
        if not current:
            row[field] = value
            changed = True
            suggestions_by_field.pop(field, None)
            continue
        suggestions_by_field[field] = company_metadata_suggestion(
            field,
            current,
            value,
            normalized_source,
            observed_at,
        )
    new_suggestions = list(suggestions_by_field.values())
    suggestions_changed = new_suggestions != suggestions
    if changed or suggestions_changed:
        if changed:
            row["company_metadata_source"] = normalized_source
            row["company_metadata_checked_at"] = observed_at
        row["company_metadata_suggestions_json"] = json.dumps(
            new_suggestions,
            ensure_ascii=False,
            sort_keys=True,
        )
        repository.update_company_fields(
            row.get("id", ""),
            {
                field: row.get(field, "")
                for field in schema.COMPANY_FIELDS
                if field != "id" and row.get(field, "") != original.get(field, "")
            },
        )
    return get_company(row.get("id", ""))


@lru_cache(maxsize=16_384)
def company_domain(value):
    host = urlparse(normalize_url(value)).netloc.lower().removeprefix("www.")
    return host


SHARED_JOB_BOARD_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.ashbyhq.com": "ashby",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever",
    "jobs.smartrecruiters.com": "smartrecruiters",
}


def compact_company_identity(value):
    ascii_value = unicodedata.normalize("NFKD", storage.clean(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_value.lower())


def shared_job_board_identity(value):
    """Return the ATS family and employer tenant encoded in a shared job-board URL."""
    parsed = urlparse(normalize_url(value))
    family = SHARED_JOB_BOARD_HOSTS.get(parsed.netloc.lower().removeprefix("www."), "")
    parts = [part for part in parsed.path.split("/") if part]
    if not family or not parts:
        return None
    return family, parts[0].lower()


def job_board_tenant_keys(value):
    cleaned = storage.clean(value).lower()
    keys = {compact_company_identity(cleaned)}
    if "." in cleaned:
        stem, suffix = cleaned.rsplit(".", 1)
        if suffix in {"ai", "com", "io", "tech"}:
            keys.add(compact_company_identity(stem))
    return {key for key in keys if key}


def shared_job_board_identities_match(left, right):
    return bool(
        left
        and right
        and left[0] == right[0]
        and job_board_tenant_keys(left[1]) & job_board_tenant_keys(right[1])
    )


def company_matches_shared_job_board_identity(company, identity):
    if not identity:
        return False
    for field in ["website", "careers_url"]:
        known_identity = shared_job_board_identity(company.get(field, ""))
        if shared_job_board_identities_match(identity, known_identity):
            return True
    tenant_keys = job_board_tenant_keys(identity[1])
    names = [company.get("name", ""), *split_aliases(company.get("aliases", ""))]
    return any(compact_company_identity(name) in tenant_keys for name in names if storage.clean(name))


def company_shared_job_board_url_conflicts(company, value):
    """Identify a definite mismatch between a company and a shared ATS tenant."""
    identity = shared_job_board_identity(value)
    if not identity:
        return False
    known_identities = [
        known_identity
        for field in ["website", "careers_url"]
        if (known_identity := shared_job_board_identity(company.get(field, "")))
    ]
    return bool(
        known_identities
        and not any(shared_job_board_identities_match(identity, known) for known in known_identities)
    )


def matching_company_record(name="", profile_url="", website=""):
    wanted_name = normalized_key(name)
    wanted_profile = normalize_company_profile_url(profile_url)
    wanted_domain = company_domain(website)
    for company in repository.read_companies():
        if wanted_name and wanted_name in company_keys(company):
            return company
        if (
            wanted_profile
            and normalize_company_profile_url(company.get("company_profile_url", "")) == wanted_profile
        ):
            return company
        if wanted_domain and company_domain(company.get("website", "")) == wanted_domain:
            return company
    return None


def matching_company_record_from_url(value):
    """Resolve a known company from an employer or ATS posting hostname."""
    host = company_domain(value)
    if not host:
        return None

    rows = repository.read_companies()
    shared_identity = shared_job_board_identity(value)
    if shared_identity:
        for company in rows:
            if company_matches_shared_job_board_identity(company, shared_identity):
                return company
        # A shared ATS hostname identifies the vendor, not the employer.
        return None

    for company in rows:
        for field in ["website", "careers_url"]:
            known_host = company_domain(company.get(field, ""))
            if known_host and (
                host == known_host
                or host.endswith(f".{known_host}")
                or known_host.endswith(f".{host}")
            ):
                return company

    generic_labels = {
        "apply",
        "boards",
        "career",
        "careers",
        "com",
        "en",
        "external",
        "global",
        "io",
        "job",
        "jobs",
        "net",
        "org",
        "recruiting",
        "talent",
        "us",
        "www",
    }
    host_labels = {
        re.sub(r"[^a-z0-9]+", "", label.lower())
        for label in host.split(".")
        if label.lower() not in generic_labels
    }
    host_labels.discard("")
    for company in rows:
        names = [company.get("name", ""), *split_aliases(company.get("aliases", ""))]
        identity_keys = {
            compact
            for name in names
            for compact in [
                re.sub(r"[^a-z0-9]+", "", normalized_key(name)),
                company_merge_key(name),
            ]
            if compact
        }
        if identity_keys & host_labels:
            return company
    return None


def company_merge_key(name):
    normalized = storage.clean(name).lower()
    normalized = re.sub(
        r"\b(?:incorporated|inc|llc|ltd|limited|corporation|corp|company|co)\.?\b",
        " ",
        normalized,
    )
    return re.sub(r"[^a-z0-9]+", "", normalized)


def company_merge_suggestions():
    groups = {}
    for company in repository.read_companies():
        key = company_merge_key(company.get("name", ""))
        if not key:
            continue
        groups.setdefault(key, []).append(company)
    suggestions = []
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
        for duplicate in ordered[1:]:
            suggestions.append(
                {
                    "id": f"{keep.get('id', '')}:{duplicate.get('id', '')}",
                    "keep_company_id": keep.get("id", ""),
                    "keep_company_name": keep.get("name", ""),
                    "merge_company_id": duplicate.get("id", ""),
                    "merge_company_name": duplicate.get("name", ""),
                    "reason": "These company names differ only by a legal suffix or punctuation.",
                    "match_key": key,
                }
            )
    return suggestions


def merge_companies(keep_company_id, merge_company_id):
    keep_id = storage.clean(keep_company_id).upper()
    merge_id = storage.clean(merge_company_id).upper()
    if not keep_id or not merge_id or keep_id == merge_id:
        raise ValueError("Choose two different company records to merge.")
    rows = repository.read_companies()
    keep = next((row for row in rows if row.get("id", "").upper() == keep_id), None)
    duplicate = next((row for row in rows if row.get("id", "").upper() == merge_id), None)
    if keep is None or duplicate is None:
        raise ValueError("One of the company records no longer exists.")

    aliases = [
        *split_aliases(keep.get("aliases", "")),
        duplicate.get("name", ""),
        *split_aliases(duplicate.get("aliases", "")),
    ]
    keep_name_key = normalized_key(keep.get("name", ""))
    keep["aliases"] = normalize_aliases(
        ", ".join(
            value
            for value in aliases
            if value and normalized_key(value) != keep_name_key
        )
    )
    for field in [
        "website",
        "careers_url",
        "industry",
        "company_size",
        "company_profile_url",
        "company_metadata_source",
        "company_metadata_checked_at",
        "company_metadata_suggestions_json",
        "company_research_status",
        "company_discovery_source",
        "company_discovery_source_url",
        "company_discovery_query",
        "company_discovery_evidence",
        "company_location_fit",
        "company_location",
        "company_remote_policy",
        "company_location_evidence",
        "company_location_checked_at",
        "company_fit_score",
        "company_fit_summary",
        "company_fit_checked_at",
        "last_checked_at",
        "last_check_status",
    ]:
        if not keep.get(field) and duplicate.get(field):
            keep[field] = duplicate[field]
    keep["tracking_status"] = (
        "tracked"
        if "tracked" in {keep.get("tracking_status"), duplicate.get("tracking_status")}
        else "discovered"
    )
    merged_interest_statuses = {
        keep.get("interest_status"),
        duplicate.get("interest_status"),
    }
    if "interested" in merged_interest_statuses:
        keep["interest_status"] = "interested"
    elif "not-interested" in merged_interest_statuses:
        keep["interest_status"] = "not-interested"
    keep["discovered_at"] = min(
        [value for value in [keep.get("discovered_at", ""), duplicate.get("discovered_at", "")] if value],
        default="",
    )
    keep["last_seen_at"] = max(keep.get("last_seen_at", ""), duplicate.get("last_seen_at", ""))
    notes = [
        storage.clean(keep.get("notes", "")),
        storage.clean(duplicate.get("notes", "")),
    ]
    keep["notes"] = "\n".join(dict.fromkeys(value for value in notes if value))

    return repository.merge_companies_atomic(keep, merge_id)


def record_discovered_company(metadata, seen_at=""):
    name = storage.clean(metadata.get("company", "") or metadata.get("name", ""))
    if not name:
        return None
    timestamp = storage.clean(seen_at) or now_iso()
    company = matching_company_record(
        name,
        metadata.get("company_profile_url", ""),
        metadata.get("website", ""),
    )
    if company is None:
        company = upsert_company(
            "",
            {
                "name": name,
                "tracking_status": "discovered",
            },
        )

    rows = repository.read_companies()
    row = next(item for item in rows if item.get("id", "") == company.get("id", ""))
    row["discovered_at"] = row.get("discovered_at", "") or timestamp
    row["last_seen_at"] = max(row.get("last_seen_at", ""), timestamp)
    repository.update_company_fields(
        row.get("id", ""),
        {
            "discovered_at": row.get("discovered_at", ""),
            "last_seen_at": row.get("last_seen_at", ""),
        },
    )
    return update_company_metadata(
        company.get("id", ""),
        metadata,
        source_url=metadata.get("company_metadata_source")
        or metadata.get("canonical_url")
        or metadata.get("url", ""),
        checked_at=timestamp,
    )


def track_company(company_id):
    return upsert_company(company_id, {"tracking_status": "tracked"})


def untrack_company(company_id):
    return upsert_company(company_id, {"tracking_status": "discovered"})


def set_company_research_status(company_id, status, checked_at=""):
    rows = repository.read_companies()
    wanted = storage.clean(company_id).upper()
    row = next((item for item in rows if item.get("id", "").upper() == wanted), None)
    if row is None:
        raise ValueError(f"No company found with id {company_id}.")
    row["company_research_status"] = storage.clean(status)
    if storage.clean(checked_at):
        row["company_metadata_checked_at"] = storage.clean(checked_at)
    repository.update_company_fields(
        row.get("id", ""),
        {
            "company_research_status": row.get("company_research_status", ""),
            **(
                {"company_metadata_checked_at": row.get("company_metadata_checked_at", "")}
                if storage.clean(checked_at)
                else {}
            ),
        },
    )
    return get_company(row.get("id", ""))


def research_company_with_openai(company_id, evaluator=None):
    from . import company_evaluation

    company = get_company(company_id)
    before = dict(company)
    before_suggestion_ids = {
        item.get("id", "")
        for item in company_metadata_suggestions(company)
    }
    result = company_evaluation.evaluate_companies(
        company_ids=[company.get("id", "")],
        tracking_status="",
        force=True,
        evaluator=evaluator,
        reason="manual-research",
        max_attempts=1,
    )
    if result.get("failed_count") or not result.get("evaluated_count"):
        message = "; ".join(result.get("errors", [])) or "No company research result was returned."
        set_company_research_status(company_id, f"error: {message}", checked_at=now_iso())
        raise RuntimeError(message)
    updated = get_company(company_id)
    fields = [
        "industry",
        "company_size",
        "company_profile_url",
        "website",
        "careers_url",
        "company_location_fit",
        "company_location",
        "company_remote_policy",
        "company_fit_score",
    ]
    applied_fields = [
        field
        for field in fields
        if updated.get(field, "") and updated.get(field, "") != before.get(field, "")
    ]
    suggestions = company_metadata_suggestions(updated)
    new_suggestions = [
        item
        for item in suggestions
        if item.get("id", "") not in before_suggestion_ids
    ]
    status = (
        f"ok: filled {len(applied_fields)}, suggested {len(new_suggestions)}"
        if applied_fields or new_suggestions
        else "ok: no new company information"
    )
    updated = set_company_research_status(company_id, status, checked_at=now_iso())
    return {
        "company": updated,
        "applied_fields": applied_fields,
        "suggestions": new_suggestions,
        "source_url": storage.clean(updated.get("company_metadata_source", "")),
        "provider": "openai",
        "run_id": result.get("run_id", ""),
        "evaluation_status": updated.get("company_evaluation_status", ""),
    }


def research_company(company_id):
    return research_company_with_openai(company_id)


def resolve_company_metadata_suggestion(company_id, suggestion_id, action):
    rows = repository.read_companies()
    wanted = storage.clean(company_id).upper()
    row = next((item for item in rows if item.get("id", "").upper() == wanted), None)
    if row is None:
        raise ValueError(f"No company found with id {company_id}.")
    suggestions = company_metadata_suggestions(row)
    suggestion = next(
        (item for item in suggestions if item.get("id", "") == storage.clean(suggestion_id)),
        None,
    )
    if suggestion is None:
        raise ValueError("Company metadata suggestion was not found.")
    normalized_action = storage.clean(action).lower()
    if normalized_action not in {"apply", "dismiss"}:
        raise ValueError("Suggestion action must be apply or dismiss.")
    if normalized_action == "apply":
        field = suggestion.get("field", "")
        if field not in {"industry", "company_size", "company_profile_url", "website"}:
            raise ValueError(f"Unsupported company metadata field: {field}")
        row[field] = company_metadata_value(field, suggestion.get("suggested", ""))
        row["company_metadata_source"] = storage.clean(suggestion.get("source_url", ""))
        row["company_metadata_checked_at"] = now_iso()
    row["company_metadata_suggestions_json"] = json.dumps(
        [item for item in suggestions if item.get("id", "") != suggestion.get("id", "")],
        ensure_ascii=False,
        sort_keys=True,
    )
    repository.update_company_fields(
        row.get("id", ""),
        {
            field: row.get(field, "")
            for field in [
                "industry",
                "company_size",
                "company_profile_url",
                "website",
                "company_metadata_source",
                "company_metadata_checked_at",
                "company_metadata_suggestions_json",
            ]
        },
    )
    return get_company(row.get("id", ""))


def archive_company(company_id):
    return upsert_company(company_id, {"interest_status": "archived"})


def restore_company(company_id, interest_status="neutral"):
    status = validate_interest_status(interest_status)
    if status == "archived":
        raise ValueError("Restore status must be interested or neutral.")
    return upsert_company(company_id, {"interest_status": status})


def associate_matching_postings(company_id):
    company = get_company(company_id)
    keys = company_keys(company)
    if not keys:
        return []
    associated = []
    for app in repository.read_applications():
        app_id = app.get("id", "")
        if app.get("company_id", "").upper() == company.get("id", "").upper():
            associated.append(applications.update_application(app_id, {"company_id": company["id"]}))
            continue
        if app.get("company_id"):
            continue
        if normalized_key(app.get("company", "")) in keys:
            associated.append(applications.update_application(app_id, {"company_id": company["id"]}))
    return associated


def associate_application(company_id, application_id):
    company = get_company(company_id)
    app = applications.update_application(application_id, {"company_id": company.get("id", "")})
    return {"company": company, "posting": app}


def link_contact(company_id, contact_id):
    company_id = storage.clean(company_id).upper()
    contact_id = storage.clean(contact_id).upper()
    get_company(company_id)
    if not any(contact.get("id", "").upper() == contact_id for contact in repository.read_contacts()):
        raise ValueError(f"No contact found with id {contact_id}.")
    return repository.link_company_contact(company_id, contact_id)


def unlink_contact(company_id, contact_id):
    return repository.unlink_company_contact(company_id, contact_id)




def job_board_family(host):
    host = host.lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "jobs.apple.com" in host:
        return "apple"
    if "lever.co" in host:
        return "lever"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "workdayjobs.com" in host:
        return "workday"
    if "icims.com" in host:
        return "icims"
    if "careers.microsoft.com" in host:
        return "microsoft"
    return re.sub(r"^www\.", "", host)


@lru_cache(maxsize=16_384)
def _posting_identity_keys_cached(normalized):
    parsed = urlparse(normalized)
    family = job_board_family(parsed.netloc)
    keys = {f"url:{normalized}"}
    path_parts = [part for part in parsed.path.split("/") if part]

    query = parse_qsl(parsed.query, keep_blank_values=False)
    for key, value in query:
        query_key = key.lower()
        cleaned_value = storage.clean(value).lower()
        if query_key in JOB_ID_QUERY_KEYS and cleaned_value:
            keys.add(f"query:{family}:{query_key}:{cleaned_value}")
            if family == "greenhouse" and query_key == "gh_jid":
                keys.add(f"greenhouse:{cleaned_value}")

    if family == "greenhouse":
        for index, part in enumerate(path_parts):
            if part == "jobs" and index + 1 < len(path_parts):
                keys.add(f"greenhouse:{path_parts[index + 1].lower()}")
    elif family == "apple":
        for index, part in enumerate(path_parts):
            if part == "details" and index + 1 < len(path_parts):
                keys.add(f"apple:{path_parts[index + 1].lower()}")
    elif family == "microsoft":
        for index, part in enumerate(path_parts):
            if part == "job" and index + 1 < len(path_parts):
                keys.add(f"microsoft:{path_parts[index + 1].lower()}")
        for key, value in query:
            if key.lower() in {"jobid", "displayjobid", "atsjobid"} and storage.clean(value):
                keys.add(f"microsoft:{storage.clean(value).lower()}")
    elif family == "smartrecruiters" and len(path_parts) >= 2:
        cleaned_job_part = re.sub(r"[^a-z0-9-]", "", path_parts[1].lower())
        if cleaned_job_part:
            keys.add(f"path:{family}:{cleaned_job_part}")
            job_id_match = NUMERIC_JOB_SLUG_PATTERN.match(cleaned_job_part)
            if job_id_match:
                keys.add(f"smartrecruiters:{job_id_match.group(1)}")
                keys.add(f"external-job-id:{job_id_match.group(1)}")

    jobish_path = any(part in {"job", "jobs", "details", "req", "requisition"} for part in path_parts)
    for index, part in enumerate(path_parts):
        if family == "smartrecruiters" and index == 0:
            continue
        cleaned_part = re.sub(r"[^a-z0-9-]", "", part.lower())
        job_id_match = NUMERIC_JOB_SLUG_PATTERN.match(cleaned_part)
        if job_id_match:
            keys.add(f"external-job-id:{job_id_match.group(1)}")
        if (
            re.fullmatch(r"\d{5,}", cleaned_part)
            or (jobish_path and re.fullmatch(r"\d{3,}", cleaned_part))
            or (len(cleaned_part) >= 8 and any(char.isdigit() for char in cleaned_part))
        ):
            keys.add(f"path:{family}:{cleaned_part}")
            if jobish_path:
                keys.add(f"job-id:{cleaned_part}")
    return frozenset(keys)


def posting_identity_keys(url):
    normalized = normalize_url(url)
    if not normalized:
        return set()
    # Keep the public return type mutable without exposing cached shared state.
    return set(_posting_identity_keys_cached(normalized))


@lru_cache(maxsize=16_384)
def _normalized_requisition_ids_cached(normalized):
    parsed = urlparse(normalized)
    values = {
        storage.clean(value).lower()
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower() in JOB_ID_QUERY_KEYS and storage.clean(value)
    }
    for key in _posting_identity_keys_cached(normalized):
        if key.startswith(("greenhouse:", "apple:", "microsoft:", "smartrecruiters:")):
            values.add(key.rsplit(":", 1)[-1])
    return frozenset(values)


def normalized_requisition_ids(url):
    """Return provider-independent requisition identifiers from a posting URL."""
    normalized = normalize_url(url)
    if not normalized:
        return set()
    return set(_normalized_requisition_ids_cached(normalized))


def normalized_source_requisition_ids(source_job_id):
    """Return stable requisition identifiers from a structured source job id."""
    value = storage.clean(source_job_id).lower()
    if not value:
        return set()
    for prefix in ["greenhouse:", "apple:", "microsoft:", "smartrecruiters:", "external-job-id:", "job-id:"]:
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if value.startswith("query:"):
        value = value.rsplit(":", 1)[-1]
    if value.startswith("path:"):
        value = value.rsplit(":", 1)[-1]
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value):
        return {value}
    if re.fullmatch(r"[a-z]*\d{3,}(?:-\d+)?", value):
        return {value}
    trailing = re.search(r"(?:^|[-_/])((?:r|req[-_]?)?\d{4,}(?:-\d+)?)$", value)
    return {trailing.group(1)} if trailing else set()


def candidate_requisition_ids(candidate):
    return normalized_requisition_ids(candidate.get("canonical_url") or candidate.get("url", "")) | normalized_source_requisition_ids(
        candidate.get("source_job_id", "")
    )


def candidate_identity_keys(candidate):
    url = candidate.get("canonical_url") or candidate.get("url", "")
    return posting_identity_keys(url) | {
        f"requisition:{requisition_id}"
        for requisition_id in candidate_requisition_ids(candidate)
    }


def posting_identity_match(left_url, right_url):
    """Compare requisitions before considering lower-confidence URL identity keys."""
    left_requisitions = normalized_requisition_ids(left_url)
    right_requisitions = normalized_requisition_ids(right_url)
    if left_requisitions and right_requisitions:
        return bool(left_requisitions & right_requisitions)
    return bool(posting_identity_keys(left_url) & posting_identity_keys(right_url))


def application_matches_company(app, company):
    company_id = company.get("id", "").upper()
    if app.get("company_id", "").upper() == company_id:
        return True
    return normalized_key(app.get("company", "")) in company_keys(company)


def tracked_posting_context(company, application_rows=None):
    postings = []
    for app in application_rows if application_rows is not None else repository.read_applications():
        if not application_matches_company(app, company):
            continue
        source_url = app.get("source_url", "")
        postings.append(
            {
                "id": app.get("id", ""),
                "url": source_url,
                "identity_keys": posting_identity_keys(source_url),
                "requisition_ids": normalized_requisition_ids(source_url),
                "title_key": normalized_key(app.get("role", "")),
            }
        )
    return {
        "postings": postings,
        # Retained for callers that use the context for display or diagnostics.
        "url_keys": set().union(*(posting["identity_keys"] for posting in postings)) if postings else set(),
        "title_keys": {posting["title_key"] for posting in postings if posting["title_key"]},
    }


def candidate_is_tracked(item, tracked):
    candidate_url = item.get("url", "")
    candidate_requisitions = candidate_requisition_ids(item)
    candidate_keys = candidate_identity_keys(item)
    title_key = normalized_key(item.get("title", ""))
    postings = tracked.get("postings")
    if postings is None:
        if candidate_keys & tracked.get("url_keys", set()):
            return True
        return bool(title_key and title_key in tracked.get("title_keys", set()))
    for posting in postings:
        posting_requisitions = posting.get("requisition_ids", set())
        if candidate_requisitions and posting_requisitions:
            if candidate_requisitions & posting_requisitions:
                return True
            # Distinct explicit requisitions override shared career URLs and titles.
            continue
        if candidate_keys & posting.get("identity_keys", set()):
            return True
        if title_key and title_key == posting.get("title_key", ""):
            return True
    return False


def matching_tracked_posting_ids(item, company=None, tracked=None):
    company = company or get_company(item.get("company_id", ""))
    context = tracked or tracked_posting_context(company)
    candidate_url = item.get("canonical_url") or item.get("url", "")
    candidate_requisitions = candidate_requisition_ids(item)
    candidate_keys = candidate_identity_keys(item)
    title_key = normalized_key(item.get("title", ""))
    matches = []
    for posting in context["postings"]:
        posting_requisitions = posting["requisition_ids"]
        if candidate_requisitions and posting_requisitions:
            matched = bool(candidate_requisitions & posting_requisitions)
        else:
            matched = bool(candidate_keys & posting["identity_keys"])
            if not matched and title_key:
                matched = title_key == posting["title_key"]
        if matched:
            matches.append(posting["id"])
    return matches


def candidate_source_urls(item):
    """Return the stored URLs needed to compare candidates across review pools."""
    values = []
    for field in ("canonical_url", "url"):
        value = storage.clean(item.get(field, ""))
        if value and value not in values:
            values.append(value)
    try:
        stored_values = json.loads(item.get("source_urls_json", "") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        stored_values = []
    for value in stored_values if isinstance(stored_values, list) else []:
        value = storage.clean(value)
        if value and value not in values:
            values.append(value)
    return values


def cross_pool_candidates_match(company_candidate, discovery_candidate):
    """Compare a company and Discovery candidate without treating status as identity."""
    company_id = storage.clean(company_candidate.get("company_id", "")).upper()
    discovery_company_id = storage.clean(discovery_candidate.get("company_id", "")).upper()
    if not company_id or company_id != discovery_company_id:
        return False
    company_urls = candidate_source_urls(company_candidate)
    discovery_urls = candidate_source_urls(discovery_candidate)
    company_requisitions = set().union(
        *(normalized_requisition_ids(url) for url in company_urls)
    ) if company_urls else set()
    company_requisitions.update(normalized_source_requisition_ids(company_candidate.get("source_job_id", "")))
    discovery_requisitions = set().union(
        *(normalized_requisition_ids(url) for url in discovery_urls)
    ) if discovery_urls else set()
    if company_requisitions and discovery_requisitions:
        return bool(company_requisitions & discovery_requisitions)
    company_keys = set().union(
        *(posting_identity_keys(url) for url in company_urls)
    ) if company_urls else set()
    discovery_keys = set().union(
        *(posting_identity_keys(url) for url in discovery_urls)
    ) if discovery_urls else set()
    return bool(company_keys & discovery_keys)


def matching_discovery_candidates(company_candidate, discovery_candidates):
    return [
        candidate
        for candidate in discovery_candidates
        if cross_pool_candidates_match(company_candidate, candidate)
    ]


def host_allowed(url, base_url):
    host = urlparse(url).netloc.lower()
    base_host = urlparse(base_url).netloc.lower()
    if not host or not base_host:
        return False
    if host == base_host or host.endswith(f".{base_host}"):
        return True
    return any(marker in host for marker in JOB_BOARD_HOST_MARKERS)


def html_base_url(page_html, fallback_url):
    match = re.search(r"<base\b[^>]*href\s*=\s*(['\"])(.*?)\1", page_html or "", re.I | re.S)
    if not match:
        return fallback_url
    href = html.unescape(match.group(2)).strip()
    return urljoin(fallback_url, href) if href else fallback_url


def clean_link_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return storage.clean(html.unescape(value))




def split_list_text(value):
    values = []
    raw_values = value if isinstance(value, list) else re.split(r"[,;]", str(value or ""))
    for raw in raw_values:
        cleaned = storage.clean(str(raw or ""))
        if cleaned and cleaned.lower() not in {item.lower() for item in values}:
            values.append(cleaned)
    return values


def posting_work_mode(location, description, current=""):
    current = storage.clean(current)
    if current:
        return current
    text = storage.clean(f"{location} {description}").lower()
    remote_patterns = [
        r"\bremotely\s+in\s+(?:the\s+)?united states\b",
        r"\bremote(?:ly)?\s+(?:from|within|in)\s+(?:the\s+)?u\.?s\.?(?:a\.)?\b",
        r"\b(?:role|position|job)\s+(?:is|may be|can be)\s+(?:held\s+)?remote\b",
        r"\bremote[- ]first\b",
        r"\bfully remote\b",
    ]
    if any(re.search(pattern, text, re.I) for pattern in remote_patterns):
        return "Remote"
    if re.search(r"\bhybrid\b", text, re.I):
        return "Hybrid"
    if re.search(r"\b(?:on[- ]site|onsite)\b", text, re.I):
        return "On-site"
    return ""


def candidate_source_job_id(url):
    keys = posting_identity_keys(url)
    preferred_prefixes = [
        "greenhouse:",
        "apple:",
        "microsoft:",
        "smartrecruiters:",
        "external-job-id:",
        "job-id:",
        "query:",
        "path:",
    ]
    for prefix in preferred_prefixes:
        matches = sorted(key for key in keys if key.startswith(prefix))
        if matches:
            return matches[0]
    return ""


def candidate_score_context():
    return {
        "fit_signals": settings_store.read_fit_signals(),
        "fit_context_hash": hashlib.sha256(fit_context_text().encode("utf-8")).hexdigest(),
    }


def candidate_score_inputs_hash(candidate, score_context=None):
    payload = {
        "candidate_text": candidate_search_text(candidate),
        "exclusion_text": candidate_exclusion_text(candidate),
        **(score_context or candidate_score_context()),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalized_candidate(item, platform_type, score_context=None):
    candidate = dict(item or {})
    candidate["title"] = storage.clean(str(candidate.get("title", "") or ""))
    candidate["url"] = normalize_url(candidate.get("url", ""))
    location = storage.clean(str(candidate.get("location", "") or ""))
    work_mode = storage.clean(str(candidate.get("work_mode", "") or ""))
    mode_match = re.search(r"(?:,|;)\s*(on-?site|onsite|hybrid|remote_local|remote)\s*$", location, re.I)
    if mode_match:
        if not work_mode:
            work_mode = mode_match.group(1)
        location = storage.clean(location[: mode_match.start()].rstrip(",; "))
    work_mode_aliases = {
        "onsite": "On-site",
        "on-site": "On-site",
        "hybrid": "Hybrid",
        "remote": "Remote",
        "remote_local": "Remote (local)",
    }
    candidate["location"] = location
    candidate["work_mode"] = work_mode_aliases.get(work_mode.lower(), work_mode)
    candidate["category"] = storage.clean(
        str(candidate.get("category", "") or candidate.get("categories", "") or "")
    )
    candidate["source_platform"] = storage.clean(platform_type)
    candidate["source_job_id"] = storage.clean(
        str(candidate.get("source_job_id", "") or candidate_source_job_id(candidate["url"]))
    )
    candidate["matched_queries"] = ", ".join(split_list_text(candidate.get("matched_queries", "")))
    description = clean_html_text(str(candidate.get("description", "") or ""))
    candidate["description"] = description
    candidate["work_mode"] = posting_work_mode(location, description, candidate.get("work_mode", ""))
    candidate["description_excerpt"] = description[:1000]
    candidate["description_hash"] = hashlib.sha256(description.encode("utf-8")).hexdigest() if description else ""
    warnings = []
    if not location:
        warnings.append("missing-location")
    if "{" in location or "}" in location:
        warnings.append("structured-location-text")
    if len(location) > 200:
        warnings.append("long-location")
    if normalized_key(candidate["title"]) in {"career", "careers", "job alert", "create a job alert"}:
        warnings.append("navigation-title")
    candidate["normalization_warnings"] = ", ".join(warnings)
    candidate["score_inputs_hash"] = candidate_score_inputs_hash(candidate, score_context)
    return candidate


def candidate_identity_key(candidate):
    requisition_ids = sorted(candidate_requisition_ids(candidate))
    if requisition_ids:
        return f"requisition:{requisition_ids[0]}"
    identity_keys = sorted(
        key for key in posting_identity_keys(candidate.get("url", "")) if not key.startswith("url:")
    )
    return identity_keys[0] if identity_keys else f"url:{normalize_url(candidate.get('url', ''))}"


def merge_candidate_values(existing, candidate):
    for field in [
        "title",
        "url",
        "location",
        "work_mode",
        "category",
        "source_platform",
        "source_job_id",
        "description",
        "description_excerpt",
        "description_hash",
        "score_inputs_hash",
        "normalization_warnings",
    ]:
        if not existing.get(field) and candidate.get(field):
            existing[field] = candidate[field]
    queries = split_list_text([*split_list_text(existing.get("matched_queries", "")), *split_list_text(candidate.get("matched_queries", ""))])
    existing["matched_queries"] = ", ".join(queries)
    return existing


def normalize_extracted_candidates(rows, platform_type):
    candidates = []
    by_identity = {}
    score_context = candidate_score_context()
    for row in rows:
        candidate = normalized_candidate(row, platform_type, score_context)
        if (
            not candidate.get("title")
            or not candidate.get("url")
            or "navigation-title" in candidate.get("normalization_warnings", "")
        ):
            continue
        identity = candidate_identity_key(candidate)
        if identity in by_identity:
            merge_candidate_values(by_identity[identity], candidate)
            continue
        by_identity[identity] = candidate
        candidates.append(candidate)
    return candidates


def title_from_url(url):
    path = urlparse(url).path.rstrip("/")
    piece = path.split("/")[-1] if path else ""
    piece = re.sub(r"\?.*$", "", piece)
    piece = re.sub(r"[-_]+", " ", piece).strip()
    return re.sub(r"\b\w", lambda match: match.group(0).upper(), piece)


def clean_candidate_title(title, url):
    parsed = urlparse(url)
    cleaned = storage.clean(title)
    if parsed.netloc.lower() == GOOGLE_CAREERS_HOST and parsed.path.startswith(GOOGLE_CAREERS_RESULTS_PATH):
        cleaned = re.sub(r"^\d{8,}\s+", "", cleaned)
    return cleaned


def candidate_location_near_link(page_html, match_start, match_end):
    article_start = page_html.rfind("<article", 0, match_start)
    article_end = page_html.find("</article>", match_end)
    if article_start < 0 or article_end < 0:
        return ""
    article = page_html[article_start : article_end + len("</article>")]
    values = []
    for location_html in re.findall(
        r"<(?:span|div|p)\b[^>]*class\s*=\s*(['\"])[^'\"]*location[^'\"]*\1[^>]*>(.*?)</(?:span|div|p)>",
        article,
        re.I | re.S,
    ):
        value = clean_link_text(location_html[1])
        if value and value.lower() not in {item.lower() for item in values}:
            values.append(value)
    return "; ".join(values)


def extract_google_careers_candidates(page_html, base_url):
    card_matches = list(
        re.finditer(
            r"<li\b[^>]*class\s*=\s*(['\"])[^'\"]*\blLd3Je\b[^'\"]*\1[^>]*>",
            page_html or "",
            re.I | re.S,
        )
    )
    candidates = []
    seen = set()
    link_base_url = html_base_url(page_html, base_url)
    for index, card_match in enumerate(card_matches):
        card_end = card_matches[index + 1].start() if index + 1 < len(card_matches) else len(page_html or "")
        card = (page_html or "")[card_match.start() : card_end]
        link_match = re.search(
            r"<a\b[^>]*href\s*=\s*(['\"])([^'\"]*jobs/results/\d{8,}[^'\"]*)\1",
            card,
            re.I | re.S,
        )
        title_match = re.search(
            r"<h3\b[^>]*class\s*=\s*(['\"])[^'\"]*\bQJPWVe\b[^'\"]*\1[^>]*>(.*?)</h3>",
            card,
            re.I | re.S,
        )
        if not link_match or not title_match:
            continue
        url = normalize_url(urljoin(link_base_url, html.unescape(link_match.group(2)).strip()))
        title = clean_candidate_title(clean_link_text(title_match.group(2)), url)
        if not url or not title or url in seen:
            continue
        seen.add(url)
        locations = []
        for location_match in re.finditer(
            r"<span\b[^>]*class\s*=\s*(['\"])[^'\"]*\br0wTof\b[^'\"]*\1[^>]*>(.*?)</span>",
            card,
            re.I | re.S,
        ):
            location = clean_link_text(location_match.group(2)).lstrip("; ")
            if location and location.lower() not in {value.lower() for value in locations}:
                locations.append(location)
        candidate = {"title": title, "url": url}
        if locations:
            candidate["location"] = "; ".join(locations)
        candidates.append(candidate)
    return candidates


def extract_candidate_links(page_html, base_url):
    candidates = extract_structured_job_candidates(page_html, base_url)
    seen = {candidate["url"] for candidate in candidates}
    link_base_url = html_base_url(page_html, base_url)
    for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", page_html or "", re.I | re.S):
        attrs = match.group(1)
        href_match = re.search(r"href\s*=\s*(['\"])(.*?)\1", attrs, re.I | re.S)
        if not href_match:
            continue
        href = html.unescape(href_match.group(2)).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = normalize_url(urljoin(link_base_url, href))
        if not host_allowed(url, base_url):
            continue
        title = clean_candidate_title(clean_link_text(match.group(2)) or title_from_url(url), url)
        if not looks_like_job_link(title, url, base_url):
            continue
        if url in seen or normalize_url(base_url) == url:
            continue
        seen.add(url)
        candidate = {"title": title, "url": url}
        location = candidate_location_near_link(page_html, match.start(), match.end())
        if location:
            candidate["location"] = location
        candidates.append(candidate)
    return candidates


def json_ld_objects(page_html):
    objects = []
    for raw in re.findall(
        r"<script[^>]+type\s*=\s*(['\"])application/ld\+json\1[^>]*>(.*?)</script>",
        page_html or "",
        re.I | re.S,
    ):
        try:
            value = json.loads(html.unescape(raw[1].strip()))
        except json.JSONDecodeError:
            continue
        pending = value if isinstance(value, list) else [value]
        for item in pending:
            if not isinstance(item, dict):
                continue
            objects.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                objects.extend(entry for entry in graph if isinstance(entry, dict))
    return objects


def structured_job_location(job):
    values = []
    location_type = storage.clean(str(job.get("jobLocationType", "") or ""))
    if location_type.upper() == "TELECOMMUTE":
        values.append("Remote")

    locations = job.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    for location in locations if isinstance(locations, list) else []:
        if not isinstance(location, dict):
            continue
        address = location.get("address") if isinstance(location.get("address"), dict) else location
        parts = []
        for field in ["addressLocality", "addressRegion", "addressCountry"]:
            value = address.get(field)
            if isinstance(value, dict):
                value = value.get("name")
            cleaned = storage.clean(str(value or ""))
            if cleaned and cleaned.lower() not in {part.lower() for part in parts}:
                parts.append(cleaned)
        if parts:
            values.append(", ".join(parts))

    requirements = job.get("applicantLocationRequirements") or []
    if isinstance(requirements, dict):
        requirements = [requirements]
    for requirement in requirements if isinstance(requirements, list) else []:
        value = requirement.get("name") if isinstance(requirement, dict) else requirement
        cleaned = storage.clean(str(value or ""))
        if cleaned:
            values.append(cleaned)

    return "; ".join(dict.fromkeys(value for value in values if value))


def extract_structured_job_candidates(page_html, base_url):
    candidates = []
    seen = set()
    for job in json_ld_objects(page_html):
        item_type = job.get("@type", "")
        types = item_type if isinstance(item_type, list) else [item_type]
        if "JobPosting" not in types:
            continue
        raw_url = job.get("url") or job.get("sameAs") or ""
        url = normalize_url(urljoin(base_url, storage.clean(str(raw_url))))
        title = storage.clean(str(job.get("title", "") or ""))
        if (
            not url
            or not title
            or not host_allowed(url, base_url)
            or not looks_like_job_link(title, url, base_url)
            or url in seen
        ):
            continue
        seen.add(url)
        candidates.append({"title": title, "url": url, "location": structured_job_location(job)})
    return candidates


def extract_href_values(page_html):
    values = []
    for match in re.finditer(r"<a\b([^>]*)>", page_html or "", re.I | re.S):
        href_match = re.search(r"href\s*=\s*(['\"])(.*?)\1", match.group(1), re.I | re.S)
        if not href_match:
            continue
        href = html.unescape(href_match.group(2)).strip()
        if href and not href.startswith(("mailto:", "tel:", "javascript:", "data:")):
            values.append(href)
    return values


def looks_like_job_link(title, url, base_url):
    parsed = urlparse(url)
    base = urlparse(base_url)
    if parsed.netloc.lower() == base.netloc.lower() and parsed.path.rstrip("/") == base.path.rstrip("/"):
        return False
    if NON_JOB_TITLE_PATTERN.search(title or "") or NON_JOB_URL_PATTERN.search(f"{parsed.path}?{parsed.query}"):
        return False
    if any(marker in parsed.netloc.lower() for marker in JOB_BOARD_HOST_MARKERS):
        return bool(JOB_URL_PATTERN.search(f"{parsed.path}?{parsed.query}"))
    return bool(JOB_LINK_PATTERN.search(title or "") or JOB_URL_PATTERN.search(f"{parsed.path}?{parsed.query}"))


def text_contains_phrase(text, phrase):
    phrase = normalized_text(phrase)
    if not phrase:
        return False
    return bool(re.search(rf"(^|\s){re.escape(phrase)}(\s|$)", text))


def phrase_match_variants(phrase):
    base = normalized_text(phrase)
    if not base:
        return []
    variants = [base]
    suffix_pairs = [
        (" manager", " management"),
        (" management", " manager"),
    ]
    for source, replacement in suffix_pairs:
        if base.endswith(source):
            variants.append(base[: -len(source)] + replacement)
    return list(dict.fromkeys(variant for variant in variants if variant))


def text_contains_phrase_variant(text, phrase):
    return any(text_contains_phrase(text, variant) for variant in phrase_match_variants(phrase))


def resume_supports_phrase(resume, phrase):
    if text_contains_phrase(resume, phrase):
        return True
    tokens = [token for token in normalized_text(phrase).split() if token]
    return len(tokens) > 1 and all(text_contains_phrase(resume, token) for token in tokens)


def resume_supports_phrase_variant(resume, phrase):
    return any(resume_supports_phrase(resume, variant) for variant in phrase_match_variants(phrase))


def candidate_role_fit_terms():
    rows = {}
    for phrase, weight in settings_store.role_fit_terms():
        key = normalized_text(phrase)
        if key:
            rows[key] = {"phrase": phrase, "weight": weight, "requires_resume_support": True}
    for phrase in settings_store.search_terms():
        key = normalized_text(phrase)
        if not key:
            continue
        existing = rows.get(key)
        rows[key] = {
            "phrase": existing.get("phrase", phrase) if existing else phrase,
            "weight": max(existing.get("weight", 0) if existing else 0, SEARCH_TERM_ROLE_WEIGHT),
            "requires_resume_support": False if not existing else existing.get("requires_resume_support", True) and False,
        }
    return list(rows.values())


def candidate_search_text(candidate):
    url = candidate.get("url", "")
    parsed = urlparse(url)
    url_text = " ".join(
        part
        for part in [
            parsed.netloc,
            parsed.path.replace("/", " "),
            parsed.query.replace("&", " ").replace("=", " "),
        ]
        if part
    )
    rich_text = " ".join(
        storage.clean(str(candidate.get(field, "")))
        for field in ["description", "location", "category", "categories", "search_text"]
        if candidate.get(field)
    )
    return normalized_text(f"{candidate.get('title', '')} {url_text} {rich_text}")


def candidate_role_text(candidate):
    url = candidate.get("url", "")
    parsed = urlparse(url)
    url_text = " ".join(
        part
        for part in [
            parsed.path.replace("/", " "),
            parsed.query.replace("&", " ").replace("=", " "),
        ]
        if part
    )
    return normalized_text(f"{candidate.get('title', '')} {url_text}")


def candidate_exclusion_text(candidate):
    return normalized_text(
        " ".join(
            storage.clean(str(candidate.get(field, "")))
            for field in ["title", "url", "category", "categories"]
            if candidate.get(field)
        )
    )


def fit_context_text():
    return settings_store.fit_context()


def score_candidate_fit(candidate, resume_text, checked_at):
    resume = normalized_text(resume_text)
    if not resume:
        return {"fit_score": "", "fit_summary": "", "fit_checked_at": ""}

    candidate_text = candidate_search_text(candidate)
    role_text = candidate_role_text(candidate)
    exclusion_text = candidate_exclusion_text(candidate)
    score = 0
    role_matches = []
    domain_matches = []
    seniority_matches = []

    matched_role_terms = []
    strong_role_match = False
    for item in candidate_role_fit_terms():
        phrase = item["phrase"]
        if item["requires_resume_support"] and not resume_supports_phrase_variant(resume, phrase):
            continue
        if text_contains_phrase_variant(role_text, phrase):
            matched_role_terms.append((phrase, item["weight"]))
    if matched_role_terms:
        phrase, weight = max(matched_role_terms, key=lambda item: item[1])
        score += weight
        role_matches.append(phrase)
        strong_role_match = weight >= 34

    domain_scores = []
    for phrase, weight in settings_store.domain_fit_terms():
        if not resume_supports_phrase(resume, phrase):
            continue
        if text_contains_phrase(role_text, phrase):
            domain_scores.append((phrase, weight))
        elif text_contains_phrase(candidate_text, phrase):
            domain_scores.append((phrase, max(1, round(weight * 0.45))))
    domain_scores = sorted(domain_scores, key=lambda item: item[1], reverse=True)[:3]
    score += min(30, sum(weight for _phrase, weight in domain_scores))
    domain_matches.extend(phrase for phrase, _weight in domain_scores)

    matched_seniority_terms = [
        (phrase, weight)
        for phrase, weight in settings_store.seniority_fit_terms()
        if resume_supports_phrase(resume, phrase) and text_contains_phrase(role_text, phrase)
    ]
    if matched_seniority_terms:
        phrase, weight = max(matched_seniority_terms, key=lambda item: item[1])
        score += weight
        seniority_matches.append(phrase)

    if role_matches and domain_matches:
        score += 6
    if strong_role_match:
        score = max(score, FIT_RECOMMENDATION_THRESHOLD)
    if any(text_contains_phrase(exclusion_text, phrase) for phrase in settings_store.role_exclusion_terms()):
        score = min(score, FIT_RECOMMENDATION_THRESHOLD - 1)
    if not role_matches and any(text_contains_phrase(exclusion_text, phrase) for phrase in settings_store.low_match_terms()):
        score -= 20
    if not role_matches:
        score = min(score, FIT_RECOMMENDATION_THRESHOLD - 1)

    score = max(0, min(100, score))
    matched = role_matches[:2] + domain_matches[:3] + seniority_matches[:1]
    if score >= 70:
        prefix = "Strong fit"
    elif score >= FIT_RECOMMENDATION_THRESHOLD:
        prefix = "Consider"
    elif score:
        prefix = "Possible fit"
    else:
        prefix = "Low fit"
    if matched:
        summary = f"{prefix}: matches " + ", ".join(matched) + "."
    else:
        summary = f"{prefix}: limited overlap with resume and search goals."
    return {"fit_score": str(score), "fit_summary": summary, "fit_checked_at": checked_at}


def annotate_candidate_fit(candidates, company_id, checked_at, only_missing=False):
    resume_text = fit_context_text()
    wanted = storage.clean(company_id).upper()
    for candidate in candidates:
        if candidate.get("company_id", "").upper() != wanted:
            continue
        if candidate.get("status", "new") != "new":
            continue
        if only_missing and candidate.get("fit_checked_at") == checked_at:
            continue
        candidate.update(score_candidate_fit(candidate, resume_text, checked_at))


def candidate_fit_score(candidate):
    try:
        return int(candidate.get("fit_score") or 0)
    except ValueError:
        return 0


def candidate_qualification(candidate, searches=None):
    """Classify a tracked-company candidate against the configured location lanes."""
    from . import discovery

    configured_searches = searches if searches is not None else discovery.list_searches()
    lanes = []
    seen_lane_ids = set()
    for search in configured_searches:
        for lane in search.get("lanes", []):
            lane_key = storage.clean(lane.get("id", "")) or json.dumps(lane, sort_keys=True)
            if lane_key in seen_lane_ids:
                continue
            seen_lane_ids.add(lane_key)
            lanes.append(lane)
    status = discovery.candidate_search_lane_status(
        {**candidate, "description_text": candidate.get("description_excerpt", "")},
        {"lanes": lanes},
    )
    if status == discovery.QUALIFICATION_NEEDS_VERIFICATION:
        return status, "location or work mode still needs verification"
    if status == discovery.QUALIFICATION_INELIGIBLE:
        return status, "the role is outside the configured location lanes"
    return status, ""


def candidate_review_state(candidate, searches=None):
    qualification_status, _reason = candidate_qualification(candidate, searches)
    if qualification_status == "needs-verification":
        return "needs-qualification"
    if qualification_status == "ineligible":
        return "ineligible"
    description = storage.clean(candidate.get("description_excerpt", ""))
    if len(description) < MIN_REVIEW_DESCRIPTION_CHARS:
        source_platform = storage.clean(candidate.get("source_platform", "")).lower()
        extraction_failed = (
            not description
            and source_platform
            and source_platform not in {
                "generic_html",
                "official_web_search",
                "avature_web_search",
                "avature_waf_blocked",
            }
        )
        if (
            extraction_failed
            or candidate.get("scan_state", "").lower() == "error"
            or "failed-extraction" in storage.clean(candidate.get("normalization_warnings", "")).lower()
        ):
            return "failed-extraction"
        return "needs-detail"
    if candidate.get("scan_state", "").lower() != "current":
        return "needs-freshness"
    return "ready"


def recommended_candidates(candidates, searches=None):
    rows = [
        candidate
        for candidate in candidates
        if candidate.get("status") == "new"
        and candidate_review_state(candidate, searches) == "ready"
        and candidate_fit_score(candidate) >= FIT_RECOMMENDATION_THRESHOLD
    ]
    return sorted(rows, key=lambda candidate: (-candidate_fit_score(candidate), candidate.get("title", ""), candidate.get("url", "")))[:RECOMMENDED_CANDIDATE_LIMIT]


def candidate_seen_in_scan(candidate, seen_urls, seen_identity_keys):
    url = normalize_url(candidate.get("url", ""))
    if url and url in seen_urls:
        return True
    return bool(candidate_identity_keys(candidate) & seen_identity_keys)


def detail_page_says_unavailable(fetched):
    try:
        status = int(fetched.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    if status in UNAVAILABLE_DETAIL_STATUS_CODES:
        return True
    if status in {0, 401, 403, 429, 500, 502, 503, 504}:
        return False
    page_text = clean_html_text(fetched.get("html", ""))
    if not page_text:
        return False
    return any(pattern.search(page_text) for pattern in UNAVAILABLE_DETAIL_PATTERNS)


def verify_unseen_candidate_availability(
    company_candidates,
    seen_urls,
    seen_identity_keys,
    fetch,
    checked_at="",
    limit=CANDIDATE_DETAIL_VERIFY_LIMIT,
):
    verification_count = 0
    unavailable_count = 0
    skipped_count = 0
    for candidate in company_candidates:
        if candidate.get("status", "new") != "new":
            continue
        if candidate_seen_in_scan(candidate, seen_urls, seen_identity_keys):
            continue
        url = normalize_url(candidate.get("url", ""))
        if not url:
            continue
        if verification_count >= limit:
            candidate["scan_state"] = "verification-pending"
            skipped_count += 1
            continue
        verification_count += 1
        fetched = fetch_with_optional_headers(fetch, url)
        candidate["last_verified_at"] = checked_at or now_iso()
        if detail_page_says_unavailable(fetched):
            candidate["status"] = "unavailable"
            candidate["scan_state"] = "unavailable"
            unavailable_count += 1
        else:
            candidate["scan_state"] = "not-seen"
    return {
        "verification_count": verification_count,
        "verification_skipped_count": skipped_count,
        "unavailable_count": unavailable_count,
    }


def response_cookie_header(response):
    values = []
    get_all = getattr(response.headers, "get_all", None)
    raw_cookies = get_all("Set-Cookie", []) if callable(get_all) else []
    if not isinstance(raw_cookies, (list, tuple)):
        raw_cookies = []
    for raw_cookie in raw_cookies:
        cookie = storage.clean(raw_cookie.split(";", 1)[0])
        if cookie:
            values.append(cookie)
    return "; ".join(values)


def fetch_careers_page(url, headers=None, method="GET", data=None):
    request_headers = {
        "User-Agent": UA,
        "Accept": DEFAULT_ACCEPT,
        "Accept-Language": "en-US,en;q=0.9",
    }
    request_headers.update(headers or {})
    request_data = None
    if data is not None:
        request_data = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json;charset=UTF-8")
    request = Request(
        url,
        data=request_data,
        headers=request_headers,
        method=method,
    )
    context = ssl.create_default_context(cafile=_certifi_ca_file())
    try:
        with urlopen(request, timeout=20, context=context) as response:
            body = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            return {
                "status": response.status,
                "final_url": response.geturl(),
                "html": body,
                "error": "",
                "cookies": response_cookie_header(response),
                "waf_action": storage.clean(response.headers.get("x-amzn-waf-action", "")),
            }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "status": exc.code,
            "final_url": exc.geturl(),
            "html": body,
            "error": str(exc),
            "cookies": response_cookie_header(exc),
            "waf_action": storage.clean(exc.headers.get("x-amzn-waf-action", "")),
        }
    except URLError as exc:
        return {"status": 0, "final_url": url, "html": "", "error": str(exc), "cookies": "", "waf_action": ""}
    except ValueError as exc:
        return {"status": 0, "final_url": url, "html": "", "error": str(exc), "cookies": "", "waf_action": ""}


def fetch_with_optional_headers(fetch, url, headers=None):
    if not headers:
        return fetch(url)
    try:
        return fetch(url, headers=headers)
    except TypeError:
        return fetch(url)


def fetch_with_options(fetch, url, headers=None, method="GET", data=None):
    try:
        return fetch(url, headers=headers, method=method, data=data)
    except TypeError:
        if method == "GET" and data is None:
            return fetch_with_optional_headers(fetch, url, headers=headers)
        return {"status": 0, "final_url": url, "html": "", "error": "fetcher does not support method/data", "cookies": ""}


def _certifi_ca_file():
    try:
        import certifi  # type: ignore
    except Exception:  # noqa: BLE001 - fall back to Python's default trust store.
        return None
    return certifi.where()


def is_google_careers_results_url(url):
    parsed = urlparse(url)
    return parsed.netloc.lower() == GOOGLE_CAREERS_HOST and parsed.path.rstrip("/") == GOOGLE_CAREERS_RESULTS_PATH


def append_search_term(terms, term):
    cleaned = storage.clean(str(term or "")).lower()
    if cleaned and cleaned not in terms:
        terms.append(cleaned)


def expanded_search_terms(signals):
    explicit_terms = []
    for term in settings_store.plain_fit_terms("search_terms", signals):
        append_search_term(explicit_terms, term)
    role_terms = []
    for phrase, weight in settings_store.weighted_fit_terms("role_terms", signals):
        if weight >= SEARCH_ROLE_TERM_MIN_WEIGHT:
            append_search_term(role_terms, phrase)

    seniority_terms = [
        phrase
        for phrase, weight in settings_store.weighted_fit_terms("seniority_terms", signals)
        if weight >= SEARCH_SENIORITY_TERM_MIN_WEIGHT
    ]
    suffix_terms = [term for term in seniority_terms if SEARCH_SUFFIX_LEVEL_PATTERN.fullmatch(normalized_text(term))]
    prefix_terms = [term for term in seniority_terms if term not in suffix_terms]

    deduped = []
    for term in explicit_terms:
        append_search_term(deduped, term)
    expansion_bases = list(dict.fromkeys([*explicit_terms, *role_terms]))
    for base in expansion_bases[:SEARCH_EXPANSION_BASE_LIMIT]:
        if len(normalized_text(base).split()) < 2:
            continue
        for suffix in suffix_terms:
            append_search_term(deduped, f"{base} {suffix}")
        for prefix in prefix_terms:
            if normalized_text(base).startswith(f"{normalized_text(prefix)} "):
                continue
            append_search_term(deduped, f"{prefix} {base}")
    for term in role_terms:
        append_search_term(deduped, term)
    return deduped


def resume_search_terms(resume_text, max_terms=None):
    del resume_text
    limit = max_terms if max_terms is not None else CAREERS_SEARCH_MAX_TERMS
    return expanded_search_terms(settings_store.read_fit_signals())[:limit]


def search_result_limit(config, key, default=CAREERS_SEARCH_RESULT_LIMIT, maximum=50):
    try:
        configured = int((config or {}).get(key, default))
    except (TypeError, ValueError):
        configured = default
    return max(1, min(maximum, max(default, configured)))


def google_careers_search_urls(careers_url):
    parsed = urlparse(careers_url)
    original_query = dict(parse_qsl(parsed.query, keep_blank_values=False))
    existing_query = storage.clean(original_query.get("q", ""))
    terms = [existing_query] if existing_query else resume_search_terms(fit_context_text())
    preserved = {
        key: value
        for key, value in original_query.items()
        if key.lower() not in {"q", "page"}
    }
    if "location" not in {key.lower() for key in preserved}:
        preserved["location"] = "United States"
    base = parsed._replace(query="", fragment="")
    urls = []
    for term in terms:
        for page in range(1, CAREERS_SEARCH_MAX_PAGES + 1):
            query = {**preserved, "q": term}
            if page > 1:
                query["page"] = str(page)
            urls.append(urlunparse(base._replace(query=urlencode(query))))
    return urls


def is_amazon_jobs_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    return host == AMAZON_JOBS_HOST


def is_target_careers_url(url):
    parsed = urlparse(url)
    return (
        parsed.netloc.lower().removeprefix("www.") == TARGET_CAREERS_HOST
        and parsed.path.rstrip("/") == TARGET_JOB_SEARCH_PATH
    )


def is_target_job_url(url):
    parsed = urlparse(url)
    return (
        parsed.netloc.lower().removeprefix("www.") == TARGET_CAREERS_HOST
        and bool(re.fullmatch(r"/jobs/w\d+/\d+/[^/]+/?", parsed.path, re.I))
    )


def target_job_api_url(careers_url):
    parsed = urlparse(careers_url)
    return urlunparse(parsed._replace(path=TARGET_JOB_API_PATH, query="", fragment=""))


def is_avature_url(url):
    return AVATURE_HOST_MARKER in urlparse(url).netloc.lower()


def is_aws_waf_challenge(fetched):
    html_text = fetched.get("html", "") or ""
    waf_action = storage.clean(fetched.get("waf_action", "")).lower()
    return (
        waf_action == "challenge"
        or "AwsWafIntegration" in html_text
        or "window.awsWafCookieDomainList" in html_text
        or "token.awswaf.com" in html_text
    )


def is_cloudflare_challenge(fetched):
    html_text = (fetched.get("html", "") or "").lower()
    return fetched.get("status") == 403 and (
        "cf_challenge" in html_text
        or "cloudflare" in html_text
        or "cf-ray" in html_text
    )


def avature_blocked_message(careers_url):
    return (
        f"{careers_url}: Avature returned an AWS WAF JavaScript challenge. "
        "The current non-browser careers checker cannot access this posting search; "
        "use a browser-backed checker or another public feed for this company."
    )


def is_greenhouse_board_url(url):
    parsed = urlparse(url)
    return parsed.netloc.lower() in GREENHOUSE_BOARD_HOSTS and bool(parsed.path.strip("/"))


def has_branded_greenhouse_job_links(page_html):
    return bool(BRANDED_GREENHOUSE_JOB_PATTERN.search(page_html or ""))


def greenhouse_board_token(url):
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parts:
        return storage.clean(parts[0])
    return ""


def greenhouse_board_url_for_token(token):
    token = storage.clean(token)
    return f"https://job-boards.greenhouse.io/{token}" if token else ""


def greenhouse_board_url(url):
    token = greenhouse_board_token(url)
    return greenhouse_board_url_for_token(token)


def greenhouse_api_url(board_token, path):
    token = storage.clean(board_token)
    clean_path = storage.clean(path).strip("/")
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/{clean_path}"


def greenhouse_headers():
    return {"Accept": "application/json"}










def greenhouse_department_matches_company(department, company):
    dept_name = normalized_key(department.get("name", "") if isinstance(department, dict) else "")
    if not dept_name:
        return False
    return dept_name in company_keys(company)


def extract_greenhouse_departments(payload):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    departments = data.get("departments", [])
    return departments if isinstance(departments, list) else []


def discover_greenhouse_board_source(company, careers_url, fetch):
    token = greenhouse_board_token(careers_url)
    if not token:
        return None
    return discover_greenhouse_board_source_for_token(company, careers_url, fetch, token)


def discover_greenhouse_board_source_for_token(company, careers_url, fetch, token, evidence_prefix=None):
    token = storage.clean(token)
    if not token:
        return None
    departments_url = greenhouse_api_url(token, "departments")
    fetched = fetch_with_optional_headers(fetch, departments_url, headers=greenhouse_headers())
    if fetched.get("error") or not fetched.get("html"):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "greenhouse_board",
            {
                "board_token": token,
                "board_url": greenhouse_board_url_for_token(token),
                "jobs_api_url": greenhouse_api_url(token, "jobs?content=true"),
                "departments_api_url": departments_url,
            },
            [evidence_prefix or "Detected Greenhouse job board."],
            status="discovered",
        )
    departments = extract_greenhouse_departments(fetched.get("html", ""))
    matched = [department for department in departments if greenhouse_department_matches_company(department, company)]
    config = {
        "board_token": token,
        "board_url": greenhouse_board_url_for_token(token),
        "jobs_api_url": greenhouse_api_url(token, "jobs?content=true"),
        "departments_api_url": departments_url,
        "department_ids": [str(department.get("id")) for department in matched if department.get("id")],
        "department_names": [storage.clean(str(department.get("name", ""))) for department in matched if department.get("name")],
    }
    evidence = [evidence_prefix or "Detected Greenhouse job board."]
    if matched:
        evidence.append("Matched company name or alias to Greenhouse department: " + ", ".join(config["department_names"]))
    else:
        evidence.append("No exact Greenhouse department match for company name or aliases; using board-wide search.")
    return save_company_career_source(
        company.get("id", ""),
        careers_url,
        "greenhouse_board",
        config,
        evidence,
        status="verified",
    )


def greenhouse_token_value(value):
    return re.sub(r"[^a-z0-9]+", "", storage.clean(value).lower())


def greenhouse_token_candidates(company, careers_url):
    tokens = []
    for value in [company.get("name", ""), *split_aliases(company.get("aliases", ""))]:
        token = greenhouse_token_value(value)
        if token:
            tokens.append(token)
    parsed = urlparse(careers_url)
    labels = [
        label
        for label in parsed.netloc.lower().split(".")
        if label and label not in {"www", "careers", "jobs", "com", "net", "org", "io", "co"}
    ]
    for label in labels:
        token = greenhouse_token_value(label)
        if token:
            tokens.append(token)
    return list(dict.fromkeys(tokens))[:GREENHOUSE_TOKEN_PROBE_LIMIT]


def discover_greenhouse_board_source_from_tokens(company, careers_url, fetch):
    for token in greenhouse_token_candidates(company, careers_url):
        departments_url = greenhouse_api_url(token, "departments")
        fetched = fetch_with_optional_headers(fetch, departments_url, headers=greenhouse_headers())
        if fetched.get("status") != 200 or not extract_greenhouse_departments(fetched.get("html", "")):
            continue
        return discover_greenhouse_board_source_for_token(
            company,
            careers_url,
            fetch,
            token,
            evidence_prefix=f"Resolved branded careers page to Greenhouse board token: {token}.",
        )
    return None


def greenhouse_board_tokens_from_text(text):
    tokens = []

    def add_token(value):
        token = greenhouse_token_value(value)
        if token and 2 <= len(token) <= 80 and token not in tokens:
            tokens.append(token)

    for match in re.finditer(r"greenhouse\.io/v1/boards/([a-z0-9_-]+)/", text or "", re.I):
        add_token(match.group(1))

    array_assignment_pattern = re.compile(
        r"(?:[A-Za-z_$][\w$]*\.)?([A-Za-z_$][\w$]*(?:boards?|BOARDS?)[A-Za-z_$\w]*)\s*=\s*\[([^\]]{1,2000})\]",
        re.I,
    )
    for match in array_assignment_pattern.finditer(text or ""):
        for value in re.findall(r"['\"]([a-z0-9][a-z0-9_-]{1,79})['\"]", match.group(2), re.I):
            add_token(value)
    return tokens


def discover_greenhouse_board_source_from_scripts(company, page_html, final_url, fetch):
    script_bodies = [page_html or ""]
    for script_url in discover_script_urls(page_html, final_url):
        fetched = fetch(script_url)
        if fetched.get("error") or not fetched.get("html"):
            continue
        script_bodies.append(fetched.get("html", ""))

    tokens = []
    for body in script_bodies:
        for token in greenhouse_board_tokens_from_text(body):
            if token not in tokens:
                tokens.append(token)
    if not tokens:
        return None

    verified = []
    for token in tokens[:GREENHOUSE_TOKEN_PROBE_LIMIT]:
        jobs_url = greenhouse_api_url(token, "jobs?content=true")
        fetched = fetch_with_optional_headers(fetch, jobs_url, headers=greenhouse_headers())
        if fetched.get("status") == 200 and greenhouse_payload_has_jobs(fetched.get("html", "")):
            verified.append(token)
    if not verified:
        return None

    config = {
        "board_tokens": verified,
        "board_urls": [greenhouse_board_url_for_token(token) for token in verified],
        "jobs_api_urls": [greenhouse_api_url(token, "jobs?content=true") for token in verified],
    }
    if len(verified) == 1:
        config.update(
            {
                "board_token": verified[0],
                "board_url": greenhouse_board_url_for_token(verified[0]),
                "jobs_api_url": greenhouse_api_url(verified[0], "jobs?content=true"),
            }
        )
    return save_company_career_source(
        company.get("id", ""),
        company.get("careers_url", ""),
        "greenhouse_board",
        config,
        [
            "Found Greenhouse board tokens in careers page scripts: " + ", ".join(verified),
            "Using script-discovered Greenhouse boards for careers checks.",
        ],
        status="verified",
    )


def extract_next_data(page_html):
    match = re.search(r'<script\s+id=["\']__NEXT_DATA__["\']\s+type=["\']application/json["\']>(.*?)</script>', page_html or "", re.I | re.S)
    if not match:
        return {}
    try:
        return json.loads(html.unescape(match.group(1)))
    except json.JSONDecodeError:
        return {}


def next_page_props(page_html):
    data = extract_next_data(page_html)
    props = data.get("props", {}) if isinstance(data, dict) else {}
    page_props = props.get("pageProps", {}) if isinstance(props, dict) else {}
    return page_props if isinstance(page_props, dict) else {}


def is_next_static_jobs_page(page_html):
    jobs = next_page_props(page_html).get("jobs")
    return isinstance(jobs, list) and any(isinstance(job, dict) and job.get("title") for job in jobs)


def decode_json_object_after(page_html, marker):
    start = (page_html or "").find(marker)
    if start < 0:
        return {}
    start += len(marker)
    try:
        value, _end = json.JSONDecoder().raw_decode(html.unescape(page_html[start:]).lstrip())
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def next_static_text_value(value):
    if isinstance(value, list):
        return " ".join(next_static_text_value(item) for item in value)
    if isinstance(value, dict):
        if "value" in value:
            return next_static_text_value(value.get("value"))
        values = []
        for key in ["name", "title", "label", "amount", "unit", "min_value", "max_value"]:
            if value.get(key) is not None:
                values.append(storage.clean(str(value.get(key))))
        if values:
            return " ".join(values)
        return " ".join(next_static_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def next_static_metadata_value(job, name):
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    value = metadata.get(name)
    return next_static_text_value(value)


def next_static_job_url(careers_url, job, config=None):
    config = config or {}
    job_id = storage.clean(str(job.get("id") or job.get("internal_job_id") or ""))
    template = storage.clean(config.get("detail_path_template", "")) or "/jobs/{id}/"
    if job_id:
        try:
            return normalize_url(urljoin(careers_url, template.format(id=job_id)))
        except (KeyError, ValueError):
            pass
    absolute_url = storage.clean(str(job.get("absolute_url", "") or ""))
    if absolute_url:
        return normalize_url(absolute_url)
    return ""


def next_static_job_location(job):
    values = []
    location = job.get("location")
    if isinstance(location, dict):
        values.append(storage.clean(str(location.get("name", "") or "")))
    elif location:
        values.append(storage.clean(str(location)))
    values.append(next_static_metadata_value(job, "Worksite Classification"))
    return ", ".join(dict.fromkeys(value for value in values if value))


def next_static_job_category(job):
    values = [
        next_static_metadata_value(job, "Company"),
        next_static_metadata_value(job, "Job Field"),
        next_static_metadata_value(job, "Sub Department"),
        storage.clean(str(job.get("studioName", "") or "")),
        storage.clean(str(job.get("company_name", "") or "")),
    ]
    return ", ".join(dict.fromkeys(value for value in values if value))


def extract_next_static_jobs_candidates(page_html, careers_url, config=None):
    jobs = next_page_props(page_html).get("jobs") or []
    resume_text = fit_context_text()
    candidates = []
    seen = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title = storage.clean(str(job.get("title", "") or ""))
        url = next_static_job_url(careers_url, job, config)
        if not title or not url or url in seen:
            continue
        description = clean_html_text(
            " ".join(
                next_static_text_value(job.get(field))
                for field in ["content", "description", "summary"]
                if job.get(field)
            )
        )
        candidate = {
            "title": title,
            "url": url,
            "description": description,
            "location": next_static_job_location(job),
            "category": next_static_job_category(job),
            "search_text": " ".join(
                next_static_text_value(job.get(field))
                for field in ["requisition_id", "internal_job_id", "first_published", "updated_at", "metadata"]
                if job.get(field)
            ),
        }
        if not candidate_matches_resume_role(candidate, resume_text):
            continue
        seen.add(url)
        candidates.append(candidate)
    return candidates




def extract_greenhouse_jobs_candidates(payload, config=None):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    jobs = data.get("jobs", [])
    if not isinstance(jobs, list):
        return []
    return greenhouse_candidates_from_jobs(jobs, config)


def greenhouse_payload_has_jobs(payload):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return False
    jobs = data.get("jobs", [])
    return isinstance(jobs, list) and bool(jobs)


def greenhouse_candidates_from_jobs(jobs, config=None):
    resume_text = fit_context_text()
    return [row for row in greenhouse_adapter.greenhouse_candidates_from_jobs(jobs, config)
            if candidate_matches_resume_role(row, resume_text)]


def fetch_greenhouse_detail_for_jobs(jobs, fetch, config):
    hydrated = []
    token = storage.clean(config.get("board_token", ""))
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job.get("content") or not token or not job.get("id"):
            hydrated.append(job)
            continue
        detail_url = greenhouse_api_url(token, f"jobs/{job.get('id')}?content=true")
        fetched = fetch_with_optional_headers(fetch, detail_url, headers=greenhouse_headers())
        if fetched.get("error") or not fetched.get("html"):
            hydrated.append(job)
            continue
        try:
            detail = json.loads(fetched.get("html", "") or "{}")
        except json.JSONDecodeError:
            detail = {}
        hydrated.append(detail if isinstance(detail, dict) and detail.get("title") else job)
    return hydrated


def amazon_jobs_locale(careers_url, config=None):
    config = config or {}
    configured = storage.clean(config.get("locale", ""))
    if configured:
        return configured
    parsed = urlparse(careers_url)
    parts = [part for part in parsed.path.split("/") if part]
    if parts and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", parts[0].lower()):
        return parts[0].lower()
    return "en"


def amazon_jobs_search_base(careers_url, config=None):
    parsed = urlparse(careers_url)
    host = parsed.netloc.lower() or "www.amazon.jobs"
    if host == AMAZON_JOBS_HOST:
        host = f"www.{AMAZON_JOBS_HOST}"
    configured = storage.clean((config or {}).get("search_json_url", ""))
    if configured:
        return normalize_url(configured)
    locale = amazon_jobs_locale(careers_url, config)
    return f"https://{host}/{locale}/search.json"


def amazon_jobs_search_terms():
    terms = resume_search_terms(fit_context_text())
    deduped = []
    for term in terms:
        if term not in deduped:
            deduped.append(term)
    return deduped[: CAREERS_SEARCH_MAX_TERMS]


def amazon_jobs_search_urls(careers_url, config=None):
    config = config or {}
    base = amazon_jobs_search_base(careers_url, config)
    limit_value = search_result_limit(config, "result_limit")
    try:
        max_pages = max(1, min(CAREERS_SEARCH_MAX_PAGES, int(config.get("max_pages", 1))))
    except (TypeError, ValueError):
        max_pages = 1
    location = storage.clean(config.get("loc_query", "")) or "United States"
    sort = storage.clean(config.get("sort", "")) or "relevant"
    urls = []
    for term in amazon_jobs_search_terms():
        for page in range(max_pages):
            query = {
                "base_query": term,
                "loc_query": location,
                "offset": str(page * limit_value),
                "result_limit": str(limit_value),
                "sort": sort,
            }
            urls.append(f"{base}?{urlencode(query)}")
    return urls


def amazon_jobs_detail_url(careers_url, job_path):
    cleaned = storage.clean(str(job_path or ""))
    if not cleaned:
        return ""
    if cleaned.startswith(("http://", "https://")):
        return normalize_url(cleaned)
    parsed = urlparse(careers_url)
    host = parsed.netloc.lower() or "www.amazon.jobs"
    if host == AMAZON_JOBS_HOST:
        host = f"www.{AMAZON_JOBS_HOST}"
    return normalize_url(urljoin(f"https://{host}", cleaned))


def amazon_jobs_text_value(value):
    if isinstance(value, list):
        return " ".join(amazon_jobs_text_value(item) for item in value)
    if isinstance(value, dict):
        values = []
        for key in ["title", "label", "name", "identifier", "city", "state", "country_code"]:
            if value.get(key):
                values.append(storage.clean(str(value.get(key))))
        if values:
            return " ".join(values)
        return " ".join(amazon_jobs_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def amazon_jobs_location_values(value):
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(amazon_jobs_location_values(item))
        return values
    if isinstance(value, dict):
        normalized = storage.clean(str(value.get("normalizedLocation", "") or ""))
        if normalized:
            return [normalized]
        city = storage.clean(str(value.get("city", "") or value.get("normalizedCityName", "") or ""))
        state = storage.clean(str(value.get("normalizedStateName", "") or value.get("region", "") or ""))
        country = storage.clean(
            str(value.get("normalizedCountryCode", "") or value.get("country_code", "") or "")
        )
        parts = [part for part in [city, state, country] if part]
        if parts:
            return [", ".join(parts)]
        fallback = storage.clean(str(value.get("locationNonStemming", "") or value.get("location", "") or ""))
        return [fallback] if fallback else []
    cleaned = storage.clean(str(value or ""))
    if not cleaned:
        return []
    if cleaned.startswith(("{", "[")):
        try:
            decoded = json.loads(cleaned)
        except json.JSONDecodeError:
            return [cleaned]
        return amazon_jobs_location_values(decoded)
    return [cleaned]


def amazon_jobs_location(job):
    parts = amazon_jobs_location_values(job.get("locations") or [])
    if not parts:
        city = storage.clean(str(job.get("city", "") or ""))
        state = storage.clean(str(job.get("state", "") or ""))
        country = storage.clean(str(job.get("country_code", "") or ""))
        parts = [", ".join(part for part in [city, state, country] if part)]
    if not any(parts):
        parts = amazon_jobs_location_values(job.get("location"))
    return "; ".join(dict.fromkeys(part for part in parts if part))


def amazon_jobs_category(job):
    parts = []
    for field in ["business_category", "job_category", "job_family", "primary_search_label", "team"]:
        value = amazon_jobs_text_value(job.get(field))
        if value:
            parts.append(value)
    return ", ".join(dict.fromkeys(parts))


def extract_amazon_jobs_candidates(payload, careers_url):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    resume_text = fit_context_text()
    candidates = []
    seen = set()
    for job in data.get("jobs", []):
        if not isinstance(job, dict):
            continue
        title = storage.clean(str(job.get("title", "") or ""))
        url = amazon_jobs_detail_url(careers_url, job.get("job_path") or job.get("url_next_step"))
        if not title or not url or url in seen:
            continue
        description = clean_html_text(
            " ".join(
                amazon_jobs_text_value(job.get(field))
                for field in ["description", "description_short", "basic_qualifications", "preferred_qualifications"]
                if job.get(field)
            )
        )
        candidate = {
            "title": title,
            "url": url,
            "description": description,
            "location": amazon_jobs_location(job),
            "category": amazon_jobs_category(job),
            "search_text": " ".join(
                amazon_jobs_text_value(job.get(field))
                for field in ["id", "id_icims", "company_name", "updated_time", "source_system"]
                if job.get(field)
            ),
        }
        if not candidate_matches_resume_role(candidate, resume_text):
            continue
        seen.add(url)
        candidates.append(candidate)
    return candidates


def extract_pcsx_data(page_html):
    match = re.search(r'<code\s+id=["\']pcsx-data["\'][^>]*>(.*?)</code>', page_html or "", re.I | re.S)
    if not match:
        return {}
    try:
        data = json.loads(html.unescape(match.group(1)))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def is_eightfold_pcs_page(page_html):
    data = extract_pcsx_data(page_html)
    return bool(((data.get("configs") or {}).get("pcsxConfig") or {}).get("searchConfig"))


def eightfold_pcs_origin(careers_url, config=None):
    configured = storage.clean((config or {}).get("origin", ""))
    if configured:
        return configured.rstrip("/")
    parsed = urlparse(careers_url)
    return f"{parsed.scheme or 'https'}://{parsed.netloc.lower()}"


def eightfold_pcs_domain(careers_url, config=None):
    configured = storage.clean((config or {}).get("domain", ""))
    if configured:
        return configured
    parsed = urlparse(careers_url)
    return parsed.netloc.lower().removeprefix("apply.").removeprefix("jobs.careers.")


def eightfold_pcs_headers(careers_url):
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": careers_url,
        "X-Requested-With": "XMLHttpRequest",
    }


def eightfold_pcs_search_terms():
    return resume_search_terms(fit_context_text())


def eightfold_pcs_search_urls(careers_url, config=None):
    config = config or {}
    search_url = storage.clean(config.get("search_url", ""))
    if not search_url:
        search_url = f"{eightfold_pcs_origin(careers_url, config)}/api/pcsx/search"
    domain = eightfold_pcs_domain(careers_url, config)
    location = storage.clean(config.get("location", "")) or "United States"
    page_size = search_result_limit(config, "page_size")
    try:
        max_pages = max(1, min(CAREERS_SEARCH_MAX_PAGES, int(config.get("max_pages", 1))))
    except (TypeError, ValueError):
        max_pages = 1
    urls = []
    for term in eightfold_pcs_search_terms():
        for page in range(max_pages):
            query = {
                "domain": domain,
                "query": term,
                "location": location,
                "start": str(page * page_size),
            }
            urls.append(f"{search_url}?{urlencode(query)}")
    return urls


def eightfold_pcs_text_value(value):
    if isinstance(value, list):
        return " ".join(eightfold_pcs_text_value(item) for item in value)
    if isinstance(value, dict):
        return " ".join(eightfold_pcs_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def eightfold_pcs_position_url(position, careers_url, config=None):
    origin = eightfold_pcs_origin(careers_url, config)
    position_path = storage.clean(str(position.get("positionUrl", "") or ""))
    if not position_path:
        position_id = storage.clean(str(position.get("id", "") or ""))
        position_path = f"{EIGHTFOLD_PCS_PATH}/job/{position_id}" if position_id else ""
    if not position_path:
        return ""
    url = normalize_url(urljoin(origin, position_path))
    display_id = storage.clean(str(position.get("displayJobId", "") or position.get("atsJobId", "") or ""))
    if display_id and "jobid=" not in url.lower():
        parsed = urlparse(url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        query.append(("jobId", display_id))
        url = urlunparse(parsed._replace(query=urlencode(query)))
    return url


def extract_eightfold_pcs_candidates(payload, careers_url, config=None):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    positions = ((data.get("data") or {}).get("positions")) or []
    if not isinstance(positions, list):
        return []
    resume_text = fit_context_text()
    candidates = []
    seen = set()
    for position in positions:
        if not isinstance(position, dict):
            continue
        title = storage.clean(str(position.get("name", "") or position.get("title", "") or ""))
        url = eightfold_pcs_position_url(position, careers_url, config)
        if not title or not url or url in seen:
            continue
        location_source = position.get("standardizedLocations") or position.get("locations") or []
        location = "; ".join(
            dict.fromkeys(
                storage.clean(str(entry.get("name", "") if isinstance(entry, dict) else entry))
                for entry in (location_source if isinstance(location_source, list) else [location_source])
                if storage.clean(str(entry.get("name", "") if isinstance(entry, dict) else entry))
            )
        )
        category = ", ".join(
            dict.fromkeys(
                storage.clean(str(position.get(field, "") or ""))
                for field in ["department", "profession", "careerDiscipline", "jobFamily"]
                if storage.clean(str(position.get(field, "") or ""))
            )
        )
        candidate = {
            "title": title,
            "url": url,
            "location": location,
            "work_mode": storage.clean(
                str(position.get("workLocationOption", "") or position.get("locationFlexibility", "") or "")
            ),
            "category": category,
            "search_text": " ".join(
                storage.clean(str(position.get(field, "") or ""))
                for field in ["displayJobId", "atsJobId", "id", "postedTs", "creationTs"]
                if position.get(field)
            ),
        }
        if not candidate_matches_resume_role(candidate, resume_text):
            continue
        seen.add(url)
        candidates.append(candidate)
    return candidates


def discover_eightfold_pcs_source(company, page_html, final_url, fetch):
    del fetch
    data = extract_pcsx_data(page_html)
    search_config = ((data.get("configs") or {}).get("pcsxConfig") or {}).get("searchConfig") or {}
    domain = storage.clean(str(data.get("domain", "") or ""))
    if not domain or not isinstance(search_config, dict):
        return None
    origin = eightfold_pcs_origin(final_url)
    return save_company_career_source(
        company.get("id", ""),
        company.get("careers_url", ""),
        "eightfold_pcs",
        {
            "origin": origin,
            "search_url": f"{origin}/api/pcsx/search",
            "domain": domain,
            "location": "United States",
            "page_size": str(CAREERS_SEARCH_RESULT_LIMIT),
            "max_pages": "1",
            "include_remote_default": str(bool(search_config.get("includeRemoteDefault"))).lower(),
        },
        [
            "Detected Eightfold PCS careers app data.",
            f"Configured PCS search domain: {domain}.",
        ],
        status="verified",
    )


def extract_smartapply_data(page_html):
    match = re.search(r'<code\s+id=["\']smartApplyData["\'][^>]*>(.*?)</code>', page_html or "", re.I | re.S)
    if not match:
        return {}
    try:
        data = json.loads(html.unescape(match.group(1)))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def is_eightfold_smartapply_page(page_html):
    data = extract_smartapply_data(page_html)
    positions = data.get("positions")
    return bool(data.get("domain") and isinstance(positions, list))


def eightfold_smartapply_origin(careers_url, config=None):
    configured = storage.clean((config or {}).get("origin", ""))
    if configured:
        return configured.rstrip("/")
    parsed = urlparse(careers_url)
    return f"{parsed.scheme or 'https'}://{parsed.netloc.lower()}"


def eightfold_smartapply_domain(careers_url, config=None):
    configured = storage.clean((config or {}).get("domain", ""))
    if configured:
        return configured
    data = extract_smartapply_data(fetch_careers_page(careers_url).get("html", ""))
    if data.get("domain"):
        return storage.clean(str(data.get("domain")))
    parsed = urlparse(careers_url)
    return parsed.netloc.lower().removeprefix("explore.jobs.")


def eightfold_smartapply_headers(careers_url):
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": careers_url,
        "X-Requested-With": "XMLHttpRequest",
    }


def eightfold_smartapply_search_terms():
    return resume_search_terms(fit_context_text())


def eightfold_smartapply_search_urls(careers_url, config=None):
    config = config or {}
    jobs_url = storage.clean(config.get("jobs_url", ""))
    if not jobs_url:
        jobs_url = f"{eightfold_smartapply_origin(careers_url, config)}/api/apply/v2/jobs"
    domain = eightfold_smartapply_domain(careers_url, config)
    page_size = search_result_limit(config, "page_size")
    try:
        max_pages = max(1, min(CAREERS_SEARCH_MAX_PAGES, int(config.get("max_pages", 2))))
    except (TypeError, ValueError):
        max_pages = 2
    urls = []
    for term in eightfold_smartapply_search_terms():
        for page in range(max_pages):
            query = {
                "domain": domain,
                "start": str(page * page_size),
                "num": str(page_size),
                "query": term,
            }
            urls.append(f"{jobs_url}?{urlencode(query)}")
    return urls


def eightfold_smartapply_text_value(value):
    if isinstance(value, list):
        return " ".join(eightfold_smartapply_text_value(item) for item in value)
    if isinstance(value, dict):
        return " ".join(eightfold_smartapply_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def eightfold_smartapply_position_url(position, careers_url, config=None):
    url = storage.clean(str(position.get("canonicalPositionUrl", "") or ""))
    if url:
        return normalize_url(url)
    position_id = storage.clean(str(position.get("id", "") or ""))
    if not position_id:
        return ""
    return normalize_url(urljoin(eightfold_smartapply_origin(careers_url, config), f"/careers/job/{position_id}"))


def extract_eightfold_smartapply_candidates(payload, careers_url, config=None):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        data = extract_smartapply_data(payload)
    positions = data.get("positions") or []
    if not isinstance(positions, list):
        return []
    resume_text = fit_context_text()
    candidates = []
    seen = set()
    for position in positions:
        if not isinstance(position, dict):
            continue
        title = storage.clean(str(position.get("name", "") or position.get("posting_name", "") or position.get("title", "") or ""))
        url = eightfold_smartapply_position_url(position, careers_url, config)
        if not title or not url or url in seen:
            continue
        location = ", ".join(
            dict.fromkeys(
                eightfold_smartapply_text_value(position.get(field))
                for field in ["locations", "location", "location_flexibility", "work_location_option"]
                if position.get(field)
            )
        )
        category = ", ".join(
            dict.fromkeys(
                storage.clean(str(position.get(field, "") or ""))
                for field in ["department", "business_unit", "type"]
                if storage.clean(str(position.get(field, "") or ""))
            )
        )
        description = clean_html_text(eightfold_smartapply_text_value(position.get("job_description")))
        candidate = {
            "title": title,
            "url": url,
            "description": description,
            "location": location,
            "category": category,
            "search_text": " ".join(
                storage.clean(str(position.get(field, "") or ""))
                for field in ["display_job_id", "ats_job_id", "id", "id_locale", "t_update", "t_create"]
                if position.get(field)
            ),
        }
        if not candidate_matches_resume_role(candidate, resume_text):
            continue
        seen.add(url)
        candidates.append(candidate)
    return candidates


def discover_eightfold_smartapply_source(company, page_html, final_url, fetch):
    del fetch
    data = extract_smartapply_data(page_html)
    positions = data.get("positions") or []
    domain = storage.clean(str(data.get("domain", "") or ""))
    if not domain or not isinstance(positions, list):
        return None
    origin = eightfold_smartapply_origin(final_url)
    return save_company_career_source(
        company.get("id", ""),
        company.get("careers_url", ""),
        "eightfold_smartapply",
        {
            "origin": origin,
            "jobs_url": f"{origin}/api/apply/v2/jobs",
            "domain": domain,
            "page_size": str(CAREERS_SEARCH_RESULT_LIMIT),
            "max_pages": "2",
        },
        [
            "Detected Eightfold SmartApply careers data.",
            f"Configured SmartApply jobs domain: {domain}.",
            f"Initial page embedded {len(positions)} job row(s).",
        ],
        status="verified",
    )


def career_source_config(source):
    try:
        value = json.loads(source.get("config_json") or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def career_source_evidence(source):
    try:
        value = json.loads(source.get("evidence") or "[]")
    except json.JSONDecodeError:
        value = []
    return [storage.clean(str(item)) for item in value if storage.clean(str(item))] if isinstance(value, list) else []


def encode_source_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def get_company_career_source(company_id):
    wanted = storage.clean(company_id).upper()
    return next(
        (row for row in repository.read_company_career_sources() if row.get("company_id", "").upper() == wanted),
        None,
    )


def save_company_career_source(company_id, source_url, platform_type, config=None, evidence=None, status="discovered", notes=""):
    wanted = storage.clean(company_id).upper()
    rows = repository.read_company_career_sources()
    source = next((row for row in rows if row.get("company_id", "").upper() == wanted), None)
    timestamp = now_iso()
    if source is None:
        source = {field: "" for field in schema.COMPANY_CAREER_SOURCE_FIELDS}
        source["company_id"] = wanted
        rows.append(source)
    source_changed = (
        normalize_url(source.get("source_url", "")) != normalize_url(source_url)
        or source.get("platform_type", "") != storage.clean(platform_type)
    )
    source.update(
        {
            "source_url": normalize_url(source_url),
            "platform_type": storage.clean(platform_type),
            "config_json": encode_source_json(config or {}),
            "evidence": encode_source_json(evidence or []),
            "discovered_at": timestamp if source_changed else source.get("discovered_at") or timestamp,
            "last_verified_at": timestamp if status == "verified" else source.get("last_verified_at", ""),
            "status": storage.clean(status),
            "notes": storage.clean(notes),
        }
    )
    return repository.upsert_company_career_source(source)


def mark_company_career_source_verified(company_id, status="verified"):
    source = get_company_career_source(company_id)
    if not source:
        return None
    source["last_verified_at"] = now_iso()
    source["status"] = storage.clean(status)
    return repository.upsert_company_career_source(source)


def custom_workday_locale(careers_url, config):
    parts = [part for part in urlparse(careers_url).path.split("/") if part]
    if parts and re.fullmatch(r"[a-z]{2}-[a-z]{2}", parts[0].lower()):
        return parts[0].lower()
    return config.get("default_locale") or "en-us"


def custom_workday_search_terms(resume_text):
    terms = resume_search_terms(resume_text)
    deduped = []
    for term in terms:
        if term not in deduped:
            deduped.append(term)
    return deduped[: CAREERS_SEARCH_MAX_TERMS + 1]


def custom_workday_search_urls(careers_url, config):
    del careers_url
    search_url = storage.clean(config.get("search_url", ""))
    query_param = storage.clean(config.get("query_param", "")) or "keyword"
    if not search_url:
        return []
    return [
        f"{search_url}?{urlencode({query_param: term})}"
        for term in custom_workday_search_terms(fit_context_text())
    ]


def custom_workday_detail_url(careers_url, config, url_part):
    locale = custom_workday_locale(careers_url, config)
    values = {
        "locale": locale,
        "slug": storage.clean(str(url_part)),
        "urlPart": storage.clean(str(url_part)),
    }
    template = storage.clean(config.get("detail_url_template", ""))
    if template:
        try:
            return normalize_url(template.format(**values))
        except (KeyError, ValueError):
            return ""
    parsed = urlparse(careers_url)
    return normalize_url(f"https://{parsed.netloc.lower()}/{locale}/careers/job/{values['slug']}")


def custom_workday_text_value(value):
    if isinstance(value, list):
        return " ".join(custom_workday_text_value(item) for item in value)
    if isinstance(value, dict):
        return " ".join(custom_workday_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def custom_workday_location(item):
    parts = []
    for field in ["locationHierarchy", "location", "country", "region"]:
        value = storage.clean(str(item.get(field, "") or ""))
        if value:
            parts.append(value)
    for location in item.get("jobPostingLocations") or []:
        if not isinstance(location, dict):
            continue
        for field in ["locationName", "country", "region"]:
            value = storage.clean(str(location.get(field, "") or ""))
            if value:
                parts.append(value)
    return ", ".join(dict.fromkeys(parts))


def candidate_matches_resume_role(candidate, resume_text):
    resume = normalized_text(resume_text)
    role_text = candidate_role_text(candidate)
    for item in candidate_role_fit_terms():
        if item["requires_resume_support"] and not resume_supports_phrase_variant(resume, item["phrase"]):
            continue
        if text_contains_phrase_variant(role_text, item["phrase"]):
            return True
    return False


def extract_custom_workday_candidates(payload, careers_url, config):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    resume_text = fit_context_text()
    candidates = []
    seen = set()
    result_path = storage.clean(config.get("result_path", "")) or "Report_Entry"
    title_field = storage.clean(config.get("title_field", "")) or "title"
    slug_field = storage.clean(config.get("slug_field", "")) or "urlPart"
    rows = data.get(result_path, [])
    if not isinstance(rows, list):
        return []
    for item in rows:
        if not isinstance(item, dict):
            continue
        title = storage.clean(str(item.get(title_field, "") or ""))
        url_part = storage.clean(str(item.get(slug_field, "") or ""))
        if not title or not url_part:
            continue
        url = custom_workday_detail_url(careers_url, config, url_part)
        if not url or url in seen:
            continue
        description = clean_html_text(
            " ".join(
                custom_workday_text_value(item.get(field))
                for field in ["jobDescription", "description", "summary", "responsibilities", "qualifications"]
                if item.get(field)
            )
        )
        category = ", ".join(
            dict.fromkeys(
                storage.clean(str(item.get(field, "") or ""))
                for field in ["jobFamilyGroup", "jobFamily", "businessArea"]
                if storage.clean(str(item.get(field, "") or ""))
            )
        )
        candidate = {
            "title": title,
            "url": url,
            "description": description,
            "location": custom_workday_location(item),
            "category": category,
            "search_text": " ".join(
                storage.clean(str(item.get(field, "") or ""))
                for field in ["referenceID", "jobRequisitionID", "postedDate"]
                if item.get(field)
            ),
        }
        if not candidate_matches_resume_role(candidate, resume_text):
            continue
        seen.add(url)
        candidates.append(candidate)
    return candidates


def discover_script_urls(page_html, base_url):
    urls = []
    for match in re.finditer(r"<script\b[^>]*\bsrc\s*=\s*(['\"])(.*?)\1", page_html or "", re.I | re.S):
        href = html.unescape(match.group(2)).strip()
        if not href or href.startswith(("data:", "javascript:")):
            continue
        url = normalize_url(urljoin(base_url, href))
        if re.search(r"\s", url):
            continue
        if not host_allowed(url, base_url):
            continue
        if url not in urls:
            urls.append(url)
    return urls[:CAREER_DISCOVERY_SCRIPT_LIMIT]


def likely_job_api_urls(script_text, base_url):
    urls = []
    for match in re.finditer(r"https?://[^\"'`\s<>]+/(?:api/[^\"'`\s<>]*?(?:GetJobs|jobs|jobsearch|positions))", script_text or "", re.I):
        url = normalize_url(match.group(0))
        if url and url not in urls:
            urls.append(url)
    for match in re.finditer(r"['\"]((?:/[^\"']*)?/(?:api/[^\"']*?(?:GetJobs|jobs|jobsearch|positions)))['\"]", script_text or "", re.I):
        url = normalize_url(urljoin(base_url, match.group(1)))
        if url and url not in urls:
            urls.append(url)
    if re.search(r"\bGetJobs\b", script_text or "", re.I):
        hosts = [
            storage.clean(match.group(1))
            for match in re.finditer(r'["\'](https?://[^"\']+)["\']', script_text or "")
        ]
        api_paths = [
            storage.clean(match.group(1))
            for match in re.finditer(r'["\'](api/v\d+[^"\']*)["\']', script_text or "", re.I)
        ]
        for host in hosts:
            for api_path in api_paths:
                url = normalize_url(f"{host.rstrip('/')}/{api_path.strip('/')}/GetJobs")
                if url and url not in urls:
                    urls.append(url)
    return urls


def extract_api_key_near_url(script_text, api_url):
    text = script_text or ""
    patterns = [
        r'["\']x-api-key["\']\s*:\s*["\']([^"\']+)["\']',
        r'["\']x-api-key["\']\s*:\s*["\']{2}\.concat\(["\']([^"\']+)["\']\)',
        r'["\']x-api-key["\']\s*:\s*["\']{0,1}\.concat\(["\']([^"\']+)["\']\)',
        r'["\']X-Api-Key["\']\s*:\s*["\']([^"\']+)["\']',
        r'headers\s*:\s*\{[^}]*?["\']x-api-key["\']\s*:\s*["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return storage.clean(match.group(1))
    parsed = urlparse(api_url)
    host_index = text.find(parsed.netloc)
    if host_index >= 0:
        window = text[max(0, host_index - 1000): host_index + 3000]
        for pattern in patterns:
            match = re.search(pattern, window, re.I | re.S)
            if match:
                return storage.clean(match.group(1))
    return ""


def infer_custom_workday_detail_template(careers_url):
    parsed = urlparse(careers_url)
    locale = custom_workday_locale(careers_url, {})
    return f"https://{parsed.netloc.lower()}/{locale}/careers/job/{{urlPart}}"


def custom_workday_headers(config):
    headers = {"Accept": "application/json"}
    configured = config.get("headers") if isinstance(config.get("headers"), dict) else {}
    for key, value in configured.items():
        key = storage.clean(str(key))
        value = storage.clean(str(value))
        if key and value:
            headers[key] = value
    return headers


def workday_cxs_headers():
    return {"Accept": "application/json"}


def workday_cxs_board_from_url(url):
    parsed = urlparse(normalize_url(url))
    host = parsed.netloc.lower()
    if "myworkdayjobs.com" not in host and "workdayjobs.com" not in host:
        return None
    tenant = host.split(".", 1)[0]
    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts and re.fullmatch(r"[a-z]{2}-[a-z]{2}", path_parts[0].lower()):
        path_parts = path_parts[1:]
    site = ""
    if path_parts:
        site = path_parts[0]
    if not tenant or not site or site.lower() in {"job", "jobs", "wday"}:
        return None
    origin = f"https://{host}"
    return {
        "origin": origin,
        "tenant": tenant,
        "site": site,
        "jobs_url": f"{origin}/wday/cxs/{tenant}/{site}/jobs",
        "board_url": f"{origin}/{site}",
        "detail_url_template": f"{origin}/{site}{{externalPath}}",
    }


def discover_workday_cxs_urls(page_html, base_url):
    urls = []
    for href in extract_href_values(page_html):
        if "myworkdayjobs.com" not in href and "workdayjobs.com" not in href:
            continue
        url = normalize_url(urljoin(base_url, href))
        if url and url not in urls:
            urls.append(url)
    if "myworkdayjobs.com" in urlparse(base_url).netloc.lower() or "workdayjobs.com" in urlparse(base_url).netloc.lower():
        normalized_base = normalize_url(base_url)
        if normalized_base and normalized_base not in urls:
            urls.insert(0, normalized_base)
    return urls


def workday_cxs_search_terms():
    return resume_search_terms(fit_context_text())


def workday_cxs_search_payloads(config):
    try:
        page_size = max(1, min(WORKDAY_CXS_RESULT_LIMIT, int(config.get("page_size", WORKDAY_CXS_RESULT_LIMIT))))
    except (TypeError, ValueError):
        page_size = WORKDAY_CXS_RESULT_LIMIT
    try:
        max_pages = max(1, min(CAREERS_SEARCH_MAX_PAGES, int(config.get("max_pages", 1))))
    except (TypeError, ValueError):
        max_pages = 1
    payloads = []
    for term in workday_cxs_search_terms():
        for page in range(max_pages):
            payloads.append(
                {
                    "limit": page_size,
                    "offset": page * page_size,
                    "searchText": term,
                    "appliedFacets": {},
                }
            )
    return payloads


def workday_cxs_detail_url(config, external_path):
    cleaned = storage.clean(str(external_path or ""))
    if not cleaned:
        return ""
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    template = storage.clean(config.get("detail_url_template", ""))
    if template:
        try:
            return normalize_url(template.format(externalPath=cleaned))
        except (KeyError, ValueError):
            return ""
    origin = storage.clean(config.get("origin", "")).rstrip("/")
    site = storage.clean(config.get("site", ""))
    if not origin or not site:
        return ""
    return normalize_url(f"{origin}/{site}{cleaned}")


def workday_cxs_text_value(value):
    if isinstance(value, list):
        return " ".join(workday_cxs_text_value(item) for item in value)
    if isinstance(value, dict):
        return " ".join(workday_cxs_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def workday_cxs_candidate_from_posting(posting, config):
    if not isinstance(posting, dict):
        return None
    title = storage.clean(str(posting.get("title", "") or ""))
    external_path = storage.clean(str(posting.get("externalPath", "") or ""))
    url = workday_cxs_detail_url(config, external_path)
    if not title or not url:
        return None
    return {
        "title": title,
        "url": url,
        "description": "",
        "location": storage.clean(str(posting.get("locationsText", "") or "")),
        "category": workday_cxs_text_value(posting.get("jobFamily")),
        "search_text": " ".join(
            storage.clean(str(item))
            for item in [*(posting.get("bulletFields") or []), posting.get("postedOn", "")]
            if storage.clean(str(item))
        ),
    }


def extract_workday_cxs_candidates(payload, config):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    postings = data.get("jobPostings") or data.get("items") or []
    if not isinstance(postings, list):
        return []
    candidates = []
    seen = set()
    for posting in postings:
        candidate = workday_cxs_candidate_from_posting(posting, config)
        if not candidate or candidate["url"] in seen:
            continue
        seen.add(candidate["url"])
        candidates.append(candidate)
    return candidates


def enrich_workday_cxs_candidate(candidate, fetch):
    fetched = fetch_with_optional_headers(fetch, candidate.get("url", ""), headers=workday_cxs_headers())
    if fetched.get("error") or not fetched.get("html"):
        return candidate
    try:
        data = json.loads(fetched.get("html", "") or "{}")
    except json.JSONDecodeError:
        return candidate
    info = data.get("jobPostingInfo") if isinstance(data.get("jobPostingInfo"), dict) else {}
    hiring = data.get("hiringOrganization") if isinstance(data.get("hiringOrganization"), dict) else {}
    description = clean_html_text(
        " ".join(
            workday_cxs_text_value(info.get(field))
            for field in ["jobDescription", "description", "summary"]
            if info.get(field)
        )
    )
    enriched = dict(candidate)
    if storage.clean(str(info.get("title", "") or "")):
        enriched["title"] = storage.clean(str(info.get("title", "") or ""))
    if description:
        enriched["description"] = description
    extra_location = storage.clean(str(info.get("location", "") or info.get("locationsText", "") or ""))
    if extra_location:
        enriched["location"] = ", ".join(dict.fromkeys([candidate.get("location", ""), extra_location]))
    hiring_profile = hiring.get("sameAs") or hiring.get("url") or ""
    if isinstance(hiring_profile, list):
        hiring_profile = next((value for value in hiring_profile if storage.clean(str(value or ""))), "")
    enriched.update(
        {
            "company_industry": normalize_company_industry(
                hiring.get("industry") or hiring.get("naics")
            ),
            "company_size": normalize_company_size(
                hiring.get("numberOfEmployees") or hiring.get("employees")
            ),
            "company_profile_url": normalize_url(str(hiring_profile or "")),
        }
    )
    enriched["search_text"] = " ".join(
        part
        for part in [
            candidate.get("search_text", ""),
            workday_cxs_text_value(info.get("jobReqId")),
            workday_cxs_text_value(info.get("jobRequisitionId")),
            workday_cxs_text_value(hiring.get("name")),
        ]
        if part
    )
    return enriched


def custom_workday_probe_terms():
    terms = custom_workday_search_terms(fit_context_text())
    return terms[:CAREERS_SEARCH_MAX_TERMS] or settings_store.search_terms()[:CAREERS_SEARCH_MAX_TERMS]


def probe_custom_workday_config(careers_url, fetch, config):
    headers = custom_workday_headers(config)
    query_param = storage.clean(config.get("query_param", "")) or "keyword"
    for term in custom_workday_probe_terms():
        probe_url = f"{config['search_url']}?{urlencode({query_param: term})}"
        fetched = fetch_with_optional_headers(fetch, probe_url, headers=headers)
        if fetched.get("error") or not fetched.get("html"):
            continue
        try:
            data = json.loads(fetched.get("html", "") or "{}")
        except json.JSONDecodeError:
            continue
        rows = data.get(config.get("result_path") or "Report_Entry", [])
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            if storage.clean(str(item.get(config.get("title_field") or "title", "") or "")) and storage.clean(str(item.get(config.get("slug_field") or "urlPart", "") or "")):
                return True, f"API probe returned job rows for '{term}'"
    return False, "API probes did not return job-shaped rows"


def probe_workday_cxs_config(fetch, config):
    jobs_url = storage.clean(config.get("jobs_url", ""))
    if not jobs_url:
        return False, ""
    payload = {
        "limit": min(WORKDAY_CXS_RESULT_LIMIT, 10),
        "offset": 0,
        "searchText": "technical program manager",
        "appliedFacets": {},
    }
    fetched = fetch_with_options(fetch, jobs_url, headers=workday_cxs_headers(), method="POST", data=payload)
    if fetched.get("error") or not fetched.get("html"):
        return False, ""
    try:
        data = json.loads(fetched.get("html", "") or "{}")
    except json.JSONDecodeError:
        return False, ""
    postings = data.get("jobPostings") or []
    if not isinstance(postings, list):
        return False, ""
    return True, f"Workday CXS jobs endpoint returned {len(postings)} posting row(s)."


def discover_workday_cxs_source(company, page_html, final_url, fetch):
    configs = []
    for url in discover_workday_cxs_urls(page_html, final_url):
        config = workday_cxs_board_from_url(url)
        if config and config not in configs:
            config.update({"page_size": str(WORKDAY_CXS_RESULT_LIMIT), "max_pages": "1"})
            configs.append(config)

    for config in configs:
        ok, evidence = probe_workday_cxs_config(fetch, config)
        if ok:
            return save_company_career_source(
                company.get("id", ""),
                config.get("board_url") or company.get("careers_url", ""),
                "workday_cxs",
                config,
                [
                    "Found linked Workday CXS careers board.",
                    evidence,
                ],
                status="verified",
            )
    return None


def discover_custom_workday_api_source(company, page_html, final_url, fetch):
    script_bodies = [page_html or ""]
    script_urls = discover_script_urls(page_html, final_url)
    for script_url in script_urls:
        fetched = fetch(script_url)
        if fetched.get("error") or not fetched.get("html"):
            continue
        script_bodies.append(fetched.get("html", ""))

    candidates = []
    for body in script_bodies:
        for api_url in likely_job_api_urls(body, final_url):
            if not re.search(r"/GetJobs(?:$|\?)", api_url, re.I):
                continue
            api_key = extract_api_key_near_url(body, api_url)
            config = {
                "search_url": normalize_url(api_url),
                "query_param": "keyword",
                "result_path": "Report_Entry",
                "title_field": "title",
                "slug_field": "urlPart",
                "detail_url_template": infer_custom_workday_detail_template(final_url),
                "headers": {"x-api-key": api_key} if api_key else {},
            }
            if config["search_url"] and config not in candidates:
                candidates.append(config)

    for config in candidates:
        ok, probe_evidence = probe_custom_workday_config(final_url, fetch, config)
        if ok:
            evidence = [
                "Found Workday-style GetJobs API in careers page scripts.",
                probe_evidence,
            ]
            if config.get("headers", {}).get("x-api-key"):
                evidence.append("Found API key header in careers page scripts.")
            return save_company_career_source(
                company.get("id", ""),
                company.get("careers_url", ""),
                "custom_workday",
                config,
                evidence,
                status="verified",
            )
    return None


def is_servicenow_portal_page(page_html):
    text = page_html or ""
    return 'ng-app="sn.$sp"' in text or "window.NOW.portal_id" in text or "Service Portal" in text


def servicenow_page_id(page_html, careers_url):
    match = re.search(r"window\.NOW\.page_id\s*=\s*['\"]([^'\"]+)['\"]", page_html or "")
    if match:
        return storage.clean(match.group(1))
    parsed = urlparse(careers_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=False))
    return storage.clean(query.get("id", "")) or "all_jobs"


def servicenow_base_url(careers_url):
    parsed = urlparse(careers_url)
    return f"{parsed.scheme or 'https'}://{parsed.netloc.lower()}"


def servicenow_portal_path(careers_url):
    parsed = urlparse(careers_url)
    return parsed.path.rstrip("/") or "/"


def servicenow_page_api_url(careers_url, page_id):
    return f"{servicenow_base_url(careers_url)}/api/now/sp/page?{urlencode({'id': page_id})}"


def find_widget_by_id(payload, widget_id):
    if isinstance(payload, dict):
        if payload.get("id") == widget_id:
            return payload
        for value in payload.values():
            found = find_widget_by_id(value, widget_id)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_widget_by_id(value, widget_id)
            if found:
                return found
    return None


def extract_servicenow_widget_config(page_payload, careers_url, page_id):
    filters_widget = find_widget_by_id(page_payload, "bby-jobs-filters")
    map_widget = find_widget_by_id(page_payload, "bby-career-map")
    if not filters_widget or not map_widget:
        return {}
    widget_id = storage.clean(str(map_widget.get("sys_id", "") or map_widget.get("id", "") or ""))
    if not widget_id:
        return {}
    base_url = servicenow_base_url(careers_url)
    portal_path = servicenow_portal_path(careers_url)
    return {
        "page_id": page_id,
        "page_api_url": servicenow_page_api_url(careers_url, page_id),
        "widget_url": f"{base_url}/api/now/sp/widget/{widget_id}",
        "portal_path": portal_path,
        "detail_url_template": f"{base_url}{portal_path}?id=job_details&req_id={{auto_req_id}}",
        "items_per_page": "20",
        "country": "US",
        "query_prefix": "GOTO123TEXTQUERY321=",
    }


def extract_servicenow_token(page_html):
    match = re.search(r"g_ck\s*=\s*['\"]([^'\"]+)['\"]", page_html or "")
    return storage.clean(match.group(1)) if match else ""


def servicenow_search_terms():
    terms = resume_search_terms(fit_context_text())
    return terms[: CAREERS_SEARCH_MAX_TERMS + 1]


def servicenow_search_payload(term, config):
    query_prefix = storage.clean(config.get("query_prefix", "")) or "GOTO123TEXTQUERY321="
    country = storage.clean(config.get("country", "")) or "US"
    items_per_page = int(storage.clean(str(config.get("items_per_page", "") or "20")) or "20")
    return {
        "action": "update_data",
        "options": {
            "items_per_page": items_per_page,
            "sort_val": "relevance",
            "bby_loc_q": None,
            "lastLimit": 0,
            "limitResults": "limitResults",
            "filters": {
                "q": f"{query_prefix}{term}",
                "c": f"country={country}",
            },
            "sort": "relevance",
            "current_page": 0,
            "initial_page": 0,
        },
    }


def servicenow_session_headers(config, fetch):
    source_url = storage.clean(config.get("source_url", ""))
    fetched = fetch(source_url)
    if fetched.get("error") or not fetched.get("html"):
        return {}, fetched.get("error") or "empty response"
    token = extract_servicenow_token(fetched.get("html", ""))
    if not token:
        return {}, "ServiceNow guest token not found"
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "Content-Type": "application/json;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "X-UserToken": token,
        "Referer": source_url,
    }
    cookies = storage.clean(fetched.get("cookies", ""))
    if cookies:
        headers["Cookie"] = cookies
    return headers, ""


def servicenow_detail_url(config, properties):
    template = storage.clean(config.get("detail_url_template", ""))
    auto_req_id = storage.clean(str(properties.get("auto_req_id", "") or ""))
    if not template or not auto_req_id:
        return ""
    try:
        return normalize_url(template.format(auto_req_id=auto_req_id))
    except (KeyError, ValueError):
        return ""


def extract_servicenow_candidates(payload, config):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    result_data = ((data.get("result") or {}).get("data") or {})
    features = (((result_data.get("items") or {}).get("features")) or [])
    resume_text = fit_context_text()
    candidates = []
    seen = set()
    for feature in features:
        properties = feature.get("properties", {}) if isinstance(feature, dict) else {}
        if not isinstance(properties, dict):
            continue
        title = storage.clean(str(properties.get("title", "") or ""))
        url = servicenow_detail_url(config, properties)
        if not title or not url or url in seen:
            continue
        location = ", ".join(
            dict.fromkeys(
                storage.clean(str(properties.get(field, "") or ""))
                for field in ["city", "state", "country"]
                if storage.clean(str(properties.get(field, "") or ""))
            )
        )
        category = ", ".join(
            dict.fromkeys(
                storage.clean(str(properties.get(field, "") or ""))
                for field in ["category", "type", "experience", "worker_type"]
                if storage.clean(str(properties.get(field, "") or ""))
            )
        )
        candidate = {
            "title": title,
            "url": url,
            "location": location,
            "category": category,
            "search_text": " ".join(
                storage.clean(str(properties.get(field, "") or ""))
                for field in ["auto_req_id", "last_updated", "address", "zip"]
                if properties.get(field)
            ),
        }
        if not candidate_matches_resume_role(candidate, resume_text):
            continue
        seen.add(url)
        candidates.append(candidate)
    return candidates


def fetch_servicenow_portal_candidates(careers_url, fetch, config=None):
    config = {**(config or {}), "source_url": careers_url}
    headers, error = servicenow_session_headers(config, fetch)
    if error:
        return [], 0, [f"{careers_url}: {error}"]
    extracted = []
    searched = 0
    errors = []
    for term in servicenow_search_terms():
        fetched = fetch_with_options(
            fetch,
            config.get("widget_url", ""),
            headers=headers,
            method="POST",
            data=servicenow_search_payload(term, config),
        )
        if fetched.get("error") or not fetched.get("html"):
            errors.append(f"{term}: {fetched.get('error') or 'empty response'}")
            continue
        searched += 1
        for candidate in extract_servicenow_candidates(fetched.get("html", ""), config):
            candidate["matched_queries"] = term
            extracted.append(candidate)
    return extracted, searched, errors


def probe_servicenow_config(careers_url, fetch, config):
    extracted, searched, errors = fetch_servicenow_portal_candidates(careers_url, fetch, config)
    if searched and extracted:
        return True, f"ServiceNow widget probe returned {len(extracted)} resume-role candidate(s)."
    if searched:
        return True, "ServiceNow widget probe returned job rows."
    return False, "; ".join(errors) or "ServiceNow widget probe failed"


def discover_servicenow_portal_source(company, page_html, final_url, fetch):
    if not is_servicenow_portal_page(page_html):
        return None
    page_id = servicenow_page_id(page_html, final_url)
    page_api_url = servicenow_page_api_url(final_url, page_id)
    fetched = fetch_with_optional_headers(fetch, page_api_url, headers={"Accept": "application/json,text/plain,*/*"})
    if fetched.get("error") or not fetched.get("html"):
        return None
    try:
        page_payload = json.loads(fetched.get("html", "") or "{}")
    except json.JSONDecodeError:
        return None
    config = extract_servicenow_widget_config(page_payload, final_url, page_id)
    if not config:
        return None
    ok, probe_evidence = probe_servicenow_config(company.get("careers_url", ""), fetch, config)
    if not ok:
        return None
    return save_company_career_source(
        company.get("id", ""),
        company.get("careers_url", ""),
        "servicenow_portal",
        config,
        [
            "Detected ServiceNow Service Portal careers page.",
            "Found public NewRocket jobs widget configuration.",
            probe_evidence,
        ],
        status="verified",
    )


def is_jibe_careers_page(page_html):
    html_text = page_html or ""
    return "data-jibe-search-version" in html_text or (
        "window.searchConfig" in html_text and "/api/jobs" in html_text
    )


def is_openai_careers_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    return host == OPENAI_CAREERS_HOST and parsed.path.startswith("/careers")


def is_ashby_jobs_url(url):
    parsed = urlparse(url)
    return parsed.netloc.lower() == "jobs.ashbyhq.com" and bool(parsed.path.strip("/"))


def ashby_board_url_from_html(page_html):
    matches = re.findall(
        r"https://jobs\.ashbyhq\.com/([a-z0-9_-]+)(?:/(?:embed|[0-9a-f-]{16,}))?",
        html.unescape(page_html or ""),
        re.I,
    )
    if not matches:
        return ""
    return f"https://jobs.ashbyhq.com/{matches[0]}"


def ashby_board_url(careers_url):
    if is_openai_careers_url(careers_url):
        return OPENAI_ASHBY_BOARD_URL
    if is_ashby_jobs_url(careers_url):
        parsed = urlparse(careers_url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            return urlunparse(parsed._replace(path=f"/{path_parts[0]}", query="", fragment=""))
    return ""


def is_lever_jobs_url(url):
    parsed = urlparse(url)
    return parsed.netloc.lower() in {"jobs.lever.co", "jobs.eu.lever.co"} and bool(parsed.path.strip("/"))


def lever_board_url(careers_url):
    if not is_lever_jobs_url(careers_url):
        return ""
    parsed = urlparse(careers_url)
    board_token = parsed.path.strip("/").split("/", 1)[0]
    return urlunparse(parsed._replace(path=f"/{board_token}", query="", fragment=""))


def lever_postings_api_url(careers_url):
    board_url = lever_board_url(careers_url)
    if not board_url:
        return ""
    parsed = urlparse(board_url)
    api_host = "api.eu.lever.co" if parsed.netloc.lower() == "jobs.eu.lever.co" else "api.lever.co"
    board_token = parsed.path.strip("/")
    return urlunparse(parsed._replace(netloc=api_host, path=f"/v0/postings/{board_token}", query="mode=json"))


def extract_lever_candidates(payload):
    resume_text = fit_context_text()
    return [row for row in lever_adapter.extract_lever_candidates(payload)
            if candidate_matches_resume_role(row, resume_text)]


def fetch_lever_candidates(careers_url, fetch, config=None):
    config = config or {}
    api_url = storage.clean(str(config.get("api_url", "") or "")) or lever_postings_api_url(careers_url)
    if not api_url:
        return [], 0, ["Lever config is missing api_url"]
    fetched = fetch_with_optional_headers(fetch, api_url, headers={"Accept": "application/json"})
    if fetched.get("error") or not fetched.get("html"):
        return [], 0, [f"{api_url}: {fetched.get('error') or 'empty response'}"]
    return extract_lever_candidates(fetched.get("html", "")), 1, []


def extract_ashby_app_data(page_html):
    match = re.search(r"window\.__appData\s*=\s*(\{.*?\});\s*\n\s*fetch\(", page_html or "", re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def ashby_posting_url(board_url, posting_id):
    return normalize_url(urljoin(board_url.rstrip("/") + "/", storage.clean(str(posting_id))))


def ashby_location(posting):
    parts = []
    for field in ["locationName", "workplaceType"]:
        value = storage.clean(str(posting.get(field, "") or ""))
        if value:
            parts.append(value)
    for location in posting.get("secondaryLocations") or []:
        if not isinstance(location, dict):
            continue
        value = storage.clean(str(location.get("locationName", "") or ""))
        if value:
            parts.append(value)
        address = location.get("address") or {}
        postal = address.get("postalAddress") if isinstance(address, dict) else {}
        if isinstance(postal, dict):
            for field in ["addressLocality", "addressRegion", "addressCountry"]:
                value = storage.clean(str(postal.get(field, "") or ""))
                if value:
                    parts.append(value)
    return ", ".join(dict.fromkeys(parts))


def ashby_category(posting):
    return ", ".join(
        dict.fromkeys(
            storage.clean(str(posting.get(field, "") or ""))
            for field in ["departmentName", "teamName"]
            if storage.clean(str(posting.get(field, "") or ""))
        )
    )


def ashby_candidate_matches_resume(candidate, resume_text):
    return candidate_matches_resume_role(candidate, resume_text)


def extract_ashby_candidates(page_html, board_url):
    data = extract_ashby_app_data(page_html)
    postings = ((data.get("jobBoard") or {}).get("jobPostings") or [])
    resume_text = fit_context_text()
    candidates = []
    seen = set()
    for posting in postings:
        if not isinstance(posting, dict) or not posting.get("isListed", True):
            continue
        title = storage.clean(str(posting.get("title", "") or ""))
        posting_id = storage.clean(str(posting.get("id", "") or ""))
        if not title or not posting_id:
            continue
        url = ashby_posting_url(board_url, posting_id)
        if not url or url in seen:
            continue
        location = ashby_location(posting)
        candidate = {
            "title": title,
            "url": url,
            "location": location,
            "category": ashby_category(posting),
        }
        if not ashby_candidate_matches_resume(candidate, resume_text):
            continue
        seen.add(url)
        candidates.append(candidate)
    return candidates


def jibe_search_urls(careers_url):
    parsed = urlparse(careers_url)
    terms = resume_search_terms(fit_context_text())
    urls = []
    for term in terms:
        for page in range(1, CAREERS_SEARCH_MAX_PAGES + 1):
            query = {
                "keywords": term,
                "limit": str(JIBE_API_LIMIT),
                "page": str(page),
                "country": "United States",
            }
            urls.append(urlunparse(parsed._replace(path="/api/jobs", query=urlencode(query), fragment="")))
    return urls


def jibe_detail_url(careers_url, slug):
    return normalize_url(urljoin(careers_url.rstrip("/") + "/", storage.clean(str(slug))))


def jibe_text_value(value):
    if isinstance(value, list):
        return " ".join(jibe_text_value(item) for item in value)
    if isinstance(value, dict):
        return " ".join(jibe_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def jibe_categories(item):
    values = []
    for field in ["category", "categories"]:
        value = item.get(field)
        if isinstance(value, list):
            for entry in value:
                name = entry.get("name") if isinstance(entry, dict) else entry
                if storage.clean(str(name)):
                    values.append(storage.clean(str(name)))
        elif storage.clean(str(value or "")):
            values.append(storage.clean(str(value)))
    return ", ".join(dict.fromkeys(values))


def jibe_location(item):
    parts = []
    for field in ["full_location", "short_location", "location_name", "country", "location_type"]:
        value = storage.clean(str(item.get(field, "") or ""))
        if value:
            parts.append(value)
    return ", ".join(dict.fromkeys(parts))


def extract_jibe_candidates(payload, careers_url):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    candidates = []
    seen = set()
    for job in data.get("jobs", []):
        item = job.get("data", job) if isinstance(job, dict) else {}
        if not isinstance(item, dict):
            continue
        title = storage.clean(str(item.get("title", "") or ""))
        slug = storage.clean(str(item.get("slug") or item.get("req_id") or ""))
        if not title or not slug:
            continue
        url = jibe_detail_url(careers_url, slug)
        if not url or url in seen:
            continue
        seen.add(url)
        description = clean_html_text(
            " ".join(
                jibe_text_value(item.get(field))
                for field in ["description", "responsibilities", "qualifications", "tags2", "tags3", "tags4", "tags5", "tags6", "tags7"]
                if item.get(field)
            )
        )
        candidates.append(
            {
                "title": title,
                "url": url,
                "description": description,
                "location": jibe_location(item),
                "category": jibe_categories(item),
            }
        )
    return candidates


def is_phenom_page(page_html):
    text = page_html or ""
    return "phApp" in text and "eagerLoadRefineSearch" in text and "phenompeople.com" in text


def phenom_refine_search_payload(page_html):
    return decode_json_object_after(page_html, '"eagerLoadRefineSearch":')


def phenom_search_urls(careers_url, config=None):
    del config
    parsed = urlparse(careers_url)
    original_query = dict(parse_qsl(parsed.query, keep_blank_values=False))
    preserved = {
        key: value
        for key, value in original_query.items()
        if key.lower() not in {"keyword", "keywords", "q", "from", "s", "page"}
    }
    urls = []
    for term in resume_search_terms(fit_context_text()):
        for page_index in range(CAREERS_SEARCH_MAX_PAGES):
            query = {**preserved, "keywords": term}
            if page_index:
                query["from"] = str(page_index * 10)
            urls.append(urlunparse(parsed._replace(query=urlencode(query), fragment="")))
    return urls


def phenom_text_value(value):
    if isinstance(value, list):
        return " ".join(phenom_text_value(item) for item in value)
    if isinstance(value, dict):
        return " ".join(phenom_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def phenom_job_slug(title):
    slug = re.sub(r"[^a-z0-9]+", "-", storage.clean(title).lower()).strip("-")
    return slug or "job"


def phenom_detail_url(careers_url, job):
    job_seq_no = storage.clean(str(job.get("jobSeqNo") or job.get("jobId") or job.get("reqId") or ""))
    if job_seq_no:
        parsed = urlparse(careers_url)
        prefix = parsed.path.split("/search-results", 1)[0].rstrip("/")
        path = f"{prefix}/job/{job_seq_no}/{phenom_job_slug(job.get('title', ''))}"
        return normalize_url(urlunparse(parsed._replace(path=path, query="", fragment="")))
    return normalize_url(storage.clean(str(job.get("applyUrl", "") or "")))


def phenom_location(job):
    parts = []
    for field in ["location", "cityStateCountry", "cityState", "city", "state", "country", "checkRemote"]:
        value = storage.clean(str(job.get(field, "") or ""))
        if value:
            parts.append(value)
    for value in job.get("multi_location") or []:
        cleaned = storage.clean(str(value or ""))
        if cleaned:
            parts.append(cleaned)
    return ", ".join(dict.fromkeys(parts))


def phenom_category(job):
    parts = []
    for field in ["category", "externalTeamName", "type"]:
        value = storage.clean(str(job.get(field, "") or ""))
        if value:
            parts.append(value)
    for value in job.get("multi_category") or []:
        cleaned = storage.clean(str(value or ""))
        if cleaned:
            parts.append(cleaned)
    return ", ".join(dict.fromkeys(parts))


def extract_phenom_candidates(page_html, careers_url):
    payload = phenom_refine_search_payload(page_html)
    jobs = ((payload.get("data") or {}).get("jobs") or []) if isinstance(payload, dict) else []
    resume_text = fit_context_text()
    candidates = []
    seen = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title = storage.clean(str(job.get("title", "") or ""))
        url = phenom_detail_url(careers_url, job)
        if not title or not url or url in seen:
            continue
        seen.add(url)
        parser_data = job.get("ml_job_parser") if isinstance(job.get("ml_job_parser"), dict) else {}
        description = clean_html_text(
            " ".join(
                phenom_text_value(value)
                for value in [
                    job.get("descriptionTeaser"),
                    parser_data.get("descriptionTeaser"),
                    parser_data.get("descriptionTeaser_first200"),
                    parser_data.get("descriptionTeaser_keyword"),
                    parser_data.get("descriptionTeaser_ats"),
                    job.get("ml_skills"),
                ]
                if value
            )
        )
        candidate = {
            "title": title,
            "url": url,
            "description": description,
            "location": phenom_location(job),
            "category": phenom_category(job),
        }
        if resume_text and not candidate_matches_resume_role(candidate, resume_text):
            continue
        candidates.append(candidate)
    return candidates


def fetch_phenom_candidates(careers_url, fetch, config=None):
    return fetch_candidate_search_pages(
        phenom_search_urls(careers_url, config),
        fetch,
        lambda page_html, final_url: extract_phenom_candidates(page_html, final_url),
    )


def cdpr_jobs_data(page_html):
    match = re.search(r"window\.cdpData\.jobsData\s*=\s*", page_html or "")
    if not match:
        return []
    try:
        value, _ = json.JSONDecoder().raw_decode((page_html or "")[match.end() :].lstrip())
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def is_cdpr_jobs_page(page_html):
    return bool(cdpr_jobs_data(page_html))


def extract_cdpr_jobs_candidates(page_html):
    resume_text = fit_context_text()
    candidates = []
    for job in cdpr_jobs_data(page_html):
        if not isinstance(job, dict):
            continue
        title = storage.clean(str(job.get("name", "") or ""))
        url = normalize_url(job.get("applyUrl", ""))
        location = job.get("location") or {}
        category = job.get("category") or {}
        project = job.get("project") or {}
        candidate = {
            "title": title,
            "url": url,
            "location": storage.clean(str(location.get("name", "") if isinstance(location, dict) else location)),
            "work_mode": "Remote" if job.get("remote") else "On-site",
            "category": ", ".join(
                dict.fromkeys(
                    storage.clean(str(value.get("name", "") if isinstance(value, dict) else value))
                    for value in [category, project]
                    if storage.clean(str(value.get("name", "") if isinstance(value, dict) else value))
                )
            ),
            "source_job_id": storage.clean(str(job.get("id", "") or "")),
        }
        if not title or not url or not candidate_matches_resume_role(candidate, resume_text):
            continue
        candidates.append(candidate)
    return candidates


def fetch_cdpr_embedded_jobs_candidates(careers_url, fetch, config=None):
    del config
    fetched = fetch(careers_url)
    if fetched.get("error") or not fetched.get("html"):
        return [], 0, [f"{careers_url}: {fetched.get('error') or 'empty response'}"]
    return extract_cdpr_jobs_candidates(fetched.get("html", "")), 1, []


def react_router_enqueued_payloads(page_html):
    payloads = []
    pattern = re.compile(
        r'window\.__reactRouterContext\.streamController\.enqueue\(("(?:\\.|[^"\\])*")\)',
        re.S,
    )
    for match in pattern.finditer(page_html or ""):
        try:
            flattened = json.loads(json.loads(match.group(1)))
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(flattened, list):
            continue
        memo = {}

        def hydrate(index):
            if not isinstance(index, int) or index < 0 or index >= len(flattened):
                return None
            if index in memo:
                return memo[index]
            value = flattened[index]
            if isinstance(value, list):
                hydrated = []
                memo[index] = hydrated
                hydrated.extend(hydrate(item) for item in value)
                return hydrated
            if isinstance(value, dict):
                hydrated = {}
                memo[index] = hydrated
                for key_ref, value_ref in value.items():
                    if not isinstance(key_ref, str) or not re.fullmatch(r"_\d+", key_ref):
                        continue
                    key = hydrate(int(key_ref[1:]))
                    if key is not None:
                        hydrated[str(key)] = hydrate(value_ref)
                return hydrated
            memo[index] = value
            return value

        payload = hydrate(0)
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def shopify_job_rows(page_html):
    for payload in react_router_enqueued_payloads(page_html):
        loader_data = payload.get("loaderData") or {}
        if not isinstance(loader_data, dict):
            continue
        for route_data in loader_data.values():
            if not isinstance(route_data, dict):
                continue
            rows = route_data.get("jobPostingsWithJobs")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def is_shopify_careers_url(url):
    parsed = urlparse(url)
    return parsed.netloc.lower() == SHOPIFY_CAREERS_HOST and parsed.path.rstrip("/") == "/careers"


def shopify_job_url(careers_url, posting):
    title_slug = re.sub(r"[^a-z0-9]+", "-", storage.clean(posting.get("title", "")).lower()).strip("-")
    posting_id = storage.clean(str(posting.get("id", "") or ""))
    parsed = urlparse(careers_url)
    return normalize_url(
        urlunparse(parsed._replace(path=f"/careers/{title_slug}_{posting_id}", query="", fragment=""))
    )


def extract_shopify_candidates(page_html, careers_url):
    resume_text = fit_context_text()
    candidates = []
    for row in shopify_job_rows(page_html):
        posting = row.get("jobPosting") if isinstance(row.get("jobPosting"), dict) else {}
        job = row.get("job") if isinstance(row.get("job"), dict) else {}
        title = storage.clean(str(posting.get("title", "") or ""))
        if (
            not title
            or storage.clean(posting.get("status", "")).lower() != "published"
            or posting.get("isListed") is False
            or storage.clean(job.get("status", "")).lower() not in {"", "open"}
        ):
            continue
        custom_fields = job.get("customFields") if isinstance(job.get("customFields"), list) else []
        category = ", ".join(
            dict.fromkeys(
                storage.clean(str(value or ""))
                for value in [
                    posting.get("teamName"),
                    *[
                        field.get("valueLabel") or field.get("value")
                        for field in custom_fields
                        if isinstance(field, dict)
                        and storage.clean(field.get("title", "")).lower() in {"discipline", "subdiscipline", "track"}
                    ],
                ]
                if storage.clean(str(value or ""))
            )
        )
        candidate = {
            "title": title,
            "url": shopify_job_url(careers_url, posting),
            "location": storage.clean(
                str(posting.get("locationExternalName", "") or posting.get("locationName", "") or "")
            ),
            "work_mode": storage.clean(str(posting.get("workplaceType", "") or "")),
            "category": category,
            "source_job_id": storage.clean(str(posting.get("id", "") or "")),
            "search_text": " ".join(
                storage.clean(str(value or ""))
                for value in [job.get("title"), category, posting.get("publishedDate")]
                if storage.clean(str(value or ""))
            ),
        }
        if candidate_matches_resume_role(candidate, resume_text):
            candidates.append(candidate)
    return candidates


def fetch_shopify_embedded_jobs_candidates(careers_url, fetch, config=None):
    del config
    fetched = fetch(careers_url)
    if fetched.get("error") or not fetched.get("html"):
        return [], 0, [f"{careers_url}: {fetched.get('error') or 'empty response'}"]
    final_url = fetched.get("final_url") or careers_url
    return extract_shopify_candidates(fetched.get("html", ""), final_url), 1, []


def embedded_json_payloads(page_html):
    payloads = []
    for match in re.finditer(r'data-props=["\'](.*?)["\']', page_html or "", re.S):
        try:
            payload = json.loads(html.unescape(match.group(1)))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def embedded_json_jobs_payload(page_html):
    for payload in embedded_json_payloads(page_html):
        jobs = payload.get("jobs")
        if isinstance(jobs, list) and any(isinstance(job, dict) and job.get("title") for job in jobs):
            return payload
    return {}


def is_embedded_json_jobs_page(page_html):
    return bool(embedded_json_jobs_payload(page_html))


def embedded_json_job_detail_url(careers_url, job):
    raw_url = storage.clean(str(job.get("url", "") or ""))
    if not raw_url:
        return ""
    parsed_raw = urlparse(raw_url)
    if parsed_raw.scheme and parsed_raw.netloc:
        return normalize_url(raw_url)
    parsed_base = urlparse(careers_url)
    if parsed_raw.path.startswith("/j/"):
        job_id = parsed_raw.path.rstrip("/").rsplit("/", 1)[-1]
        base_path = parsed_base.path.rstrip("/")
        if base_path.endswith("/jobs"):
            detail_prefix = base_path.rsplit("/", 1)[0] + "/job"
        else:
            detail_prefix = base_path.rstrip("/") + "/job"
        return normalize_url(urlunparse(parsed_base._replace(path=f"{detail_prefix}/{job_id}", query="", fragment="")))
    return normalize_url(urljoin(careers_url.rstrip("/") + "/", raw_url))


def embedded_json_text_value(value):
    if isinstance(value, list):
        return " ".join(embedded_json_text_value(item) for item in value)
    if isinstance(value, dict):
        return " ".join(embedded_json_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def embedded_json_location(job):
    parts = []
    for field in ["office", "location", "city", "country"]:
        value = storage.clean(str(job.get(field, "") or ""))
        if value:
            parts.append(value)
    for value in job.get("additionalOfficeNames") or []:
        cleaned = storage.clean(str(value or ""))
        if cleaned:
            parts.append(cleaned)
    return ", ".join(dict.fromkeys(parts))


def embedded_json_category(job):
    parts = []
    for field in ["products", "craft", "discipline", "subDiscipline"]:
        value = storage.clean(str(job.get(field, "") or ""))
        if value:
            parts.append(value)
    return ", ".join(dict.fromkeys(parts))


def extract_embedded_json_jobs_candidates(page_html, careers_url):
    payload = embedded_json_jobs_payload(page_html)
    jobs = payload.get("jobs") if isinstance(payload, dict) else []
    resume_text = fit_context_text()
    candidates = []
    seen = set()
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        title = storage.clean(str(job.get("title", "") or ""))
        url = embedded_json_job_detail_url(careers_url, job)
        if not title or not url or url in seen:
            continue
        seen.add(url)
        description = clean_html_text(
            " ".join(
                embedded_json_text_value(job.get(field))
                for field in ["products", "craft", "discipline", "subDiscipline", "internalId"]
                if job.get(field)
            )
        )
        candidate = {
            "title": title,
            "url": url,
            "description": description,
            "location": embedded_json_location(job),
            "category": embedded_json_category(job),
        }
        if resume_text and not candidate_matches_resume_role(candidate, resume_text):
            continue
        candidates.append(candidate)
    return candidates


def fetch_embedded_json_jobs_candidates(careers_url, fetch, config=None):
    del config
    fetched = fetch(careers_url)
    if fetched.get("error") or not fetched.get("html"):
        return [], 0, [f"{careers_url}: {fetched.get('error') or 'empty response'}"]
    final_url = fetched.get("final_url") or careers_url
    return extract_embedded_json_jobs_candidates(fetched.get("html", ""), final_url), 1, []


def endpoint_json_jobs_url(careers_url, config=None):
    config = config or {}
    endpoint = storage.clean(config.get("endpoint_url", ""))
    if endpoint:
        return normalize_url(urljoin(careers_url, endpoint))
    return ""


def endpoint_json_text_value(value):
    if isinstance(value, list):
        return " ".join(endpoint_json_text_value(item) for item in value)
    if isinstance(value, dict):
        return " ".join(endpoint_json_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def endpoint_json_job_url(job):
    portal = job.get("portalJobPost") if isinstance(job.get("portalJobPost"), dict) else {}
    for value in [portal.get("portalUrl"), job.get("url"), job.get("detailUrl"), job.get("applyUrl")]:
        raw_url = storage.clean(str(value or ""))
        if raw_url:
            return normalize_url(raw_url)
    return ""


def endpoint_json_location(job):
    parts = []
    for field in ["location", "locations", "city", "state", "country"]:
        value = job.get(field)
        if isinstance(value, list):
            parts.extend(storage.clean(str(item or "")) for item in value)
        else:
            parts.append(storage.clean(str(value or "")))
    return ", ".join(dict.fromkeys(part for part in parts if part))


def endpoint_json_category(job):
    parts = []
    for field in ["category", "team", "department", "type"]:
        value = job.get(field)
        if isinstance(value, list):
            parts.extend(storage.clean(str(item or "")) for item in value)
        else:
            parts.append(storage.clean(str(value or "")))
    return ", ".join(dict.fromkeys(part for part in parts if part))


def extract_endpoint_json_jobs_candidates(payload, careers_url, config=None):
    del careers_url, config
    try:
        data = json.loads(payload or "[]")
    except json.JSONDecodeError:
        return []
    jobs = data if isinstance(data, list) else data.get("jobs", [])
    if not isinstance(jobs, list):
        return []
    resume_text = fit_context_text()
    candidates = []
    seen = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title = storage.clean(str(job.get("title", "") or ""))
        url = endpoint_json_job_url(job)
        if not title or not url or url in seen:
            continue
        seen.add(url)
        description = clean_html_text(
            " ".join(
                endpoint_json_text_value(job.get(field))
                for field in ["overview", "description", "responsibilities", "qualifications", "summary"]
                if job.get(field)
            )
        )
        candidate = {
            "title": title,
            "url": url,
            "description": description,
            "location": endpoint_json_location(job),
            "category": endpoint_json_category(job),
            "search_text": endpoint_json_text_value(job.get("portalJobPost")),
        }
        if resume_text and not candidate_matches_resume_role(candidate, resume_text):
            continue
        candidates.append(candidate)
    return candidates


def fetch_endpoint_json_jobs_candidates(careers_url, fetch, config=None):
    config = config or {}
    endpoint_url = endpoint_json_jobs_url(careers_url, config)
    if not endpoint_url:
        return [], 0, [f"{careers_url}: missing JSON jobs endpoint"]
    fetched = fetch_with_optional_headers(
        fetch,
        endpoint_url,
        headers={"Accept": "application/json, text/plain, */*", "Referer": careers_url},
    )
    if fetched.get("error") or not fetched.get("html"):
        return [], 0, [f"{endpoint_url}: {fetched.get('error') or 'empty response'}"]
    return extract_endpoint_json_jobs_candidates(fetched.get("html", ""), careers_url, config), 1, []


def fetch_static_json_careers_candidates(careers_url, fetch, config=None):
    config = config or {}
    index_url = storage.clean(config.get("index_url", ""))
    if not index_url:
        parsed = urlparse(careers_url)
        index_url = f"{parsed.scheme or 'https'}://{parsed.netloc.lower()}/data/careers.json"
    fetched = fetch_with_optional_headers(fetch, index_url, headers={"Accept": "application/json"})
    if fetched.get("error") or not fetched.get("html"):
        return [], 0, [f"{index_url}: {fetched.get('error') or 'empty response'}"]
    return extract_static_json_careers_candidates(fetched.get("html", ""), config or static_json_careers_config(index_url), fetch), 1, []


def is_endpoint_json_jobs_page(page_html):
    text = page_html or ""
    return bool(
        re.search(r'["\']type["\']\s*:\s*["\']Careers["\']', text)
        and "imkt-jsx--careers" in text
    )


def discover_endpoint_json_jobs_source(company, page_html, final_url, fetch):
    if not is_endpoint_json_jobs_page(page_html):
        return None
    endpoint_url = normalize_url(urljoin(final_url, "/endpoint/careers/listings"))
    fetched = fetch_with_optional_headers(
        fetch,
        endpoint_url,
        headers={"Accept": "application/json, text/plain, */*", "Referer": final_url},
    )
    if fetched.get("error") or not fetched.get("html"):
        return None
    extracted = extract_endpoint_json_jobs_candidates(fetched.get("html", ""), final_url, {"endpoint_url": endpoint_url})
    if not extracted:
        try:
            data = json.loads(fetched.get("html", "") or "[]")
        except json.JSONDecodeError:
            return None
        if not isinstance(data, list) or not any(isinstance(item, dict) and item.get("title") for item in data):
            return None
    return save_company_career_source(
        company.get("id", ""),
        company.get("careers_url", ""),
        "endpoint_json_jobs",
        {"endpoint_url": endpoint_url},
        ["Detected JSON careers listings endpoint from careers page component."],
        status="verified",
    )


def static_json_careers_index_urls(page_html, final_url, fetch):
    urls = [normalize_url(urljoin(final_url, "/data/careers.json"))]
    if "/data/careers.json" in (page_html or ""):
        urls.append(normalize_url(urljoin(final_url, "/data/careers.json")))
    for script_url in discover_script_urls(page_html, final_url):
        if "/js/" not in urlparse(script_url).path:
            continue
        fetched = fetch(script_url)
        if fetched.get("error") or not fetched.get("html"):
            continue
        if "/data/careers.json" in fetched.get("html", ""):
            urls.append(normalize_url(urljoin(final_url, "/data/careers.json")))
    return list(dict.fromkeys(url for url in urls if url))


def static_json_careers_config(index_url):
    parsed = urlparse(index_url)
    origin = f"{parsed.scheme or 'https'}://{parsed.netloc.lower()}"
    return {
        "index_url": normalize_url(index_url),
        "detail_url_template": f"{origin}/data/careers/{{slug}}.json",
        "posting_url_template": f"{origin}/careers/{{category}}/{{slug}}",
    }


def static_json_careers_text(value):
    if isinstance(value, list):
        return " ".join(static_json_careers_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(static_json_careers_text(item) for item in value.values())
    return storage.clean(str(value or ""))


def extract_static_json_careers_rows(payload):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    positions = data.get("positions") or {}
    if not isinstance(positions, dict):
        return []
    rows = []
    for category_key, category in positions.items():
        if not isinstance(category, dict):
            continue
        jobs = category.get("jobs") or []
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            if not isinstance(job, dict):
                continue
            title = storage.clean(str(job.get("position", "") or job.get("title", "") or ""))
            slug = storage.clean(str(job.get("slug", "") or ""))
            if not title or not slug:
                continue
            item = dict(job)
            item.setdefault("category", category_key)
            item.setdefault("category_title", storage.clean(str(category.get("title", "") or category_key)))
            rows.append(item)
    return rows


def static_json_careers_url(config, key, **values):
    template = storage.clean(config.get(key, ""))
    if not template:
        return ""
    try:
        return normalize_url(template.format(**values))
    except (KeyError, ValueError):
        return ""


def static_json_candidate_from_row(row, config):
    title = storage.clean(str(row.get("position", "") or row.get("title", "") or ""))
    slug = storage.clean(str(row.get("slug", "") or ""))
    category = storage.clean(str(row.get("category", "") or ""))
    if not title or not slug:
        return None
    url = static_json_careers_url(config, "posting_url_template", category=category, slug=slug)
    detail_url = static_json_careers_url(config, "detail_url_template", category=category, slug=slug)
    if not url:
        return None
    return {
        "title": title,
        "url": url,
        "description": "",
        "location": "",
        "category": storage.clean(str(row.get("category_title", "") or category)),
        "search_text": " ".join(
            storage.clean(str(row.get(field, "") or ""))
            for field in ["id", "type", "date"]
            if storage.clean(str(row.get(field, "") or ""))
        ),
        "_detail_url": detail_url,
    }


def enrich_static_json_careers_candidate(candidate, fetch):
    detail_url = candidate.pop("_detail_url", "")
    if not detail_url:
        return candidate
    fetched = fetch_with_optional_headers(fetch, detail_url, headers={"Accept": "application/json"})
    if fetched.get("error") or not fetched.get("html"):
        return candidate
    try:
        data = json.loads(fetched.get("html", "") or "{}")
    except json.JSONDecodeError:
        return candidate
    if data.get("error"):
        return candidate
    enriched = dict(candidate)
    title = storage.clean(str(data.get("position", "") or ""))
    if title:
        enriched["title"] = title
    description = clean_html_text(
        " ".join(
            [
                static_json_careers_text(data.get("subtitle")),
                static_json_careers_text(data.get("details")),
                static_json_careers_text(data.get("content")),
            ]
        )
    )
    if description:
        enriched["description"] = description
    enriched["category"] = storage.clean(str(data.get("category", "") or enriched.get("category", "")))
    enriched["search_text"] = " ".join(
        part
        for part in [
            enriched.get("search_text", ""),
            static_json_careers_text(data.get("type")),
            static_json_careers_text(data.get("date")),
        ]
        if part
    )
    return enriched


def extract_static_json_careers_candidates(payload, config, fetch):
    candidates = []
    seen = set()
    for row in extract_static_json_careers_rows(payload):
        candidate = static_json_candidate_from_row(row, config)
        if not candidate or candidate["url"] in seen:
            continue
        seen.add(candidate["url"])
        candidates.append(enrich_static_json_careers_candidate(candidate, fetch))
    return candidates


def discover_static_json_careers_source(company, page_html, final_url, fetch):
    for index_url in static_json_careers_index_urls(page_html, final_url, fetch):
        fetched = fetch_with_optional_headers(fetch, index_url, headers={"Accept": "application/json"})
        if fetched.get("error") or not fetched.get("html"):
            continue
        rows = extract_static_json_careers_rows(fetched.get("html", ""))
        if not rows:
            continue
        return save_company_career_source(
            company.get("id", ""),
            company.get("careers_url", ""),
            "static_json_careers",
            static_json_careers_config(index_url),
            [
                "Detected static JSON careers feed referenced by the careers app.",
                f"Careers JSON feed returned {len(rows)} posting row(s).",
            ],
            status="verified",
        )
    return None


def extract_window_preloaded_state(page_html):
    match = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>", page_html, re.S)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def discover_algolia_source_config(page_html):
    state = extract_window_preloaded_state(page_html)
    modules = state.get("configuration", {}).get("modules", {}) if isinstance(state.get("configuration"), dict) else {}
    algolia_config = modules.get("dm-AlgoliaSearch", {}) if isinstance(modules, dict) else {}
    app_id = storage.clean(str(algolia_config.get("AlgoliaAppId", "") or ""))
    api_key = storage.clean(str(algolia_config.get("AlgoliaApiKey", "") or ""))
    if not app_id or not api_key:
        return {}

    locale = storage.clean(str(state.get("language", {}).get("locale", "") or "en-us")) if isinstance(state.get("language"), dict) else "en-us"
    index_names = []
    for pattern in [
        rf"\bjobs_{re.escape(locale)}_default\b",
        r"\bjobs_[a-z]{2}-[a-z]{2}_default\b",
    ]:
        for index_name in re.findall(pattern, page_html):
            if index_name not in index_names:
                index_names.append(index_name)
    if not index_names:
        index_names.append(f"jobs_{locale}_default")

    return {
        "app_id": app_id,
        "api_key": api_key,
        "index_name": index_names[0],
        "queries": resume_search_terms(fit_context_text()),
        "hits_per_page": 10,
        "max_queries": CAREERS_SEARCH_MAX_TERMS,
    }


def is_algolia_jobs_page(page_html):
    return bool(discover_algolia_source_config(page_html))


def algolia_headers(config):
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Algolia-API-Key": storage.clean(str(config.get("api_key", "") or "")),
        "X-Algolia-Application-Id": storage.clean(str(config.get("app_id", "") or "")),
    }


def algolia_query_url(config):
    app_id = storage.clean(str(config.get("app_id", "") or ""))
    index_name = storage.clean(str(config.get("index_name", "") or ""))
    if not app_id or not index_name:
        return ""
    return f"https://{app_id}-dsn.algolia.net/1/indexes/{index_name}/query"


def algolia_hit_url(hit):
    for field in ["link", "url", "applyUrl", "referralUrl"]:
        value = storage.clean(str(hit.get(field, "") or ""))
        if value.startswith("http://") or value.startswith("https://"):
            return normalize_url(value)
    slug = storage.clean(str(hit.get("slug", "") or ""))
    if slug:
        return normalize_url(f"https://jobs.smartrecruiters.com/Ubisoft2/{slug}")
    return ""


def algolia_text_value(value):
    if isinstance(value, list):
        return " ".join(algolia_text_value(item) for item in value)
    if isinstance(value, dict):
        return " ".join(algolia_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def extract_algolia_jobs_candidates(payload, careers_url):
    del careers_url
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    hits = data.get("hits") if isinstance(data, dict) else []
    if not isinstance(hits, list):
        return []
    candidates = []
    resume_text = fit_context_text()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        title = storage.clean(str(hit.get("title", "") or ""))
        url = algolia_hit_url(hit)
        if not title or not url:
            continue
        description = clean_html_text(
            " ".join(
                algolia_text_value(hit.get(field))
                for field in [
                    "description",
                    "qualifications",
                    "additionalInformation",
                    "jobFamily",
                    "team",
                    "department",
                    "experienceLevel",
                    "contractType",
                ]
                if hit.get(field)
            )
        )
        location = ", ".join(
            dict.fromkeys(
                storage.clean(str(value or ""))
                for value in [hit.get("city"), *(hit.get("cities") or []), hit.get("countryCode")]
                if storage.clean(str(value or ""))
            )
        )
        category = ", ".join(
            dict.fromkeys(
                storage.clean(str(value or ""))
                for value in [hit.get("jobFamily"), hit.get("team"), hit.get("department"), hit.get("contractType")]
                if storage.clean(str(value or ""))
            )
        )
        candidate = {
            "title": title,
            "url": url,
            "description": description,
            "location": location,
            "category": category,
        }
        if resume_text and not candidate_matches_resume_role(candidate, resume_text):
            continue
        candidates.append(candidate)
    return candidates


def fetch_algolia_jobs_candidates(careers_url, fetch, config=None):
    config = config or {}
    url = algolia_query_url(config)
    if not url:
        return [], 0, ["Algolia config is missing app_id or index_name"]
    current_queries = resume_search_terms(fit_context_text())
    queries = [
        storage.clean(str(query))
        for query in [*(config.get("queries") or []), *current_queries]
        if storage.clean(str(query))
    ]
    queries = list(dict.fromkeys(queries))
    try:
        max_queries = max(CAREERS_SEARCH_MAX_TERMS, int(config.get("max_queries") or CAREERS_SEARCH_MAX_TERMS))
    except (TypeError, ValueError):
        max_queries = CAREERS_SEARCH_MAX_TERMS
    queries = queries[:max_queries]
    if not queries:
        queries = resume_search_terms(fit_context_text())

    extracted = []
    searched = 0
    errors = []
    for query in queries:
        params = {
            "query": query,
            "hitsPerPage": int(config.get("hits_per_page") or 10),
            "page": 0,
        }
        filters = storage.clean(str(config.get("filters", "") or ""))
        if filters:
            params["filters"] = filters
        body = {"params": urlencode(params)}
        fetched = fetch_with_options(fetch, url, headers=algolia_headers(config), method="POST", data=body)
        if fetched.get("error") or not fetched.get("html"):
            errors.append(f"{url}: {fetched.get('error') or 'empty response'}")
            continue
        searched += 1
        for candidate in extract_algolia_jobs_candidates(fetched.get("html", ""), careers_url):
            candidate["matched_queries"] = query
            extracted.append(candidate)
    return extracted, searched, errors


def search_query_label(url):
    query = dict(parse_qsl(urlparse(url).query, keep_blank_values=False))
    for key in ["q", "base_query", "query", "keywords", "keyword", "searchText"]:
        value = storage.clean(str(query.get(key, "") or ""))
        if value:
            return value
    return ""


def fetch_candidate_search_pages(urls, fetch, extractor, headers=None):
    extracted = []
    searched = 0
    errors = []
    for url in urls:
        fetched = fetch_with_optional_headers(fetch, url, headers=headers)
        if fetched.get("error") or not fetched.get("html"):
            errors.append(f"{url}: {fetched.get('error') or 'empty response'}")
            continue
        searched += 1
        final_url = fetched.get("final_url") or url
        query_label = search_query_label(url)
        for candidate in extractor(fetched.get("html", ""), final_url):
            if query_label:
                candidate["matched_queries"] = ", ".join(
                    split_list_text([*split_list_text(candidate.get("matched_queries", "")), query_label])
                )
            extracted.append(candidate)
    return extracted, searched, errors


def fetch_google_careers_candidates(careers_url, fetch, config=None):
    del config
    return fetch_candidate_search_pages(
        google_careers_search_urls(careers_url),
        fetch,
        extract_google_careers_candidates,
    )


def fetch_amazon_jobs_candidates(careers_url, fetch, config=None):
    config = config or {}
    return fetch_candidate_search_pages(
        amazon_jobs_search_urls(careers_url, config),
        fetch,
        lambda payload, _final_url: extract_amazon_jobs_candidates(payload, careers_url),
        headers={"Accept": "application/json"},
    )


def fetch_eightfold_pcs_candidates(careers_url, fetch, config=None):
    config = config or {}
    return fetch_candidate_search_pages(
        eightfold_pcs_search_urls(careers_url, config),
        fetch,
        lambda payload, _final_url: extract_eightfold_pcs_candidates(payload, careers_url, config),
        headers=eightfold_pcs_headers(careers_url),
    )


def fetch_eightfold_smartapply_candidates(careers_url, fetch, config=None):
    config = config or {}
    return fetch_candidate_search_pages(
        eightfold_smartapply_search_urls(careers_url, config),
        fetch,
        lambda payload, _final_url: extract_eightfold_smartapply_candidates(payload, careers_url, config),
        headers=eightfold_smartapply_headers(careers_url),
    )


def blocked_career_message(careers_url, config=None):
    reason = storage.clean((config or {}).get("reason", ""))
    if reason == "aws_waf_javascript_challenge" or is_avature_url(careers_url):
        return avature_blocked_message(careers_url)
    return (
        f"{careers_url}: the official careers site returned a browser verification challenge. "
        "The direct non-browser careers checker cannot access its posting search."
    )


def fetch_official_web_search_candidates(careers_url, fetch, config=None):
    del fetch
    config = config or {}
    from . import agent, api_usage

    search_terms = resume_search_terms("")
    if not search_terms:
        return [], 0, [blocked_career_message(careers_url, config)]
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "jobs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "location": {"type": "string"},
                        "work_mode": {"type": "string"},
                        "source_job_id": {"type": "string"},
                        "freshness_evidence": {"type": "string"},
                    },
                    "required": [
                        "title",
                        "url",
                        "location",
                        "work_mode",
                        "source_job_id",
                        "freshness_evidence",
                    ],
                },
            }
        },
        "required": ["jobs"],
    }
    prompt = (
        "Find current open jobs for the employer represented by this official careers site: "
        f"{careers_url}. Match any of these role families: {', '.join(search_terms)}. Search current public web "
        "evidence, including reputable job indexes when the official careers site is blocked, but return only direct "
        f"official job-detail URLs on {urlparse(careers_url).netloc}. The URL must be a specific job detail, not a "
        "search page. A role is current only when it is linked from the current official search-results page, or an "
        "authoritative source shows it was posted within the last 180 days. A detail page that merely still resolves "
        "or shows an Apply button is not enough. Exclude roles older than 180 days, expired, closed, cached-only, and "
        "third-party application URLs. Return no more than 25 jobs. Leave unknown location or work mode blank. "
        "source_job_id must be the job or requisition identifier from the official URL. freshness_evidence must briefly "
        "state the current official listing or recent posted date used; return no job without freshness evidence."
    )
    payload = {
        "model": CAREER_SEARCH_MODEL,
        "input": prompt,
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "tool_choice": "required",
        "max_tool_calls": 5,
        "include": ["web_search_call.action.sources"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "hunter_official_career_search",
                "strict": True,
                "schema": schema,
            }
        },
        "max_output_tokens": 3_000,
        "reasoning": {"effort": "low"},
        "store": False,
        "metadata": {"feature": "career-search", "source": "official-site-waf-fallback"},
    }
    try:
        openai_config = agent._settings()
        response = agent._request_json(
            f"{openai_config['api_base']}/responses",
            openai_config["token"],
            payload,
        )
        api_usage.log_usage(
            "career-search",
            response.get("model") or CAREER_SEARCH_MODEL,
            response,
            operation="official-site-waf-fallback",
            context={
                "run_id": config.get("_run_id", ""),
                "company_id": config.get("_company_id", ""),
            },
        )
        result = json.loads(agent._output_text(response))
    except (ValueError, RuntimeError, URLError, TypeError, json.JSONDecodeError) as exc:
        return [], 0, [f"{blocked_career_message(careers_url, config)} OpenAI fallback unavailable: {exc}"]

    expected_host = urlparse(careers_url).netloc.lower()
    candidates = []
    seen = set()
    for item in result.get("jobs", []) if isinstance(result, dict) else []:
        if not isinstance(item, dict):
            continue
        url = normalize_url(item.get("url", ""))
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=False))
        source_job_id = storage.clean(
            str(item.get("source_job_id", "") or query.get("jobId", "") or candidate_source_job_id(url))
        )
        if (
            not storage.clean(item.get("title", ""))
            or parsed.netloc.lower() != expected_host
            or not JOB_URL_PATTERN.search(f"{parsed.path}?{parsed.query}")
            or not source_job_id
            or not storage.clean(item.get("freshness_evidence", ""))
            or url in seen
        ):
            continue
        seen.add(url)
        candidates.append(
            {
                "title": storage.clean(item.get("title", "")),
                "url": url,
                "location": storage.clean(item.get("location", "")),
                "work_mode": storage.clean(item.get("work_mode", "")),
                "source_job_id": source_job_id,
                "matched_queries": ", ".join(search_terms),
            }
        )
    if not candidates:
        return [], 0, [f"{blocked_career_message(careers_url, config)} OpenAI fallback found no verified current jobs."]
    return candidates, 1, []


# Keep previously saved Avature sources executable while they are migrated by
# current_company_career_source on their next check.
fetch_avature_waf_blocked_candidates = fetch_official_web_search_candidates
fetch_avature_web_search_candidates = fetch_official_web_search_candidates


def fetch_next_static_jobs_candidates(careers_url, fetch, config=None):
    config = config or {}
    fetched = fetch(careers_url)
    if fetched.get("error") or not fetched.get("html"):
        return [], 0, [f"{careers_url}: {fetched.get('error') or 'empty response'}"]
    final_url = fetched.get("final_url") or careers_url
    return extract_next_static_jobs_candidates(fetched.get("html", ""), final_url, config), 1, []


def fetch_greenhouse_board_candidates(careers_url, fetch, config=None):
    config = config or {}
    raw_board_tokens = config.get("board_tokens", [])
    if isinstance(raw_board_tokens, str):
        raw_board_tokens = [raw_board_tokens]
    if not isinstance(raw_board_tokens, list):
        raw_board_tokens = []
    board_tokens = [
        storage.clean(str(token))
        for token in raw_board_tokens
        if storage.clean(str(token))
    ]
    if board_tokens:
        candidates = []
        seen = set()
        searched = 0
        errors = []
        for token in board_tokens:
            single_config = {
                key: value
                for key, value in config.items()
                if key not in {"board_tokens", "board_urls", "jobs_api_urls", "department_ids", "department_names"}
            }
            single_config.update(
                {
                    "board_token": token,
                    "board_url": greenhouse_board_url_for_token(token),
                    "jobs_api_url": greenhouse_api_url(token, "jobs?content=true"),
                }
            )
            board_candidates, board_searched, board_errors = fetch_greenhouse_board_candidates(
                careers_url,
                fetch,
                single_config,
            )
            searched += board_searched
            errors.extend(board_errors)
            for candidate in board_candidates:
                url = candidate.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    candidates.append(candidate)
        return candidates, searched, errors

    token = storage.clean(config.get("board_token", "")) or greenhouse_board_token(careers_url)
    if not token:
        return [], 0, [f"{careers_url}: missing Greenhouse board token"]
    config = {
        **config,
        "board_token": token,
        "board_url": storage.clean(config.get("board_url", "")) or greenhouse_board_url(careers_url),
    }
    department_ids = {storage.clean(str(item)) for item in config.get("department_ids", []) if storage.clean(str(item))}
    if department_ids:
        departments_url = storage.clean(config.get("departments_api_url", "")) or greenhouse_api_url(token, "departments")
        fetched = fetch_with_optional_headers(fetch, departments_url, headers=greenhouse_headers())
        if fetched.get("error") or not fetched.get("html"):
            return [], 0, [f"{departments_url}: {fetched.get('error') or 'empty response'}"]
        jobs = []
        for department in extract_greenhouse_departments(fetched.get("html", "")):
            if storage.clean(str(department.get("id", ""))) in department_ids:
                jobs.extend(job for job in department.get("jobs", []) if isinstance(job, dict))
        hydrated = fetch_greenhouse_detail_for_jobs(jobs, fetch, config)
        return greenhouse_candidates_from_jobs(hydrated, config), 1, []

    jobs_url = storage.clean(config.get("jobs_api_url", "")) or greenhouse_api_url(token, "jobs?content=true")
    fetched = fetch_with_optional_headers(fetch, jobs_url, headers=greenhouse_headers())
    if fetched.get("error") or not fetched.get("html"):
        return [], 0, [f"{jobs_url}: {fetched.get('error') or 'empty response'}"]
    return extract_greenhouse_jobs_candidates(fetched.get("html", ""), config), 1, []


def fetch_custom_workday_api_candidates(careers_url, fetch, config=None):
    config = config or {}
    return fetch_candidate_search_pages(
        custom_workday_search_urls(careers_url, config),
        fetch,
        lambda payload, _final_url: extract_custom_workday_candidates(payload, careers_url, config),
        headers=custom_workday_headers(config),
    )


def fetch_workday_cxs_candidates(careers_url, fetch, config=None):
    del careers_url
    config = config or {}
    jobs_url = storage.clean(config.get("jobs_url", ""))
    if not jobs_url:
        return [], 0, ["Workday CXS config is missing jobs_url"]
    extracted = []
    searched = 0
    errors = []
    seen = set()
    resume_text = fit_context_text()
    for payload in workday_cxs_search_payloads(config):
        fetched = fetch_with_options(fetch, jobs_url, headers=workday_cxs_headers(), method="POST", data=payload)
        if fetched.get("error") or not fetched.get("html"):
            errors.append(f"{jobs_url}: {fetched.get('error') or 'empty response'}")
            continue
        searched += 1
        for candidate in extract_workday_cxs_candidates(fetched.get("html", ""), config):
            if candidate["url"] in seen:
                continue
            if not candidate_matches_resume_role(candidate, resume_text):
                continue
            seen.add(candidate["url"])
            enriched = enrich_workday_cxs_candidate(candidate, fetch)
            enriched["matched_queries"] = storage.clean(str(payload.get("searchText", "") or ""))
            extracted.append(enriched)
    return extracted, searched, errors


def fetch_ashby_candidates(careers_url, fetch, config=None):
    config = config or {}
    ashby_url = storage.clean(config.get("board_url", "")) or ashby_board_url(careers_url)
    if not ashby_url:
        return [], 0, []
    fetched = fetch(ashby_url)
    if not fetched.get("error") and fetched.get("html"):
        final_url = fetched.get("final_url") or ashby_url
        return extract_ashby_candidates(fetched.get("html", ""), final_url), 1, []
    if is_ashby_jobs_url(careers_url):
        return [], 0, [f"{ashby_url}: {fetched.get('error') or 'empty response'}"]
    return [], 0, []


def fetch_jibe_candidates_from_source(careers_url, fetch, config=None):
    del config
    extracted, searched, errors = fetch_candidate_search_pages(
        jibe_search_urls(careers_url),
        fetch,
        lambda payload, _final_url: extract_jibe_candidates(payload, careers_url),
    )
    return extracted, searched, errors


def target_candidate_location(document):
    job_address = storage.clean(str(document.get("jobaddress", "") or ""))
    if job_address:
        return job_address
    return ", ".join(
        dict.fromkeys(
            storage.clean(str(value or ""))
            for value in [
                document.get("city"),
                document.get("stateabbreviated") or document.get("state"),
                document.get("country"),
            ]
            if storage.clean(str(value or ""))
        )
    )


def extract_target_jobsearch_candidates(payload, careers_url):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return []
    results = data.get("results") or []
    if not isinstance(results, list):
        return []

    candidates = []
    for result in results:
        document = result.get("document") if isinstance(result, dict) else None
        if not isinstance(document, dict):
            continue
        title = storage.clean(str(document.get("title", "") or ""))
        url = normalize_url(urljoin(careers_url, storage.clean(str(document.get("url", "") or ""))))
        if not title or not is_target_job_url(url):
            continue
        remote_type = storage.clean(str(document.get("remotetype", "") or ""))
        categories = document.get("jobcategories") or []
        if not isinstance(categories, list):
            categories = [categories]
        category = ", ".join(
            dict.fromkeys(
                storage.clean(str(value or ""))
                for value in [document.get("jobarea"), document.get("jobfamily"), *categories]
                if storage.clean(str(value or ""))
            )
        )
        skills = document.get("jobskills") or []
        if not isinstance(skills, list):
            skills = [skills]
        description = " ".join(
            value
            for value in [category, " ".join(storage.clean(str(skill or "")) for skill in skills)]
            if value
        )
        candidates.append(
            {
                "title": title,
                "url": url,
                "location": target_candidate_location(document),
                "work_mode": remote_type,
                "category": category,
                "description": description,
                "source_job_id": storage.clean(
                    str(document.get("requisitionid", "") or document.get("postingid", "") or "")
                ),
            }
        )
    return candidates


def fetch_target_jobsearch_candidates(careers_url, fetch, config=None):
    config = config or {}
    api_url = storage.clean(str(config.get("api_url", "") or "")) or target_job_api_url(careers_url)
    queries = [
        storage.clean(str(query))
        for query in [*(config.get("queries") or []), *resume_search_terms(fit_context_text())]
        if storage.clean(str(query))
    ]
    queries = list(dict.fromkeys(queries))[:CAREERS_SEARCH_MAX_TERMS]
    if not queries:
        queries = ["technical program manager", "program manager"]

    extracted = []
    searched = 0
    errors = []
    resume_text = fit_context_text()
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    for query in queries:
        request_data = urlencode({"currentPage": 1, "q": query, "culture": "en-us"}).encode("utf-8")
        fetched = fetch_with_options(fetch, api_url, headers=headers, method="POST", data=request_data)
        if fetched.get("error") or not fetched.get("html"):
            errors.append(f"{api_url}: {fetched.get('error') or 'empty response'}")
            continue
        searched += 1
        for candidate in extract_target_jobsearch_candidates(fetched.get("html", ""), careers_url):
            if not candidate_matches_resume_role(candidate, resume_text):
                continue
            candidate["matched_queries"] = query
            extracted.append(candidate)
    return extracted, searched, errors


def fetch_generic_html_candidates(careers_url, fetch, config=None):
    del config
    fetched = fetch(careers_url)
    if fetched.get("error") or not fetched.get("html"):
        return [], 0, [f"{careers_url}: {fetched.get('error') or 'empty response'}"]

    final_url = fetched.get("final_url") or careers_url
    if is_ashby_jobs_url(final_url) or extract_ashby_app_data(fetched.get("html", "")):
        return extract_ashby_candidates(fetched.get("html", ""), ashby_board_url(final_url) or final_url), 1, []

    if is_jibe_careers_page(fetched.get("html", "")):
        extracted, searched, errors = fetch_candidate_search_pages(
            jibe_search_urls(final_url),
            fetch,
            lambda payload, _final_url: extract_jibe_candidates(payload, final_url),
        )
        if searched:
            return extracted, searched + 1, errors
        if extracted:
            return extracted, 1, errors

    return extract_candidate_links(fetched.get("html", ""), final_url), 1, []


CAREER_PLATFORM_EXECUTORS = {
    "google_careers": fetch_google_careers_candidates,
    "amazon_jobs": fetch_amazon_jobs_candidates,
    "eightfold_pcs": fetch_eightfold_pcs_candidates,
    "eightfold_smartapply": fetch_eightfold_smartapply_candidates,
    "avature_waf_blocked": fetch_avature_waf_blocked_candidates,
    "avature_web_search": fetch_avature_web_search_candidates,
    "official_web_search": fetch_official_web_search_candidates,
    "next_static_jobs": fetch_next_static_jobs_candidates,
    "greenhouse_board": fetch_greenhouse_board_candidates,
    "custom_workday": fetch_custom_workday_api_candidates,
    "workday_cxs": fetch_workday_cxs_candidates,
    "servicenow_portal": fetch_servicenow_portal_candidates,
    "ashby": fetch_ashby_candidates,
    "lever": fetch_lever_candidates,
    "icims_jibe": fetch_jibe_candidates_from_source,
    "phenom": fetch_phenom_candidates,
    "cdpr_embedded_jobs": fetch_cdpr_embedded_jobs_candidates,
    "shopify_embedded_jobs": fetch_shopify_embedded_jobs_candidates,
    "embedded_json_jobs": fetch_embedded_json_jobs_candidates,
    "endpoint_json_jobs": fetch_endpoint_json_jobs_candidates,
    "static_json_careers": fetch_static_json_careers_candidates,
    "algolia_jobs": fetch_algolia_jobs_candidates,
    "target_jobsearch": fetch_target_jobsearch_candidates,
    "generic_html": fetch_generic_html_candidates,
}


def discover_company_career_source(company, fetch):
    careers_url = storage.clean(company.get("careers_url", ""))
    if is_target_careers_url(careers_url):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "target_jobsearch",
            {"api_url": target_job_api_url(careers_url)},
            ["Recognized Target's structured corporate careers search API."],
            status="discovered",
        )

    if is_google_careers_results_url(careers_url):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "google_careers",
            {},
            ["Recognized Google Careers search URL."],
            status="discovered",
        )

    if is_amazon_jobs_url(careers_url):
        config = {
            "search_json_url": amazon_jobs_search_base(careers_url),
            "locale": amazon_jobs_locale(careers_url),
            "loc_query": "United States",
            "result_limit": str(CAREERS_SEARCH_RESULT_LIMIT),
            "max_pages": 1,
            "sort": "relevant",
        }
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "amazon_jobs",
            config,
            ["Recognized Amazon Jobs search platform."],
            status="discovered",
        )

    lever_url = lever_board_url(careers_url)
    if lever_url:
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "lever",
            {"board_url": lever_url, "api_url": lever_postings_api_url(lever_url)},
            ["Recognized Lever careers platform and public postings API."],
            status="discovered",
        )

    ashby_url = ashby_board_url(careers_url)
    if ashby_url:
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "ashby",
            {"board_url": ashby_url},
            ["Resolved careers URL to an Ashby job board."],
            status="discovered",
        )

    if is_greenhouse_board_url(careers_url):
        greenhouse_source = discover_greenhouse_board_source(company, careers_url, fetch)
        if greenhouse_source:
            return greenhouse_source

    fetched = fetch(careers_url)
    final_url = fetched.get("final_url") or careers_url
    if is_cloudflare_challenge(fetched):
        greenhouse_source = discover_greenhouse_board_source_from_tokens(company, careers_url, fetch)
        if greenhouse_source:
            return greenhouse_source

    if is_avature_url(final_url) and is_aws_waf_challenge(fetched):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "avature_web_search",
            {
                "reason": "aws_waf_javascript_challenge",
                "fallback": "openai_web_search",
            },
            [
                "Detected Avature careers portal.",
                "Avature returned an AWS WAF JavaScript challenge to the non-browser checker.",
                "OpenAI web search is used as a fallback; only direct official Avature job-detail URLs are accepted.",
            ],
            status="discovered",
            notes="Uses OpenAI web search because direct Avature fetching is WAF-blocked.",
        )

    if is_shopify_careers_url(final_url) and shopify_job_rows(fetched.get("html", "")):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "shopify_embedded_jobs",
            {},
            ["Detected Shopify's serialized current careers listings in the official careers page."],
            status="verified",
        )

    if is_cloudflare_challenge(fetched):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "official_web_search",
            {
                "reason": "cloudflare_browser_verification_challenge",
                "fallback": "openai_web_search",
            },
            [
                "The official careers site returned a Cloudflare browser verification challenge.",
                "OpenAI web search is used as a fallback; only direct job-detail URLs on the official host are accepted.",
            ],
            status="discovered",
            notes="Uses OpenAI web search because direct careers-site fetching is Cloudflare-blocked.",
        )

    if fetched.get("error") or not fetched.get("html"):
        raise ValueError(f"error: {careers_url}: {fetched.get('error') or 'empty response'}")

    if is_next_static_jobs_page(fetched.get("html", "")):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "next_static_jobs",
            {"detail_path_template": "/jobs/{id}/"},
            ["Detected Next.js pageProps.jobs static job data."],
            status="verified",
        )

    if is_eightfold_pcs_page(fetched.get("html", "")):
        source = discover_eightfold_pcs_source(company, fetched.get("html", ""), final_url, fetch)
        if source:
            return source

    if is_eightfold_smartapply_page(fetched.get("html", "")):
        source = discover_eightfold_smartapply_source(company, fetched.get("html", ""), final_url, fetch)
        if source:
            return source

    branded_ashby_url = ashby_board_url_from_html(fetched.get("html", ""))
    if is_ashby_jobs_url(final_url) or extract_ashby_app_data(fetched.get("html", "")) or branded_ashby_url:
        board_url = ashby_board_url(final_url) or branded_ashby_url or final_url
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "ashby",
            {"board_url": board_url},
            ["Detected Ashby app data on the careers page."],
            status="verified",
        )

    if has_branded_greenhouse_job_links(fetched.get("html", "")):
        greenhouse_source = discover_greenhouse_board_source_from_tokens(company, careers_url, fetch)
        if greenhouse_source:
            return greenhouse_source

    greenhouse_script_source = discover_greenhouse_board_source_from_scripts(company, fetched.get("html", ""), final_url, fetch)
    if greenhouse_script_source:
        return greenhouse_script_source

    workday_cxs_source = discover_workday_cxs_source(company, fetched.get("html", ""), final_url, fetch)
    if workday_cxs_source:
        return workday_cxs_source

    custom_source = discover_custom_workday_api_source(company, fetched.get("html", ""), final_url, fetch)
    if custom_source:
        return custom_source

    servicenow_source = discover_servicenow_portal_source(company, fetched.get("html", ""), final_url, fetch)
    if servicenow_source:
        return servicenow_source

    if is_jibe_careers_page(fetched.get("html", "")):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "icims_jibe",
            {},
            ["Detected iCIMS/Jibe search markers on the careers page."],
            status="verified",
        )

    if is_phenom_page(fetched.get("html", "")):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "phenom",
            {"search_param": "keywords", "page_size": CAREERS_SEARCH_RESULT_LIMIT},
            ["Detected Phenom People search page with eagerLoadRefineSearch job data."],
            status="verified",
        )

    if is_cdpr_jobs_page(fetched.get("html", "")):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "cdpr_embedded_jobs",
            {},
            ["Detected CD PROJEKT RED's embedded cdpData.jobsData job feed."],
            status="verified",
        )

    if is_embedded_json_jobs_page(fetched.get("html", "")):
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "embedded_json_jobs",
            {"source": "data-props.jobs"},
            ["Detected embedded data-props JSON job list."],
            status="verified",
        )

    endpoint_json_source = discover_endpoint_json_jobs_source(company, fetched.get("html", ""), final_url, fetch)
    if endpoint_json_source:
        return endpoint_json_source

    static_json_source = discover_static_json_careers_source(company, fetched.get("html", ""), final_url, fetch)
    if static_json_source:
        return static_json_source

    algolia_config = discover_algolia_source_config(fetched.get("html", ""))
    if algolia_config:
        return save_company_career_source(
            company.get("id", ""),
            careers_url,
            "algolia_jobs",
            algolia_config,
            ["Detected Algolia-backed careers search configuration."],
            status="verified",
        )

    greenhouse_source = discover_greenhouse_board_source_from_tokens(company, careers_url, fetch)
    if greenhouse_source:
        return greenhouse_source

    return save_company_career_source(
        company.get("id", ""),
        careers_url,
        "generic_html",
        {},
        ["No known platform API detected; using conservative HTML link extraction."],
        status="verified",
    )


def current_company_career_source(company, fetch):
    careers_url = normalize_url(company.get("careers_url", ""))
    source = get_company_career_source(company.get("id", ""))
    if (
        source
        and normalize_url(source.get("source_url", "")) == careers_url
        and source.get("platform_type", "") in CAREER_PLATFORM_EXECUTORS
    ):
        if source.get("platform_type", "") in {
            "generic_html",
            "avature_waf_blocked",
            "avature_web_search",
            "official_web_search",
        }:
            discovered = discover_company_career_source(company, fetch)
            if discovered.get("platform_type") != source.get("platform_type", ""):
                return discovered
        return source
    return discover_company_career_source(company, fetch)


def fetch_career_candidates_with_source(source, fetch, *, run_id="", company_id=""):
    platform_type = source.get("platform_type", "")
    executor = CAREER_PLATFORM_EXECUTORS.get(platform_type)
    if not executor:
        return [], 0, [f"Unsupported career platform: {platform_type or 'unknown'}"]
    careers_url = source.get("source_url", "")
    config = career_source_config(source)
    config.update({"_run_id": run_id, "_company_id": company_id})
    return executor(careers_url, fetch, config)


def career_sources_equivalent(left, right):
    if not left or not right:
        return False
    return (
        normalize_url(left.get("source_url", "")) == normalize_url(right.get("source_url", ""))
        and left.get("platform_type", "") == right.get("platform_type", "")
        and left.get("config_json", "") == right.get("config_json", "")
    )


def check_company_postings(company_id, fetcher=None):
    company = get_company(company_id)
    if candidate_eligibility.company_is_excluded(company):
        raise ValueError(
            f"Cannot check candidate postings: company {company.get('id', '')} is "
            f"{company.get('interest_status', '')}."
        )
    if company.get("tracking_status", "").lower() != "tracked":
        raise ValueError("Track this discovered company before checking its careers page.")
    careers_url = storage.clean(company.get("careers_url", ""))
    if not careers_url:
        raise ValueError("Company careers_url is required before checking postings.")

    checked_at = now_scan_iso()
    scan_run_id = f"company-scan-{company.get('id', '').lower()}-{checked_at.replace(':', '').replace('-', '')}"
    fetch = fetcher or fetch_careers_page
    source = current_company_career_source(company, fetch)
    raw_extracted, search_count, errors = fetch_career_candidates_with_source(
        source,
        fetch,
        run_id=scan_run_id,
        company_id=company.get("id", ""),
    )
    extracted = normalize_extracted_candidates(raw_extracted, source.get("platform_type", ""))
    if search_count == 0 or not extracted:
        try:
            rediscovered = discover_company_career_source(company, fetch)
        except ValueError:
            rediscovered = None
        if rediscovered and not career_sources_equivalent(source, rediscovered):
            source = rediscovered
            rediscovered_rows, rediscovered_search_count, rediscovered_errors = fetch_career_candidates_with_source(
                source,
                fetch,
                run_id=scan_run_id,
                company_id=company.get("id", ""),
            )
            raw_extracted = [*raw_extracted, *rediscovered_rows]
            search_count += rediscovered_search_count
            errors = [*errors, *rediscovered_errors]
            extracted = normalize_extracted_candidates(rediscovered_rows, source.get("platform_type", ""))
    if search_count == 0:
        status = f"error: {'; '.join(errors) or 'empty response'}"
        scan = repository.write_company_career_scan(
            {
                "company_id": company.get("id", ""),
                "checked_at": checked_at,
                "run_id": scan_run_id,
                "platform_type": source.get("platform_type", ""),
                "status": "error",
                "requests_succeeded": "0",
                "requests_failed": str(max(1, len(errors))),
                "extracted_count": str(len(raw_extracted)),
                "unique_candidate_count": str(len(extracted)),
                "errors_json": json.dumps(errors or ["empty response"]),
            }
        )
        update_check_status(company.get("id", ""), checked_at, status)
        raise ValueError(status)
    mark_company_career_source_verified(company.get("id", ""))

    resume_text = fit_context_text()
    tracked = tracked_posting_context(company)
    candidates = repository.read_company_posting_candidates()
    original_by_id = {
        row.get("id", ""): dict(row)
        for row in candidates
        if row.get("id", "")
    }
    existing_by_url = {
        (row.get("company_id", "").upper(), normalize_url(row.get("url", ""))): row
        for row in candidates
    }
    existing_by_identity = {}
    for row in candidates:
        row_company_id = row.get("company_id", "").upper()
        for identity_key in candidate_identity_keys(row):
            existing_by_identity.setdefault((row_company_id, identity_key), row)
    new_rows = []
    seen_urls = set()
    seen_identity_keys = set()

    for item in extracted:
        url = normalize_url(item["url"])
        if not url or url in seen_urls:
            continue
        item["url"] = url
        item_identity_keys = candidate_identity_keys(item)
        if item_identity_keys and item_identity_keys & seen_identity_keys:
            continue
        seen_identity_keys.update(item_identity_keys)
        seen_urls.add(url)
        key = (company.get("id", "").upper(), url)
        existing = existing_by_url.get(key)
        if existing is None:
            existing = next(
                (
                    existing_by_identity.get((company.get("id", "").upper(), identity_key))
                    for identity_key in item_identity_keys
                    if existing_by_identity.get((company.get("id", "").upper(), identity_key))
                ),
                None,
            )
        if existing:
            for field in [
                "title",
                "url",
                "location",
                "work_mode",
                "category",
                "source_platform",
                "source_job_id",
                "matched_queries",
                "description_excerpt",
                "description_hash",
                "score_inputs_hash",
                "normalization_warnings",
                "qualification_status",
                "qualification_reason",
            ]:
                existing[field] = item.get(field, "") or existing.get(field, "")
            existing["last_seen_at"] = checked_at
            existing["last_scan_run_id"] = scan_run_id
            existing["scan_state"] = "current"
            if candidate_is_tracked(item, tracked):
                existing["status"] = "pursued"
            elif existing.get("status") in {"new", "unavailable"}:
                existing["status"] = "new"
                existing.update(score_candidate_fit({**existing, **item}, resume_text, checked_at))
            continue
        if candidate_is_tracked(item, tracked):
            continue
        row = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        row.update(
            {
                "id": next_candidate_id(candidates + new_rows),
                "company_id": company.get("id", ""),
                "title": item.get("title", ""),
                "url": url,
                "location": item.get("location", ""),
                "work_mode": item.get("work_mode", ""),
                "category": item.get("category", ""),
                "source_platform": item.get("source_platform", ""),
                "source_job_id": item.get("source_job_id", ""),
                "matched_queries": item.get("matched_queries", ""),
                "description_excerpt": item.get("description_excerpt", ""),
                "description_hash": item.get("description_hash", ""),
                "score_inputs_hash": item.get("score_inputs_hash", ""),
                "normalization_warnings": item.get("normalization_warnings", ""),
                "last_scan_run_id": scan_run_id,
                "scan_state": "current",
                "status": "new",
                "first_seen_at": checked_at,
                "last_seen_at": checked_at,
            }
        )
        row.update(score_candidate_fit({**row, **item}, resume_text, checked_at))
        row["qualification_status"], row["qualification_reason"] = candidate_qualification(row)
        candidates.append(row)
        new_rows.append(row)

    company_candidates = [
        row
        for row in candidates
        if row.get("company_id", "").upper() == company.get("id", "").upper()
    ]
    for candidate in company_candidates:
        if candidate_seen_in_scan(candidate, seen_urls, seen_identity_keys):
            candidate["scan_state"] = "current"
        elif candidate.get("status") == "unavailable":
            candidate["scan_state"] = "unavailable"
        else:
            candidate["scan_state"] = "not-seen"
    invalid_candidate_count = 0
    for candidate in company_candidates:
        if candidate.get("status", "new") != "new" or candidate_seen_in_scan(candidate, seen_urls, seen_identity_keys):
            continue
        invalid_for_source = (
            source.get("platform_type", "") == "target_jobsearch"
            and not is_target_job_url(candidate.get("url", ""))
        )
        is_navigation_title = normalized_key(candidate.get("title", "")) in {
            "career",
            "careers",
            "job alert",
            "create a job alert",
        }
        if not invalid_for_source and not is_navigation_title and looks_like_job_link(
            candidate.get("title", ""), candidate.get("url", ""), careers_url
        ):
            continue
        candidate["status"] = "unavailable"
        candidate["scan_state"] = "unavailable"
        invalid_candidate_count += 1
    verification = verify_unseen_candidate_availability(
        company_candidates,
        seen_urls,
        seen_identity_keys,
        fetch,
        checked_at=checked_at,
    )
    verification["unavailable_count"] += invalid_candidate_count
    annotate_candidate_fit(candidates, company.get("id", ""), checked_at, only_missing=True)
    current_candidates = [row for row in company_candidates if row.get("last_seen_at") == checked_at]
    for candidate in company_candidates:
        candidate["qualification_status"], candidate["qualification_reason"] = candidate_qualification(candidate)
    recommended = recommended_candidates(current_candidates)
    field_updates = {}
    status_updates = []
    for candidate in candidates:
        candidate_id = candidate.get("id", "")
        original = original_by_id.get(candidate_id)
        if original is None:
            continue
        changed = {
            field: candidate.get(field, "")
            for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS
            if field not in {"id", "company_id", "notes"}
            and candidate.get(field, "") != original.get(field, "")
        }
        if "status" in changed:
            status_change = {"status": changed.pop("status")}
            if "scan_state" in changed:
                status_change["scan_state"] = changed.pop("scan_state")
            status_updates.append(
                (candidate_id, {"status": original.get("status", "")}, status_change)
            )
        if changed:
            field_updates[candidate_id] = changed
    if field_updates:
        repository.bulk_update_company_posting_candidate_fields(field_updates)
    for candidate_id, expected, updates in status_updates:
        repository.compare_and_update_company_posting_candidate_fields(
            candidate_id,
            expected,
            updates,
        )
    if new_rows:
        pending_new_rows = new_rows
        new_rows = repository.insert_company_posting_candidates(pending_new_rows)
        for pending, saved in zip(pending_new_rows, new_rows):
            pending["id"] = saved.get("id", "")
    scan_status = "partial" if errors else "ok"
    status = (
        f"{scan_status}: {len(new_rows)} new, {len(extracted)} found, "
        f"{len(recommended)} recommended, {search_count} searched"
    )
    if len(raw_extracted) != len(extracted):
        status += f", {len(raw_extracted)} extracted"
    if errors:
        status += f", {len(errors)} request errors"
    if verification["unavailable_count"]:
        status += f", {verification['unavailable_count']} unavailable"
    if verification["verification_count"]:
        status += f", {verification['verification_count']} detail checked"
    if verification["verification_skipped_count"]:
        status += f", {verification['verification_skipped_count']} detail skipped"
    scan = repository.write_company_career_scan(
        {
            "company_id": company.get("id", ""),
            "checked_at": checked_at,
            "run_id": scan_run_id,
            "platform_type": source.get("platform_type", ""),
            "status": scan_status,
            "requests_succeeded": str(search_count),
            "requests_failed": str(len(errors)),
            "extracted_count": str(len(raw_extracted)),
            "unique_candidate_count": str(len(extracted)),
            "new_count": str(len(new_rows)),
            "recommended_count": str(len(recommended)),
            "unavailable_count": str(verification["unavailable_count"]),
            "verification_count": str(verification["verification_count"]),
            "verification_skipped_count": str(verification["verification_skipped_count"]),
            "errors_json": json.dumps(errors),
        }
    )
    update_check_status(company.get("id", ""), checked_at, status)
    return {
        "company": get_company(company.get("id", "")),
        "career_source": get_company_career_source(company.get("id", "")),
        "candidates": candidates_for_company(company.get("id", "")),
        "new": new_rows,
        "recommended": recommended,
        "scan": scan,
        **verification,
    }


def check_all_company_postings(fetcher=None):
    checked = []
    skipped = []
    errors = []
    for company in repository.read_companies():
        company_id = company.get("id", "")
        if company.get("tracking_status", "").lower() != "tracked":
            skipped.append({"company": company, "reason": "not tracked"})
            continue
        interest_status = company.get("interest_status", "").lower()
        if interest_status in {"archived", "not-interested"}:
            skipped.append(
                {
                    "company": company,
                    "reason": "not interested" if interest_status == "not-interested" else "archived",
                }
            )
            continue
        if not storage.clean(company.get("careers_url", "")):
            skipped.append({"company": company, "reason": "missing careers URL"})
            continue
        try:
            result = check_company_postings(company_id, fetcher=fetcher)
            checked.append(
                {
                    "company": result["company"],
                    "new_count": len(result.get("new", [])),
                    "recommended_count": len(result.get("recommended", [])),
                    "unavailable_count": int(result.get("unavailable_count") or 0),
                    "verification_count": int(result.get("verification_count") or 0),
                    "verification_skipped_count": int(result.get("verification_skipped_count") or 0),
                    "candidate_count": len(result.get("candidates", [])),
                }
            )
        except ValueError as exc:
            errors.append({"company": get_company(company_id), "error": str(exc)})
    return {
        "checked": checked,
        "skipped": skipped,
        "errors": errors,
        "checked_count": len(checked),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "new_count": sum(item["new_count"] for item in checked),
        "recommended_count": sum(item["recommended_count"] for item in checked),
        "unavailable_count": sum(item["unavailable_count"] for item in checked),
        "verification_count": sum(item["verification_count"] for item in checked),
        "verification_skipped_count": sum(item["verification_skipped_count"] for item in checked),
    }


def update_check_status(company_id, checked_at, status):
    rows = repository.read_companies()
    wanted = storage.clean(company_id).upper()
    for row in rows:
        if row.get("id", "").upper() == wanted:
            return repository.update_company_fields(
                row.get("id", ""),
                {
                    "last_checked_at": checked_at,
                    "last_check_status": storage.clean(status),
                },
            )
    raise ValueError(f"No company found with id {company_id}.")


def candidates_for_company(company_id, include_excluded_companies=False):
    wanted = storage.clean(company_id).upper()
    rows = [
        row
        for row in repository.read_company_posting_candidates()
        if row.get("company_id", "").upper() == wanted
    ]
    visible, _excluded = candidate_eligibility.partition_candidates(
        rows,
        [get_company(wanted)],
        include_excluded_companies=include_excluded_companies,
    )
    return visible


def update_candidate_status(candidate_id, status):
    result = update_candidate_statuses([candidate_id], status)
    return result["candidates"][0]


def update_candidate_statuses(candidate_ids, status):
    status = validate_candidate_status(status)
    wanted_ids = {
        storage.clean(candidate_id).upper()
        for candidate_id in candidate_ids or []
        if storage.clean(candidate_id)
    }
    if not wanted_ids:
        raise ValueError("Select at least one company posting candidate.")
    candidates = repository.read_company_posting_candidates()
    found_ids = {
        row.get("id", "").upper()
        for row in candidates
        if row.get("id", "").upper() in wanted_ids
    }
    missing_ids = wanted_ids - found_ids
    if missing_ids:
        raise ValueError(
            "No company posting candidate found with "
            + ", ".join(sorted(missing_ids))
            + "."
        )
    company_rows = repository.read_companies()
    for row in candidates:
        if row.get("id", "").upper() in wanted_ids:
            candidate_eligibility.require_candidate_eligible(
                row, company_rows, operation="update"
            )
    repository.update_company_posting_candidate_statuses(wanted_ids, status)
    sync_linked_discovery_candidate_statuses(
        [row for row in candidates if row.get("id", "").upper() in wanted_ids],
        status,
    )
    updated = [
        row
        for row in repository.read_company_posting_candidates()
        if row.get("id", "").upper() in wanted_ids
    ]
    return {"candidates": updated, "count": len(updated)}


def sync_linked_discovery_candidate_statuses(company_candidates, status):
    """Keep the decision aligned while cross-pool identity remains URL-based."""
    discovery_candidates = repository.read_discovery_candidates()
    if not discovery_candidates:
        return
    application_rows = repository.read_applications()
    company_rows = {
        row.get("id", "").upper(): row
        for row in repository.read_companies()
    }
    updates = {}
    for company_candidate in company_candidates:
        for discovery_candidate in matching_discovery_candidates(
            company_candidate, discovery_candidates
        ):
            before = {
                field: discovery_candidate.get(field, "")
                for field in [
                    "status",
                    "ingested_application_id",
                    "ignore_reason",
                    "ignore_reason_detail",
                ]
            }
            if discovery_candidate.get("status") != status:
                discovery_candidate["status"] = status
            if status == "pursued":
                company = company_rows.get(
                    company_candidate.get("company_id", "").upper()
                )
                matching_ids = (
                    matching_tracked_posting_ids(
                        company_candidate,
                        company=company,
                        tracked=tracked_posting_context(
                            company, application_rows=application_rows
                        ),
                    )
                    if company
                    else []
                )
                if matching_ids and discovery_candidate.get("ingested_application_id") != matching_ids[0]:
                    discovery_candidate["ingested_application_id"] = matching_ids[0]
            if status != "ignored":
                if discovery_candidate.get("ignore_reason") or discovery_candidate.get("ignore_reason_detail"):
                    discovery_candidate["ignore_reason"] = ""
                    discovery_candidate["ignore_reason_detail"] = ""
            after = {
                "status": discovery_candidate.get("status", ""),
                "ingested_application_id": discovery_candidate.get(
                    "ingested_application_id", ""
                ),
                "ignore_reason": discovery_candidate.get("ignore_reason", ""),
                "ignore_reason_detail": discovery_candidate.get(
                    "ignore_reason_detail", ""
                ),
            }
            if after != before:
                updates[discovery_candidate.get("id", "")] = after
    if updates:
        repository.bulk_update_discovery_candidate_fields(updates)


def pursue_candidate(candidate_id):
    candidate = next(
        (row for row in repository.read_company_posting_candidates() if row.get("id", "").upper() == storage.clean(candidate_id).upper()),
        None,
    )
    if not candidate:
        raise ValueError(f"No company posting candidate found with id {candidate_id}.")
    company = candidate_eligibility.require_candidate_eligible(
        candidate, repository.read_companies(), operation="add to Considering"
    )
    review_state = candidate_review_state(candidate)
    if review_state != "ready":
        raise ValueError(
            f"Candidate is {review_state}; complete posting detail and freshness checks before adding it to Considering."
        )
    command = [
        sys.executable,
        str(paths.ROOT / "scripts" / "ingest_postings.py"),
        "--company",
        company.get("name", ""),
        "--role",
        candidate.get("title", ""),
    ]
    if candidate.get("location"):
        command.extend(["--location", candidate.get("location", "")])
    command.append(candidate.get("url", ""))
    # Keep cwd unset so the threaded macOS app server can use posix_spawn
    # instead of an unsafe fork before exec. The script path is absolute.
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or "candidate ingest failed").strip())

    app = next(
        (
            row
            for row in repository.read_applications()
            if posting_identity_match(candidate.get("url", ""), row.get("source_url", ""))
        ),
        None,
    )
    if app:
        app = associate_application(company.get("id", ""), app.get("id", "")).get("posting")
    candidate = update_candidate_status(candidate.get("id", ""), "pursued")
    return {"candidate": candidate, "posting": app, "stdout": result.stdout.strip()}


def ingest_candidate(candidate_id):
    """Compatibility alias for callers using the legacy ingest name."""
    return pursue_candidate(candidate_id)


def build_company_export_payload(company_id=""):
    companies = repository.read_companies()
    wanted = storage.clean(company_id).upper()
    if wanted:
        selected_companies = [row for row in companies if row.get("id", "").upper() == wanted]
        if not selected_companies:
            raise ValueError(f"No company found with id {company_id}.")
    else:
        selected_companies = companies

    selected_ids = {row.get("id", "").upper() for row in selected_companies}
    contacts = repository.read_contacts()
    applications = repository.read_applications()
    actions = repository.read_actions()
    contact_links = repository.read_company_contacts()
    career_sources = repository.read_company_career_sources()
    candidates = repository.read_company_posting_candidates()
    career_scans = repository.read_company_career_scans(limit=1000)
    posting_snapshots = repository.read_posting_snapshots()

    contacts_by_id = {row.get("id", "").upper(): row for row in contacts}
    actions_by_application_id = {}
    for action in actions:
        actions_by_application_id.setdefault(action.get("application_id", "").upper(), []).append(action)

    records = []
    for company in selected_companies:
        current_company_id = company.get("id", "").upper()
        current_links = [row for row in contact_links if row.get("company_id", "").upper() == current_company_id]
        linked_postings = [
            row
            for row in applications
            if row.get("company_id", "").upper() == current_company_id
        ]
        linked_actions = [
            action
            for posting in linked_postings
            for action in actions_by_application_id.get(posting.get("id", "").upper(), [])
        ]
        linked_posting_ids = {posting.get("id", "").upper() for posting in linked_postings}
        linked_snapshots = [
            snapshot
            for snapshot in posting_snapshots
            if snapshot.get("application_id", "").upper() in linked_posting_ids
        ]
        records.append(
            {
                "company": company,
                "contact_links": current_links,
                "contacts": [
                    contacts_by_id[link.get("contact_id", "").upper()]
                    for link in current_links
                    if link.get("contact_id", "").upper() in contacts_by_id
                ],
                "postings": linked_postings,
                "actions": linked_actions,
                "posting_snapshots": linked_snapshots,
                "career_sources": [
                    row
                    for row in career_sources
                    if row.get("company_id", "").upper() == current_company_id
                ],
                "posting_candidates": [
                    row
                    for row in candidates
                    if row.get("company_id", "").upper() == current_company_id
                ],
                "career_scans": [
                    row
                    for row in career_scans
                    if row.get("company_id", "").upper() == current_company_id
                ],
            }
        )

    return {
        "generated_at": now_iso(),
        "scope": {"company_id": wanted, "company_count": len(records)},
        "companies": records,
        "tables": {
            "companies": selected_companies,
            "company_contacts": [row for row in contact_links if row.get("company_id", "").upper() in selected_ids],
            "contacts": [
                row
                for row in contacts
                if row.get("id", "").upper()
                in {
                    link.get("contact_id", "").upper()
                    for link in contact_links
                    if link.get("company_id", "").upper() in selected_ids
                }
            ],
            "applications": [row for row in applications if row.get("company_id", "").upper() in selected_ids],
            "actions": [
                action
                for action in actions
                if action.get("application_id", "").upper()
                in {
                    app.get("id", "").upper()
                    for app in applications
                    if app.get("company_id", "").upper() in selected_ids
                }
            ],
            "posting_snapshots": [
                snapshot
                for snapshot in posting_snapshots
                if snapshot.get("application_id", "").upper()
                in {
                    app.get("id", "").upper()
                    for app in applications
                    if app.get("company_id", "").upper() in selected_ids
                }
            ],
            "company_career_sources": [row for row in career_sources if row.get("company_id", "").upper() in selected_ids],
            "company_posting_candidates": [row for row in candidates if row.get("company_id", "").upper() in selected_ids],
            "company_career_scans": [row for row in career_scans if row.get("company_id", "").upper() in selected_ids],
        },
    }


def company_export_filename(company_id=""):
    scope = storage.clean(company_id).upper() or "all"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"company-data-{scope}-{timestamp}.json"


def write_company_export(company_id="", output_path=None):
    payload = build_company_export_payload(company_id)
    path = Path(output_path) if output_path else paths.EXPORTS_DIR / company_export_filename(company_id)
    path = path if path.is_absolute() else paths.ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": path, "payload": payload}
