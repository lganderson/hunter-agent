"""Non-interactive providers for Discovery role acquisition."""

import json
import re
import threading
from urllib.parse import urlencode, urlparse

from . import agent, api_usage, candidate_eligibility, companies, repository, settings, storage


OPENAI_RESULT_LIMIT = 50
OPENAI_RESULTS_PER_FAMILY = 20
ADZUNA_RESULTS_PER_QUERY = 25
ADZUNA_QUERY_LIMIT = 20
OPENAI_DISCOVERY_ATTEMPTS = 2
OPENAI_DISCOVERY_ATTEMPT_TIMEOUT_SECONDS = 15
OPENAI_BLOCKED_HOSTS = {
    "adzuna.com",
    "glassdoor.com",
    "indeed.com",
    "linkedin.com",
    "simplyhired.com",
    "ziprecruiter.com",
}
OPENAI_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "roles": {
            "type": "array",
            "maxItems": OPENAI_RESULT_LIMIT,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "job_url": {"type": "string"},
                    "location": {"type": "string"},
                    "work_mode": {
                        "type": "string",
                        "enum": ["on-site", "hybrid", "remote", "unknown"],
                    },
                    "description_summary": {"type": "string"},
                    "role_family_id": {"type": "string"},
                    "lane_id": {"type": "string"},
                },
                "required": [
                    "title",
                    "company",
                    "job_url",
                    "location",
                    "work_mode",
                    "description_summary",
                    "role_family_id",
                    "lane_id",
                ],
            },
        }
    },
    "required": ["roles"],
}


def provider_bundle(search, family_definitions, *, fetcher=None, openai_requester=None, progress=None):
    results = []
    sources = []
    errors = []

    ats_results = ats_inventory_results(search, family_definitions)
    results.extend(ats_results)
    sources.append(
        {
            "source": "ats-inventory",
            "label": "Direct company career sources",
            "query_family": "all",
            "query_family_label": "All selected role families",
            "lane_id": "all",
            "lane_label": "All configured locations",
            "query": search.get("name", ""),
            "found_count": len(ats_results),
            "page_count": 1,
            "engine": "direct-ats",
        }
    )
    _progress(progress, "Searching direct company career sources…", 1, 3, "direct-ats")

    try:
        openai_results = _openai_results_with_retry(
            search,
            family_definitions,
            requester=openai_requester,
        )
        results.extend(openai_results)
        selected_ids = set(search.get("role_family_ids", []))
        selected_families = [
            family
            for family in family_definitions
            if not selected_ids or family.get("id") in selected_ids
        ]
        for family in selected_families:
            family_id = family.get("id", "")
            sources.append(
                {
                    "source": "openai-web",
                    "label": "OpenAI source-backed web search",
                    "query_family": family_id,
                    "query_family_label": family.get("label", family_id),
                    "lane_id": "all",
                    "lane_label": "All configured locations",
                    "query": search.get("name", ""),
                    "found_count": sum(
                        family_id in result.get("role_family_ids", [])
                        for result in openai_results
                    ),
                    "page_count": 1,
                    "engine": "openai-web-search",
                }
            )
    except (RuntimeError, ValueError, TimeoutError, OSError) as exc:
        errors.append(f"OpenAI web search: {storage.clean(str(exc))}")
        sources.append(
            {
                "source": "openai-web",
                "label": "OpenAI source-backed web search",
                "query_family": "all",
                "query_family_label": "All selected role families",
                "lane_id": "all",
                "lane_label": "All configured locations",
                "query": search.get("name", ""),
                "found_count": 0,
                "page_count": 0,
                "engine": "openai-web-search",
            }
        )
    _progress(progress, "Searching the web for direct employer postings…", 2, 3, "openai-web")

    credentials = settings.adzuna_credentials()
    if credentials["app_id"] and credentials["app_key"]:
        adzuna_results, adzuna_sources, adzuna_errors = adzuna_role_results(
            search,
            family_definitions,
            credentials,
            fetcher=fetcher,
        )
        results.extend(adzuna_results)
        sources.extend(adzuna_sources)
        errors.extend(adzuna_errors)
    else:
        sources.append(
            {
                "source": "adzuna",
                "label": "Jobs by Adzuna",
                "query_family": "all",
                "query_family_label": "All selected role families",
                "lane_id": "all",
                "lane_label": "All configured locations",
                "query": search.get("name", ""),
                "found_count": 0,
                "page_count": 0,
                "engine": "not-configured",
            }
        )
        errors.append("Adzuna is not configured. Add its App ID and App Key in Settings to include those results.")
    _progress(progress, "Searching Jobs by Adzuna…", 3, 3, "adzuna")
    return {"results": results, "sources": sources, "errors": errors}


