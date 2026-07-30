"""Saved Discovery searches and review-first posting capture."""

import html
import json
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse, urlunparse

from . import applications, browser_discovery, companies, posting_snapshots, repository, schema, settings, storage


URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.I)
LINKEDIN_HOSTS = {"linkedin.com", "www.linkedin.com"}
MAX_DESCRIPTION_CHARS = 80_000
LINKEDIN_DETAILS_WARNING = "LinkedIn-assisted result needs copied posting details or an employer posting URL."
WORK_MODE_CODES = {"on-site": "1", "remote": "2", "hybrid": "3"}
ALL_WORK_MODES = ["on-site", "hybrid", "remote"]
YAHOO_SEARCH_URL = "https://search.yahoo.com/search?p={query}"
DUCKDUCKGO_SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"
SEARCH_RESULT_LIMIT = 10
GOOGLE_PAGE_COUNT = 3
LINKEDIN_PAGE_COUNT = 3
GOOGLE_CONTINUE_YIELD = 4
LINKEDIN_CONTINUE_YIELD = 8
RAW_DISCOVERY_RESULT_LIMIT = 200
DISCOVERY_RESULT_LIMIT = 50
DETAIL_ENRICHMENT_LIMIT = 12
COMPANY_RESEARCH_LIMIT = 3
CONTINUE_ENRICHMENT_LIMIT = 10
FRESHNESS_RECHECK_DAYS = 7
MIN_READY_DESCRIPTION_CHARS = 500
MIN_REVIEW_FIT_SCORE = 45
SCREENED_STATUS = "screened"
SCREENING_WARNING_PREFIX = "Screened from New: "
DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES = {"not-interested", "archived"}
TPM_QUERY_FAMILIES = [
    {
        "id": "exact",
        "label": "Technical program manager",
        "terms": ["technical program manager"],
    },
    {
        "id": "senior",
        "label": "Senior technical program leadership",
        "terms": [
            "senior technical program manager",
            "staff technical program manager",
            "principal technical program manager",
            "lead technical program manager",
        ],
    },
    {
        "id": "adjacent",
        "label": "Adjacent technical program roles",
        "terms": ["technical project manager", "engineering program manager"],
    },
]
SEARCH_ENGINE_HOSTS = {
    "duckduckgo.com",
    "google.com",
    "r.search.yahoo.com",
    "search.yahoo.com",
    "www.google.com",
    "www.yahoo.com",
    "yahoo.com",
}
COLLECTION_HOSTS = {
    "dice.com",
    "glassdoor.com",
    "indeed.com",
    "jooble.org",
    "simplyhired.com",
    "ziprecruiter.com",
}
DIRECT_ATS_PLATFORMS = {"ashby", "greenhouse", "lever", "smartrecruiters", "workday"}
DIRECT_ATS_HOSTS = {
    "eightfold.ai",
    "icims.com",
    "oraclecloud.com",
}
LOW_TRUST_SOURCE_HOSTS = {
    *COLLECTION_HOSTS,
    "anitab.org",
    "bebee.com",
    "infosecjobboard.com",
    "jobright.ai",
    "jobs.capitalfactory.com",
    "lensa.com",
    "swiftcruit.ai",
    "tealhq.com",
}
IGNORE_REASONS = {
    "wrong-role",
    "company",
    "level",
    "industry",
    "location",
    "stale",
    "poor-source",
    "other",
    "search-exclusion",
}
BUILT_IN_SEARCH_DOMAINS = {
    "builtin.com",
    "builtinaustin.com",
    "builtinboston.com",
    "builtinchicago.org",
    "builtinsf.com",
}
BUILT_IN_SEARCH_STRATEGIES = [
    {
        "id": "job-boards",
        "label": "Major employer job boards",
        "query": (
            "(site:jobs.ashbyhq.com OR site:jobs.lever.co OR "
            "site:boards.greenhouse.io OR site:job-boards.greenhouse.io OR "
            "site:myworkdayjobs.com OR site:careers.smartrecruiters.com)"
        ),
    },
    {
        "id": "employer-web",
        "label": "Direct posting pages",
        "query": (
            "job careers -indeed.com -glassdoor.com -ziprecruiter.com "
            "-simplyhired.com -jooble.org "
            + " ".join(f"-site:{domain}" for domain in sorted(BUILT_IN_SEARCH_DOMAINS))
        ),
    },
    {
        "id": "linkedin",
        "label": "LinkedIn job postings",
        "query": "site:linkedin.com/jobs/view",
    },
]
US_STATE_ABBREVIATIONS = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def next_id(rows, prefix):
    highest = 0
    for row in rows:
        match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", row.get("id", "").upper())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1:04d}"


def list_searches():
    return [search_payload(row) for row in repository.read_discovery_searches()]


def normalized_lane(lane, index):
    location = storage.clean((lane or {}).get("location", ""))
    label = storage.clean((lane or {}).get("label", "")) or location or f"Search {index + 1}"
    raw_modes = (lane or {}).get("work_modes", [])
    if isinstance(raw_modes, str):
        raw_modes = raw_modes.split(",")
    work_modes = []
    for value in raw_modes:
        normalized = storage.clean(value).lower()
        if normalized in WORK_MODE_CODES and normalized not in work_modes:
            work_modes.append(normalized)
    return {
        "id": storage.clean((lane or {}).get("id", "")) or f"lane-{index + 1}",
        "label": label,
        "location": location,
        "work_modes": work_modes,
    }


def search_lanes(search):
    raw_lanes = []
    try:
        decoded = json.loads(search.get("lanes_json", "") or "[]")
        raw_lanes = decoded if isinstance(decoded, list) else []
    except (TypeError, ValueError):
        raw_lanes = []
    lanes = [normalized_lane(lane, index) for index, lane in enumerate(raw_lanes)]
    if lanes:
        return lanes

    legacy = []
    location = storage.clean(search.get("location", ""))
    remote_location = storage.clean(search.get("remote_location", ""))
    if location:
        legacy.append({"id": "primary", "label": location, "location": location, "work_modes": ALL_WORK_MODES})
    if remote_location:
        legacy.append(
            {
                "id": "remote",
                "label": f"{remote_location} remote",
                "location": remote_location,
                "work_modes": ["remote"],
            }
        )
    return legacy


def search_payload(row):
    payload = dict(row)
    payload["lanes"] = search_lanes(row)
    try:
        summary = json.loads(row.get("last_run_summary_json", "") or "{}")
        payload["last_run_summary"] = summary if isinstance(summary, dict) else {}
    except (TypeError, ValueError):
        payload["last_run_summary"] = {}
    try:
        excluded_terms = json.loads(row.get("excluded_terms_json", "") or "[]")
        payload["excluded_terms"] = [
            storage.clean(term)
            for term in excluded_terms
            if storage.clean(term)
        ] if isinstance(excluded_terms, list) else []
    except (TypeError, ValueError):
        payload["excluded_terms"] = []
    payload.pop("lanes_json", None)
    payload.pop("excluded_terms_json", None)
    payload.pop("location", None)
    payload.pop("remote_location", None)
    payload.pop("last_run_summary_json", None)
    return payload


def get_search(search_id):
    wanted = storage.clean(search_id).upper()
    row = next(
        (item for item in list_searches() if item.get("id", "").upper() == wanted),
        None,
    )
    if row is None:
        raise ValueError(f"No Discovery search found with id {search_id}.")
    return row


def upsert_search(search_id="", updates=None):
    rows = repository.read_discovery_searches()
    wanted = storage.clean(search_id).upper()
    row = next((item for item in rows if item.get("id", "").upper() == wanted), None) if wanted else None
    timestamp = now_iso()
    if row is None:
        row = {field: "" for field in schema.DISCOVERY_SEARCH_FIELDS}
        row.update({"id": next_id(rows, "DS"), "created_at": timestamp})
        rows.append(row)

    for field in ["name", "keywords"]:
        if field in (updates or {}):
            row[field] = storage.clean(updates.get(field, ""))
    if "lanes" in (updates or {}):
        lanes = [
            normalized_lane(lane, index)
            for index, lane in enumerate((updates or {}).get("lanes", []))
        ]
        if not lanes:
            raise ValueError("Add at least one Discovery search lane.")
        for lane in lanes:
            if not lane["location"]:
                raise ValueError("Every Discovery search lane needs a location.")
            if not lane["work_modes"]:
                raise ValueError("Every Discovery search lane needs at least one work mode.")
        row["lanes_json"] = json.dumps(lanes)
        row["location"] = lanes[0]["location"]
        row["remote_location"] = ""
    if "excluded_terms" in (updates or {}):
        terms = []
        for value in (updates or {}).get("excluded_terms", []):
            term = storage.clean(value)
            if term and term.lower() not in {item.lower() for item in terms}:
                terms.append(term)
        row["excluded_terms_json"] = json.dumps(terms, ensure_ascii=False)
    if not row.get("name"):
        raise ValueError("Discovery search name is required.")
    if not row.get("keywords"):
        raise ValueError("Discovery search keywords are required.")
    if not search_lanes(row):
        raise ValueError("Add at least one Discovery search lane.")
    row["updated_at"] = timestamp
    repository.write_discovery_searches(rows)
    return get_search(row["id"])


def apply_search_exclusions(search_id, excluded_terms=None):
    search = get_search(search_id)
    if excluded_terms is not None:
        search = {
            **search,
            "excluded_terms": [
                storage.clean(term)
                for term in excluded_terms
                if storage.clean(term)
            ],
        }
    rows = repository.read_discovery_candidates()
    changed_ids = []
    for candidate in rows:
        if candidate.get("search_id", "").upper() != search["id"].upper():
            continue
        if candidate.get("status") != "new":
            continue
        matches = matching_excluded_terms(search, candidate)
        if not matches:
            continue
        candidate["status"] = "ignored"
        candidate["ignore_reason"] = "search-exclusion"
        candidate["ignore_reason_detail"] = ", ".join(matches)
        candidate["ingested_application_id"] = ""
        changed_ids.append(candidate.get("id", ""))
    if changed_ids:
        repository.write_discovery_candidates(rows)
    return {"candidate_ids": changed_ids, "count": len(changed_ids)}


def undo_search_exclusions(candidate_ids):
    wanted_ids = {
        storage.clean(candidate_id).upper()
        for candidate_id in candidate_ids or []
        if storage.clean(candidate_id)
    }
    rows = repository.read_discovery_candidates()
    restored_ids = []
    for candidate in rows:
        if candidate.get("id", "").upper() not in wanted_ids:
            continue
        if (
            candidate.get("status") != "ignored"
            or candidate.get("ignore_reason") != "search-exclusion"
        ):
            continue
        candidate["status"] = "new"
        candidate["ignore_reason"] = ""
        candidate["ignore_reason_detail"] = ""
        restored_ids.append(candidate.get("id", ""))
    if restored_ids:
        repository.write_discovery_candidates(rows)
    return {"candidate_ids": restored_ids, "count": len(restored_ids)}


def search_keyword_families(search):
    keywords = storage.clean(search.get("keywords", ""))
    match = re.match(r"^technical program manager\b(.*)$", keywords, re.I)
    if not match:
        return [{"id": "saved", "label": "Saved keywords", "query": keywords}]
    qualifiers = storage.clean(match.group(1))
    families = []
    for family in TPM_QUERY_FAMILIES:
        terms = family["terms"]
        query = (
            f'"{terms[0]}"'
            if len(terms) == 1
            else "(" + " OR ".join(f'"{term}"' for term in terms) + ")"
        )
        families.append(
            {
                "id": family["id"],
                "label": family["label"],
                "query": f"{query} {qualifiers}".strip(),
            }
        )
    return families


def expanded_search_keywords(search):
    return search_keyword_families(search)[0]["query"]


