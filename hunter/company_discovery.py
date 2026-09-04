"""Company-first discovery for reviewable smaller-company targets."""

import re
import json
from datetime import datetime
from urllib.parse import urlparse

from . import agent, api_usage, companies, repository, settings, storage


DEFAULT_FOCUS = (
    "interactive customer experiences, builder productivity and workflow platforms, "
    "complex technical products and services"
)
DEFAULT_SIZES = ["51–200 employees", "201–500 employees"]
DEFAULT_SOURCES = [
    "direct-employers",
    "startup-directories",
    "venture-portfolios",
    "linkedin-companies",
]
DEFAULT_LOCATION_PREFERENCES = ["us-remote", "metro-area"]
DEFAULT_REMOTE_REGION = "United States"
DEFAULT_METRO_AREA = "Minneapolis-Saint Paul metro"
MAX_RESULTS = 40
MAX_RESEARCH = 12
MAX_FOCUS_LANES = 6
MAX_PER_FOCUS_LANE = 4
MAX_SOURCE_RESULTS = 24
MAX_WEB_SEARCH_CALLS = 5
LOCATION_RESEARCH_BATCH_SIZE = 5
MAX_LOCATION_RESEARCH_TOOL_CALLS = 10
DISCOVERY_MODEL = "gpt-5.6-luna"

SIZE_OPTIONS = {
    "11–50 employees": (11, 50),
    "51–200 employees": (51, 200),
    "201–500 employees": (201, 500),
    "501–1,000 employees": (501, 1_000),
    "1,001+ employees": (1_001, None),
}

LOCATION_DEFINITIONS = {
    "us-remote": {
        "label": "U.S. remote",
    },
    "metro-area": {
        "label": "Metro area",
    },
}

SOURCE_DEFINITIONS = {
    "direct-employers": {
        "label": "Direct employer sites",
        "engine": "google",
        "domains": [],
        "query": '("careers" OR "jobs" OR "join our team")',
    },
    "startup-directories": {
        "label": "Startup directory",
        "engine": "google",
        "domains": ["wellfound.com", "ycombinator.com"],
        "query": "(site:wellfound.com/company OR site:ycombinator.com/companies)",
    },
    "venture-portfolios": {
        "label": "Venture portfolio",
        "engine": "google",
        "domains": [
            "jobs.techstars.com",
            "jobs.luxcapital.com",
            "careers.bitkraft.vc",
            "jobs.spacetalent.org",
        ],
        "query": (
            "(site:jobs.techstars.com/companies OR site:jobs.luxcapital.com/companies "
            "OR site:careers.bitkraft.vc/companies OR site:jobs.spacetalent.org/companies)"
        ),
    },
    "linkedin-companies": {
        "label": "Public company profile",
        "engine": "linkedin-companies",
        "domains": ["linkedin.com"],
        "query": "",
    },
}

FOCUS_LANES_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "focus_lanes": {
            "type": "array",
            "minItems": 3,
            "maxItems": MAX_FOCUS_LANES,
            "items": {"type": "string"},
        }
    },
    "required": ["focus_lanes"],
    "additionalProperties": False,
}

DISCOVERY_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "maxItems": MAX_SOURCE_RESULTS,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source_url": {"type": "string"},
                    "focus_lane": {"type": "string"},
                    "evidence": {"type": "string"},
                    "company_size": {"type": "string"},
                    "industry": {"type": "string"},
                    "description": {"type": "string"},
                    "website": {"type": "string"},
                    "company_profile_url": {"type": "string"},
                    "location_fit": {
                        "type": "string",
                        "enum": ["us-remote", "metro-area", "both", "unknown"],
                    },
                    "location": {"type": "string"},
                    "remote_policy": {"type": "string"},
                    "location_evidence": {"type": "string"},
                },
                "required": [
                    "name",
                    "source_url",
                    "focus_lane",
                    "evidence",
                    "company_size",
                    "industry",
                    "description",
                    "website",
                    "company_profile_url",
                    "location_fit",
                    "location",
                    "remote_policy",
                    "location_evidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["companies"],
    "additionalProperties": False,
}

WEBSITE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "maxItems": MAX_RESULTS,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "website": {"type": "string"},
                },
                "required": ["name", "website"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["companies"],
    "additionalProperties": False,
}

LOCATION_RESEARCH_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "maxItems": LOCATION_RESEARCH_BATCH_SIZE,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "location_fit": {
                        "type": "string",
                        "enum": ["us-remote", "metro-area", "both", "unknown"],
                    },
                    "location": {"type": "string"},
                    "remote_policy": {"type": "string"},
                    "location_evidence": {"type": "string"},
                    "source_urls": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "name",
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

SOURCE_HOSTS = {
    "startup-directories": {"wellfound.com", "www.wellfound.com", "www.ycombinator.com", "ycombinator.com"},
    "venture-portfolios": {
        "jobs.techstars.com",
        "jobs.luxcapital.com",
        "careers.bitkraft.vc",
        "jobs.spacetalent.org",
    },
    "linkedin-companies": {"linkedin.com", "www.linkedin.com"},
}

DIRECT_SOURCE_BLOCKED_HOSTS = {
    "bing.com",
    "facebook.com",
    "glassdoor.com",
    "google.com",
    "indeed.com",
    "instagram.com",
    "linkedin.com",
    "wellfound.com",
    "x.com",
    "yahoo.com",
    "ycombinator.com",
    "ziprecruiter.com",
}

