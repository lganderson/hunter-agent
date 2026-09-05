"""Parse Greenhouse jobs into evidence without workflow or storage dependencies."""

from urllib.parse import urljoin
from .. import storage
from .text import normalize_url, clean_html_text


def greenhouse_text_value(value):
    if isinstance(value, list):
        return " ".join(greenhouse_text_value(item) for item in value)
    if isinstance(value, dict):
        if "value" in value:
            return greenhouse_text_value(value.get("value"))
        values = []
        for key in ["name", "title", "label", "amount", "unit", "min_value", "max_value"]:
            if value.get(key) is not None:
                values.append(storage.clean(str(value.get(key))))
        if values:
            return " ".join(values)
        return " ".join(greenhouse_text_value(item) for item in value.values())
    return storage.clean(str(value or ""))


def greenhouse_metadata_value(job, name):
    metadata = job.get("metadata")
    if isinstance(metadata, list):
        for item in metadata:
            if isinstance(item, dict) and storage.clean(item.get("name", "")).lower() == name.lower():
                return greenhouse_text_value(item.get("value"))
    if isinstance(metadata, dict):
        return greenhouse_text_value(metadata.get(name))
    return ""


def greenhouse_location(job):
    values = []
    location = job.get("location")
    if isinstance(location, dict):
        values.append(storage.clean(str(location.get("name", "") or "")))
    elif location:
        values.append(storage.clean(str(location)))
    values.append(greenhouse_metadata_value(job, "Career Page - Office Location"))
    values.append(greenhouse_metadata_value(job, "Worksite Classification"))
    return ", ".join(dict.fromkeys(value for value in values if value))


def greenhouse_category(job):
    values = []
    department = job.get("department")
    if isinstance(department, dict):
        values.extend(storage.clean(str(part)) for part in department.get("path") or [] if storage.clean(str(part)))
        values.append(storage.clean(str(department.get("name", "") or "")))
    values.append(greenhouse_metadata_value(job, "Career Page - Department"))
    values.append(greenhouse_metadata_value(job, "Career Page - Sub Department"))
    values.append(greenhouse_metadata_value(job, "Career Page - Studio Project"))
    values.append(storage.clean(str(job.get("company_name", "") or "")))
    return ", ".join(dict.fromkeys(value for value in values if value))


def greenhouse_candidate_url(job):
    url = storage.clean(str(job.get("absolute_url", "") or ""))
    if url:
        return normalize_url(url)
    job_id = storage.clean(str(job.get("id", "") or ""))
    board_url = storage.clean(str(job.get("board_url", "") or ""))
    if job_id and board_url:
        return normalize_url(urljoin(board_url.rstrip("/") + "/", f"jobs/{job_id}"))
    return ""


def greenhouse_candidates_from_jobs(jobs, config=None):
    config = config or {}
    candidates = []
    seen = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if config.get("board_url"):
            job = {**job, "board_url": config.get("board_url")}
        title = storage.clean(str(job.get("title", "") or ""))
        url = greenhouse_candidate_url(job)
        if not title or not url or url in seen:
            continue
        description = clean_html_text(
            " ".join(
                greenhouse_text_value(job.get(field))
                for field in ["content", "description"]
                if job.get(field)
            )
        )
        candidate = {
            "title": title,
            "url": url,
            "description": description,
            "location": greenhouse_location(job),
            "category": greenhouse_category(job),
            "search_text": " ".join(
                greenhouse_text_value(job.get(field))
                for field in ["requisition_id", "internal_job_id", "updated_at", "first_published", "metadata"]
                if job.get(field)
            ),
        }
        seen.add(url)
        candidates.append(candidate)
    return candidates