def linkedin_search_url(search, lane, keywords=""):
    query = quote_plus(storage.clean(keywords) or expanded_search_keywords(search))
    location = quote_plus(storage.clean(lane.get("location", "")))
    parts = [f"keywords={query}"]
    if location:
        parts.append(f"location={location}")
    work_mode_codes = [
        WORK_MODE_CODES[mode]
        for mode in lane.get("work_modes", [])
        if mode in WORK_MODE_CODES
    ]
    if set(work_mode_codes) != set(WORK_MODE_CODES.values()):
        parts.append(f"f_WT={quote_plus(','.join(work_mode_codes))}")
    return "https://www.linkedin.com/jobs/search/?" + "&".join(parts)


def linkedin_search_lanes(search):
    return [
        {**lane, "url": linkedin_search_url(search, lane)}
        for lane in search.get("lanes", search_lanes(search))
    ]


def open_linkedin_search(search_id):
    rows = repository.read_discovery_searches()
    wanted = storage.clean(search_id).upper()
    row = next((item for item in rows if item.get("id", "").upper() == wanted), None)
    if row is None:
        raise ValueError(f"No Discovery search found with id {search_id}.")
    row["last_opened_at"] = now_iso()
    repository.write_discovery_searches(rows)
    search = search_payload(row)
    lanes = linkedin_search_lanes(search)
    return {"search": search, "url": lanes[0]["url"], "lanes": lanes}


def work_mode_query(lane):
    modes = lane.get("work_modes", [])
    if set(modes) == set(ALL_WORK_MODES):
        return ""
    labels = {
        "on-site": '"on-site" OR onsite',
        "hybrid": "hybrid",
        "remote": "remote",
    }
    selected = [labels[mode] for mode in modes if mode in labels]
    return f"({' OR '.join(selected)})" if selected else ""


def discovery_query(search, lane, strategy, keywords=""):
    exclusions = " ".join(
        f'-"{term}"' if " " in term else f"-{term}"
        for term in search.get("excluded_terms", [])
    )
    return " ".join(
        part
        for part in [
            storage.clean(keywords) or expanded_search_keywords(search),
            f'"{storage.clean(lane.get("location", ""))}"' if storage.clean(lane.get("location", "")) else "",
            work_mode_query(lane),
            strategy.get("query", ""),
            exclusions,
        ]
        if part
    )


def candidate_is_excluded(search, candidate):
    text = storage.clean(
        f"{candidate.get('title', '')} {candidate.get('description_text', '')}"
    ).lower()
    return any(
        storage.clean(term).lower() in text
        for term in search.get("excluded_terms", [])
        if storage.clean(term)
    )


def matching_excluded_terms(search, candidate):
    text = storage.clean(
        f"{candidate.get('title', '')} {candidate.get('description_text', '')}"
    ).lower()
    return [
        storage.clean(term)
        for term in search.get("excluded_terms", [])
        if storage.clean(term) and storage.clean(term).lower() in text
    ]


def search_request_url(query, engine="yahoo"):
    template = DUCKDUCKGO_SEARCH_URL if engine == "duckduckgo" else YAHOO_SEARCH_URL
    return template.format(query=quote_plus(query))


def yahoo_redirect_target(url):
    match = re.search(r"/RU=([^/]+)/RK=", html.unescape(url or ""), re.I)
    return unquote(match.group(1)) if match else ""


def duckduckgo_redirect_target(url):
    parsed = urlparse(html.unescape(url or ""))
    values = parse_qs(parsed.query).get("uddg", [])
    return unquote(values[0]) if values else ""


def clean_search_result_text(value):
    return companies.clean_html_text(value or "")


def normalize_search_result_url(url):
    normalized = companies.normalize_url(url)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    path = re.sub(r"/(?:apply|application)/?$", "", parsed.path, flags=re.I)
    return companies.normalize_url(urlunparse(parsed._replace(path=path)))


def likely_individual_posting(url, title=""):
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path_and_query = f"{parsed.path}?{parsed.query}".lower()
    if not host or host in SEARCH_ENGINE_HOSTS:
        return False
    if host == "linkedin.com":
        return bool(re.search(r"/jobs/view/(?:[^/?#]+-)?\d+", parsed.path, re.I))
    if host in COLLECTION_HOSTS:
        return bool(re.search(r"/viewjob\b|[?&]jk=|/job/[^/?#]+", path_and_query, re.I))
    if "ashbyhq.com" in host or "lever.co" in host:
        segments = [segment for segment in parsed.path.split("/") if segment]
        return len(segments) >= 2 and segments[-1].lower() not in {"apply", "application"}
    if "greenhouse.io" in host:
        return bool(re.search(r"/jobs?/\d+|[?&]gh_jid=\d+", path_and_query, re.I))
    if "workdayjobs.com" in host or "myworkdayjobs.com" in host:
        return "/job/" in parsed.path.lower()
    if "smartrecruiters.com" in host:
        return len([segment for segment in parsed.path.split("/") if segment]) >= 2
    normalized_path = parsed.path.rstrip("/").lower()
    if normalized_path in {
        "",
        "/career",
        "/career/search",
        "/careers",
        "/careers/search",
        "/job/search",
        "/job-search",
        "/jobs",
        "/jobs/search",
        "/open-positions",
        "/positions",
        "/search",
    }:
        return False
    if re.fullmatch(
        r"(?:careers?|jobs?|job search|search jobs|open positions|career opportunities|jobs at .+)",
        storage.clean(title).lower(),
    ):
        return False
    if re.match(r"^/(?:careers?|jobs?)/.+", normalized_path):
        return True
    return bool(
        companies.JOB_URL_PATTERN.search(path_and_query)
        or re.search(r"\b(job|position|role|opening|career)\b", title or "", re.I)
    )


def failed_posting_url(url):
    parsed = urlparse(companies.normalize_url(url))
    query = parse_qs(parsed.query)
    return any(
        storage.clean(value).lower() not in {"", "0", "false", "none"}
        for key in {"error", "invalid", "notfound", "not_found"}
        for value in query.get(key, [])
    )


def individual_ats_posting_url(url, platform=""):
    normalized = companies.normalize_url(url)
    effective_platform = platform or source_platform(normalized)
    if effective_platform not in DIRECT_ATS_PLATFORMS:
        return True
    return bool(
        normalized
        and not failed_posting_url(normalized)
        and likely_individual_posting(normalized)
    )


def ignored_discovery_source(url):
    host = urlparse(companies.normalize_url(url)).netloc.lower().removeprefix("www.")
    return bool(re.fullmatch(r"builtin[a-z]*\.(?:com|org)", host))


def search_result_from_block(block, engine):
    href_match = re.search(r"<a\b[^>]*href\s*=\s*(['\"])(.*?)\1", block or "", re.I | re.S)
    if not href_match:
        return None
    href = html.unescape(href_match.group(2)).strip()
    if engine == "yahoo":
        target = yahoo_redirect_target(href)
    else:
        target = duckduckgo_redirect_target(href)
    if not target and href.startswith(("http://", "https://")):
        target = href
    target = normalize_search_result_url(target)

    title_match = re.search(r"<h3\b[^>]*>(.*?)</h3>", block or "", re.I | re.S)
    if not title_match and engine == "duckduckgo":
        title_match = re.search(
            r"<a\b[^>]*class\s*=\s*(['\"])[^'\"]*result__a[^'\"]*\1[^>]*>(.*?)</a>",
            block or "",
            re.I | re.S,
        )
        title_html = title_match.group(2) if title_match else ""
    else:
        title_html = title_match.group(1) if title_match else ""
    snippet_match = re.search(r"<p\b[^>]*>(.*?)</p>", block or "", re.I | re.S)
    if not snippet_match and engine == "duckduckgo":
        snippet_match = re.search(
            r"<a\b[^>]*class\s*=\s*(['\"])[^'\"]*result__snippet[^'\"]*\1[^>]*>(.*?)</a>",
            block or "",
            re.I | re.S,
        )
        snippet_html = snippet_match.group(2) if snippet_match else ""
    else:
        snippet_html = snippet_match.group(1) if snippet_match else ""
    title = clean_search_result_text(title_html)
    if not target or not likely_individual_posting(target, title):
        return None
    return {
        "url": target,
        "title": title,
        "snippet": clean_search_result_text(snippet_html),
    }


def parse_search_results(page_html, engine="yahoo"):
    if engine == "duckduckgo":
        blocks = re.findall(
            r"<div\b[^>]*class\s*=\s*(['\"])[^'\"]*\bresult\b[^'\"]*\1[^>]*>(.*?)</div>\s*</div>",
            page_html or "",
            re.I | re.S,
        )
        blocks = [block for _quote, block in blocks]
        if not blocks:
            blocks = re.findall(
                r"(<a\b[^>]*class\s*=\s*(['\"])[^'\"]*result__a[^'\"]*\2[^>]*>.*?</a>.*?"
                r"(?:<a\b[^>]*class\s*=\s*(['\"])[^'\"]*result__snippet[^'\"]*\3[^>]*>.*?</a>)?)",
                page_html or "",
                re.I | re.S,
            )
            blocks = [block[0] for block in blocks]
    else:
        blocks = re.findall(
            r"(<li\b[^>]*>.*?<div\b[^>]*class\s*=\s*(['\"])[^'\"]*\balgo\b[^'\"]*\2.*?</li>)",
            page_html or "",
            re.I | re.S,
        )
        blocks = [block[0] for block in blocks]

    results = []
    seen = set()
    for block in blocks:
        item = search_result_from_block(block, engine)
        if not item or item["url"] in seen:
            continue
        seen.add(item["url"])
        results.append(item)
        if len(results) >= SEARCH_RESULT_LIMIT:
            break
    return results


def fetch_search_results(query, fetcher=None):
    fetch = fetcher or companies.fetch_careers_page
    attempts = []
    for engine in ["yahoo", "duckduckgo"]:
        url = search_request_url(query, engine)
        response = fetch(url)
        error = storage.clean(response.get("error", ""))
        items = parse_search_results(response.get("html", "") or "", engine)
        attempts.append({"engine": engine, "url": url, "error": error, "count": len(items)})
        if items:
            return items, attempts
    return [], attempts


def normalize_browser_results(items, limit=SEARCH_RESULT_LIMIT):
    results = []
    seen = set()
    for item in items or []:
        url = normalize_search_result_url((item or {}).get("url", ""))
        title = storage.clean((item or {}).get("title", ""))
        if (
            not url
            or url in seen
            or ignored_discovery_source(url)
            or not likely_individual_posting(url, title)
        ):
            continue
        seen.add(url)
        results.append(
            {
                "url": url,
                "title": title,
                "snippet": storage.clean((item or {}).get("snippet", ""))[:2000],
                "company": storage.clean((item or {}).get("company", "")),
                "location": storage.clean((item or {}).get("location", "")),
                "work_mode": storage.clean((item or {}).get("work_mode", "")),
            }
        )
        if len(results) >= limit:
            break
    return results


def fetch_browser_results(engine, value, page=0, searcher=None):
    browser_search = searcher or browser_discovery.search
    items = browser_search(engine, value, page)
    limit = browser_discovery.LINKEDIN_PAGE_SIZE if engine == "linkedin" else browser_discovery.GOOGLE_PAGE_SIZE
    return normalize_browser_results(items, limit=limit)


