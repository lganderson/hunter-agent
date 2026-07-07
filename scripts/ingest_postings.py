#!/usr/bin/env python3
"""Ingest or refresh job posting URLs into the local tracker."""

import argparse
import html
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import tracker
import action_engine
from hunter import applications as application_store
from hunter import companies as company_store
from hunter import repository

try:
    import requests
except ImportError:  # pragma: no cover - only used on minimal Python installs.
    requests = None


ROOT = Path(__file__).resolve().parents[1]

DEFAULT_NEXT_ACTION = "Review fit and tailor resume"
DEFAULT_STAGE = "posting-review"
DEFAULT_OUTCOME = ""
DEFAULT_TAGS = ""

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)


def normalize_url(value):
    parsed = urlparse((value or "").strip())
    scheme = "https" if parsed.scheme.lower() in {"http", "https", ""} else parsed.scheme.lower()
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    normalized = parsed._replace(
        scheme=scheme,
        netloc=parsed.netloc.lower(),
        path=parsed.path.rstrip("/"),
        query=urlencode(query, doseq=True),
        fragment="",
    )
    return urlunparse(normalized)


def fetch(url):
    if requests is not None:
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=20,
                allow_redirects=True,
            )
            return {
                "status": response.status_code,
                "final_url": response.url,
                "html": response.text,
                "error": "",
            }
        except requests.RequestException as exc:
            return {"status": 0, "final_url": url, "html": "", "error": str(exc)}

    request = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            return {"status": response.status, "final_url": response.geturl(), "html": body, "error": ""}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"status": exc.code, "final_url": exc.geturl(), "html": body, "error": str(exc)}
    except URLError as exc:
        return {"status": 0, "final_url": url, "html": "", "error": str(exc)}


def attr_map(tag):
    attrs = {}
    for key, quote, value in re.findall(r"([a-zA-Z_:.-]+)\s*=\s*(['\"])(.*?)\2", tag, re.S):
        attrs[key.lower()] = html.unescape(value)
    return attrs


def meta_values(page_html):
    values = {}
    for tag in re.findall(r"<meta\b[^>]*>", page_html, re.I | re.S):
        attrs = attr_map(tag)
        key = attrs.get("property") or attrs.get("name")
        content = attrs.get("content")
        if key and content:
            values[key.lower()] = content.strip()
    return values


def page_title(page_html):
    match = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.I | re.S)
    if not match:
        return ""
    return clean_text(match.group(1))


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def visible_text(page_html):
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", page_html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(text)


def json_ld_items(page_html):
    items = []
    for raw in re.findall(r"<script[^>]+type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>", page_html, re.I | re.S):
        try:
            data = json.loads(html.unescape(raw.strip()))
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            items.extend(data)
        else:
            items.append(data)
    return items


def first_job_posting_json(page_html):
    for item in json_ld_items(page_html):
        if isinstance(item, dict) and item.get("@type") == "JobPosting":
            return item
        if isinstance(item, dict) and "@graph" in item:
            for graph_item in item["@graph"]:
                if isinstance(graph_item, dict) and graph_item.get("@type") == "JobPosting":
                    return graph_item
    return {}


def title_case_slug(path_part):
    path_part = re.sub(r"\?.*$", "", path_part)
    path_part = re.sub(r"[-_]+", " ", path_part).strip()
    return re.sub(r"\b\w", lambda match: match.group(0).upper(), path_part)


