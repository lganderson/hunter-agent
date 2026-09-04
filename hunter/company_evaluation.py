"""Shared, versioned company evaluation for every discovery entry point."""

import hashlib
import json
import math
from datetime import datetime

from . import agent, api_usage, companies, paths, repository, storage


EVALUATION_VERSION = "1"
PROFILE_FILE_NAME = "company_evaluation_profile.json"
BATCH_SIZE = 5
MAX_BATCH_ATTEMPTS = 2
MODEL = "gpt-5.6-luna"
ACTIVE_INTEREST_STATUSES = {"interested", "neutral"}

EVALUATION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "maxItems": BATCH_SIZE,
            "items": {
                "type": "object",
                "properties": {
                    "company_id": {"type": "string"},
                    "name": {"type": "string"},
                    "website": {"type": "string"},
                    "careers_url": {"type": "string"},
                    "industry": {"type": "string"},
                    "company_size": {"type": "string"},
                    "description": {"type": "string"},
                    "location_fit": {
                        "type": "string",
                        "enum": ["us-remote", "metro-area", "both", "unknown"],
                    },
                    "location": {"type": "string"},
                    "remote_policy": {"type": "string"},
                    "location_evidence": {"type": "string"},
                    "source_urls": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "company_id",
                    "name",
                    "website",
                    "careers_url",
                    "industry",
                    "company_size",
                    "description",
                    "location_fit",
                    "location",
                    "remote_policy",
                    "location_evidence",
                    "source_urls",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["companies"],
    "additionalProperties": False,
}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def default_profile():
    # Import lazily so company_discovery can call this module without a cycle.
    from . import company_discovery

    return {
        "focus": company_discovery.DEFAULT_FOCUS,
        "sizes": list(company_discovery.DEFAULT_SIZES),
        "locations": list(company_discovery.DEFAULT_LOCATION_PREFERENCES),
        "remote_region": company_discovery.DEFAULT_REMOTE_REGION,
        "metro_area": company_discovery.DEFAULT_METRO_AREA,
    }


def normalize_profile(value=None):
    from . import company_discovery

    incoming = value or {}
    defaults = default_profile()
    focus_terms = company_discovery.normalized_focus_terms(
        incoming.get("focus", defaults["focus"])
    )
    return {
        "focus": ", ".join(focus_terms) or defaults["focus"],
        "sizes": company_discovery.normalized_sizes(
            incoming.get("sizes", defaults["sizes"])
        ),
        "locations": company_discovery.normalized_location_preferences(
            incoming.get("locations", defaults["locations"])
        ),
        "remote_region": storage.clean(
            incoming.get("remote_region", defaults["remote_region"])
        ) or defaults["remote_region"],
        "metro_area": storage.clean(
            incoming.get("metro_area", defaults["metro_area"])
        ) or defaults["metro_area"],
    }


def profile_path():
    return paths.DATA_DIR / PROFILE_FILE_NAME


def load_profile():
    path = profile_path()
    if not path.exists():
        return normalize_profile()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return normalize_profile()
    return normalize_profile(value if isinstance(value, dict) else {})


def save_profile(value):
    profile = normalize_profile(value)
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = profile_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(profile, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
    return profile


def evaluation_version(profile=None):
    normalized = normalize_profile(profile or load_profile())
    fingerprint = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    return f"{EVALUATION_VERSION}:{fingerprint}"


def evaluation_needed(company, profile=None, force=False):
    if force:
        return True
    if company.get("interest_status", "neutral").lower() not in ACTIVE_INTEREST_STATUSES:
        return False
    expected_version = evaluation_version(profile)
    if company.get("company_evaluation_version", "") != expected_version:
        return True
    return company.get("company_evaluation_status", "") in {"", "pending", "evaluating"}


def mark_pending(company_ids, profile=None, force=False):
    wanted = {storage.clean(value).upper() for value in company_ids or [] if storage.clean(value)}
    if not wanted:
        return []
    normalized_profile = normalize_profile(profile or load_profile())
    rows = repository.read_companies()
    updates = {}
    for row in rows:
        if row.get("id", "").upper() not in wanted:
            continue
        if not evaluation_needed(row, normalized_profile, force=force):
            continue
        company_id = row.get("id", "")
        updates[company_id] = {
            "company_evaluation_status": "pending",
            "company_evaluation_error": "",
        }
    if updates:
        repository.bulk_update_company_fields(updates)
    return list(updates)


def pending_company_ids():
    return [
        row.get("id", "")
        for row in repository.read_companies()
        if row.get("company_evaluation_status", "") == "pending"
        and row.get("interest_status", "neutral").lower() in ACTIVE_INTEREST_STATUSES
    ]


def evaluation_targets(company_ids=None, tracking_status="discovered", profile=None, force=False):
    wanted = {storage.clean(value).upper() for value in company_ids or [] if storage.clean(value)}
    normalized_profile = normalize_profile(profile or load_profile())
    return [
        row
        for row in repository.read_companies()
        if (not wanted or row.get("id", "").upper() in wanted)
        and (not tracking_status or row.get("tracking_status", "") == tracking_status)
        and row.get("interest_status", "neutral").lower() in ACTIVE_INTEREST_STATUSES
        and evaluation_needed(row, normalized_profile, force=force)
    ]


def evaluation_batch_count(company_ids=None, tracking_status="discovered", profile=None, force=False):
    return math.ceil(
        len(evaluation_targets(company_ids, tracking_status, profile, force)) / BATCH_SIZE
    )


def openai_company_evaluation(
    config,
    company_rows,
    profile,
    batch_number=1,
    reason="backfill",
    run_id="",
):
    targets = []
    for company in (company_rows or [])[:BATCH_SIZE]:
        targets.append(
            {
                "company_id": storage.clean(company.get("id", "")),
                "name": storage.clean(company.get("name", "")),
                "known_website": companies.normalize_url(company.get("website", "")),
                "known_careers_url": companies.normalize_url(company.get("careers_url", "")),
                "known_industry": storage.clean(company.get("industry", "")),
                "known_size": storage.clean(company.get("company_size", "")),
                "profile_url": companies.normalize_url(company.get("company_profile_url", "")),
                "discovery_evidence": storage.clean(company.get("company_discovery_evidence", "")),
                "discovery_source_url": companies.normalize_url(
                    company.get("company_discovery_source_url", "")
                ),
            }
        )
    targets = [target for target in targets if target["company_id"] and target["name"]]
    if not targets:
        return []

    prompt = (
        "Evaluate every employer in the JSON list using current public web evidence. Verify identity before "
        "returning an official homepage. Prefer official company and careers pages; use reputable company "
        "profiles only when official sources do not state size or industry. Return an empty value rather than "
        "guessing. Company size must use one of: 2–10 employees, 11–50 employees, 51–200 employees, "
        "201–500 employees, 501–1,000 employees, 1,001–5,000 employees, 5,001–10,000 employees, or "
        "10,001+ employees. Location fit is us-remote only with explicit current evidence that employees or "
        f"roles can be based in {profile['remote_region']}; it is metro-area only with an office or current "
        f"hiring presence in {profile['metro_area']}; use both when both are supported, otherwise unknown. "
        "Keep evidence concise and include direct supporting URLs. Preserve company_id and name exactly.\n\n"
        f"Employer fit focus: {profile['focus']}\n"
        f"Preferred sizes: {', '.join(profile['sizes'])}\n\n"
        "Companies:\n" + json.dumps(targets, indent=2)
    )
    payload = {
        "model": MODEL,
        "input": prompt,
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "tool_choice": "required",
        "max_tool_calls": 10,
        "include": ["web_search_call.action.sources"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "hunter_company_evaluation",
                "strict": True,
                "schema": EVALUATION_RESPONSE_SCHEMA,
            }
        },
        "max_output_tokens": 4_000,
        "reasoning": {"effort": "low"},
        "store": False,
        "metadata": {"feature": "company-evaluation", "source": storage.clean(reason) or "backfill"},
    }
    response = agent._request_json(
        f"{config['api_base']}/responses",
        config["token"],
        payload,
    )
    api_usage.log_usage(
        "company-evaluation",
        response.get("model") or MODEL,
        response,
        operation=f"{storage.clean(reason) or 'backfill'}-batch-{batch_number}",
        context={
            "run_id": run_id,
            "company_ids": ",".join(target["company_id"] for target in targets),
        },
    )
    try:
        result = json.loads(agent._output_text(response))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OpenAI returned unreadable company evaluation results.") from exc
    rows = result.get("companies", []) if isinstance(result, dict) else []
    return rows if isinstance(rows, list) else []


def _set_status(company_ids, status, error=""):
    wanted = {storage.clean(value).upper() for value in company_ids or [] if storage.clean(value)}
    updates = {
        company_id: {
            "company_evaluation_status": storage.clean(status),
            "company_evaluation_error": storage.clean(error),
        }
        for company_id in wanted
    }
    if updates:
        repository.bulk_update_company_fields(updates)


def _apply_result(company, result, profile, checked_at):
    from . import company_discovery

    source_urls = [
        companies.normalize_url(value)
        for value in (result.get("source_urls", []) if isinstance(result, dict) else [])
    ]
    source_urls = [value for value in source_urls if value]
    metadata_source = source_urls[0] if source_urls else company.get("company_discovery_source_url", "")
    updated = companies.update_company_metadata(
        company.get("id", ""),
        {
            "company_industry": result.get("industry", ""),
            "company_size": result.get("company_size", ""),
            "website": company_discovery.official_company_website(result.get("website", "")),
        },
        source_url=metadata_source,
        checked_at=checked_at,
    )
    location_fit = company_discovery.normalized_location_fit(result.get("location_fit", ""))
    metadata = {
        "company": updated.get("name", ""),
        "company_industry": updated.get("industry", "") or result.get("industry", ""),
        "company_size": updated.get("company_size", "") or result.get("company_size", ""),
        "company_description": result.get("description", ""),
        "website": updated.get("website", "") or result.get("website", ""),
        "company_profile_url": updated.get("company_profile_url", ""),
    }
    score, summary = company_discovery.score_company(
        metadata,
        company_discovery.normalized_focus_terms(profile["focus"]),
        profile["sizes"],
        company.get("company_discovery_source", "") or "company evaluation",
        " ".join(
            value
            for value in [
                company.get("company_discovery_evidence", ""),
                result.get("description", ""),
                result.get("location_evidence", ""),
            ]
            if storage.clean(value)
        ),
        location_fit,
        profile["remote_region"],
        profile["metro_area"],
    )
    rows = repository.read_companies()
    row = next(item for item in rows if item.get("id", "") == company.get("id", ""))
    careers_url = companies.normalize_url(result.get("careers_url", ""))
    if careers_url and not row.get("careers_url", ""):
        row["careers_url"] = careers_url
    row["company_location_fit"] = location_fit
    row["company_location"] = storage.clean(result.get("location", ""))
    row["company_remote_policy"] = storage.clean(result.get("remote_policy", ""))
    evidence = storage.clean(result.get("location_evidence", ""))
    if source_urls:
        evidence = " ".join(value for value in [evidence, "Sources: " + ", ".join(source_urls)] if value)
    row["company_location_evidence"] = evidence
    row["company_location_checked_at"] = checked_at
    row["company_fit_score"] = str(score)
    row["company_fit_summary"] = summary
    row["company_fit_checked_at"] = checked_at
    row["company_evaluation_version"] = evaluation_version(profile)
    row["company_evaluation_checked_at"] = checked_at
    row["company_evaluation_error"] = ""
    row["company_evaluation_status"] = (
        "ready"
        if row.get("website", "")
        and row.get("company_size", "")
        and row.get("company_location_fit", "")
        else "needs-verification"
    )
    repository.update_company_fields(
        row.get("id", ""),
        {
            field: row.get(field, "")
            for field in [
                "careers_url",
                "company_location_fit",
                "company_location",
                "company_remote_policy",
                "company_location_evidence",
                "company_location_checked_at",
                "company_fit_score",
                "company_fit_summary",
                "company_fit_checked_at",
                "company_evaluation_version",
                "company_evaluation_checked_at",
                "company_evaluation_error",
                "company_evaluation_status",
            ]
        },
    )
    return companies.get_company(company.get("id", ""))


def evaluate_companies(
    company_ids=None,
    tracking_status="discovered",
    profile=None,
    force=False,
    evaluator=None,
    progress=None,
    reason="backfill",
    run_id="",
    max_attempts=MAX_BATCH_ATTEMPTS,
):
    run_id = storage.clean(run_id) or f"company-evaluation-{now_iso().replace(':', '').replace('-', '')}"
    normalized_profile = save_profile(profile or load_profile())
    targets = evaluation_targets(
        company_ids=company_ids,
        tracking_status=tracking_status,
        profile=normalized_profile,
        force=force,
    )
    total_batches = math.ceil(len(targets) / BATCH_SIZE)
    if evaluator is None and targets:
        config = agent._settings()

        def evaluator(batch, current_profile, batch_number):
            return openai_company_evaluation(
                config,
                batch,
                current_profile,
                batch_number=batch_number,
                reason=reason,
                run_id=run_id,
            )

    evaluated_ids = []
    ready_count = 0
    verification_count = 0
    failed_count = 0
    errors = []
    for offset in range(0, len(targets), BATCH_SIZE):
        batch_number = (offset // BATCH_SIZE) + 1
        batch = targets[offset:offset + BATCH_SIZE]
        batch_ids = [row.get("id", "") for row in batch]
        _set_status(batch_ids, "evaluating")
        if progress:
            progress(
                {
                    "phase": "evaluating",
                    "message": (
                        f"Evaluating companies {offset + 1}–{min(offset + len(batch), len(targets))} "
                        f"of {len(targets)}…"
                    ),
                    "completed_steps": batch_number - 1,
                    "total_steps": max(1, total_batches),
                    "source": "company-evaluation",
                }
            )
        results = None
        failure = None
        for _attempt in range(1, max(1, int(max_attempts or 1)) + 1):
            try:
                results = evaluator(batch, normalized_profile, batch_number) or []
                failure = None
                break
            except Exception as exc:  # noqa: BLE001 - retry once, then isolate the failed batch.
                failure = exc
        if failure is not None:
            message = storage.clean(str(failure))
            _set_status(batch_ids, "failed", message)
            errors.append(f"Batch {batch_number}: {message}")
            failed_count += len(batch)
            continue
        results_by_id = {
            storage.clean(item.get("company_id", "")).upper(): item
            for item in results
            if isinstance(item, dict) and storage.clean(item.get("company_id", ""))
        }
        for company in batch:
            company_id = company.get("id", "")
            result = results_by_id.get(company_id.upper())
            if result is None:
                _set_status([company_id], "failed", "No matching evaluation result was returned.")
                errors.append(f"{company.get('name', company_id)}: no matching evaluation result")
                failed_count += 1
                continue
            updated = _apply_result(company, result, normalized_profile, now_iso())
            evaluated_ids.append(company_id)
            if updated.get("company_evaluation_status") == "ready":
                ready_count += 1
            else:
                verification_count += 1
        if progress:
            progress(
                {
                    "phase": "evaluating",
                    "message": f"Evaluated {min(offset + len(batch), len(targets))} of {len(targets)} companies…",
                    "completed_steps": batch_number,
                    "total_steps": max(1, total_batches),
                    "source": "company-evaluation",
                }
            )
    return {
        "target_count": len(targets),
        "evaluated_count": len(evaluated_ids),
        "ready_count": ready_count,
        "needs_verification_count": verification_count,
        "failed_count": failed_count,
        "company_ids": evaluated_ids,
        "evaluation_version": evaluation_version(normalized_profile),
        "profile": normalized_profile,
        "errors": errors,
        "run_id": run_id,
    }