def search_title_details(title, platform=""):
    cleaned = storage.clean(title)
    cleaned = re.sub(r"\s*[|·]\s*LinkedIn\s*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*-\s*(?:jobs\.)?(?:lever\.co|ashbyhq\.com)\s*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"(?:\s+-\s+Logo)?\s+-\s+Myworkdayjobs\.com\s*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^Job Application for\s+", "", cleaned, flags=re.I)
    hiring_match = re.match(r"^(.+?)\s+hiring\s+(.+?)(?:\s*\.{3})?$", cleaned, re.I)
    if hiring_match:
        return storage.clean(hiring_match.group(2)), storage.clean(hiring_match.group(1))
    for separator in [" @ ", " at "]:
        if separator in cleaned:
            role, company = cleaned.rsplit(separator, 1)
            return storage.clean(role), storage.clean(company)
    if " | " in cleaned:
        role, company = cleaned.rsplit(" | ", 1)
        company = re.sub(r"\s+(?:careers?|jobs?)$", "", company, flags=re.I)
        if role and company:
            return storage.clean(role), storage.clean(company)
    if platform == "lever" and " - " in cleaned:
        left, right = cleaned.rsplit(" - ", 1)
        role_pattern = r"\b(?:manager|engineer|designer|director|lead|program|product|developer|architect)\b"
        if re.search(role_pattern, left, re.I) and not re.search(role_pattern, right, re.I):
            role, company = left, right
        else:
            company, role = left, right
        return storage.clean(role), storage.clean(company)
    return cleaned, ""


def company_from_posting_url(url, platform):
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    segments = [segment for segment in parsed.path.split("/") if segment]
    slug = ""
    if platform in {"greenhouse", "lever", "smartrecruiters"} and segments:
        slug = segments[0]
    elif platform == "workday":
        slug = host.split(".", 1)[0]
    if not slug:
        return ""
    words = re.sub(r"[-_]+", " ", slug).split()
    return " ".join(word.upper() if len(word) <= 4 else word.title() for word in words)


def apply_search_result_details(candidate, result):
    platform = candidate.get("source_platform", "")
    source_url = candidate.get("canonical_url") or candidate.get("url", "")
    result_title_text = result.get("title", "")
    if source_url_is_low_trust(source_url):
        host = normalized_source_host(source_url)
        source_name = re.escape(host.split(".", 1)[0])
        result_title_text = re.sub(
            rf"\s*[|·-]\s*(?:www\.)?{source_name}(?:\.[a-z]+)?(?:\s+.*)?$",
            "",
            result_title_text,
            flags=re.I,
        )
    result_title, result_company = search_title_details(result_title_text, platform)
    explicit_company = storage.clean(result.get("company", ""))
    if explicit_company and not company_name_matches_source(explicit_company, source_url):
        result_company = explicit_company
    if company_name_matches_source(result_company, source_url):
        result_company = ""
    current_title = candidate.get("title", "")
    current_title_is_generic = (
        not current_title
        or "." in current_title and " " not in current_title
        or current_title.lower() in {"careers", "jobs", "job search"}
    )
    if result_title and (current_title_is_generic or platform == "lever" and result_company):
        candidate["title"] = result_title
    if result_company and (
        not candidate.get("company")
        or candidate.get("company", "").lower() == candidate.get("title", "").lower()
    ):
        candidate["company"] = result_company
    if not candidate.get("company"):
        candidate["company"] = company_from_posting_url(
            candidate.get("canonical_url") or candidate.get("url", ""),
            platform,
        )
    if not candidate.get("location") and result.get("location"):
        candidate["location"] = storage.clean(result.get("location", ""))
    if not candidate.get("description_text") and result.get("snippet"):
        candidate["description_text"] = storage.clean(result.get("snippet", ""))[:MAX_DESCRIPTION_CHARS]
    if not candidate.get("work_mode") and result.get("work_mode"):
        candidate["work_mode"] = storage.clean(result.get("work_mode", ""))
    if not candidate.get("work_mode"):
        candidate["work_mode"] = work_mode_from_text(
            candidate.get("location", ""),
            candidate.get("description_text", ""),
        )
    return candidate


def normalized_location_text(value):
    return re.sub(r"[^a-z0-9]+", " ", storage.clean(value).lower()).strip()


def location_match_terms(location):
    normalized = normalized_location_text(location)
    terms = [normalized] if normalized else []
    abbreviation = US_STATE_ABBREVIATIONS.get(normalized, "")
    if abbreviation:
        terms.append(abbreviation.lower())
    return terms


def contains_location_term(text, terms):
    return any(
        re.search(rf"(^|\s){re.escape(term)}(\s|$)", text)
        for term in terms
        if term
    )


def result_has_location_signal(result, desired_location):
    text = normalized_location_text(
        " ".join([result.get("title", ""), result.get("snippet", "")])
    )
    disclaimer_terms = {
        "annual salary",
        "base pay",
        "candidates outside",
        "compensation",
        "pay range",
        "residents of",
        "salary range",
    }
    for term in location_match_terms(desired_location):
        for match in re.finditer(rf"(^|\s){re.escape(term)}(\s|$)", text):
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 100)
            context = text[start:end]
            if any(disclaimer in context for disclaimer in disclaimer_terms):
                continue
            return True
    return False


def candidate_matches_lane(candidate, result, lane):
    expected_modes = set(lane.get("work_modes", []))
    actual_mode = storage.clean(candidate.get("work_mode", "")).lower()
    actual_mode = "on-site" if actual_mode in {"onsite", "on site"} else actual_mode
    if actual_mode and actual_mode not in expected_modes:
        return False

    desired_location = normalized_location_text(lane.get("location", ""))
    candidate_text = normalized_location_text(
        " ".join(
            [
                candidate.get("location", ""),
                candidate.get("description_text", ""),
                result.get("title", ""),
                result.get("snippet", ""),
            ]
        )
    )
    country_wide = desired_location in {"united states", "us", "usa", "u s"}
    remote_only = expected_modes == {"remote"}
    candidate_location = normalized_location_text(candidate.get("location", ""))
    if remote_only:
        if actual_mode != "remote":
            return False
        return (
            country_wide
            or contains_location_term(candidate_location, location_match_terms(desired_location))
            or result_has_location_signal(result, desired_location)
        )
    if candidate_location and actual_mode != "remote":
        return country_wide or contains_location_term(candidate_location, location_match_terms(desired_location))
    if actual_mode == "remote" and not country_wide:
        return result_has_location_signal(result, desired_location)
    return country_wide or result_has_location_signal(result, desired_location)


def meaningful_description(value):
    description = storage.clean(value)
    if len(description) < MIN_READY_DESCRIPTION_CHARS:
        return False
    if description.startswith("{"):
        try:
            payload = json.loads(description)
        except (TypeError, ValueError):
            payload = {}
        if isinstance(payload, dict) and (
            payload.get("widget") == "redirect"
            or payload.get("externalSpa")
            or set(payload).issubset({"widget", "url", "externalSpa"})
        ):
            return False
    return True


def apply_browser_details(candidate, details):
    updates = {
        "company": storage.clean((details or {}).get("company", "")),
        "title": storage.clean((details or {}).get("title", "")),
        "canonical_url": companies.normalize_url((details or {}).get("canonical_url", "")),
        "location": storage.clean((details or {}).get("location", "")),
        "work_mode": storage.clean((details or {}).get("work_mode", "")),
        "company_industry": companies.normalize_company_industry(
            (details or {}).get("company_industry", "")
        ),
        "company_size": companies.normalize_company_size((details or {}).get("company_size", "")),
        "company_profile_url": companies.normalize_company_profile_url(
            (details or {}).get("company_profile_url", "")
        ),
        "company_metadata_source": companies.normalize_url(
            (details or {}).get("company_metadata_source", "")
        ),
        "description_text": str((details or {}).get("description_text", "") or "").strip()[:MAX_DESCRIPTION_CHARS],
    }
    for field, value in updates.items():
        if not value:
            continue
        if field == "description_text":
            if not meaningful_description(candidate.get(field, "")) or len(value) > len(candidate.get(field, "")):
                candidate[field] = value
        elif not candidate.get(field) or field in {"company", "title", "location", "work_mode"}:
            candidate[field] = value
    detected_platform = source_platform(
        candidate.get("canonical_url") or candidate.get("url", "")
    )
    if detected_platform != "employer":
        candidate["source_platform"] = detected_platform
    if not candidate.get("work_mode"):
        candidate["work_mode"] = work_mode_from_text(
            candidate.get("location", ""),
            candidate.get("description_text", ""),
        )
    if (
        candidate.get("company")
        and candidate.get("title")
        and meaningful_description(candidate.get("description_text", ""))
    ):
        candidate["warnings"] = "\n".join(
            line
            for line in (candidate.get("warnings", "") or "").splitlines()
            if line != LINKEDIN_DETAILS_WARNING
        )
    availability = storage.clean((details or {}).get("availability_status", "")).lower()
    if (
        availability != "closed"
        and posting_valid_through_expired((details or {}).get("valid_through", ""))
    ):
        availability = "closed"
    if availability == "closed":
        candidate["freshness_status"] = "closed"
        candidate["status"] = "unavailable"
    elif availability == "open":
        candidate["freshness_status"] = "confirmed-open"
        if candidate.get("status") == "unavailable":
            candidate["status"] = "new"
    if availability:
        candidate["freshness_checked_at"] = now_iso()
    return candidate


def posting_valid_through_expired(value, reference=None):
    cleaned = storage.clean(value)
    if not cleaned:
        return False
    current = reference or datetime.now()
    try:
        expires = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return expires.date() < current.date()
    if expires.tzinfo is not None and current.tzinfo is None:
        current = current.astimezone()
    elif expires.tzinfo is None and current.tzinfo is not None:
        current = current.replace(tzinfo=None)
    return expires < current


def enrich_workday_candidate(candidate, fetcher=None):
    if candidate.get("source_platform") != "workday" or meaningful_description(candidate.get("description_text", "")):
        return candidate
    fetch = fetcher or companies.fetch_careers_page
    config = companies.workday_cxs_board_from_url(candidate.get("canonical_url") or candidate.get("url", ""))
    if not config:
        return candidate
    seed = {
        "title": candidate.get("title", ""),
        "url": candidate.get("canonical_url") or candidate.get("url", ""),
        "description": "",
        "location": candidate.get("location", ""),
        "category": "",
        "search_text": "",
    }
    enriched = companies.enrich_workday_cxs_candidate(seed, fetch)
    details = {
        "title": enriched.get("title", ""),
        "location": enriched.get("location", ""),
        "company_industry": enriched.get("company_industry", ""),
        "company_size": enriched.get("company_size", ""),
        "company_profile_url": enriched.get("company_profile_url", ""),
        "company_metadata_source": candidate.get("canonical_url") or candidate.get("url", ""),
        "description_text": enriched.get("description", ""),
    }
    return apply_browser_details(candidate, details)


def candidate_rank_key(candidate):
    processing_rank = {"ready": 2, "partial": 1, "needs-details": 0}
    freshness_rank = {"confirmed-open": 3, "": 1, "needs-review": 0, "closed": -1}
    trust_rank = {"employer": 3, "network": 2, "unverified": 1, "aggregator": 0}
    try:
        fit_score = int(candidate.get("fit_score", "") or 0)
    except (TypeError, ValueError):
        fit_score = 0
    return (
        candidate.get("freshness_status", "") != "closed",
        freshness_rank.get(candidate.get("freshness_status", ""), 0),
        trust_rank.get(candidate_source_trust(candidate)["id"], 0),
        processing_rank.get(candidate.get("processing_status", ""), 0),
        fit_score,
        len(candidate.get("description_text", "") or ""),
        candidate.get("title", ""),
    )


def candidate_source_urls(candidate):
    urls = []
    try:
        decoded = json.loads(candidate.get("source_urls_json", "") or "[]")
    except (TypeError, ValueError):
        decoded = []
    for value in [
        *(decoded if isinstance(decoded, list) else []),
        candidate.get("url", ""),
        candidate.get("canonical_url", ""),
    ]:
        normalized = companies.normalize_url(str(value or ""))
        if normalized and normalized not in urls:
            urls.append(normalized)
    return urls


def sync_candidate_source_urls(candidate):
    candidate["source_urls_json"] = json.dumps(candidate_source_urls(candidate), ensure_ascii=False)
    return candidate


def discovery_excluded_company_identity(company_rows=None):
    company_ids = set()
    company_keys = set()
    company_profiles = set()
    company_domains = set()
    for company in company_rows if company_rows is not None else repository.read_companies():
        if company.get("interest_status", "").lower() not in DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES:
            continue
        company_id = storage.clean(company.get("id", "")).upper()
        if company_id:
            company_ids.add(company_id)
        names = [company.get("name", ""), *companies.split_aliases(company.get("aliases", ""))]
        for name in names:
            normalized_name = companies.normalized_key(name)
            merge_key = companies.company_merge_key(name)
            if normalized_name:
                company_keys.add(normalized_name)
            if merge_key:
                company_keys.add(merge_key)
        profile = companies.normalize_company_profile_url(company.get("company_profile_url", ""))
        domain = companies.company_domain(company.get("website", ""))
        if profile:
            company_profiles.add(profile)
        if domain:
            company_domains.add(domain)
    return company_ids, company_keys, company_profiles, company_domains