def _openai_results_with_retry(search, family_definitions, *, requester=None):
    last_error = None
    for attempt in range(OPENAI_DISCOVERY_ATTEMPTS):
        try:
            return _run_openai_results_attempt(
                search,
                family_definitions,
                requester=requester,
            )
        except (TimeoutError, OSError) as exc:
            last_error = exc
        except RuntimeError as exc:
            if "could not be completed" not in str(exc).lower():
                raise
            last_error = exc
        if attempt + 1 < OPENAI_DISCOVERY_ATTEMPTS:
            continue
    raise last_error


def _run_openai_results_attempt(search, family_definitions, *, requester=None):
    outcome = {}

    def run():
        try:
            outcome["results"] = openai_role_results(
                search,
                family_definitions,
                requester=requester,
            )
        except BaseException as exc:  # noqa: BLE001 - preserve provider errors for the caller.
            outcome["error"] = exc

    worker = threading.Thread(target=run, name="hunter-openai-discovery", daemon=True)
    worker.start()
    worker.join(OPENAI_DISCOVERY_ATTEMPT_TIMEOUT_SECONDS)
    if worker.is_alive():
        raise TimeoutError(
            f"OpenAI web search exceeded {OPENAI_DISCOVERY_ATTEMPT_TIMEOUT_SECONDS} seconds."
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("results", [])


def ats_inventory_results(search, family_definitions):
    company_by_id = {
        company.get("id", "").upper(): company
        for company in repository.read_companies()
    }
    results = []
    for candidate in repository.read_company_posting_candidates():
        company = company_by_id.get(candidate.get("company_id", "").upper(), {})
        if candidate_eligibility.company_is_excluded(company):
            continue
        if candidate.get("status") in {"ignored", "ingested", "pursued", "unavailable"}:
            continue
        if candidate.get("scan_state") == "unavailable":
            continue
        url = companies.normalize_url(candidate.get("url", ""))
        title = storage.clean(candidate.get("title", ""))
        family_ids = matching_family_ids(title, search, family_definitions)
        if not url or not title or not family_ids or not candidate_matches_focus(candidate, search):
            continue
        results.append(
            {
                "provider": "ats",
                "url": url,
                "title": title,
                "company": company.get("name", ""),
                "location": storage.clean(candidate.get("location", "")),
                "work_mode": storage.clean(candidate.get("work_mode", "")),
                "snippet": storage.clean(candidate.get("description_excerpt", ""))[:2_000],
                "description_text": storage.clean(candidate.get("description_excerpt", ""))[:4_000],
                "source_platform": storage.clean(candidate.get("source_platform", "")),
                "fit_score": storage.clean(candidate.get("fit_score", "")),
                "fit_summary": storage.clean(candidate.get("fit_summary", "")),
                "fit_checked_at": storage.clean(candidate.get("fit_checked_at", "")),
                "last_verified_at": storage.clean(candidate.get("last_verified_at", "")),
                "role_family_ids": family_ids,
                "lane_ids": [lane.get("id", "") for lane in search.get("lanes", [])],
            }
        )
    return results


def search_focus_terms(search):
    value = storage.clean(search.get("keywords", ""))
    if not value:
        return []
    if search.get("role_family_ids") and companies.normalized_text(value) in {
        "tpm",
        "technical program manager",
    }:
        return []
    terms = []
    for part in re.split(r"\s+OR\s+", value, flags=re.I):
        term = storage.clean(re.sub(r'^[\s(\"]+|[\s)\"]+$', "", part))
        if term and term.lower() not in {"and", "or", "not"} and term not in terms:
            terms.append(term)
    return terms


def candidate_matches_focus(candidate, search):
    terms = search_focus_terms(search)
    if not terms:
        return True
    text = companies.normalized_text(
        " ".join(
            [
                candidate.get("title", ""),
                candidate.get("description_excerpt", ""),
                candidate.get("matched_queries", ""),
            ]
        )
    )
    return any(companies.text_contains_phrase_variant(text, term) for term in terms)


def matching_family_ids(title, search, family_definitions):
    normalized_title = companies.normalized_text(title)
    selected_ids = set(search.get("role_family_ids", []))
    matches = []
    for family in family_definitions:
        if selected_ids and family.get("id") not in selected_ids:
            continue
        if any(
            companies.text_contains_phrase_variant(normalized_title, term)
            for term in family.get("terms", [])
        ):
            matches.append(family.get("id", ""))
    if matches or selected_ids:
        return matches
    keywords = companies.normalized_text(search.get("keywords", ""))
    return ["saved"] if keywords and keywords in normalized_title else []


def openai_role_results(search, family_definitions, requester=None):
    config = agent._settings()
    selected_ids = set(search.get("role_family_ids", []))
    selected_families = [
        family
        for family in family_definitions
        if not selected_ids or family.get("id") in selected_ids
    ]
    lane_lines = "\n".join(
        f"- {lane.get('id')}: {lane.get('location')} ({', '.join(lane.get('work_modes', []))})"
        for lane in search.get("lanes", [])
    )
    excluded = ", ".join(search.get("excluded_terms", [])) or "none"
    allowed_lane_ids = {lane.get("id", "") for lane in search.get("lanes", [])}
    results = []
    seen = set()
    for family in selected_families:
        family_id = family.get("id", "")
        prompt = (
            "Find currently open jobs that match this role family and at least one location lane below. "
            f"Return at most {OPENAI_RESULTS_PER_FAMILY} roles distributed across the eligible lanes. Search official "
            "employer career sites and direct employer ATS job-detail pages. Do not return LinkedIn, Adzuna, "
            "Indeed, Glassdoor, ZipRecruiter, staffing-agency reposts, search-result pages, career indexes, or "
            "expired postings. job_url must be the current individual posting URL that supports the returned "
            "title, company, location, and summary. The location and work mode must actually satisfy the selected "
            "lane; do not infer remote eligibility from a nationwide salary disclaimer. Do not invent missing facts. "
            "description_summary should be a concise source-backed summary of responsibilities and requirements. "
            "Use only the role_family_id and lane_id values listed below.\n\n"
            f"Role family:\n- {family_id}: {', '.join(family.get('terms', []))}\n\n"
            f"Location lanes:\n{lane_lines}\n\n"
            f"Required additional focus: {storage.clean(search.get('keywords', '')) or 'none'}\n"
            f"Excluded title terms: {excluded}\n"
        )
        payload = {
            "model": config["model"],
            "input": prompt,
            "tools": [{"type": "web_search", "search_context_size": "medium"}],
            "tool_choice": "required",
            "max_tool_calls": 8,
            "include": ["web_search_call.action.sources"],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "hunter_candidate_discovery",
                    "strict": True,
                    "schema": OPENAI_RESPONSE_SCHEMA,
                }
            },
            "max_output_tokens": 10_000,
            "reasoning": {"effort": "low"},
            "store": False,
            "metadata": {
                "feature": "candidate-discovery",
                "source": "openai-web-search",
                "role_family": family_id,
            },
        }
        response = (requester or agent._request_json)(
            f"{config['api_base']}/responses",
            config["token"],
            payload,
        )
        api_usage.log_usage(
            "candidate-discovery",
            response.get("model") or config["model"],
            response,
            operation="openai-web-search",
        )
        try:
            decoded = json.loads(agent._output_text(response))
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("OpenAI returned an unreadable candidate search result.") from exc
        roles = decoded.get("roles", []) if isinstance(decoded, dict) else []
        if not isinstance(roles, list):
            continue
        source_keys = openai_source_keys(response)
        for role in roles:
            url = companies.normalize_url((role or {}).get("job_url", ""))
            host = urlparse(url).netloc.lower().removeprefix("www.")
            returned_family_id = storage.clean((role or {}).get("role_family_id", ""))
            lane_id = storage.clean((role or {}).get("lane_id", ""))
            identity_keys = companies.posting_identity_keys(url)
            if (
                not url
                or url in seen
                or any(host == blocked or host.endswith(f".{blocked}") for blocked in OPENAI_BLOCKED_HOSTS)
                or returned_family_id != family_id
                or lane_id not in allowed_lane_ids
                or not identity_keys.intersection(source_keys)
            ):
                continue
            seen.add(url)
            work_mode = storage.clean((role or {}).get("work_mode", ""))
            results.append(
                {
                    "provider": "openai",
                    "url": url,
                    "title": storage.clean((role or {}).get("title", "")),
                    "company": storage.clean((role or {}).get("company", "")),
                    "location": storage.clean((role or {}).get("location", "")),
                    "work_mode": "" if work_mode == "unknown" else work_mode.title(),
                    "snippet": storage.clean((role or {}).get("description_summary", ""))[:4_000],
                    "description_text": storage.clean((role or {}).get("description_summary", ""))[:4_000],
                    "role_family_ids": [returned_family_id],
                    "lane_ids": [lane_id],
                }
            )
    return results


