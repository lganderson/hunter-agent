"""Shared rules for deciding whether a posting source capture is usable."""

import json

from urllib.parse import urlparse

from . import storage


def is_usable(snapshot):
    has_content = (
        bool((snapshot.get("content_text") or "").strip())
        and bool((snapshot.get("source_html") or "").strip())
    )
    capture_method = storage.clean(snapshot.get("capture_method", ""))
    if capture_method == "manual":
        return has_content
    if capture_method == "ai-web":
        try:
            sources = json.loads(snapshot.get("sources_json", "[]") or "[]")
        except (TypeError, json.JSONDecodeError):
            return False
        return (
            has_content
            and bool(storage.clean(snapshot.get("capture_model", "")))
            and isinstance(sources, list)
            and any(isinstance(source, dict) and storage.clean(source.get("url", "")) for source in sources)
        )
    try:
        status = int(storage.clean(snapshot.get("http_status", "")))
    except (TypeError, ValueError):
        return False
    return (
        200 <= status < 400
        and has_content
    )


def failure_message(snapshot):
    source_url = storage.clean(snapshot.get("source_url", ""))
    if urlparse(source_url).netloc.lower().endswith(".invalid"):
        return "This demo placeholder URL cannot be archived. Use a real employer posting URL."
    status = storage.clean(snapshot.get("http_status", ""))
    if status in {"401", "403"}:
        return f"The employer site blocked the archive request (HTTP {status}). Open the source in a browser or use a supported public job feed."
    if not status:
        return "Hunter could not reach the source URL. Check that the posting URL is valid and still available."
    return f"Hunter received HTTP {status} but could not capture readable posting content."