def candidate_company_is_excluded(candidate, excluded_identity=None):
    company_ids, company_keys, company_profiles, company_domains = (
        excluded_identity or discovery_excluded_company_identity()
    )
    company_id = storage.clean(candidate.get("company_id", "")).upper()
    if company_id and company_id in company_ids:
        return True
    names = [candidate.get("company", ""), candidate.get("name", "")]
    for name in names:
        normalized_name = companies.normalized_key(name)
        merge_key = companies.company_merge_key(name)
        if normalized_name and normalized_name in company_keys:
            return True
        if merge_key and merge_key in company_keys:
            return True
    profile = companies.normalize_company_profile_url(candidate.get("company_profile_url", ""))
    website_domain = companies.company_domain(candidate.get("website", ""))
    return bool(
        profile and profile in company_profiles
        or website_domain and website_domain in company_domains
    )


def filter_discovery_excluded_companies(candidates, excluded_identity=None):
    identity = excluded_identity or discovery_excluded_company_identity()
    kept = [
        candidate
        for candidate in candidates
        if not candidate_company_is_excluded(candidate, identity)
    ]
    return kept, len(candidates) - len(kept)


def connect_candidate_company(candidate, seen_at=""):
    company = None
    if candidate.get("company_id"):
        try:
            company = companies.get_company(candidate.get("company_id", ""))
        except ValueError:
            candidate["company_id"] = ""
    if company is None:
        company = companies.record_discovered_company(candidate, seen_at=seen_at)
    if company is None:
        return None
    candidate["company_id"] = company.get("id", "")
    if any(
        candidate.get(field)
        for field in ["company_industry", "company_size", "company_profile_url", "website"]
    ):
        company = companies.update_company_metadata(
            company.get("id", ""),
            candidate,
            source_url=(
                candidate.get("company_metadata_source")
                or candidate.get("canonical_url")
                or candidate.get("url", "")
            ),
            checked_at=storage.clean(seen_at) or now_iso(),
        )
    return company


def canonicalize_candidate_rows(rows):
    canonical = []
    status_rank = {
        "ingested": 6,
        "duplicate": 5,
        "new": 4,
        "ignored": 3,
        "unavailable": 2,
        SCREENED_STATUS: 1,
    }
    for original in rows:
        candidate = dict(original)
        sync_candidate_source_urls(candidate)
        duplicate = matching_candidate(canonical, candidate)
        if duplicate is None:
            canonical.append(candidate)
            continue
        duplicate_priority = (
            status_rank.get(duplicate.get("status", ""), 0),
            candidate_rank_key(duplicate),
        )
        candidate_priority = (
            status_rank.get(candidate.get("status", ""), 0),
            candidate_rank_key(candidate),
        )
        if candidate_priority > duplicate_priority:
            index = canonical.index(duplicate)
            preserved_status = candidate.get("status", "")
            merge_candidate(candidate, duplicate)
            candidate["status"] = preserved_status
            canonical[index] = candidate
        else:
            preserved_status = duplicate.get("status", "")
            merge_candidate(duplicate, candidate)
            duplicate["status"] = preserved_status
    return canonical


def canonicalize_candidates():
    rows = repository.read_discovery_candidates()
    canonical = canonicalize_candidate_rows(rows)
    if canonical != rows:
        repository.write_discovery_candidates(canonical)
    return {
        "before_count": len(rows),
        "after_count": len(canonical),
        "merged_count": max(0, len(rows) - len(canonical)),
    }


def sync_discovered_companies():
    canonicalize_candidates()
    rows = repository.read_discovery_candidates()
    linked_count = 0
    changed = False
    for candidate in rows:
        previous_company_id = candidate.get("company_id", "")
        company = connect_candidate_company(
            candidate,
            seen_at=candidate.get("last_seen_at") or candidate.get("captured_at") or now_iso(),
        )
        if not company:
            continue
        if candidate.get("company_id", "") != previous_company_id:
            linked_count += 1
            changed = True
    if changed:
        repository.write_discovery_candidates(rows)
    return linked_count


def run_search(
    search_id,
    search_fetcher=None,
    posting_fetcher=None,
    browser_searcher=None,
    browser_detailer=None,
    company_researcher=None,
):
    search = get_search(search_id)
    timestamp = now_iso()
    found = []
    source_runs = []
    errors = []
    found_by_url = {}
    duplicate_count = 0
    chrome_browser = None
    if search_fetcher is None and browser_searcher is None:
        chrome_browser = browser_discovery.HunterChrome()

        def browser_searcher(engine, value, page):
            return browser_discovery.search(engine, value, page=page, browser=chrome_browser)

        browser_detailer = chrome_browser.details
        company_researcher = chrome_browser.company

    attempted_sources = 0
    failed_sources = 0
    for lane in search.get("lanes", []):
        for family in search_keyword_families(search):
            for strategy in BUILT_IN_SEARCH_STRATEGIES:
                query = discovery_query(search, lane, strategy, family["query"])
                attempted_sources += 1
                engine = ""
                source_items = []
                source_seen = set()
                page_limit = 1 if search_fetcher is not None else (
                    LINKEDIN_PAGE_COUNT if strategy["id"] == "linkedin" else GOOGLE_PAGE_COUNT
                )
                successful_pages = 0
                source_error = ""
                for page in range(page_limit):
                    attempts = []
                    try:
                        if search_fetcher is not None:
                            page_items, attempts = fetch_search_results(query, fetcher=search_fetcher)
                            engine = attempts[-1]["engine"] if attempts else ""
                        elif strategy["id"] == "linkedin":
                            engine = "hunter-chrome-linkedin"
                            page_items = fetch_browser_results(
                                "linkedin",
                                linkedin_search_url(search, lane, family["query"]),
                                page=page,
                                searcher=browser_searcher,
                            )
                        else:
                            engine = "hunter-chrome-google"
                            page_items = fetch_browser_results(
                                "google",
                                query,
                                page=page,
                                searcher=browser_searcher,
                            )
                        successful_pages += 1
                    except browser_discovery.BrowserDiscoveryError as exc:
                        raise RuntimeError(storage.clean(str(exc))) from exc
                    except RuntimeError as exc:
                        page_items = []
                        source_error = storage.clean(str(exc))
                    attempt_errors = [attempt["error"] for attempt in attempts if attempt.get("error")]
                    if attempt_errors and not page_items:
                        source_error = attempt_errors[-1]
                    page_new_count = 0
                    for item in page_items:
                        if item["url"] in source_seen:
                            duplicate_count += 1
                            continue
                        source_seen.add(item["url"])
                        source_items.append(item)
                        page_new_count += 1
                    if search_fetcher is not None:
                        break
                    continue_yield = (
                        LINKEDIN_CONTINUE_YIELD
                        if strategy["id"] == "linkedin"
                        else GOOGLE_CONTINUE_YIELD
                    )
                    if page_new_count < continue_yield:
                        break

                if successful_pages == 0:
                    failed_sources += 1
                if source_error:
                    errors.append(
                        f"{strategy['label']} · {lane.get('label') or lane.get('location')}: {source_error}"
                    )
                source_runs.append(
                    {
                        "source": strategy["id"],
                        "label": strategy["label"],
                        "query_family": family["id"],
                        "query_family_label": family["label"],
                        "lane_id": lane.get("id", ""),
                        "lane_label": lane.get("label", "") or lane.get("location", ""),
                        "query": query,
                        "found_count": len(source_items),
                        "page_count": successful_pages,
                        "engine": engine,
                    }
                )
                for item in source_items:
                    existing = found_by_url.get(item["url"])
                    match_context = {"strategy": strategy, "lane": lane, "family": family}
                    if existing:
                        duplicate_count += 1
                        existing["matches"].append(match_context)
                        continue
                    combined = {**item, "matches": [match_context]}
                    found_by_url[item["url"]] = combined
                    found.append(combined)
    found = found[:RAW_DISCOVERY_RESULT_LIMIT]

    if attempted_sources and failed_sources == attempted_sources:
        first_error = errors[0].split(": ", 1)[-1] if errors else "Hunter Chrome search was unavailable."
        raise RuntimeError(first_error)

    prepared = []
    skipped_count = 0
    for result in found:
        candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        candidate.update(extracted_candidate(result["url"], fetcher=posting_fetcher))
        apply_search_result_details(candidate, result)
        enrich_workday_candidate(candidate, fetcher=posting_fetcher)
        posting_evidence = candidate.pop("_posting_evidence", "")
        if posting_evidence != "individual":
            skipped_count += 1
            continue
        if candidate_is_excluded(search, candidate):
            skipped_count += 1
            continue
        matched_context = next(
            (
                context
                for context in result["matches"]
                if candidate_matches_lane(candidate, result, context["lane"])
            ),
            None,
        )
        if matched_context is None:
            skipped_count += 1
            continue
        candidate.update(
            {
                "search_id": search["id"],
                "captured_at": timestamp,
                "last_seen_at": timestamp,
                "status": "new",
                "notes": "Found automatically in Discovery.",
            }
        )
        score_candidate(candidate, timestamp)
        apply_candidate_review_admission(candidate)
        candidate["_match_context"] = matched_context
        candidate["_search_result"] = result
        duplicate = matching_candidate(prepared, candidate)
        if duplicate:
            duplicate_count += 1
            if candidate_rank_key(candidate) > candidate_rank_key(duplicate):
                prepared[prepared.index(duplicate)] = candidate
            continue
        prepared.append(candidate)

    excluded_company_identity = discovery_excluded_company_identity()
    prepared, excluded_company_count = filter_discovery_excluded_companies(
        prepared,
        excluded_company_identity,
    )
    skipped_count += excluded_company_count

    enriched_count = 0
    invalid_after_enrichment = set()
    if browser_detailer is not None:
        enrichment_candidates = [
            candidate
            for candidate in sorted(
                prepared,
                key=lambda item: (
                    item.get("processing_status") != "ready",
                    candidate_rank_key(item),
                ),
                reverse=True,
            )
            if (
                candidate.get("processing_status") != "ready"
                or not candidate.get("company_industry")
                or not candidate.get("company_size")
            )
            and candidate.get("status") == "new"
        ][:DETAIL_ENRICHMENT_LIMIT]
        for candidate in enrichment_candidates:
            try:
                details = browser_detailer(candidate.get("canonical_url") or candidate.get("url", ""))
            except browser_discovery.BrowserDiscoveryError as exc:
                raise RuntimeError(storage.clean(str(exc))) from exc
            except RuntimeError as exc:
                errors.append(f"Posting detail enrichment: {storage.clean(str(exc))}")
                continue
            if not details:
                continue
            previous_status = candidate.get("processing_status", "")
            previous_description = candidate.get("description_text", "")
            previous_company_metadata = (
                candidate.get("company_industry", ""),
                candidate.get("company_size", ""),
                candidate.get("company_profile_url", ""),
            )
            apply_browser_details(candidate, details)
            score_candidate(candidate, timestamp)
            apply_candidate_review_admission(candidate)
            context = candidate.get("_match_context", {})
            result = candidate.get("_search_result", {})
            if context and not candidate_matches_lane(candidate, result, context.get("lane", {})):
                invalid_after_enrichment.add(id(candidate))
                skipped_count += 1
                continue
            if (
                candidate.get("processing_status") != previous_status
                or candidate.get("description_text", "") != previous_description
                or previous_company_metadata
                != (
                    candidate.get("company_industry", ""),
                    candidate.get("company_size", ""),
                    candidate.get("company_profile_url", ""),
                )
            ):
                enriched_count += 1

    prepared = [
        candidate
        for candidate in prepared
        if id(candidate) not in invalid_after_enrichment
    ]
    prepared, excluded_after_enrichment = filter_discovery_excluded_companies(
        prepared,
        excluded_company_identity,
    )
    skipped_count += excluded_after_enrichment
    deduped_prepared = []
    for candidate in prepared:
        duplicate = matching_candidate(deduped_prepared, candidate)
        if duplicate is None:
            deduped_prepared.append(candidate)
            continue
        duplicate_count += 1
        if candidate_rank_key(candidate) > candidate_rank_key(duplicate):
            deduped_prepared[deduped_prepared.index(duplicate)] = candidate
    prepared = deduped_prepared
    for candidate in prepared:
        candidate.pop("_match_context", None)
        candidate.pop("_search_result", None)

    qualified_count = sum(candidate.get("status") == "new" for candidate in prepared)
    screened_count = sum(candidate.get("status") == SCREENED_STATUS for candidate in prepared)
    selected = sorted(
        prepared,
        key=lambda candidate: (
            candidate.get("status") == "new",
            candidate_rank_key(candidate),
        ),
        reverse=True,
    )[:DISCOVERY_RESULT_LIMIT]
    limited_count = max(0, len(prepared) - len(selected))
    company_by_id = {}
    connected_selected = []
    excluded_after_connection = 0
    for candidate in selected:
        if candidate.get("status") == SCREENED_STATUS:
            connected_selected.append(candidate)
            continue
        company = connect_candidate_company(candidate, seen_at=timestamp)
        if (
            company
            and company.get("interest_status", "").lower()
            in DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES
        ):
            excluded_after_connection += 1
            continue
        connected_selected.append(candidate)
        if company:
            company_by_id[company.get("id", "")] = company
        apply_candidate_review_admission(candidate, company)
    selected = connected_selected
    screened_count = sum(candidate.get("status") == SCREENED_STATUS for candidate in selected)
    if excluded_after_connection:
        skipped_count += excluded_after_connection

    company_researched_count = 0
    company_suggestion_count = 0
    company_research_attempt_count = 0
    researched_company_ids = set()
    if company_researcher is not None:
        for candidate in selected:
            company_id = candidate.get("company_id", "")
            company = company_by_id.get(company_id)
            if (
                not company
                or company_id in researched_company_ids
                or company.get("industry") and company.get("company_size")
            ):
                continue
            if company_research_attempt_count >= COMPANY_RESEARCH_LIMIT:
                break
            company_research_attempt_count += 1
            try:
                research = companies.research_company(
                    company_id,
                    researcher=company_researcher,
                )
            except RuntimeError as exc:
                errors.append(
                    f"Company research for {company.get('name', 'company')}: "
                    f"{storage.clean(str(exc))}"
                )
                researched_company_ids.add(company_id)
                continue
            researched_company_ids.add(company_id)
            company_researched_count += 1
            company_suggestion_count += len(research.get("suggestions", []))
            company_by_id[company_id] = research.get("company", company)

    rows = repository.read_discovery_candidates()
    captured = []
    new_count = 0
    updated_count = 0
    captured_ids = set()
    for candidate in selected:
        sync_candidate_source_urls(candidate)
        existing = matching_candidate(rows, candidate)
        if existing:
            merge_candidate(existing, candidate)
            if existing["id"] not in captured_ids:
                captured.append(existing)
                captured_ids.add(existing["id"])
                updated_count += 1
        else:
            candidate["id"] = next_id(rows, "DC")
            rows.append(candidate)
            captured.append(candidate)
            captured_ids.add(candidate["id"])
            if candidate.get("status") == "new":
                new_count += 1

    rows = canonicalize_candidate_rows(rows)
    captured = [
        matching_candidate(rows, candidate) or candidate
        for candidate in captured
    ]
    repository.write_discovery_candidates(rows)
    stored_by_id = {
        row.get("id", ""): row
        for row in repository.read_discovery_candidates()
    }
    captured = [
        stored_by_id.get(candidate.get("id", ""), candidate)
        for candidate in captured
    ]
    result = {
        "search": get_search(search["id"]),
        "captured": captured,
        "evaluated_count": len(found),
        "qualified_count": qualified_count,
        "screened_count": screened_count,
        "found_count": len(captured),
        "new_count": new_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "duplicate_count": duplicate_count,
        "limited_count": limited_count,
        "enriched_count": enriched_count,
        "company_researched_count": company_researched_count,
        "company_suggestion_count": company_suggestion_count,
        "sources": source_runs,
        "errors": errors,
    }
    stored_rows = repository.read_discovery_searches()
    stored = next((row for row in stored_rows if row.get("id") == search["id"]), None)
    if stored is not None:
        stored["last_opened_at"] = timestamp
        stored["last_run_at"] = timestamp
        stored["last_run_summary_json"] = json.dumps(
            {
                field: result[field]
                for field in [
                    "evaluated_count",
                    "qualified_count",
                    "screened_count",
                    "found_count",
                    "new_count",
                    "updated_count",
                    "skipped_count",
                    "duplicate_count",
                    "limited_count",
                    "enriched_count",
                    "company_researched_count",
                    "company_suggestion_count",
                ]
            }
            | {
                "sources": result["sources"],
                "errors": result["errors"],
            }
        )
        repository.write_discovery_searches(stored_rows)
    result["search"] = get_search(search["id"])
    return result


