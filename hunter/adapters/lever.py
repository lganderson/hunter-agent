"""Parse Lever payloads into candidate evidence; do not score or save it."""

import json
from .. import storage
from .text import normalize_url


def extract_lever_candidates(payload):
    try:
        postings = json.loads(payload or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(postings, list):
        return []
    candidates = []
    for posting in postings:
        if not isinstance(posting, dict):
            continue
        title = storage.clean(str(posting.get("text", "") or ""))
        url = normalize_url(posting.get("hostedUrl", ""))
        categories = posting.get("categories") or {}
        if not isinstance(categories, dict):
            categories = {}
        location = storage.clean(str(categories.get("location", "") or ""))
        workplace_type = storage.clean(str(posting.get("workplaceType", "") or ""))
        category = ", ".join(
            dict.fromkeys(
                storage.clean(str(categories.get(field, "") or ""))
                for field in ["team", "department", "commitment"]
                if storage.clean(str(categories.get(field, "") or ""))
            )
        )
        description = " ".join(
            storage.clean(str(posting.get(field, "") or ""))
            for field in ["descriptionPlain", "additionalPlain"]
            if storage.clean(str(posting.get(field, "") or ""))
        )
        candidate = {
            "title": title,
            "url": url,
            "location": location,
            "work_mode": workplace_type,
            "category": category,
            "description": description,
            "source_job_id": storage.clean(str(posting.get("id", "") or "")),
        }
        if not title or not url:
            continue
        candidates.append(candidate)
    return candidates
