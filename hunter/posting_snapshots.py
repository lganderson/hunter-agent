"""Shared rules for deciding whether a posting source capture is usable."""

import json

from urllib.parse import urlparse

from . import storage


GOOGLE_CAREERS_HOSTS = {"google.com", "www.google.com"}


def readable_content(source_url, content_text):
    """Return the useful posting body while preserving the raw capture separately."""
    text = content_text or ""
    parsed = urlparse(storage.clean(source_url))
    if parsed.netloc.lower() not in GOOGLE_CAREERS_HOSTS or "/about/careers/" not in parsed.path.lower():
        return text
    return _focused_google_careers_content(text)


def _focused_google_careers_content(content_text):
    lines = [line.strip() for line in (content_text or "").splitlines() if line.strip()]
    if not lines:
        return ""

    minimum_index = next(
        (index for index, line in enumerate(lines) if line.lstrip("# ").lower() == "minimum qualifications:"),
        -1,
    )
    results_index = next(
        (index for index, line in enumerate(lines[:minimum_index]) if line.lstrip("# ").lower() == "jobs search results"),
        -1,
    )
    if minimum_index < 0 or results_index < 0:
        return content_text or ""

    start_index = -1
    for index in range(minimum_index - 1, results_index, -1):
        line = lines[index]
        if line.startswith("## ") and line[3:].strip().lower() not in {"advanced", "jobs search results"}:
            start_index = index
            break
    if start_index < 0:
        return content_text or ""

    end_index = len(lines)
    for index in range(minimum_index + 1, len(lines)):
        normalized = lines[index].lstrip("# ").lower()
        if normalized.startswith("information collected and processed as part of your google careers profile"):
            end_index = index
            break
        if normalized in {"follow life at google on", "more about us"}:
            end_index = index
            break

    focused = []
    ignored = {
        "-",
        "apply",
        "share",
        "link copy link",
        "email email a friend",
        "copy link",
        "email a friend",
    }
    for line in lines[start_index:end_index]:
        normalized = line.lower()
        plain_normalized = normalized.lstrip("- ").strip()
        if not plain_normalized or normalized in ignored or plain_normalized in ignored or normalized == "bar_chart advanced":
            continue
        if normalized.startswith("corporate_fare google place "):
            line = f"Google · {line[len('corporate_fare Google place '):]}"
        elif line == "## Advanced":
            line = "**Experience level:** Advanced"
        elif not focused and line.startswith("## "):
            line = f"# {line[3:]}"
        elif line.startswith("### "):
            line = f"## {line[4:]}"
        if focused and focused[-1] == line:
            continue
        focused.append(line)

    return "\n".join(focused).strip() or (content_text or "")


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