def parse_timestamp(value):
    try:
        return datetime.fromisoformat(storage.clean(value))
    except (TypeError, ValueError):
        return None


def freshness_due(candidate, reference=None):
    checked_at = parse_timestamp(candidate.get("freshness_checked_at", ""))
    if checked_at is None:
        return True
    return checked_at <= (reference or datetime.now()) - timedelta(days=FRESHNESS_RECHECK_DAYS)


def company_research_needed(company, reference=None):
    if not company:
        return False
    if company.get("interest_status", "").lower() in DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES:
        return False
    if company.get("industry") and company.get("company_size"):
        return False
    checked_at = parse_timestamp(company.get("company_metadata_checked_at", ""))
    if checked_at is None:
        return True
    return checked_at <= (reference or datetime.now()) - timedelta(days=FRESHNESS_RECHECK_DAYS)


def enrichment_needed(candidate, company=None, reference=None):
    if candidate.get("status") in {"ignored", "ingested", "duplicate", SCREENED_STATUS}:
        return False
    if ignored_discovery_source(candidate.get("canonical_url") or candidate.get("url", "")):
        return False
    if (
        company
        and company.get("interest_status", "").lower()
        in DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES
    ):
        return False
    if candidate.get("processing_status") != "ready":
        return True
    if not candidate.get("canonical_url"):
        return True
    if freshness_due(candidate, reference):
        return True
    if company and (
        not company.get("industry")
        or not company.get("company_size")
    ) and not storage.clean(company.get("company_research_status", "")).startswith("ok:"):
        return True
    return False


def enrichment_priority(candidate, company=None):
    return (
        candidate.get("status") == "new",
        candidate.get("processing_status") != "ready",
        not candidate.get("canonical_url"),
        bool(company and (not company.get("industry") or not company.get("company_size"))),
        freshness_due(candidate),
        candidate_rank_key(candidate),
    )


def continue_enrichment(
    limit=CONTINUE_ENRICHMENT_LIMIT,
    browser_detailer=None,
    company_researcher=None,
):
    rows = canonicalize_candidate_rows(repository.read_discovery_candidates())
    companies_by_id = {
        company.get("id", ""): company
        for company in repository.read_companies()
    }
    chrome_browser = None
    if browser_detailer is None or company_researcher is None:
        chrome_browser = browser_discovery.HunterChrome()
        chrome_browser.find_window()
        browser_detailer = browser_detailer or chrome_browser.details
        company_researcher = company_researcher or chrome_browser.company

    selected = sorted(
        [
            candidate
            for candidate in rows
            if enrichment_needed(
                candidate,
                companies_by_id.get(candidate.get("company_id", "")),
            )
        ],
        key=lambda candidate: enrichment_priority(
            candidate,
            companies_by_id.get(candidate.get("company_id", "")),
        ),
        reverse=True,
    )[:max(1, int(limit or CONTINUE_ENRICHMENT_LIMIT))]

    timestamp = now_iso()
    posting_checked_count = 0
    posting_enriched_count = 0
    company_researched_count = 0
    unavailable_count = 0
    errors = []
    researched_company_ids = set()
    for candidate in selected:
        target_url = candidate.get("canonical_url") or candidate.get("url", "")
        if target_url and (
            candidate.get("processing_status") != "ready"
            or not candidate.get("canonical_url")
            or freshness_due(candidate)
        ):
            posting_checked_count += 1
            before = (
                candidate.get("processing_status", ""),
                candidate.get("canonical_url", ""),
                candidate.get("location", ""),
                candidate.get("work_mode", ""),
                candidate.get("description_text", ""),
            )
            try:
                details = browser_detailer(target_url) or {}
            except (browser_discovery.BrowserDiscoveryError, RuntimeError) as exc:
                candidate["freshness_status"] = "needs-review"
                candidate["freshness_checked_at"] = timestamp
                errors.append(
                    f"{candidate.get('title') or target_url}: {storage.clean(str(exc))}"
                )
                details = {}
            if details:
                apply_browser_details(candidate, details)
                if not candidate.get("freshness_status"):
                    candidate["freshness_status"] = "confirmed-open"
                    candidate["freshness_checked_at"] = timestamp
                if candidate.get("freshness_status") == "closed":
                    unavailable_count += 1
                after = (
                    candidate.get("processing_status", ""),
                    candidate.get("canonical_url", ""),
                    candidate.get("location", ""),
                    candidate.get("work_mode", ""),
                    candidate.get("description_text", ""),
                )
                if after != before:
                    posting_enriched_count += 1

        company = connect_candidate_company(candidate, seen_at=timestamp)
        score_candidate(candidate, timestamp)
        sync_candidate_source_urls(candidate)
        if not company:
            continue
        company_id = company.get("id", "")
        companies_by_id[company_id] = company
        if (
            company_research_needed(company)
            and company_id not in researched_company_ids
            and len(researched_company_ids) < COMPANY_RESEARCH_LIMIT
        ):
            researched_company_ids.add(company_id)
            try:
                research = companies.research_company(
                    company_id,
                    researcher=company_researcher,
                )
                companies_by_id[company_id] = research.get("company", company)
                company_researched_count += 1
            except (browser_discovery.BrowserDiscoveryError, RuntimeError) as exc:
                errors.append(
                    f"Company research for {company.get('name', 'company')}: "
                    f"{storage.clean(str(exc))}"
                )

    active_company_ids = {
        candidate.get("company_id", "")
        for candidate in rows
        if candidate.get("status") == "new" and candidate.get("company_id")
    }
    company_queue = sorted(
        [
            company
            for company in companies_by_id.values()
            if company.get("id") in active_company_ids
            and company.get("id") not in researched_company_ids
            and company_research_needed(company)
        ],
        key=lambda company: (
            company.get("discovered_at", ""),
            company.get("last_seen_at", ""),
            company.get("name", ""),
        ),
        reverse=True,
    )
    for company in company_queue[:max(0, COMPANY_RESEARCH_LIMIT - len(researched_company_ids))]:
        company_id = company.get("id", "")
        researched_company_ids.add(company_id)
        try:
            research = companies.research_company(
                company_id,
                researcher=company_researcher,
            )
            companies_by_id[company_id] = research.get("company", company)
            company_researched_count += 1
        except (browser_discovery.BrowserDiscoveryError, RuntimeError) as exc:
            errors.append(
                f"Company research for {company.get('name', 'company')}: "
                f"{storage.clean(str(exc))}"
            )

    rows = canonicalize_candidate_rows(rows)
    repository.write_discovery_candidates(rows)
    refreshed_companies = {
        company.get("id", ""): company
        for company in repository.read_companies()
    }
    remaining_count = sum(
        1
        for candidate in rows
        if enrichment_needed(
            candidate,
            refreshed_companies.get(candidate.get("company_id", "")),
        )
    )
    ready_count = sum(
        1
        for candidate in rows
        if candidate.get("status") == "new"
        and candidate.get("processing_status") == "ready"
        and candidate.get("freshness_status") != "closed"
    )
    company_research_remaining_count = sum(
        1
        for company in refreshed_companies.values()
        if company.get("id") in active_company_ids and company_research_needed(company)
    )
    return {
        "processed_count": len(selected),
        "posting_checked_count": posting_checked_count,
        "posting_enriched_count": posting_enriched_count,
        "company_researched_count": company_researched_count,
        "company_research_remaining_count": company_research_remaining_count,
        "unavailable_count": unavailable_count,
        "remaining_count": remaining_count,
        "ready_count": ready_count,
        "errors": errors,
    }


