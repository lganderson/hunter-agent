"""Pure saved-search query planning, separate from acquisition and persistence."""

import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from . import storage


WORK_MODE_CODES = {"on-site": "1", "remote": "2", "hybrid": "3"}


ALL_WORK_MODES = ["on-site", "hybrid", "remote"]


ROLE_QUERY_FAMILIES = [
    {
        "id": "technical-program",
        "label": "Technical program leadership",
        "terms": [
            "technical program manager",
            "senior technical program manager",
            "staff technical program manager",
            "principal technical program manager",
            "lead technical program manager",
            "ai program manager",
        ],
        "strong_terms": ["technical program manager"],
    },
    {
        "id": "engineering-delivery",
        "label": "Engineering delivery",
        "terms": [
            "engineering program manager",
            "software program manager",
            "technical project manager",
            "technical delivery manager",
            "engineering delivery lead",
            "technical program lead",
        ],
        "strong_terms": [
            "engineering program manager",
            "software program manager",
            "technical project manager",
            "technical delivery manager",
        ],
    },
    {
        "id": "product-platform",
        "label": "Product and platform strategy",
        "terms": [
            "senior product manager",
            "principal product manager",
            "staff product manager",
            "technical product manager",
            "platform product manager",
            "product program manager",
            "product strategy lead",
            "technical product lead",
            "product platform lead",
            "technology product management",
            "product lead",
        ],
        "strong_terms": [
            "senior product manager",
            "principal product manager",
            "staff product manager",
            "technical product manager",
            "platform product manager",
            "product program manager",
        ],
    },
    {
        "id": "product-operations",
        "label": "Product systems and operations",
        "terms": [
            "product operations manager",
            "product ops manager",
            "product systems manager",
            "product systems lead",
            "product development operations",
            "product enablement manager",
            "ai performance operations",
        ],
        "strong_terms": [
            "product operations manager",
            "product ops manager",
            "product systems manager",
            "product systems lead",
            "ai performance operations",
        ],
    },
    {
        "id": "technologist-prototyping",
        "label": "Technologist and prototyping",
        "terms": [
            "product technologist",
            "creative technologist",
            "design technologist",
            "innovation lead",
            "prototyping lead",
        ],
        "strong_terms": [
            "product technologist",
            "creative technologist",
            "design technologist",
        ],
    },
    {
        "id": "customer-implementation",
        "label": "Customer implementation",
        "terms": [
            "solutions program manager",
            "implementation program manager",
            "customer engineering program manager",
            "technical implementation manager",
            "technical engagement manager",
        ],
        "strong_terms": [
            "solutions program manager",
            "customer engineering program manager",
            "technical implementation manager",
            "technical engagement manager",
        ],
    },
    {
        "id": "games-interactive",
        "label": "Games and interactive delivery",
        "terms": [
            "technical producer",
            "game producer",
            "development director",
            "release manager",
        ],
        "strong_terms": ["technical producer", "game producer"],
        "context_query": "(game OR gaming OR interactive)",
    },
    {
        "id": "systems-hardware",
        "label": "Systems and product development",
        "terms": [
            "systems program manager",
            "product development program manager",
            "new product introduction program manager",
            "NPI program manager",
        ],
        "strong_terms": [
            "systems program manager",
            "product development program manager",
            "new product introduction program manager",
            "NPI program manager",
        ],
    },
]


ROLE_QUERY_FAMILIES_BY_ID = {family["id"]: family for family in ROLE_QUERY_FAMILIES}


def search_keyword_families(search):
    keywords = storage.clean(search.get("keywords", ""))
    selected_ids = search.get("role_family_ids", [])
    selected_families = [
        ROLE_QUERY_FAMILIES_BY_ID[family_id]
        for family_id in selected_ids
        if family_id in ROLE_QUERY_FAMILIES_BY_ID
    ]
    if not selected_families:
        return [{"id": "saved", "label": "Saved keywords", "query": keywords}]
    qualifier_match = re.match(r"^(?:technical program manager|tpm)\b(.*)$", keywords, re.I)
    qualifiers = storage.clean(qualifier_match.group(1) if qualifier_match else keywords)
    families = []
    for family in selected_families:
        terms = family["terms"]
        query = "(" + " OR ".join(f'"{term}"' for term in terms) + ")"
        if family.get("context_query"):
            query = f"{query} {family['context_query']}"
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
    parts.extend(["f_TPR=r2592000", "sortBy=DD"])
    return "https://www.linkedin.com/jobs/search/?" + "&".join(parts)


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


def location_query(lane):
    location = storage.clean(lane.get("location", ""))
    normalized = normalized_location_text(location)
    aliases = {
        "minnesota": [
            "Minnesota",
            "Minneapolis",
            "Saint Paul",
            "St. Paul",
            "Twin Cities",
        ],
        "united states": [
            "United States",
            "US Remote",
            "Remote USA",
        ],
    }
    terms = aliases.get(normalized, [location] if location else [])
    if not terms:
        return ""
    if len(terms) == 1:
        return f'"{terms[0]}"'
    return "(" + " OR ".join(f'"{term}"' for term in terms) + ")"


def recent_query(days):
    if not storage.clean(days):
        return ""
    try:
        lookback_days = max(1, int(days))
    except (TypeError, ValueError):
        return ""
    return f"after:{(datetime.now() - timedelta(days=lookback_days)).date().isoformat()}"


def discovery_query(search, lane, strategy, keywords=""):
    return " ".join(
        part
        for part in [
            storage.clean(keywords) or expanded_search_keywords(search),
            location_query(lane),
            work_mode_query(lane),
            strategy.get("query", ""),
            recent_query(strategy.get("recent_days", "")),
        ]
        if part
    )


def normalized_location_text(value):
    return re.sub(r"[^a-z0-9]+", " ", storage.clean(value).lower()).strip()