NON_COMPANY_WEBSITE_HOSTS = {
    "ashbyhq.com",
    "facebook.com",
    "glassdoor.com",
    "greenhouse.io",
    "indeed.com",
    "instagram.com",
    "lever.co",
    "linkedin.com",
    "techstars.com",
    "wellfound.com",
    "x.com",
    "ycombinator.com",
}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def emit_progress(callback, phase, message, completed_steps, total_steps, source=""):
    if callback is None:
        return
    callback(
        {
            "phase": phase,
            "message": message,
            "completed_steps": completed_steps,
            "total_steps": total_steps,
            "source": source,
        }
    )


def normalized_focus_terms(value):
    if isinstance(value, list):
        raw_terms = value
    else:
        raw_terms = re.split(r"[\n,]", storage.clean(value))
    terms = []
    for value in raw_terms:
        cleaned = storage.clean(str(value or "")).lower()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
    return terms[:MAX_FOCUS_LANES]


def normalized_planned_focus_terms(values):
    terms = []
    for value in values or []:
        cleaned = storage.clean(str(value or "")).lower()
        if (
            cleaned
            and len(cleaned) <= 80
            and len(cleaned.split()) <= 10
            and cleaned not in terms
        ):
            terms.append(cleaned)
    return terms[:MAX_FOCUS_LANES]


def openai_focus_lane_search(config, focus_terms):
    prompt = (
        "Turn the employer-search focus below into distinct company-market search lanes. "
        "The goal is broad recall without drifting away from the user's intent. Expand broad phrases "
        "such as AI into different kinds of products, platforms, infrastructure, workflows, and "
        "technical services where the same interest could apply. Keep lanes broad enough to identify "
        "many employers. Do not use job titles, company names, locations, employee counts, or near-duplicate "
        "wording. Return between 3 and 6 concise lanes, each 2 to 8 words.\n\n"
        "User focus:\n- " + "\n- ".join(focus_terms)
    )
    payload = {
        "model": DISCOVERY_MODEL,
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "hunter_company_focus_lanes",
                "strict": True,
                "schema": FOCUS_LANES_RESPONSE_SCHEMA,
            }
        },
        "max_output_tokens": 800,
        "reasoning": {"effort": "low"},
        "store": False,
        "metadata": {"feature": "company-discovery", "source": "focus-planning"},
    }
    response = agent._request_json(
        f"{config['api_base']}/responses",
        config["token"],
        payload,
    )
    api_usage.log_usage(
        "company-discovery",
        response.get("model") or DISCOVERY_MODEL,
        response,
        operation="focus-planning",
    )
    try:
        result = json.loads(agent._output_text(response))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OpenAI returned unreadable company search lanes.") from exc
    lanes = normalized_planned_focus_terms(
        result.get("focus_lanes", []) if isinstance(result, dict) else []
    )
    if len(lanes) < 3:
        raise RuntimeError("OpenAI did not return enough distinct company search lanes.")
    return lanes


def normalized_sizes(values):
    selected = []
    for value in values or []:
        normalized = companies.normalize_company_size(value)
        if normalized in SIZE_OPTIONS and normalized not in selected:
            selected.append(normalized)
    return selected or list(DEFAULT_SIZES)


def normalized_sources(values):
    selected = []
    for value in values or []:
        cleaned = storage.clean(value).lower()
        if cleaned in SOURCE_DEFINITIONS and cleaned not in selected:
            selected.append(cleaned)
    return selected or list(DEFAULT_SOURCES)


def normalized_location_preferences(values):
    selected = []
    for value in values or []:
        cleaned = storage.clean(value).lower()
        if cleaned in LOCATION_DEFINITIONS and cleaned not in selected:
            selected.append(cleaned)
    return selected or list(DEFAULT_LOCATION_PREFERENCES)


def location_definition(location_id, remote_region="", metro_area=""):
    remote_region = storage.clean(remote_region) or DEFAULT_REMOTE_REGION
    metro_area = storage.clean(metro_area) or DEFAULT_METRO_AREA
    if location_id == "us-remote":
        return {
            "label": f"Remote in {remote_region}",
            "prompt": (
                f"The company explicitly supports remote work for employees based in {remote_region}, "
                f"is remote-first there, or has current remote roles open to people in {remote_region}. "
                "Do not infer remote support from a distributed team."
            ),
            "query": f'"remote" "{remote_region}"',
        }
    return {
        "label": metro_area,
        "prompt": (
            f"The company has a headquarters, office, or current hiring presence in {metro_area}."
        ),
        "query": f'"{metro_area}"',
    }


def normalized_location_fit(value):
    cleaned = storage.clean(value).lower()
    aliases = {
        "remote": "us-remote",
        "u.s. remote": "us-remote",
        "us remote": "us-remote",
        "twin cities": "metro-area",
        "twin-cities": "metro-area",
        "minneapolis": "metro-area",
        "minneapolis-saint paul": "metro-area",
        "metro": "metro-area",
        "metro area": "metro-area",
    }
    return aliases.get(cleaned, cleaned if cleaned in {"us-remote", "metro-area", "both"} else "")


def location_fit_matches(value, selected_locations):
    fit = normalized_location_fit(value)
    if fit == "both":
        return True
    return fit in selected_locations