def infer_company_role(url, title, meta, job_json):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path
    role = clean_text(job_json.get("title") if isinstance(job_json, dict) else "") or title
    company = ""

    hiring_org = job_json.get("hiringOrganization") if isinstance(job_json, dict) else None
    if isinstance(hiring_org, dict):
        company = clean_text(hiring_org.get("name", ""))

    if "greenhouse.io" in host:
        match = re.search(r"Job Application for (.*?) at (.*?)(?:$|\|)", title)
        if match:
            role, company = clean_text(match.group(1)), clean_text(match.group(2))
        elif "/anthropic/" in path:
            company = company or "Anthropic"
    elif "linkedin.com" in host:
        match = re.search(r"(.+?) hiring (.+?) in (.+?) \| LinkedIn", title)
        if match:
            company, role = clean_text(match.group(1)), clean_text(match.group(2))
    elif "bestbuy.com" in host:
        company = "Best Buy"
        role = re.sub(r"\s+-\s+Job Details.*$", "", title).strip()
    elif "delta.avature.net" in host:
        company = "Delta Air Lines"
        bits = [part for part in path.split("/") if part]
        role = title_case_slug(bits[-1]) if bits else role
    elif "playstation.com" in host:
        company = "Sony Interactive Entertainment / PlayStation"
        match = re.search(r"Careers at Sony Interactive Entertainment I (.+)$", title)
        role = clean_text(match.group(1)) if match else re.sub(r"\s*\|.*$", "", title).strip()
    elif "ubisoft.com" in host:
        company = "Ubisoft"
        role = re.sub(r"\s*\|\s*Ubisoft Careers.*$", "", title).strip()
    elif "talent.com" in host:
        match = re.search(r"(.+?)\s+[–-]\s+(.+?)\s+[–-]\s+Job\s+(.+)$", title)
        if match:
            role, company = clean_text(match.group(1)), clean_text(match.group(2))
            role = re.sub(r"^Senor\b", "Senior", role)
    elif "snowflake.com" in host:
        company = "Snowflake"
        role = re.sub(r"\s*\|.*$", "", title).strip()
    elif "jobs.apple.com" in host:
        company = "Apple"
        role = re.sub(r"\s+-\s+Jobs\s+-\s+Careers at Apple.*$", "", title).strip()

    if not company and "og:site_name" in meta:
        company = meta["og:site_name"]
    if not role and "og:title" in meta:
        role = meta["og:title"]

    return company.strip(), role.strip()


def format_address(address):
    if not isinstance(address, dict):
        return ""
    locality = address.get("addressLocality") or address.get("addressRegion") or ""
    region = address.get("addressRegion") or ""
    country = address.get("addressCountry") or ""
    if isinstance(country, dict):
        country = country.get("name", "")
    parts = []
    if locality:
        parts.append(clean_text(locality))
    if region and clean_text(region) not in parts:
        parts.append(clean_text(region))
    if not parts and country:
        parts.append(clean_text(country))
    return ", ".join(parts)


def infer_location(url, title, text, job_json):
    locations = []
    job_location = job_json.get("jobLocation") if isinstance(job_json, dict) else None
    if isinstance(job_location, dict):
        job_location = [job_location]
    if isinstance(job_location, list):
        for item in job_location:
            address = item.get("address") if isinstance(item, dict) else None
            formatted = format_address(address)
            if formatted and formatted not in locations:
                locations.append(formatted)

    patterns = [
        r"hiring .*? in ([^|]+?) \| LinkedIn",
        r"Job ([A-Z][A-Za-z .]+,\s*[A-Z]{2})$",
        r"Location:\s*([^\.]+?)(?: Department| Category| Req ID| Role Overview|$)",
        r"Location\s+([A-Z][A-Za-z .]+,\s*[A-Z]{2})",
        r"This job is available in \d+ locations\s+(.+?)\s+APPLY NOW",
    ]
    for pattern in patterns:
        match = re.search(pattern, f"{title} {text}", re.I)
        if match:
            value = clean_text(match.group(1))
            value = re.sub(r"\s+See all\s+", "; ", value, flags=re.I)
            value = re.sub(r"\s{2,}", " ", value)
            if value and value not in locations:
                locations.append(value)

    return "; ".join(locations[:4])


