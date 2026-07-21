"""Shared rules for deciding whether a posting source capture is usable."""

import html
import json
import re

from urllib.parse import urlparse

from . import storage


GOOGLE_CAREERS_HOSTS = {"google.com", "www.google.com"}
APPLE_JOBS_HOSTS = {"jobs.apple.com"}
AMAZON_JOBS_HOSTS = {"amazon.jobs", "www.amazon.jobs"}
GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
LINKEDIN_HOSTS = {"linkedin.com", "www.linkedin.com"}
RIOT_HOSTS = {"riotgames.com", "www.riotgames.com"}
EA_JOBS_HOSTS = {"jobs.ea.com"}


def readable_content(source_url, content_text, source_html=""):
    """Return the useful posting body while preserving the raw capture separately."""
    text = content_text or ""
    parsed = urlparse(storage.clean(source_url))
    host = parsed.netloc.lower()
    if host in APPLE_JOBS_HOSTS:
        apple_content = _apple_jobs_content(source_html)
        return apple_content or text
    if host in AMAZON_JOBS_HOSTS:
        return _focused_amazon_jobs_content(text)
    if host in GREENHOUSE_HOSTS:
        return _focused_greenhouse_content(text)
    if host in LINKEDIN_HOSTS:
        return _focused_linkedin_content(text)
    if host in RIOT_HOSTS:
        return _focused_riot_content(text)
    if host in EA_JOBS_HOSTS:
        return _focused_ea_content(text)
    if host in GOOGLE_CAREERS_HOSTS and "/about/careers/" in parsed.path.lower():
        return _focused_google_careers_content(text)
    return text