def continue_discovery(search_id, enrichment_limit=CONTINUE_ENRICHMENT_LIMIT):
    search = get_search(search_id)
    chrome_browser = browser_discovery.HunterChrome()
    chrome_browser.find_window()

    def browser_searcher(engine, value, page):
        return browser_discovery.search(engine, value, page=page, browser=chrome_browser)

    try:
        result = run_search(
            search_id,
            browser_searcher=browser_searcher,
            browser_detailer=chrome_browser.details,
            company_researcher=chrome_browser.company,
        )
    except RuntimeError as exc:
        result = {
            "search": search,
            "captured": [],
            "evaluated_count": 0,
            "qualified_count": 0,
            "found_count": 0,
            "new_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "duplicate_count": 0,
            "limited_count": 0,
            "enriched_count": 0,
            "company_researched_count": 0,
            "company_suggestion_count": 0,
            "sources": [],
            "errors": [storage.clean(str(exc))],
        }
    enrichment = continue_enrichment(
        limit=enrichment_limit,
        browser_detailer=chrome_browser.details,
        company_researcher=chrome_browser.company,
    )
    result["enrichment"] = enrichment
    result["errors"] = [*result.get("errors", []), *enrichment.get("errors", [])]

    stored_rows = repository.read_discovery_searches()
    stored = next((row for row in stored_rows if row.get("id") == search["id"]), None)
    if stored is not None:
        try:
            summary = json.loads(stored.get("last_run_summary_json", "") or "{}")
        except (TypeError, ValueError):
            summary = {}
        summary["enrichment"] = enrichment
        summary["errors"] = result["errors"]
        stored["last_run_summary_json"] = json.dumps(summary)
        repository.write_discovery_searches(stored_rows)
    result["search"] = get_search(search_id)
    return result


def normalized_source_host(url):
    return urlparse(storage.clean(url)).netloc.lower().removeprefix("www.").split(":", 1)[0]


def host_matches(host, expected):
    return bool(host and expected and (host == expected or host.endswith(f".{expected}")))


def source_url_is_low_trust(url):
    host = normalized_source_host(url)
    return any(host_matches(host, expected) for expected in LOW_TRUST_SOURCE_HOSTS)


def company_name_matches_source(company_name, source_url):
    normalized_name = re.sub(r"[^a-z0-9]+", "", storage.clean(company_name).lower())
    host_label = normalized_source_host(source_url).split(".", 1)[0]
    normalized_host = re.sub(r"[^a-z0-9]+", "", host_label.lower())
    return bool(
        normalized_name
        and normalized_host
        and (
            normalized_name == normalized_host
            or normalized_name.startswith(normalized_host)
            or normalized_host.startswith(normalized_name)
        )
    )


def candidate_source_trust(candidate, company=None):
    source_url = candidate.get("canonical_url") or candidate.get("url", "")
    host = normalized_source_host(source_url)
    detected_platform = source_platform(source_url)
    platform = (
        detected_platform
        if detected_platform != "employer"
        else candidate.get("source_platform") or detected_platform
    )
    if candidate.get("freshness_status") == "closed":
        return {"id": "closed", "label": "Closed", "is_direct_employer_source": False}
    if source_url_is_low_trust(source_url):
        return {"id": "aggregator", "label": "Aggregator", "is_direct_employer_source": False}
    if platform in DIRECT_ATS_PLATFORMS and individual_ats_posting_url(source_url, platform):
        return {"id": "employer", "label": "Employer", "is_direct_employer_source": True}
    if any(host_matches(host, expected) for expected in DIRECT_ATS_HOSTS):
        return {"id": "employer", "label": "Employer", "is_direct_employer_source": True}
    company_domain = companies.company_domain((company or {}).get("website", ""))
    if company_domain and host_matches(host, company_domain):
        return {"id": "employer", "label": "Employer", "is_direct_employer_source": True}
    if host.startswith(("careers.", "jobs.")) and not any(
        host_matches(host, expected) for expected in LOW_TRUST_SOURCE_HOSTS
    ):
        return {"id": "employer", "label": "Employer", "is_direct_employer_source": True}
    if host_matches(host, "linkedin.com") or platform == "linkedin":
        return {"id": "network", "label": "LinkedIn", "is_direct_employer_source": False}
    return {"id": "unverified", "label": "Unverified", "is_direct_employer_source": False}


def candidate_review_admission(candidate, company=None):
    source_url = candidate.get("canonical_url") or candidate.get("url", "")
    detected_platform = source_platform(source_url)
    platform = (
        detected_platform
        if detected_platform != "employer"
        else candidate.get("source_platform") or detected_platform
    )
    if platform in DIRECT_ATS_PLATFORMS and not individual_ats_posting_url(source_url, platform):
        return False, "the ATS URL is a board, redirect, or error page"
    try:
        fit_score = int(candidate.get("fit_score", "") or 0)
    except (TypeError, ValueError):
        fit_score = 0
    if fit_score < MIN_REVIEW_FIT_SCORE:
        return False, f"the role match score is below {MIN_REVIEW_FIT_SCORE}"
    trust = candidate_source_trust(candidate, company)
    if trust["id"] == "aggregator":
        return False, "the posting is from an aggregator without a verified employer source"
    if candidate.get("freshness_status") == "closed":
        return False, "the posting is closed"
    return True, ""


def apply_candidate_review_admission(candidate, company=None):
    admitted, reason = candidate_review_admission(candidate, company)
    warning_lines = [
        line
        for line in (candidate.get("warnings", "") or "").splitlines()
        if line and not line.startswith(SCREENING_WARNING_PREFIX)
    ]
    if admitted:
        if candidate.get("status") == SCREENED_STATUS:
            candidate["status"] = "new"
    else:
        candidate["status"] = SCREENED_STATUS
        warning_lines.append(f"{SCREENING_WARNING_PREFIX}{reason}.")
    candidate["warnings"] = "\n".join(dict.fromkeys(warning_lines))
    return admitted


def reclassify_review_queue():
    rows = canonicalize_candidate_rows(repository.read_discovery_candidates())
    company_by_id = {
        company.get("id", ""): company
        for company in repository.read_companies()
    }
    screened_count = 0
    restored_count = 0
    for candidate in rows:
        if candidate.get("status") not in {"new", SCREENED_STATUS}:
            continue
        previous_status = candidate.get("status", "")
        apply_candidate_review_admission(
            candidate,
            company_by_id.get(candidate.get("company_id", "")),
        )
        if previous_status != SCREENED_STATUS and candidate.get("status") == SCREENED_STATUS:
            screened_count += 1
        elif previous_status == SCREENED_STATUS and candidate.get("status") == "new":
            restored_count += 1
    repository.write_discovery_candidates(rows)
    return {
        "screened_count": screened_count,
        "restored_count": restored_count,
    }


def recommendation_eligible(candidate, company=None):
    trust = candidate_source_trust(candidate, company)
    try:
        fit_score = int(candidate.get("fit_score", "") or 0)
    except (TypeError, ValueError):
        fit_score = 0
    return bool(
        candidate.get("status") == "new"
        and candidate.get("processing_status") == "ready"
        and candidate.get("freshness_status") == "confirmed-open"
        and trust["id"] in {"employer", "network"}
        and fit_score >= companies.FIT_RECOMMENDATION_THRESHOLD
    )


def fit_strengths(candidate, company=None):
    summary = storage.clean(candidate.get("fit_summary", ""))
    match = re.search(r"\bmatches\s+(.+?)\.?$", summary, re.I)
    strengths = []
    if match:
        strengths.extend(
            storage.clean(value)
            for value in match.group(1).split(",")
            if storage.clean(value)
        )
    if candidate.get("processing_status") == "ready":
        strengths.append("Verified posting details")
    if candidate_source_trust(candidate, company)["is_direct_employer_source"]:
        strengths.append("Direct employer posting available")
    return list(dict.fromkeys(strengths))


def fit_gaps(candidate, company=None):
    gaps = []
    if candidate.get("processing_status") != "ready":
        gaps.append("Posting details still need verification")
    if not candidate.get("location"):
        gaps.append("Location is unknown")
    if not candidate.get("work_mode"):
        gaps.append("Work mode is unknown")
    if not candidate.get("canonical_url"):
        gaps.append("Direct employer posting link is missing")
    if company and not company.get("industry"):
        gaps.append("Company industry has not been researched")
    if company and not company.get("company_size"):
        gaps.append("Company size has not been researched")
    if candidate.get("freshness_status") not in {"confirmed-open", "closed"}:
        gaps.append("Posting freshness has not been confirmed")
    source_trust = candidate_source_trust(candidate, company)
    if source_trust["id"] == "aggregator":
        gaps.append("Posting comes from an aggregator; confirm it with the employer")
    elif source_trust["id"] == "unverified":
        gaps.append("Posting source has not been verified")
    if candidate.get("warnings"):
        gaps.extend(
            storage.clean(line)
            for line in candidate.get("warnings", "").splitlines()
            if storage.clean(line)
        )
    return list(dict.fromkeys(gaps))


def candidate_source_confidence(candidate, company=None):
    trust = candidate_source_trust(candidate, company)
    if trust["id"] == "closed":
        return "Closed"
    if trust["id"] == "employer" and candidate.get("processing_status") == "ready":
        return "High"
    if trust["id"] == "network" and candidate.get("processing_status") == "ready":
        return "Medium"
    return "Low"


def candidate_lane_match(candidate):
    matches = []
    for search in list_searches():
        for lane in search.get("lanes", []):
            if candidate_matches_lane(candidate, {}, lane):
                label = lane.get("label", "") or lane.get("location", "")
                mode = storage.clean(candidate.get("work_mode", ""))
                value = f"{label} · {mode}" if mode else label
                if value and value not in matches:
                    matches.append(value)
    return ", ".join(matches[:2])


def candidate_payload(candidate, company_by_id=None):
    payload = dict(candidate)
    company = (company_by_id or {}).get(candidate.get("company_id", ""))
    source_trust = candidate_source_trust(candidate, company)
    payload["source_urls"] = candidate_source_urls(candidate)
    payload["fit_strengths"] = fit_strengths(candidate, company)
    payload["fit_gaps"] = fit_gaps(candidate, company)
    payload["source_confidence"] = candidate_source_confidence(candidate, company)
    payload["source_trust"] = source_trust["id"]
    payload["source_trust_label"] = source_trust["label"]
    payload["is_direct_employer_source"] = source_trust["is_direct_employer_source"]
    payload["recommendation_eligible"] = recommendation_eligible(candidate, company)
    payload["lane_match"] = candidate_lane_match(candidate)
    return payload


def list_candidates():
    collapsed = canonicalize_candidate_rows(repository.read_discovery_candidates())
    company_by_id = {
        company.get("id", ""): company
        for company in repository.read_companies()
    }
    return [
        candidate_payload(candidate, company_by_id)
        for candidate in collapsed
        if not ignored_discovery_source(
            candidate.get("canonical_url") or candidate.get("url", "")
        )
    ]


