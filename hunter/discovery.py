"""Saved Discovery searches and review-first posting capture."""

import html
import json
import re
from difflib import SequenceMatcher
from datetime import datetime
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
MIN_READY_DESCRIPTION_CHARS = 500
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
            "-simplyhired.com -jooble.org"
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
    payload.pop("lanes_json", None)
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
    if not row.get("name"):
        raise ValueError("Discovery search name is required.")
    if not row.get("keywords"):
        raise ValueError("Discovery search keywords are required.")
    if not search_lanes(row):
        raise ValueError("Add at least one Discovery search lane.")
    row["updated_at"] = timestamp
    repository.write_discovery_searches(rows)
    return get_search(row["id"])


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
    return " ".join(
        part
        for part in [
            storage.clean(keywords) or expanded_search_keywords(search),
            f'"{storage.clean(lane.get("location", ""))}"' if storage.clean(lane.get("location", "")) else "",
            work_mode_query(lane),
            strategy.get("query", ""),
        ]
        if part
    )


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
        if not url or url in seen or not likely_individual_posting(url, title):
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
    result_title, result_company = search_title_details(result.get("title", ""), platform)
    result_company = storage.clean(result.get("company", "")) or result_company
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
        elif not candidate.get(field) or field in {"company", "title", "location"}:
            candidate[field] = value
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
    return candidate


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
    try:
        fit_score = int(candidate.get("fit_score", "") or 0)
    except (TypeError, ValueError):
        fit_score = 0
    return (
        processing_rank.get(candidate.get("processing_status", ""), 0),
        fit_score,
        len(candidate.get("description_text", "") or ""),
        candidate.get("title", ""),
    )


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
    return company


def sync_discovered_companies():
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
        if posting_evidence != "individual" and candidate.get("source_platform") in {
            "ashby",
            "greenhouse",
            "lever",
            "smartrecruiters",
            "workday",
        }:
            posting_evidence = "individual"
        if posting_evidence != "individual":
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
        candidate["_match_context"] = matched_context
        candidate["_search_result"] = result
        duplicate = matching_candidate(prepared, candidate)
        if duplicate:
            duplicate_count += 1
            if candidate_rank_key(candidate) > candidate_rank_key(duplicate):
                prepared[prepared.index(duplicate)] = candidate
            continue
        prepared.append(candidate)

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

    qualified_count = len(prepared)
    selected = sorted(prepared, key=candidate_rank_key, reverse=True)[:DISCOVERY_RESULT_LIMIT]
    limited_count = max(0, qualified_count - len(selected))
    company_by_id = {}
    for candidate in selected:
        company = connect_candidate_company(candidate, seen_at=timestamp)
        if company:
            company_by_id[company.get("id", "")] = company

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
            new_count += 1

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
        )
        repository.write_discovery_searches(stored_rows)
    result["search"] = get_search(search["id"])
    return result


def list_candidates():
    collapsed = []
    status_rank = {"ingested": 3, "ignored": 2, "new": 1}
    for candidate in repository.read_discovery_candidates():
        duplicate = matching_candidate(collapsed, candidate)
        if duplicate is None:
            collapsed.append(candidate)
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
            collapsed[collapsed.index(duplicate)] = candidate
    return collapsed


def get_candidate(candidate_id):
    wanted = storage.clean(candidate_id).upper()
    row = next(
        (item for item in repository.read_discovery_candidates() if item.get("id", "").upper() == wanted),
        None,
    )
    if row is None:
        raise ValueError(f"No Discovery candidate found with id {candidate_id}.")
    return row


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
    text = f"{location} {description}".lower()
    if "hybrid" in text:
        return "Hybrid"
    if "remote" in text or "telecommute" in text:
        return "Remote"
    if "on-site" in text or "onsite" in text:
        return "On-site"
    return ""


def individual_posting_evidence(page_html, job, title, description, fetch_error=""):
    if fetch_error or not page_html:
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
    company = organization_name(job) or meta.get("og:site_name", "")
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
    )

    return {
        "company": company,
        "title": title,
        "url": companies.normalize_url(url),
        "canonical_url": canonical_url,
        "location": location,
        "work_mode": work_mode_from_text(location, description),
        **company_metadata,
        "company_metadata_source": final_url if any(company_metadata.values()) else "",
        "source_platform": source_platform(final_url or url),
        "description_text": description,
        "warnings": "\n".join(dict.fromkeys(warnings)),
        "_posting_evidence": "individual" if posting_evidence else "",
    }


def apply_manual_details(candidate, details):
    for field in [
        "company_id",
        "title",
        "canonical_url",
        "location",
        "work_mode",
        "notes",
    ]:
        value = storage.clean((details or {}).get(field, ""))
        if value:
            if field == "canonical_url":
                candidate[field] = companies.normalize_url(value)
            else:
                candidate[field] = value
    company_name = storage.clean(
        (details or {}).get("company_name", "")
        or (details or {}).get("company", "")
    )
    if company_name:
        candidate["company"] = company_name
    description = str((details or {}).get("description_text", "") or "").strip()
    if description:
        candidate["description_text"] = description[:MAX_DESCRIPTION_CHARS]
    if candidate.get("company") and candidate.get("title") and candidate.get("description_text"):
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
    existing["last_seen_at"] = incoming.get("last_seen_at", "")
    if existing.get("status") != "ingested":
        existing["status"] = "new"
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
        existing = matching_candidate(rows, candidate)
        if existing:
            merge_candidate(existing, candidate)
            captured.append(existing)
        else:
            rows.append(candidate)
            captured.append(candidate)
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


def update_candidate_status(candidate_id, status):
    cleaned_status = storage.clean(status).lower()
    if cleaned_status not in schema.DISCOVERY_CANDIDATE_STATUSES:
        raise ValueError(f"Unsupported Discovery candidate status: {cleaned_status}")
    rows = repository.read_discovery_candidates()
    wanted = storage.clean(candidate_id).upper()
    row = next((item for item in rows if item.get("id", "").upper() == wanted), None)
    if row is None:
        raise ValueError(f"No Discovery candidate found with id {candidate_id}.")
    row["status"] = cleaned_status
    repository.write_discovery_candidates(rows)
    return row


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