def _apple_jobs_content(source_html):
    match = re.search(
        r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\((\".*?\")\);",
        source_html or "",
        re.S,
    )
    if not match:
        return ""
    try:
        hydration = json.loads(json.loads(match.group(1)))
    except (TypeError, json.JSONDecodeError):
        return ""
    job = (
        hydration.get("loaderData", {})
        .get("jobDetails", {})
        .get("jobsData", {})
    )
    if not isinstance(job, dict) or not storage.clean(job.get("postingTitle", "")):
        return ""

    title = storage.clean(job.get("postingTitle", ""))
    parts = [f"# {title}"]
    metadata = ["Apple"]
    location = job.get("selectedLocation", {})
    if isinstance(location, dict):
        place = ", ".join(
            value for value in [
                storage.clean(location.get("city", "")),
                storage.clean(location.get("stateProvince", "")),
                storage.clean(location.get("countryName", "")),
            ]
            if value
        )
        if place:
            metadata.append(place)
    teams = job.get("teamNames", [])
    if isinstance(teams, list):
        team_label = ", ".join(storage.clean(team) for team in teams if storage.clean(team))
        if team_label:
            metadata.append(team_label)
    if storage.clean(job.get("postingDate", "")):
        metadata.append(f"Posted {storage.clean(job.get('postingDate', ''))}")
    parts.append(" · ".join(metadata))
    if storage.clean(job.get("jobNumber", "")):
        parts.append(f"**Role number:** {storage.clean(job.get('jobNumber', ''))}")

    for heading, field, list_style in [
        ("Summary", "jobSummary", False),
        ("Description", "description", False),
        ("Responsibilities", "responsibilities", True),
        ("Minimum Qualifications", "minimumQualifications", True),
        ("Preferred Qualifications", "preferredQualifications", True),
    ]:
        value = job.get(field, "")
        formatted = _apple_text_block(value, list_style=list_style)
        if formatted:
            parts.extend([f"## {heading}", formatted])
    return "\n\n".join(parts).strip()


def _apple_text_block(value, list_style=False):
    text = html.unescape(value) if isinstance(value, str) else ""
    if not text.strip():
        return ""
    if list_style:
        items = [re.sub(r"\s+", " ", line).strip(" -") for line in text.splitlines()]
        return "\n".join(f"- {item}" for item in items if item)
    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n\s*\n+", text)]
    return "\n\n".join(item for item in paragraphs if item)


def _focused_amazon_jobs_content(content_text):
    lines = [line.strip() for line in (content_text or "").splitlines() if line.strip()]
    description_index = next(
        (index for index, line in enumerate(lines) if line.lstrip("# ").lower() == "description"),
        -1,
    )
    if description_index < 0:
        return content_text or ""
    start_index = next(
        (index for index in range(description_index - 1, -1, -1) if lines[index].startswith("# ")),
        -1,
    )
    if start_index < 0:
        return content_text or ""
    end_index = next(
        (
            index for index in range(description_index + 1, len(lines))
            if lines[index].lstrip("# ").lower() in {"share this job", "join us on", "find careers"}
        ),
        len(lines),
    )
    focused = []
    for line in lines[start_index:end_index]:
        normalized = line.lower().lstrip("- ").strip()
        if not normalized or normalized == "apply now":
            continue
        if normalized == "key job responsibilities":
            line = "## Key job responsibilities"
        if focused and focused[-1] == line:
            continue
        focused.append(line)
    return "\n".join(focused).strip() or (content_text or "")


def _focused_greenhouse_content(content_text):
    lines = [line.strip() for line in (content_text or "").splitlines() if line.strip()]
    start_index = next((index for index, line in enumerate(lines) if line.startswith("# ")), -1)
    if start_index < 0:
        return content_text or ""
    end_index = next(
        (
            index for index in range(start_index + 1, len(lines))
            if lines[index].lstrip("# ").strip().lower() == "apply for this job"
        ),
        len(lines),
    )
    return "\n".join(lines[start_index:end_index]).strip() or (content_text or "")


def _focused_linkedin_content(content_text):
    lines = [line.strip() for line in (content_text or "").splitlines() if line.strip()]
    start_index = next((index for index, line in enumerate(lines) if line.startswith("# ")), -1)
    if start_index < 0:
        return content_text or ""
    description_index = next(
        (index + 1 for index in range(start_index + 1, len(lines)) if lines[index].lower() == "report this job"),
        -1,
    )
    if description_index < 0:
        return content_text or ""
    end_index = next(
        (index for index in range(description_index, len(lines)) if lines[index].lower() == "show more"),
        len(lines),
    )
    header_end = next(
        (
            index for index in range(start_index + 1, description_index)
            if lines[index].lower().startswith("sign in")
        ),
        description_index - 1,
    )
    header = [line for line in lines[start_index:header_end] if line and line not in {"###", "##"}]
    header = header[:4]
    body = []
    for index, line in enumerate(lines[description_index:end_index]):
        next_line = lines[description_index + index + 1] if description_index + index + 1 < end_index else ""
        if (
            not line.startswith("#")
            and len(line) < 72
            and next_line.startswith("-")
            and line.lower() not in {"learn more"}
        ):
            line = f"## {line}"
        body.append(line)
    return "\n".join(header + body).strip() or (content_text or "")


def _focused_riot_content(content_text):
    lines = [line.strip() for line in (content_text or "").splitlines() if line.strip()]
    start_index = next((index for index, line in enumerate(lines) if line.startswith("# ")), -1)
    if start_index < 0:
        return content_text or ""
    end_index = next(
        (
            index for index in range(start_index + 1, len(lines))
            if lines[index].lower() == "apply" and index - start_index > 4
        ),
        len(lines),
    )
    return "\n".join(lines[start_index:end_index]).strip() or (content_text or "")


def _focused_ea_content(content_text):
    lines = [line.strip() for line in (content_text or "").splitlines() if line.strip()]
    start_index = next(
        (
            index for index, line in enumerate(lines)
            if (
                line.startswith("## ") and line[3:].strip()
            ) or (
                line == "##" and index + 1 < len(lines) and lines[index + 1].strip()
            )
        ),
        -1,
    )
    if start_index < 0:
        return content_text or ""
    end_index = next(
        (
            index for index in range(start_index + 1, len(lines))
            if lines[index].lower() in {"post to", "back to role list"}
        ),
        len(lines),
    )
    title = lines[start_index][3:].strip() if lines[start_index].startswith("## ") else lines[start_index + 1]
    body_start = start_index + 1 if lines[start_index].startswith("## ") else start_index + 2
    focused = [f"# {title}"]
    heading_level = ""
    for line in lines[body_start:end_index]:
        if line in {"-", "..."}:
            continue
        if line in {"##", "###"}:
            heading_level = line
            continue
        if heading_level:
            line = f"## {line}"
            heading_level = ""
        focused.append(line)
    return "\n".join(focused).strip() or (content_text or "")


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
    if minimum_index < 0:
        return content_text or ""

    start_index = -1
    for index in range(minimum_index - 1, results_index, -1):
        line = lines[index]
        if line.startswith("# "):
            start_index = index
            break
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
        elif normalized.startswith("corporate_fare ") and " place " in normalized:
            identity, location = re.split(r"\s+place\s+", line[len("corporate_fare "):], maxsplit=1)
            line = f"{identity.strip()} · {location.strip()}"
        elif normalized.startswith("info_outline x "):
            line = line[len("info_outline X "):]
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


def is_relevant(snapshot, role=""):
    if not is_usable(snapshot):
        return False
    if storage.clean(snapshot.get("capture_method", "")) == "manual":
        return True
    content = readable_content(
        snapshot.get("final_url") or snapshot.get("source_url"),
        snapshot.get("content_text", ""),
        snapshot.get("source_html", ""),
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", content.lower()).strip()
    if any(
        marker in normalized
        for marker in [
            "job you re looking for isn t available",
            "job is no longer available",
            "position is no longer available",
            "enable javascript and cookies to continue",
        ]
    ):
        return False
    wanted = re.sub(r"[^a-z0-9]+", " ", storage.clean(role).lower()).strip()
    if not wanted:
        return True
    tokens = {token for token in wanted.split() if len(token) >= 3}
    token_matches = sum(1 for token in tokens if re.search(rf"\b{re.escape(token)}\b", normalized))
    role_matches = wanted in normalized or (
        bool(tokens)
        and token_matches >= max(2, (len(tokens) * 2 + 2) // 3)
    )
    if not role_matches:
        return False
    section_markers = [
        "about the job",
        "the job",
        "about the role",
        "description",
        "job summary",
        "key responsibilities",
        "minimum qualifications",
        "preferred qualifications",
        "requirements",
        "responsibilities",
        "role summary",
        "what you will do",
        "what you ll do",
        "who you are",
    ]
    return len(content) < 500 or any(marker in normalized for marker in section_markers)


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