def location_fit_label(value, remote_region="", metro_area=""):
    fit = normalized_location_fit(value)
    remote_region = storage.clean(remote_region) or DEFAULT_REMOTE_REGION
    metro_area = storage.clean(metro_area) or DEFAULT_METRO_AREA
    return {
        "us-remote": f"Remote in {remote_region}",
        "metro-area": metro_area,
        "both": f"Remote in {remote_region} and {metro_area}",
    }.get(fit, "Verify location")


def focus_query(terms):
    quoted = [f'"{term}"' if " " in term else term for term in terms]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return quoted[0]
    return "(" + " OR ".join(quoted) + ")"


def source_query(
    source_id,
    focus_terms,
    selected_locations=None,
    remote_region="",
    metro_area="",
):
    source = SOURCE_DEFINITIONS[source_id]
    focus = focus_query(focus_terms)
    location = " OR ".join(
        location_definition(item, remote_region, metro_area)["query"]
        for item in normalized_location_preferences(selected_locations)
    )
    if source["engine"] == "linkedin-companies":
        return " ".join(part for part in [" ".join(focus_terms), f"({location})"] if part)
    return " ".join(part for part in [focus, f"({location})", source["query"]] if part)


def openai_source_search(
    config,
    source_id,
    focus_terms,
    selected_sizes,
    selected_locations=None,
    remote_region="",
    metro_area="",
):
    source = SOURCE_DEFINITIONS[source_id]
    selected_locations = normalized_location_preferences(selected_locations)
    lanes = "\n".join(f"- {term}: at most {MAX_PER_FOCUS_LANE}" for term in focus_terms)
    location_requirements = "\n".join(
        f"- {location_definition(item, remote_region, metro_area)['label']}: "
        f"{location_definition(item, remote_region, metro_area)['prompt']}"
        for item in selected_locations
    )
    source_scope = (
        "Use only company profile pages from the allowed domains."
        if source["domains"]
        else (
            "Use official company websites, official careers pages, or direct employer ATS pages. "
            "Do not use search-result pages, staffing agencies, job aggregators, social media, or news articles."
        )
    )
    prompt = (
        "Find smaller companies that could be good employer targets. Search every focus lane "
        "independently and keep the results balanced; do not let one lane consume another lane's quota. "
        "Return no more than the stated number for each lane and no more than "
        f"{MAX_SOURCE_RESULTS} companies total. {source_scope} "
        "Prefer current, specific evidence. Do not invent employee counts: use an empty string when the "
        "size is not supported by a source. Return the direct profile URL as source_url and the "
        "company's official public homepage as website. Website must not be a directory, social-media, "
        "job-board, or ATS URL; leave it empty unless the official site is supported by the source. A company may "
        "appear only once. Prefer companies with evidence for at least one location eligibility option "
        "below, but do not discard an otherwise strong employer solely because this source does not prove "
        "location eligibility. In that case set location_fit to unknown and leave location_evidence empty; "
        "do not guess.\n\n"
        f"Focus lanes:\n{lanes}\n\n"
        f"Location eligibility (match any):\n{location_requirements}\n\n"
        f"Target company sizes: {', '.join(selected_sizes)}\n"
        f"Source family: {source['label']}\n"
    )
    payload = {
        "model": DISCOVERY_MODEL,
        "input": prompt,
        "tools": [{
            "type": "web_search",
            "search_context_size": "medium",
            **({"filters": {"allowed_domains": source["domains"]}} if source["domains"] else {}),
        }],
        "tool_choice": "required",
        "max_tool_calls": MAX_WEB_SEARCH_CALLS,
        "include": ["web_search_call.action.sources"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "hunter_company_discovery",
                "strict": True,
                "schema": DISCOVERY_RESPONSE_SCHEMA,
            }
        },
        "max_output_tokens": 8_000,
        "reasoning": {"effort": "low"},
        "store": False,
        "metadata": {"feature": "company-discovery", "source": source_id},
    }
    response = agent._request_json(
        f"{config['api_base']}/responses",
        config["token"],
        payload,
    )
    api_usage.log_usage(
        "company-discovery",
        response.get("model") or DISCOVERY_MODEL,
        response,
        operation=source_id,
    )
    try:
        result = json.loads(agent._output_text(response))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OpenAI returned an unreadable {source['label'].lower()} result.") from exc
    rows = result.get("companies", []) if isinstance(result, dict) else []
    return rows if isinstance(rows, list) else []


def official_company_website(value):
    website = companies.normalize_url(value)
    host = urlparse(website).netloc.lower().removeprefix("www.")
    if not website or not host:
        return ""
    if any(host == blocked or host.endswith(f".{blocked}") for blocked in NON_COMPANY_WEBSITE_HOSTS):
        return ""
    return website