def openai_source_keys(response):
    keys = set()
    for item in (response or {}).get("output", []):
        if item.get("type") != "web_search_call":
            continue
        action = item.get("action") or {}
        for source in action.get("sources", []) or []:
            keys.update(companies.posting_identity_keys((source or {}).get("url", "")))
    return keys


def adzuna_role_results(search, family_definitions, credentials, *, fetcher=None):
    fetch = fetcher or companies.fetch_careers_page
    selected_ids = set(search.get("role_family_ids", []))
    selected_families = [
        family
        for family in family_definitions
        if not selected_ids or family.get("id") in selected_ids
    ]
    results = []
    sources = []
    errors = []
    query_count = 0
    for lane in search.get("lanes", []):
        for family in selected_families:
            if query_count >= ADZUNA_QUERY_LIMIT:
                break
            phrase = next(iter(family.get("strong_terms", []) or family.get("terms", [])), "")
            if not phrase:
                continue
            query_count += 1
            params = {
                "app_id": credentials["app_id"],
                "app_key": credentials["app_key"],
                "results_per_page": str(ADZUNA_RESULTS_PER_QUERY),
                "what_phrase": phrase,
                "where": storage.clean(lane.get("location", "")),
                "max_days_old": "90",
                "sort_by": "relevance",
                "content-type": "application/json",
            }
            if set(lane.get("work_modes", [])) == {"remote"}:
                params["what_and"] = "remote"
            if search.get("excluded_terms"):
                params["what_exclude"] = " ".join(search.get("excluded_terms", []))
            url = f"https://api.adzuna.com/v1/api/jobs/us/search/1?{urlencode(params)}"
            response = fetch(url)
            source_error = storage.clean(response.get("error", ""))
            items = []
            if not source_error:
                try:
                    payload = json.loads(response.get("html", "") or "{}")
                    items = payload.get("results", []) if isinstance(payload, dict) else []
                except (TypeError, json.JSONDecodeError):
                    source_error = "Adzuna returned unreadable JSON."
            if not isinstance(items, list):
                items = []
            accepted = 0
            for item in items:
                result = adzuna_result(item, family.get("id", ""), lane.get("id", ""))
                if not result:
                    continue
                results.append(result)
                accepted += 1
            sources.append(
                {
                    "source": "adzuna",
                    "label": "Jobs by Adzuna",
                    "query_family": family.get("id", ""),
                    "query_family_label": family.get("label", ""),
                    "lane_id": lane.get("id", ""),
                    "lane_label": lane.get("label", "") or lane.get("location", ""),
                    "query": f'{phrase} · {lane.get("location", "")}',
                    "found_count": accepted,
                    "page_count": 0 if source_error else 1,
                    "engine": "adzuna-api",
                }
            )
            if source_error:
                errors.append(
                    f"Jobs by Adzuna · {family.get('label', '')} · "
                    f"{lane.get('label', '') or lane.get('location', '')}: {source_error}"
                )
        if query_count >= ADZUNA_QUERY_LIMIT:
            break
    return results, sources, errors