def preference_suggestions():
    searches = list_searches()
    searches_by_id = {
        search.get("id", ""): search
        for search in searches
        if search.get("id", "")
    }
    fallback_search_id = searches[0].get("id", "") if len(searches) == 1 else ""
    ignored_by_search = {}
    for candidate in list_candidates():
        if candidate.get("status") != "ignored":
            continue
        if candidate.get("ignore_reason") not in {"", "wrong-role", "level", "other"}:
            continue
        search_id = storage.clean(candidate.get("search_id", "")) or fallback_search_id
        if search_id in searches_by_id:
            ignored_by_search.setdefault(search_id, []).append(candidate)
    stop_words = {
        "and", "for", "the", "technical", "technology", "program", "programme",
        "manager", "management", "senior", "staff", "principal", "lead", "director",
        "remote", "hybrid", "onsite", "role", "jobs", "system", "systems",
        "product", "engineering", "software",
    }
    suggestions = []
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
            candidate_terms = {
                token
                for token in re.findall(r"[a-z][a-z0-9+#.-]{2,}", title.lower())
                if token not in stop_words and token not in excluded_terms
            }
            for term in candidate_terms:
                item = terms.setdefault(term, {"candidate_ids": [], "samples": []})
                item["candidate_ids"].append(candidate.get("id", ""))
                if title and title not in item["samples"]:
                    item["samples"].append(title)
        suggestions.extend(
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
        suggestions,
        key=lambda item: (-item["ignored_count"], item["search_name"].lower(), item["term"]),
    )[:10]


def get_candidate(candidate_id):
    wanted = storage.clean(candidate_id).upper()
    row = next(
        (item for item in repository.read_discovery_candidates() if item.get("id", "").upper() == wanted),
        None,
    )
    if row is None:
        raise ValueError(f"No Discovery candidate found with id {candidate_id}.")
    company_by_id = {
        company.get("id", ""): company
        for company in repository.read_companies()
    }
    return candidate_payload(row, company_by_id)


def parse_capture_urls(value):
    urls = []
    for match in URL_PATTERN.findall(value or ""):
        cleaned = match.rstrip(".,);]}")
        normalized = companies.normalize_url(cleaned)
        if normalized and normalized not in urls:
            urls.append(normalized)
    return urls


def meta_values(page_html):
    values = {}
    for tag in re.findall(r"<meta\b[^>]*>", page_html or "", re.I | re.S):
        attributes = {
            key.lower(): html.unescape(value)
            for key, _quote, value in re.findall(r"([a-zA-Z_:.-]+)\s*=\s*(['\"])(.*?)\2", tag, re.S)
        }
        key = attributes.get("property") or attributes.get("name")
        content = attributes.get("content")
        if key and content:
            values[key.lower()] = storage.clean(content)
    return values


def page_title(page_html):
    match = re.search(r"<title[^>]*>(.*?)</title>", page_html or "", re.I | re.S)
    return companies.clean_html_text(match.group(1)) if match else ""


def canonical_page_url(page_html, base_url):
    for tag in re.findall(r"<link\b[^>]*>", page_html or "", re.I | re.S):
        rel = re.search(r"\brel\s*=\s*(['\"])(.*?)\1", tag, re.I | re.S)
        href = re.search(r"\bhref\s*=\s*(['\"])(.*?)\1", tag, re.I | re.S)
        if rel and href and "canonical" in rel.group(2).lower().split():
            return companies.normalize_url(urljoin(base_url, html.unescape(href.group(2)).strip()))
    return ""


def source_platform(url):
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if host == "linkedin.com":
        return "linkedin"
    for marker, label in [
        ("greenhouse.io", "greenhouse"),
        ("ashbyhq.com", "ashby"),
        ("lever.co", "lever"),
        ("myworkdayjobs.com", "workday"),
        ("workdayjobs.com", "workday"),
        ("smartrecruiters.com", "smartrecruiters"),
    ]:
        if marker in host:
            return label
    return "employer"


def structured_job(page_html):
    return next(
        (
            item
            for item in companies.json_ld_objects(page_html)
            if "JobPosting" in (
                item.get("@type", []) if isinstance(item.get("@type"), list) else [item.get("@type")]
            )
        ),
        {},
    )


def organization_name(job):
    organization = job.get("hiringOrganization") if isinstance(job, dict) else None
    return storage.clean(organization.get("name", "")) if isinstance(organization, dict) else ""


def job_description(job):
    if not isinstance(job, dict):
        return ""
    parts = []
    for field in [
        "description",
        "responsibilities",
        "qualifications",
        "skills",
        "experienceRequirements",
        "educationRequirements",
    ]:
        value = job.get(field)
        if isinstance(value, (list, dict)):
            value = str(value)
        if value:
            parts.append(str(value))
    return companies.clean_html_text(" ".join(parts))[:MAX_DESCRIPTION_CHARS]


def work_mode_from_text(location, description):
    location_text = storage.clean(location).lower()
    if re.search(r"\bhybrid\b", location_text):
        return "Hybrid"
    if re.search(r"\b(?:remote|telecommute|work from home)\b", location_text):
        return "Remote"
    if re.search(r"\b(?:on-site|onsite|on site)\b", location_text):
        return "On-site"
    description_text = storage.clean(description).lower()
    if re.search(
        r"\b(?:this|the)?\s*(?:role|position|job|work arrangement|work location)\s+"
        r"(?:is|will be|can be|may be|offers?)\s+(?:fully\s+)?hybrid\b"
        r"|\bhybrid\s+(?:role|position|job|schedule|work arrangement)\b",
        description_text,
    ):
        return "Hybrid"
    if re.search(
        r"\b(?:this|the)?\s*(?:role|position|job|work arrangement|work location)\s+"
        r"(?:is|will be|can be|may be|offers?)\s+(?:fully\s+)?remote\b"
        r"|\bremote\s+(?:role|position|job|work arrangement)\b"
        r"|\bremote\s+(?:in|within|across)\s+(?:the\s+)?(?:united states|u\.?s\.?|usa)\b"
        r"|\b(?:united states|u\.?s\.?|usa)\s*[·|,;-]\s*remote\b"
        r"|\bwork(?:ing)?\s+(?:fully\s+)?remotely\b"
        r"|\btelecommut(?:e|ing)\b",
        description_text,
    ):
        return "Remote"
    if re.search(
        r"\b(?:this|the)?\s*(?:role|position|job|work arrangement|work location)\s+"
        r"(?:is|will be|can be|may be|requires?)\s+(?:fully\s+)?(?:on-site|onsite|on site)\b"
        r"|\b(?:on-site|onsite|on site)\s+(?:role|position|job|work arrangement)\b",
        description_text,
    ):
        return "On-site"
    return ""


def individual_posting_evidence(
    page_html,
    job,
    title,
    description,
    fetch_error="",
    url="",
    platform="",
):
    if fetch_error or not page_html:
        return False
    if platform in DIRECT_ATS_PLATFORMS and not individual_ats_posting_url(url, platform):
        return False
    text = storage.clean(f"{title} {description} {companies.clean_html_text(page_html)[:12000]}").lower()
    blocked_markers = [
        "access denied",
        "captcha",
        "enable javascript and cookies to continue",
        "just a moment",
        "page not found",
        "request unsuccessful",
        "robot or human",
        "security check",
        "verify you are human",
    ]
    if any(marker in text for marker in blocked_markers):
        return False
    if job:
        return bool(storage.clean(str(job.get("title", ""))) and job_description(job))
    if len(description) < 250:
        return False
    generic_title = re.fullmatch(
        r"(?:careers?|jobs?|job search|search jobs|open positions|career opportunities|home)",
        storage.clean(title).lower(),
    )
    if generic_title:
        return False
    return bool(
        re.search(
            r"\b(?:apply(?: now)?|job description|responsibilities|qualifications|requirements|"
            r"employment type|compensation|salary range|what you(?:'|’)ll do)\b",
            text,
            re.I,
        )
    )


def extracted_candidate(url, fetcher=None):
    if urlparse(url).netloc.lower() in LINKEDIN_HOSTS:
        return {
            "company": "",
            "title": "",
            "url": companies.normalize_url(url),
            "canonical_url": "",
            "location": "",
            "work_mode": "",
            "source_platform": "linkedin",
            "description_text": "",
            "warnings": LINKEDIN_DETAILS_WARNING,
            "_posting_evidence": "individual",
        }

    fetched = (fetcher or companies.fetch_careers_page)(url)
    page_html = fetched.get("html", "") or ""
    final_url = companies.normalize_url(fetched.get("final_url") or url)
    parsed_host = urlparse(final_url or url).netloc.lower()
    warnings = []
    if fetched.get("error"):
        warnings.append(f"Could not read the source: {fetched.get('error')}")
    if not page_html:
        warnings.append("No readable posting content was returned.")

    meta = meta_values(page_html)
    job = structured_job(page_html)
    title = storage.clean(str(job.get("title", "") or "")) or meta.get("og:title", "") or page_title(page_html)
    platform = source_platform(final_url or url)
    company = organization_name(job)
    if not company and not source_url_is_low_trust(final_url or url):
        company = meta.get("og:site_name", "")
    company_metadata = companies.structured_company_metadata(page_html, job)
    description = job_description(job)
    if not description and page_html and parsed_host not in LINKEDIN_HOSTS:
        description = companies.clean_html_text(page_html)[:MAX_DESCRIPTION_CHARS]
    location = companies.structured_job_location(job) if job else ""
    canonical_url = canonical_page_url(page_html, final_url)
    if parsed_host in LINKEDIN_HOSTS:
        canonical_url = ""
        if not description:
            warnings.append(LINKEDIN_DETAILS_WARNING)
    elif not canonical_url:
        canonical_url = final_url
    posting_evidence = individual_posting_evidence(
        page_html,
        job,
        title,
        description,
        fetched.get("error", ""),
        canonical_url or final_url,
        platform,
    )
    structured_work_mode = (
        "Remote"
        if storage.clean(str(job.get("jobLocationType", ""))).upper() == "TELECOMMUTE"
        else ""
    )

    return {
        "company": company,
        "title": title,
        "url": companies.normalize_url(url),
        "canonical_url": canonical_url,
        "location": location,
        "work_mode": structured_work_mode or work_mode_from_text(location, description),
        **company_metadata,
        "company_metadata_source": final_url if any(company_metadata.values()) else "",
        "source_platform": platform,
        "description_text": description,
        "warnings": "\n".join(dict.fromkeys(warnings)),
        "_posting_evidence": "individual" if posting_evidence else "",
    }


def apply_manual_details(candidate, details):
    details = details or {}
    if "company_id" in details:
        candidate["company_id"] = storage.clean(details.get("company_id", ""))
    for field in [
        "title",
        "canonical_url",
        "location",
        "work_mode",
        "notes",
    ]:
        value = storage.clean(details.get(field, ""))
        if value:
            if field == "canonical_url":
                candidate[field] = companies.normalize_url(value)
            else:
                candidate[field] = value
    company_name = storage.clean(
        details.get("company_name", "")
        or details.get("company", "")
    )
    if company_name:
        candidate["company"] = company_name
    description = str(details.get("description_text", "") or "").strip()
    if description:
        candidate["description_text"] = description[:MAX_DESCRIPTION_CHARS]
    if (
        (candidate.get("company_id") or candidate.get("company"))
        and candidate.get("title")
        and meaningful_description(candidate.get("description_text", ""))
    ):
        candidate["warnings"] = "\n".join(
            line
            for line in (candidate.get("warnings", "") or "").splitlines()
            if line != LINKEDIN_DETAILS_WARNING
        )
    return candidate


def processing_status(candidate):
    has_identity = bool(
        (candidate.get("company_id") or candidate.get("company"))
        and candidate.get("title")
    )
    has_posting_details = meaningful_description(candidate.get("description_text", ""))
    has_lane_evidence = bool(
        candidate.get("location")
        or storage.clean(candidate.get("work_mode", "")).lower() == "remote"
    )
    if has_identity and has_posting_details and has_lane_evidence:
        if candidate.get("source_platform") == "linkedin" and LINKEDIN_DETAILS_WARNING in candidate.get("warnings", ""):
            return "partial"
        return "ready"
    if has_identity:
        return "partial"
    return "needs-details"