def infer_compensation(text):
    patterns = [
        r"(?:base salary range|estimated base salary range|pay range|salary range)[^$]{0,120}(\$[\d,]+(?:\.\d+)?\s*(?:-|–|—)\s*\$[\d,]+(?:\.\d+)?(?:\s*(?:USD|/yr|per year))?)",
        r"(\$[\d,]+(?:\.\d+)?\s*(?:-|–|—)\s*\$[\d,]+(?:\.\d+)?\s*(?:USD|/yr|per year))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return clean_text(match.group(1)).replace(" — ", "-").replace(" – ", "-")
    return ""


def infer_work_mode(text):
    lowered = text.lower()
    if "remote in us" in lowered:
        return "Remote in US"
    if "all-remote" in lowered or "all remote" in lowered:
        return "Remote"
    if "hybrid" in lowered:
        match = re.search(r"[^.]{0,120}hybrid[^.]{0,160}", text, re.I)
        return clean_text(match.group(0)) if match else "Hybrid"
    if "office-based" in lowered:
        return "Office-based"
    if "at least 25% of the time" in lowered:
        return "Hybrid, office at least 25%"
    if "four days a week" in lowered:
        return "Hybrid, 4 days/week"
    return ""


def infer_source(url):
    host = urlparse(url).netloc.lower()
    if "greenhouse.io" in host:
        return "Greenhouse job board"
    if "linkedin.com" in host:
        return "LinkedIn"
    if "talent.com" in host:
        return "Talent.com job board"
    if "playstation.com" in host:
        return "PlayStation careers"
    if "ubisoft.com" in host:
        return "Ubisoft careers"
    if "delta.avature.net" in host:
        return "Delta career site"
    return "Company career site"


def build_notes(company, role, text, warnings):
    notes = []
    if company or role:
        notes.append(f"Ingested posting for {company or 'unknown company'} - {role or 'unknown role'}.")
    if warnings:
        notes.append("Warnings: " + " ".join(warnings))
    if "direct page requires javascript" not in " ".join(warnings).lower():
        for phrase in ["About the role", "Responsibilities", "Role Overview", "Job description", "Minimum qualifications"]:
            idx = text.lower().find(phrase.lower())
            if idx >= 0:
                notes.append(clean_text(text[idx : idx + 420]))
                break
    return " ".join(notes)


def extract_posting(url, args):
    fetched = fetch(url)
    page_html = fetched["html"]
    meta = meta_values(page_html)
    job_json = first_job_posting_json(page_html)
    title = page_title(page_html) or meta.get("og:title", "") or meta.get("twitter:title", "")
    text = visible_text(page_html)
    final_url = fetched["final_url"] or url
    company, role = infer_company_role(final_url, title, meta, job_json)
    location = infer_location(final_url, title, text, job_json)
    compensation = infer_compensation(text)
    work_mode = infer_work_mode(text)
    warnings = []

    parsed = urlparse(final_url)
    if fetched["error"]:
        warnings.append(f"Fetch failed: {fetched['error']}")
    if fetched["status"] in {202, 403} or not text:
        warnings.append("Direct page may require JavaScript/browser verification.")
    closed = bool(re.search(r"job has been closed|oh snap! this job has been closed|position has been filled", text, re.I))
    active = bool(re.search(r"apply now|apply for this job|submit application", text, re.I))
    if closed and not active:
        warnings.append("Closed-posting text detected in static fetch; not archiving unless --mark-closed is used.")
        if "snowflake.com" in parsed.netloc.lower():
            warnings.append("Snowflake can show a false closed fallback to non-browser fetches; verify in the browser.")

    return {
        "company": args.company or company,
        "role": args.role or role,
        "location": args.location or location,
        "work_mode": args.work_mode or work_mode,
        "source": args.source or infer_source(final_url),
        "source_url": url,
        "compensation": args.compensation or compensation,
        "stage": args.stage or ("closed" if closed and args.mark_closed else DEFAULT_STAGE),
        "outcome": args.outcome or ("closed-posting" if closed and args.mark_closed else DEFAULT_OUTCOME),
        "tags": tracker.normalize_tags(args.tags or ("closed-posting" if closed and args.mark_closed else DEFAULT_TAGS)),
        "priority": args.priority,
        "notes": args.notes or build_notes(args.company or company, args.role or role, text, warnings),
        "closed_detected": closed,
        "active_detected": active,
        "warnings": warnings,
    }


def find_existing(rows, url):
    wanted = normalize_url(url)
    for row in rows:
        if normalize_url(row.get("source_url", "")) == wanted:
            return row
    return None


def apply_fields(row, data, overwrite):
    for field in [
        "company",
        "role",
        "location",
        "work_mode",
        "source",
        "source_url",
        "compensation",
        "priority",
    ]:
        if data.get(field) and (overwrite or not row.get(field)):
            row[field] = tracker.clean(data[field])

    if overwrite or row.get("stage", "").lower() == "closed":
        row["stage"] = tracker.clean(data["stage"])
        row["outcome"] = tracker.clean(data["outcome"])
        row["tags"] = tracker.normalize_tags(data["tags"])

    if data.get("notes"):
        if overwrite or not row.get("notes"):
            row["notes"] = tracker.clean(data["notes"])
        elif data["notes"] not in row["notes"]:
            row["notes"] = tracker.clean(f"{row['notes']} | {tracker.today_iso()}: {data['notes']}")


def find_matching_company(company_name):
    wanted = company_store.normalized_key(company_name)
    if not wanted:
        return None
    for company in repository.read_companies():
        if wanted in company_store.company_keys(company):
            return company
    return None


def associate_company(row):
    if row.get("company_id"):
        return row
    company_name = tracker.clean(row.get("company", ""))
    if not company_name:
        return row
    company = find_matching_company(company_name)
    if not company:
        company = company_store.upsert_company("", {"name": company_name})
    return application_store.update_application(row.get("id", ""), {"company_id": company.get("id", "")})


def upsert(url, args):
    rows = tracker.read_rows(tracker.APPLICATIONS, tracker.APPLICATION_FIELDS)
    data = extract_posting(url, args)
    row = find_existing(rows, url)
    created = False

    if row is None:
        row = {field: "" for field in tracker.APPLICATION_FIELDS}
        row.update(
            {
                "id": tracker.next_application_id(rows),
                "stage": data["stage"],
                "outcome": data["outcome"],
                "tags": data["tags"],
                "priority": data["priority"],
                "date_found": tracker.normalize_date(args.date_found or "today"),
                "date_applied": "",
                "next_action": args.next_action,
                "next_action_date": tracker.normalize_date(args.next_action_date),
            }
        )
        apply_fields(row, data, overwrite=True)
        rows.append(row)
        created = True
    else:
        apply_fields(row, data, overwrite=args.overwrite)
        if not row.get("next_action"):
            row["next_action"] = args.next_action
        if not row.get("next_action_date"):
            row["next_action_date"] = tracker.normalize_date(args.next_action_date)

    if args.dry_run:
        return created, row, data

    note_path, _created_note = tracker.make_posting_note(row, force=args.force_note)
    row["posting_file"] = note_path
    tracker.write_rows(tracker.APPLICATIONS, tracker.APPLICATION_FIELDS, rows)
    row = associate_company(row)
    return created, row, data


def build_parser():
    parser = argparse.ArgumentParser(description="Ingest or refresh job posting URLs.")
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite non-empty inferred fields on existing rows.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-note", action="store_true", help="Regenerate posting note from the template.")
    parser.add_argument("--mark-closed", action="store_true", help="Trust closed-posting text from the fetch and archive the row.")
    parser.add_argument("--use-ai-actions", action="store_true", help="Use configured AI settings to add posting-specific actions.")
    parser.add_argument("--company")
    parser.add_argument("--role")
    parser.add_argument("--location")
    parser.add_argument("--work-mode")
    parser.add_argument("--source")
    parser.add_argument("--compensation")
    parser.add_argument("--stage")
    parser.add_argument("--outcome")
    parser.add_argument("--tags")
    parser.add_argument("--priority", default=tracker.DEFAULT_PRIORITY)
    parser.add_argument("--date-found", default="today")
    parser.add_argument("--next-action", default=DEFAULT_NEXT_ACTION)
    parser.add_argument("--next-action-date", default=(date.today() + timedelta(days=1)).isoformat())
    parser.add_argument("--notes")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    tracker.ensure_workspace()
    results = []
    for url in args.urls:
        created, row, data = upsert(url, args)
        action_count = 0
        action_warning = ""
        if not args.dry_run:
            actions, action_warning = action_engine.create_actions_for_application(
                row,
                warnings=data["warnings"],
                use_ai=args.use_ai_actions,
            )
            action_count = len(actions)
        action = "would add" if args.dry_run and created else "would update" if args.dry_run else "added" if created else "updated"
        results.append((action, row, data, action_count, action_warning))

    for action, row, data, action_count, action_warning in results:
        print(f"{action} {row['id']}: {row.get('company', '')} - {row.get('role', '')}")
        if action_count:
            print(f"  created {action_count} actions")
        for warning in data["warnings"]:
            print(f"  warning: {warning}")
        if action_warning:
            print(f"  warning: {action_warning}")


if __name__ == "__main__":
    main()