def adzuna_result(item, family_id, lane_id):
    # Adzuna requires consumers to use its returned redirect URL for attribution;
    # do not canonicalize away its query parameters.
    url = storage.clean((item or {}).get("redirect_url", ""))
    if urlparse(url).scheme not in {"http", "https"}:
        url = ""
    title = storage.clean((item or {}).get("title", ""))
    company = (item or {}).get("company") or {}
    location = (item or {}).get("location") or {}
    company_name = storage.clean(
        company.get("display_name", "") or company.get("canonical_name", "")
    ) if isinstance(company, dict) else ""
    location_name = storage.clean(location.get("display_name", "")) if isinstance(location, dict) else ""
    if not url or not title:
        return None
    return {
        "provider": "adzuna",
        "url": url,
        "title": title,
        "company": company_name,
        "location": location_name,
        "work_mode": "Remote" if "remote" in f"{title} {location_name} {(item or {}).get('description', '')}".lower() else "",
        "snippet": storage.clean((item or {}).get("description", ""))[:2_000],
        "role_family_ids": [family_id],
        "lane_ids": [lane_id],
        "provider_record_id": storage.clean((item or {}).get("id", "")),
    }


def _progress(progress, message, completed_steps, total_steps, source):
    if progress:
        progress(
            {
                "phase": "searching",
                "message": message,
                "completed_steps": completed_steps,
                "total_steps": total_steps,
                "source": source,
            }
        )