def openai_company_website_search(config, candidates):
    names = [storage.clean(item.get("company", "")) for item in candidates]
    names = [name for name in names if name][:MAX_RESULTS]
    if not names:
        return {}
    prompt = (
        "Find the official public homepage for each company below. Return the canonical company website, "
        "not a directory profile, social-media page, news article, job board, careers page, or ATS page. "
        "Use an empty string when the official website cannot be verified. Do not substitute a similarly "
        "named company's site.\n\nCompanies:\n- " + "\n- ".join(names)
    )
    payload = {
        "model": DISCOVERY_MODEL,
        "input": prompt,
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "tool_choice": "required",
        "max_tool_calls": MAX_WEB_SEARCH_CALLS,
        "include": ["web_search_call.action.sources"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "hunter_company_websites",
                "strict": True,
                "schema": WEBSITE_RESPONSE_SCHEMA,
            }
        },
        "max_output_tokens": 3_000,
        "reasoning": {"effort": "low"},
        "store": False,
        "metadata": {"feature": "company-discovery", "source": "website-lookup"},
    }
    response = agent._request_json(
        f"{config['api_base']}/responses",
        config["token"],
        payload,
    )
    api_usage.log_usage(
        "company-discovery",
        response.get("model") or DISCOVERY_MODEL,
        response,
        operation="website-lookup",
    )
    try:
        result = json.loads(agent._output_text(response))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OpenAI returned unreadable company website results.") from exc
    rows = result.get("companies", []) if isinstance(result, dict) else []
    return {
        companies.company_merge_key(row.get("name", "")): official_company_website(row.get("website", ""))
        for row in rows
        if isinstance(row, dict) and companies.company_merge_key(row.get("name", ""))
    }


def openai_company_location_search(
    config,
    company_rows,
    remote_region=DEFAULT_REMOTE_REGION,
    metro_area=DEFAULT_METRO_AREA,
    batch_number=1,
):
    targets = []
    for company in (company_rows or [])[:LOCATION_RESEARCH_BATCH_SIZE]:
        targets.append(
            {
                "name": storage.clean(company.get("name", "")),
                "website": companies.normalize_url(company.get("website", "")),
                "careers_url": companies.normalize_url(company.get("careers_url", "")),
                "company_profile_url": companies.normalize_url(
                    company.get("company_profile_url", "")
                ),
            }
        )
    targets = [target for target in targets if target["name"]]
    if not targets:
        return []

    remote_region = storage.clean(remote_region) or DEFAULT_REMOTE_REGION
    metro_area = storage.clean(metro_area) or DEFAULT_METRO_AREA
    prompt = (
        "Research current employer location eligibility for every company in the JSON list below. "
        "Use current official company, careers, or job pages whenever possible. A company is us-remote "
        f"only when the evidence explicitly supports remote employees or current remote roles in {remote_region}; "
        "do not infer eligibility from a distributed team or a generic remote statement. A company is "
        f"metro-area only when it has an office, headquarters, or current hiring presence in {metro_area}. "
        "Use both only when both standards are supported. Use unknown when current evidence is insufficient. "
        "Do not guess from a company headquarters, employee profile, or stale third-party summary. Keep the "
        "evidence concise and return the direct supporting URLs. Return exactly one result for every company, "
        "preserving each company name exactly as supplied.\n\nCompanies:\n"
        + json.dumps(targets, indent=2)
    )
    payload = {
        "model": DISCOVERY_MODEL,
        "input": prompt,
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "tool_choice": "required",
        "max_tool_calls": MAX_LOCATION_RESEARCH_TOOL_CALLS,
        "include": ["web_search_call.action.sources"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "hunter_company_location_research",
                "strict": True,
                "schema": LOCATION_RESEARCH_RESPONSE_SCHEMA,
            }
        },
        "max_output_tokens": 3_000,
        "reasoning": {"effort": "low"},
        "store": False,
        "metadata": {
            "feature": "company-location-research",
            "batch": str(batch_number),
        },
    }
    response = agent._request_json(
        f"{config['api_base']}/responses",
        config["token"],
        payload,
    )
    api_usage.log_usage(
        "company-location-research",
        response.get("model") or DISCOVERY_MODEL,
        response,
        operation=f"batch-{batch_number}",
    )
    try:
        result = json.loads(agent._output_text(response))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OpenAI returned unreadable company location research.") from exc
    rows = result.get("companies", []) if isinstance(result, dict) else []
    return rows if isinstance(rows, list) else []


def likely_company_profile(source_id, url):
    parsed = urlparse(companies.normalize_url(url))
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if source_id == "direct-employers":
        normalized_host = host.removeprefix("www.")
        if not normalized_host:
            return False
        return not any(
            normalized_host == blocked or normalized_host.endswith(f".{blocked}")
            for blocked in DIRECT_SOURCE_BLOCKED_HOSTS
        )
    if host not in SOURCE_HOSTS[source_id]:
        return False
    if source_id == "linkedin-companies":
        return bool(re.search(r"/company/[^/?#]+", path))
    if host == "wellfound.com":
        return bool(re.fullmatch(r"/company/[^/]+/?", path))
    return "/companies/" in path


def company_name_from_result(result, source_id):
    title = storage.clean((result or {}).get("title", ""))
    title = re.sub(r"\s*[|·]\s*(?:LinkedIn|Wellfound|Y Combinator).*$", "", title, flags=re.I)
    title = re.sub(r"\s*[|·]\s*(?:Techstars|Lux Capital|BITKRAFT|SpaceTalent).*$", "", title, flags=re.I)
    title = re.sub(r"^(?:jobs?|careers?|open positions)\s+(?:at|with)\s+", "", title, flags=re.I)
    title = re.sub(r"\s*\((?:we(?:'|’)re\s+)?hiring!?\)\s*", " ", title, flags=re.I)
    title = re.sub(r"\s+careers?\s*[-–—:]\s*(?:insights?\s+and\s+opportunities|explore\s+current\s+opportunities).*$", "", title, flags=re.I)
    title = re.sub(r"\s+(?:jobs?|careers?|open positions)$", "", title, flags=re.I)
    if source_id != "linkedin-companies" and ":" in title:
        title = title.split(":", 1)[0]
    title = storage.clean(title)
    if (
        not title
        or len(title) > 100
        or len(title.split()) > 12
        or title.lower() in {"company", "companies", "jobs", "careers", "startup jobs"}
    ):
        return ""
    return title