def score_candidate(candidate, checked_at):
    description = candidate.get("description_text", "")
    normalized = companies.normalized_candidate(
        {
            "title": candidate.get("title", ""),
            "url": candidate.get("canonical_url") or candidate.get("url", ""),
            "location": candidate.get("location", ""),
            "work_mode": candidate.get("work_mode", ""),
            "description": description,
        },
        candidate.get("source_platform", "") or "discovery",
    )
    candidate["description_excerpt"] = description[:1000]
    candidate.update(companies.score_candidate_fit(normalized, settings.fit_context(), checked_at))
    candidate["processing_status"] = processing_status(candidate)
    if candidate["processing_status"] != "ready":
        candidate["fit_summary"] = "Fit pending automatic detail enrichment."
    return candidate


def candidate_identity_keys(candidate):
    keys = set()
    for field in ["url", "canonical_url"]:
        value = storage.clean(candidate.get(field, ""))
        if value:
            keys.update(companies.posting_identity_keys(value))
            for requisition in re.findall(r"(?i)(?:req(?:uisition)?[-_ ]*)?((?:r|jr)\d{5,})", value):
                keys.add(f"requisition:{requisition.lower()}")
    return keys


def normalized_candidate_company(candidate):
    company_id = storage.clean(candidate.get("company_id", "")).upper()
    if company_id:
        return company_id
    return re.sub(r"[^a-z0-9]+", "", storage.clean(candidate.get("company", "")).lower())


def normalized_candidate_title(candidate):
    title = storage.clean(candidate.get("title", "")).lower()
    title = re.sub(r"\s*(?:\.{3}|…)\s*$", "", title)
    title = re.sub(r"\s*-\s*(?:logo\s*-\s*)?myworkdayjobs\.com\s*$", "", title)
    return re.sub(r"[^a-z0-9]+", " ", title).strip()


def candidates_semantically_match(left, right):
    left_company = normalized_candidate_company(left)
    right_company = normalized_candidate_company(right)
    if not left_company or left_company != right_company:
        return False
    left_title = normalized_candidate_title(left)
    right_title = normalized_candidate_title(right)
    if not left_title or not right_title:
        return False
    if left_title == right_title:
        return True
    shorter, longer = sorted([left_title, right_title], key=len)
    if len(shorter) >= 32 and longer.startswith(shorter):
        return True
    return SequenceMatcher(None, left_title, right_title).ratio() >= 0.9


def matching_candidate(rows, candidate):
    candidate_keys = candidate_identity_keys(candidate)
    for row in rows:
        if candidate_keys & candidate_identity_keys(row):
            return row
        if candidates_semantically_match(row, candidate):
            return row
    return None


def merge_candidate(existing, incoming):
    source_urls = []
    for value in [*candidate_source_urls(existing), *candidate_source_urls(incoming)]:
        if value not in source_urls:
            source_urls.append(value)
    replace_partial_linkedin_details = (
        incoming.get("source_platform") == "linkedin"
        and existing.get("processing_status") != "ready"
    )
    for field in [
        "url",
        "title",
        "canonical_url",
        "location",
        "work_mode",
        "company_id",
        "source_platform",
        "description_text",
        "description_excerpt",
        "fit_score",
        "fit_summary",
        "fit_checked_at",
        "processing_status",
        "ingested_application_id",
        "notes",
    ]:
        richer_description = (
            field in {"description_text", "description_excerpt"}
            and meaningful_description(incoming.get("description_text", ""))
            and not meaningful_description(existing.get("description_text", ""))
        )
        richer_title = (
            field == "title"
            and len(storage.clean(incoming.get("title", ""))) > len(storage.clean(existing.get("title", ""))) + 4
        )
        richer_location = field in {"location", "work_mode"} and incoming.get(field) and not existing.get(field)
        replaceable = (
            not existing.get(field)
            or field in {"fit_score", "fit_summary", "fit_checked_at", "processing_status"}
            or richer_description
            or richer_title
            or richer_location
            or replace_partial_linkedin_details and field in {"url", "title", "location", "work_mode"}
        )
        if incoming.get(field) and replaceable:
            existing[field] = incoming[field]
    warning_lines = [
        line
        for line in [*(existing.get("warnings", "") or "").splitlines(), *(incoming.get("warnings", "") or "").splitlines()]
        if line
    ]
    existing["warnings"] = "\n".join(dict.fromkeys(warning_lines))
    existing["source_urls_json"] = json.dumps(source_urls, ensure_ascii=False)
    captured_dates = [
        value
        for value in [existing.get("captured_at", ""), incoming.get("captured_at", "")]
        if value
    ]
    if captured_dates:
        existing["captured_at"] = min(captured_dates)
    existing["last_seen_at"] = max(
        existing.get("last_seen_at", ""),
        incoming.get("last_seen_at", ""),
    )
    if incoming.get("freshness_checked_at", "") > existing.get("freshness_checked_at", ""):
        existing["freshness_checked_at"] = incoming.get("freshness_checked_at", "")
        existing["freshness_status"] = incoming.get("freshness_status", "")
    if existing.get("status") not in {"ingested", "duplicate", "ignored", "unavailable"}:
        existing["status"] = incoming.get("status") or "new"
    return existing


def capture_candidates(search_id, capture_text, details=None, fetcher=None):
    get_search(search_id)
    urls = parse_capture_urls(capture_text)
    if not urls:
        raise ValueError("Paste at least one http or https job link.")
    if details and len(urls) > 1 and any(str(value or "").strip() for value in details.values()):
        raise ValueError("Copied posting details can be applied only when capturing one link at a time.")

    rows = repository.read_discovery_candidates()
    timestamp = now_iso()
    captured = []
    for url in urls:
        candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        candidate.update(extracted_candidate(url, fetcher=fetcher))
        candidate.pop("_posting_evidence", None)
        candidate.update(
            {
                "id": next_id(rows, "DC"),
                "search_id": storage.clean(search_id).upper(),
                "captured_at": timestamp,
                "last_seen_at": timestamp,
                "status": "new",
            }
        )
        if len(urls) == 1:
            apply_manual_details(candidate, details or {})
        connect_candidate_company(candidate, seen_at=timestamp)
        score_candidate(candidate, timestamp)
        sync_candidate_source_urls(candidate)
        existing = matching_candidate(rows, candidate)
        if existing:
            merge_candidate(existing, candidate)
            captured.append(existing)
        else:
            rows.append(candidate)
            captured.append(candidate)
    rows = canonicalize_candidate_rows(rows)
    captured = [
        matching_candidate(rows, candidate) or candidate
        for candidate in captured
    ]
    repository.write_discovery_candidates(rows)
    stored_by_id = {
        row.get("id", ""): row
        for row in repository.read_discovery_candidates()
    }
    captured = [
        stored_by_id.get(candidate.get("id", ""), candidate)
        for candidate in captured
    ]
    return {"captured": captured, "count": len(captured)}


def update_candidate_details(candidate_id, updates):
    rows = repository.read_discovery_candidates()
    wanted = storage.clean(candidate_id).upper()
    row = next((item for item in rows if item.get("id", "").upper() == wanted), None)
    if row is None:
        raise ValueError(f"No Discovery candidate found with id {candidate_id}.")
    apply_manual_details(row, updates or {})
    connect_candidate_company(row, seen_at=now_iso())
    score_candidate(row, now_iso())
    repository.write_discovery_candidates(rows)
    return get_candidate(candidate_id)


def update_candidate_status(candidate_id, status, ignore_reason="", ignore_reason_detail=""):
    cleaned_status = storage.clean(status).lower()
    if cleaned_status not in schema.DISCOVERY_CANDIDATE_STATUSES:
        raise ValueError(f"Unsupported Discovery candidate status: {cleaned_status}")
    cleaned_reason = storage.clean(ignore_reason).lower()
    if cleaned_reason and cleaned_reason not in IGNORE_REASONS:
        raise ValueError(f"Unsupported Discovery ignore reason: {cleaned_reason}")
    rows = repository.read_discovery_candidates()
    wanted = storage.clean(candidate_id).upper()
    row = next((item for item in rows if item.get("id", "").upper() == wanted), None)
    if row is None:
        raise ValueError(f"No Discovery candidate found with id {candidate_id}.")
    row["status"] = cleaned_status
    if cleaned_status in {"new", "ignored", "unavailable"}:
        row["ingested_application_id"] = ""
    if cleaned_status == "ignored":
        row["ignore_reason"] = cleaned_reason
        row["ignore_reason_detail"] = storage.clean(ignore_reason_detail)
    else:
        row["ignore_reason"] = ""
        row["ignore_reason_detail"] = ""
    repository.write_discovery_candidates(rows)
    return get_candidate(candidate_id)


def mark_candidate_duplicate(candidate_id, application_id):
    rows = repository.read_discovery_candidates()
    wanted_candidate = storage.clean(candidate_id).upper()
    candidate = next(
        (item for item in rows if item.get("id", "").upper() == wanted_candidate),
        None,
    )
    if candidate is None:
        raise ValueError(f"No Discovery candidate found with id {candidate_id}.")

    wanted_application = storage.clean(application_id).upper()
    posting = next(
        (
            item
            for item in repository.read_applications()
            if item.get("id", "").upper() == wanted_application
        ),
        None,
    )
    if posting is None:
        raise ValueError(f"No application found with id {application_id}.")

    candidate["status"] = "duplicate"
    candidate["ingested_application_id"] = posting.get("id", "")
    repository.write_discovery_candidates(rows)
    return {"candidate": get_candidate(candidate_id), "posting": posting}


def matching_company(company_name):
    return companies.matching_company_record(company_name)


def matching_application(candidate):
    wanted_keys = candidate_identity_keys(candidate)
    return next(
        (
            application
            for application in repository.read_applications()
            if wanted_keys & companies.posting_identity_keys(application.get("source_url", ""))
        ),
        None,
    )


def ingest_candidate(candidate_id):
    candidate = get_candidate(candidate_id)
    if not candidate.get("company_id") or not candidate.get("title"):
        raise ValueError("Link a company and add the role title before ingesting this Discovery result.")
    existing = matching_application(candidate)
    if existing:
        updated = update_candidate_status(candidate_id, "ingested")
        rows = repository.read_discovery_candidates()
        for row in rows:
            if row.get("id", "").upper() == updated.get("id", "").upper():
                row["ingested_application_id"] = existing.get("id", "")
        repository.write_discovery_candidates(rows)
        return {"candidate": get_candidate(candidate_id), "posting": existing, "created": False}

    company = companies.get_company(candidate.get("company_id", ""))
    source_url = candidate.get("canonical_url") or candidate.get("url", "")
    search = get_search(candidate.get("search_id", ""))
    posting = applications.create_application(
        {
            "company_id": company.get("id", ""),
            "company": company.get("name", ""),
            "role": candidate.get("title", ""),
            "location": candidate.get("location", ""),
            "work_mode": candidate.get("work_mode", ""),
            "source": f"Discovery · {candidate.get('source_platform', '') or 'manual'}",
            "source_url": source_url,
            "stage": schema.DEFAULT_STAGE,
            "priority": schema.DEFAULT_PRIORITY,
            "date_found": "today",
            "notes": f"Discovered through {search.get('name', 'Discovery')}.",
        }
    )
    description = candidate.get("description_text", "")
    if description:
        repository.write_posting_snapshot(
            posting.get("id", ""),
            {
                "source_url": candidate.get("url", ""),
                "final_url": source_url,
                "capture_method": "manual",
                "content_text": posting_snapshots.readable_content(source_url, description, description),
                "source_html": description,
                "warnings": candidate.get("warnings", ""),
            },
        )
    rows = repository.read_discovery_candidates()
    for row in rows:
        if row.get("id", "").upper() == storage.clean(candidate_id).upper():
            row["status"] = "ingested"
            row["ingested_application_id"] = posting.get("id", "")
    repository.write_discovery_candidates(rows)
    return {"candidate": get_candidate(candidate_id), "posting": posting, "created": True}