def balanced_candidate_batch(candidates, source_ids, limit=MAX_RESULTS):
    queues = {
        source_id: [item for item in candidates if item.get("source_id") == source_id]
        for source_id in source_ids
    }
    batch = []
    while len(batch) < limit and any(queues.values()):
        for source_id in source_ids:
            queue = queues.get(source_id, [])
            if queue and len(batch) < limit:
                batch.append(queue.pop(0))
    return batch


def size_bounds(value):
    normalized = companies.normalize_company_size(value)
    if normalized in SIZE_OPTIONS:
        return SIZE_OPTIONS[normalized]
    numbers = [int(item.replace(",", "")) for item in re.findall(r"\d[\d,]*", normalized)]
    if not numbers:
        return None
    return min(numbers), max(numbers)


def size_matches(value, selected_sizes):
    bounds = size_bounds(value)
    if bounds is None:
        return None
    minimum, maximum = bounds
    maximum = maximum if maximum is not None else minimum
    return any(
        minimum >= selected_min
        and (selected_max is None or maximum <= selected_max)
        for selected_min, selected_max in (SIZE_OPTIONS[item] for item in selected_sizes)
    )


def inferred_size(text):
    match = re.search(
        r"\b\d[\d,]*(?:\s*[-–]\s*\d[\d,]*|\+)?\s+(?:employees?|people)\b",
        storage.clean(text),
        re.I,
    )
    return companies.normalize_company_size(match.group(0)) if match else ""


def score_company(
    metadata,
    focus_terms,
    selected_sizes,
    source_label,
    evidence="",
    location_fit="",
    remote_region="",
    metro_area="",
):
    combined = " ".join(
        storage.clean(value).lower()
        for value in [
            metadata.get("company", ""),
            metadata.get("company_industry", ""),
            metadata.get("industry", ""),
            metadata.get("company_description", ""),
            evidence,
        ]
        if storage.clean(value)
    )
    focus_matches = [term for term in focus_terms if term in combined]
    domain_matches = [
        (term, weight)
        for term, weight in settings.domain_fit_terms()
        if term in combined and term not in focus_matches
    ]
    domain_matches.sort(key=lambda item: (-item[1], item[0]))
    size_match = size_matches(metadata.get("company_size", ""), selected_sizes)

    score = 12
    score += 25 if size_match is True else 5 if size_match is None else 0
    score += min(36, len(focus_matches) * 12)
    score += min(26, sum(weight for _term, weight in domain_matches[:4]) // 2)
    if metadata.get("website") or metadata.get("company_profile_url"):
        score += 5
    if normalized_location_fit(location_fit):
        score += 8
    score = min(100, score)

    signals = [*focus_matches, *(term for term, _weight in domain_matches)]
    signals = list(dict.fromkeys(signals))[:3]
    if signals:
        summary = f"Matched profile signals: {', '.join(signals)}."
    else:
        summary = f"Found through {source_label.lower()}; profile fit needs review."
    company_size = companies.normalize_company_size(metadata.get("company_size", ""))
    if company_size:
        summary += f" {company_size}."
    else:
        summary += " Company size needs verification."
    if normalized_location_fit(location_fit):
        summary += f" Location: {location_fit_label(location_fit, remote_region, metro_area)}."
    else:
        summary += " Location eligibility needs verification."
    return score, summary


def update_discovery_evidence(company_id, values):
    rows = repository.read_companies()
    row = next((item for item in rows if item.get("id", "") == company_id), None)
    if row is None:
        raise ValueError(f"No company found with id {company_id}.")
    fields = [
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
    ]
    for field in fields:
        row[field] = storage.clean(values.get(field, ""))
    repository.update_company_fields(
        company_id,
        {field: row.get(field, "") for field in fields},
    )
    return companies.get_company(company_id)


def update_company_location_evidence(company_id, values):
    rows = repository.read_companies()
    row = next((item for item in rows if item.get("id", "") == company_id), None)
    if row is None:
        raise ValueError(f"No company found with id {company_id}.")
    fields = [
        "company_location_fit",
        "company_location",
        "company_remote_policy",
        "company_location_evidence",
        "company_location_checked_at",
    ]
    for field in fields:
        row[field] = storage.clean(values.get(field, ""))
    repository.update_company_fields(
        company_id,
        {field: row.get(field, "") for field in fields},
    )
    return companies.get_company(company_id)


def research_tracked_company_locations(
    remote_region=DEFAULT_REMOTE_REGION,
    metro_area=DEFAULT_METRO_AREA,
    *,
    include_known=False,
    batch_size=LOCATION_RESEARCH_BATCH_SIZE,
    searcher=None,
    progress=None,
):
    remote_region = storage.clean(remote_region) or DEFAULT_REMOTE_REGION
    metro_area = storage.clean(metro_area) or DEFAULT_METRO_AREA
    batch_size = max(1, min(LOCATION_RESEARCH_BATCH_SIZE, int(batch_size or 1)))
    tracked = [
        company
        for company in repository.read_companies()
        if company.get("tracking_status", "").lower() == "tracked"
    ]
    targets = [
        company
        for company in tracked
        if include_known or not normalized_location_fit(company.get("company_location_fit", ""))
    ]
    timestamp = now_iso()
    batches = [targets[index:index + batch_size] for index in range(0, len(targets), batch_size)]
    config = None if searcher is not None else agent._settings()
    updated = []
    needs_verification = []
    errors = []

    emit_progress(
        progress,
        "preparing",
        f"Preparing location research for {len(targets)} tracked companies…",
        0,
        len(batches),
    )
    for batch_index, batch in enumerate(batches, start=1):
        emit_progress(
            progress,
            "researching-locations",
            f"Researching company locations ({batch_index} of {len(batches)})…",
            batch_index - 1,
            len(batches),
        )
        try:
            if searcher is None:
                researched = openai_company_location_search(
                    config,
                    batch,
                    remote_region,
                    metro_area,
                    batch_index,
                )
            else:
                researched = searcher(batch, remote_region, metro_area) or []
        except RuntimeError as exc:
            errors.append(f"Batch {batch_index}: {storage.clean(str(exc))}")
            continue

        by_name = {
            companies.company_merge_key(item.get("name", "")): item
            for item in researched
            if isinstance(item, dict) and companies.company_merge_key(item.get("name", ""))
        }
        for company in batch:
            item = by_name.get(companies.company_merge_key(company.get("name", "")))
            if item is None:
                errors.append(f"{company.get('name', 'Company')}: no location result returned")
                continue
            fit = normalized_location_fit(item.get("location_fit", ""))
            evidence = storage.clean(item.get("location_evidence", ""))
            source_urls = []
            for value in item.get("source_urls", []) if isinstance(item.get("source_urls"), list) else []:
                url = companies.normalize_url(value)
                if url and url not in source_urls:
                    source_urls.append(url)
            if fit and (not evidence or not source_urls):
                fit = ""
            if source_urls:
                evidence = f"{evidence} Source{'s' if len(source_urls) != 1 else ''}: " + ", ".join(source_urls)
            saved = update_company_location_evidence(
                company.get("id", ""),
                {
                    "company_location_fit": fit,
                    "company_location": item.get("location", ""),
                    "company_remote_policy": item.get("remote_policy", ""),
                    "company_location_evidence": evidence,
                    "company_location_checked_at": timestamp,
                },
            )
            if fit:
                updated.append(saved)
            else:
                needs_verification.append(saved)

        emit_progress(
            progress,
            "batch-complete",
            f"Finished location research batch {batch_index} of {len(batches)}.",
            batch_index,
            len(batches),
        )

    emit_progress(
        progress,
        "complete",
        f"Location research complete: {len(updated)} verified and {len(needs_verification)} need verification.",
        len(batches),
        len(batches),
    )
    return {
        "tracked_count": len(tracked),
        "target_count": len(targets),
        "batch_count": len(batches),
        "updated_count": len(updated),
        "needs_verification_count": len(needs_verification),
        "error_count": len(errors),
        "updated": updated,
        "needs_verification": needs_verification,
        "errors": errors,
        "remote_region": remote_region,
        "metro_area": metro_area,
    }


def run_company_discovery(
    focus=DEFAULT_FOCUS,
    sizes=None,
    sources=None,
    locations=None,
    remote_region=DEFAULT_REMOTE_REGION,
    metro_area=DEFAULT_METRO_AREA,
    searcher=None,
    researcher=None,
    progress=None,
):
    from . import company_evaluation

    focus_terms = normalized_focus_terms(focus)
    if not focus_terms:
        raise ValueError("Add at least one company search focus.")
    selected_sizes = normalized_sizes(sizes)
    selected_sources = normalized_sources(sources)
    selected_locations = normalized_location_preferences(locations)
    remote_region = storage.clean(remote_region) or DEFAULT_REMOTE_REGION
    metro_area = storage.clean(metro_area) or DEFAULT_METRO_AREA
    evaluation_profile = company_evaluation.save_profile(
        {
            "focus": focus,
            "sizes": selected_sizes,
            "locations": selected_locations,
            "remote_region": remote_region,
            "metro_area": metro_area,
        }
    )
    timestamp = now_iso()
    total_steps = len(selected_sources) + 2
    emit_progress(progress, "preparing", "Preparing company discovery…", 0, total_steps)
    openai_config = None
    errors = []
    search_focus_terms = list(focus_terms)
    if searcher is None:
        openai_config = agent._settings()
        emit_progress(
            progress,
            "planning",
            "Expanding the search into company market lanes…",
            0,
            total_steps,
        )
        try:
            search_focus_terms = openai_focus_lane_search(openai_config, focus_terms)
        except RuntimeError as exc:
            errors.append(f"Search planning: {storage.clean(str(exc))}")

    candidates = []
    seen = set()
    source_runs = []
    for source_index, source_id in enumerate(selected_sources):
        source = SOURCE_DEFINITIONS[source_id]
        query = source_query(
            source_id,
            search_focus_terms,
            selected_locations,
            remote_region,
            metro_area,
        )
        emit_progress(
            progress,
            "searching",
            f"Searching {source['label'].lower()} ({source_index + 1} of {len(selected_sources)})…",
            source_index + 1,
            total_steps,
            source_id,
        )
        try:
            if openai_config is not None:
                items = openai_source_search(
                    openai_config,
                    source_id,
                    search_focus_terms,
                    selected_sizes,
                    selected_locations,
                    remote_region,
                    metro_area,
                )
            else:
                items = searcher(source["engine"], query, 0) or []
        except RuntimeError as exc:
            errors.append(f"{source['label']}: {storage.clean(str(exc))}")
            items = []
        kept = 0
        lane_counts = {}
        location_counts = {}
        for item in items:
            url = companies.normalize_url(
                (item or {}).get("source_url", "") or (item or {}).get("url", "")
            )
            if not likely_company_profile(source_id, url):
                continue
            name = storage.clean((item or {}).get("name", "")) or company_name_from_result(item, source_id)
            key = companies.company_merge_key(name)
            if not name or not key or key in seen:
                continue
            focus_lane = storage.clean((item or {}).get("focus_lane", "")).lower()
            if focus_lane not in search_focus_terms:
                lane_text = " ".join(
                    [
                        focus_lane,
                        storage.clean((item or {}).get("evidence", "")).lower(),
                        storage.clean((item or {}).get("description", "")).lower(),
                    ]
                )
                focus_lane = next((term for term in search_focus_terms if term in lane_text), "")
            if openai_config is not None and not focus_lane:
                continue
            location_fit = normalized_location_fit((item or {}).get("location_fit", ""))
            location_evidence = storage.clean((item or {}).get("location_evidence", ""))
            if openai_config is not None and (
                not location_fit_matches(location_fit, selected_locations)
                or not location_evidence
            ):
                location_fit = ""
                location_evidence = ""
            if focus_lane:
                lane_count = lane_counts.get(focus_lane, 0)
                if lane_count >= MAX_PER_FOCUS_LANE:
                    continue
                lane_counts[focus_lane] = lane_count + 1
            location_key = location_fit or "verify"
            location_counts[location_key] = location_counts.get(location_key, 0) + 1
            seen.add(key)
            kept += 1
            candidates.append(
                {
                    "company": name,
                    "source_id": source_id,
                    "source_label": source["label"],
                    "source_url": url,
                    "query": query,
                    "focus_lane": focus_lane,
                    "evidence": storage.clean(
                        (item or {}).get("evidence", "") or (item or {}).get("snippet", "")
                    )[:2_000],
                    "company_size": storage.clean((item or {}).get("company_size", "")),
                    "industry": storage.clean((item or {}).get("industry", "")),
                    "company_description": storage.clean((item or {}).get("description", "")),
                    "website": companies.normalize_url((item or {}).get("website", "")),
                    "company_profile_url": companies.normalize_url(
                        (item or {}).get("company_profile_url", "")
                    ) or (url if source_id == "linkedin-companies" else ""),
                    "location_fit": location_fit,
                    "location": storage.clean((item or {}).get("location", "")),
                    "remote_policy": storage.clean((item or {}).get("remote_policy", "")),
                    "location_evidence": location_evidence,
                }
            )
        source_runs.append(
            {
                "source": source_id,
                "label": source["label"],
                "query": query,
                "found_count": len(items),
                "qualified_count": kept,
                "lane_counts": lane_counts,
                "location_counts": location_counts,
            }
        )
        emit_progress(
            progress,
            "source-complete",
            f"Finished {source['label'].lower()}: {kept} candidate{'s' if kept != 1 else ''} found.",
            source_index + 2,
            total_steps,
            source_id,
        )

    discovered = []
    location_verification = []
    new_count = 0
    updated_count = 0
    skipped_size_count = 0
    skipped_not_interested_count = 0
    already_tracked_count = 0
    research_count = 0
    research_errors = []
    candidate_batch = balanced_candidate_batch(candidates, selected_sources)
    missing_website_candidates = [
        candidate for candidate in candidate_batch if not official_company_website(candidate.get("website", ""))
    ]
    if openai_config is not None and missing_website_candidates:
        emit_progress(
            progress,
            "websites",
            f"Finding official websites for {len(missing_website_candidates)} candidate{'s' if len(missing_website_candidates) != 1 else ''}…",
            len(selected_sources) + 1,
            total_steps,
        )
        try:
            websites = openai_company_website_search(openai_config, missing_website_candidates)
            for candidate in missing_website_candidates:
                candidate["website"] = websites.get(
                    companies.company_merge_key(candidate.get("company", "")),
                    "",
                )
        except RuntimeError as exc:
            errors.append(f"Official websites: {storage.clean(str(exc))}")
    emit_progress(
        progress,
        "reviewing",
        f"Checking evidence for {len(candidate_batch)} candidate{'s' if len(candidate_batch) != 1 else ''}…",
        len(selected_sources) + 1,
        total_steps,
    )
    evaluation_payloads = {}
    evaluation_company_ids = []
    for candidate_index, candidate in enumerate(candidate_batch):
        emit_progress(
            progress,
            "reviewing",
            f"Checking {candidate['company']} ({candidate_index + 1} of {len(candidate_batch)})…",
            len(selected_sources) + 1,
            total_steps,
        )
        existing = companies.matching_company_record(
            candidate["company"],
            candidate.get("company_profile_url", ""),
            "",
        )
        if existing and existing.get("interest_status", "").lower() == "not-interested":
            skipped_not_interested_count += 1
            continue

        metadata = {
            "company": candidate["company"],
            "company_profile_url": candidate.get("company_profile_url", ""),
            "company_size": candidate.get("company_size", "") or inferred_size(candidate.get("evidence", "")),
            "company_industry": candidate.get("industry", ""),
            "company_description": candidate.get("company_description", ""),
            "website": official_company_website(candidate.get("website", "")),
            "company_metadata_source": candidate["source_url"],
        }
        if openai_config is not None:
            research_count += 1
        if researcher is not None and research_count < MAX_RESEARCH:
            research_count += 1
            try:
                researched = researcher(
                    candidate["company"],
                    candidate.get("company_profile_url", ""),
                ) or {}
                metadata.update({key: value for key, value in researched.items() if value})
                metadata["company"] = candidate["company"]
            except RuntimeError as exc:
                research_errors.append(f"{candidate['company']}: {storage.clean(str(exc))}")

        if size_matches(metadata.get("company_size", ""), selected_sizes) is False:
            skipped_size_count += 1
            continue
        candidate_location_fit = normalized_location_fit(
            candidate.get("location_fit", "")
            or metadata.get("company_location_fit", "")
            or (existing or {}).get("company_location_fit", "")
        )
        candidate_location = (
            candidate.get("location", "")
            or metadata.get("company_location", "")
            or (existing or {}).get("company_location", "")
        )
        candidate_remote_policy = (
            candidate.get("remote_policy", "")
            or metadata.get("company_remote_policy", "")
            or (existing or {}).get("company_remote_policy", "")
        )
        candidate_location_evidence = (
            candidate.get("location_evidence", "")
            or metadata.get("company_location_evidence", "")
            or (existing or {}).get("company_location_evidence", "")
        )
        company = companies.record_discovered_company(metadata, seen_at=timestamp)
        if company is None:
            continue
        score, summary = score_company(
            metadata,
            focus_terms,
            selected_sizes,
            candidate["source_label"],
            candidate.get("evidence", ""),
            candidate_location_fit,
            remote_region,
            metro_area,
        )
        company = update_discovery_evidence(
            company["id"],
            {
                "company_discovery_source": candidate["source_label"],
                "company_discovery_source_url": candidate["source_url"],
                "company_discovery_query": candidate["query"],
                "company_discovery_evidence": candidate.get("evidence", ""),
                "company_location_fit": candidate_location_fit,
                "company_location": candidate_location,
                "company_remote_policy": candidate_remote_policy,
                "company_location_evidence": candidate_location_evidence,
                "company_location_checked_at": timestamp,
                "company_fit_score": str(score),
                "company_fit_summary": summary,
                "company_fit_checked_at": timestamp,
            },
        )
        evaluation_company_ids.append(company["id"])
        evaluation_payloads[company["id"]] = {
            "company_id": company["id"],
            "name": company["name"],
            "website": metadata.get("website", ""),
            "careers_url": metadata.get("careers_url", ""),
            "industry": metadata.get("company_industry", "") or metadata.get("industry", ""),
            "company_size": metadata.get("company_size", ""),
            "description": metadata.get("company_description", ""),
            "location_fit": candidate_location_fit or "unknown",
            "location": candidate_location,
            "remote_policy": candidate_remote_policy,
            "location_evidence": candidate_location_evidence,
            "source_urls": [
                value
                for value in [candidate.get("source_url", ""), metadata.get("website", "")]
                if storage.clean(value)
            ],
        }
        if existing is None:
            new_count += 1
        else:
            updated_count += 1
        if company.get("tracking_status") == "tracked":
            already_tracked_count += 1
        elif company.get("interest_status", "neutral") == "neutral":
            if company.get("company_location_fit"):
                discovered.append(company)
            else:
                location_verification.append(company)

    if evaluation_company_ids:
        def source_evaluator(batch, _profile, _batch_number):
            return [
                evaluation_payloads[row.get("id", "")]
                for row in batch
                if row.get("id", "") in evaluation_payloads
            ]

        company_evaluation.evaluate_companies(
            company_ids=evaluation_company_ids,
            tracking_status="",
            profile=evaluation_profile,
            force=True,
            evaluator=source_evaluator,
            reason="company-search",
        )
        refreshed = {
            row.get("id", ""): row
            for row in repository.read_companies()
        }
        discovered = [refreshed.get(row.get("id", ""), row) for row in discovered]
        location_verification = [
            refreshed.get(row.get("id", ""), row)
            for row in location_verification
        ]

    discovered.sort(
        key=lambda item: (
            -int(item.get("company_fit_score", "") or 0),
            item.get("name", "").lower(),
        )
    )
    location_verification.sort(
        key=lambda item: (
            -int(item.get("company_fit_score", "") or 0),
            item.get("name", "").lower(),
        )
    )
    emit_progress(
        progress,
        "complete",
        f"Discovery complete: {len(discovered)} ready and {len(location_verification)} need location verification.",
        total_steps,
        total_steps,
    )
    return {
        "focus": ", ".join(focus_terms),
        "focus_lanes": search_focus_terms,
        "sizes": selected_sizes,
        "sources": selected_sources,
        "locations": selected_locations,
        "remote_region": remote_region,
        "metro_area": metro_area,
        "companies": discovered,
        "location_verification_companies": location_verification,
        "review_count": len(discovered),
        "location_verification_count": len(location_verification),
        "new_count": new_count,
        "updated_count": updated_count,
        "already_tracked_count": already_tracked_count,
        "skipped_size_count": skipped_size_count,
        "skipped_not_interested_count": skipped_not_interested_count,
        "research_count": research_count,
        "source_runs": source_runs,
        "errors": [*errors, *research_errors],
    }
