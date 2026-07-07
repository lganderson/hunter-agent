#!/usr/bin/env python3
"""Create and maintain user actions for tracked job applications."""

import argparse
import csv
import json
import os
import re
import ssl
import sys
from datetime import date, timedelta
from urllib.error import HTTPError
from pathlib import Path
from urllib.request import Request, urlopen

ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORTS))

from hunter import actions as action_store
from hunter import paths as hunter_paths
from hunter import repository
from hunter import schema as hunter_schema
from hunter import settings as settings_store
from hunter import workflow

import tracker


ROOT = hunter_paths.ROOT
SETTINGS_FILE = hunter_paths.SETTINGS_FILE

DEFAULT_REVIEW_DAYS = 1
ACTION_STATUSES = hunter_schema.ACTION_STATUSES


def today_iso():
    return date.today().isoformat()


def default_due_date():
    return (date.today() + timedelta(days=DEFAULT_REVIEW_DAYS)).isoformat()


def action_key(action):
    return action_store.action_key(action)


def next_action_id(rows):
    return action_store.next_action_id(rows)


def open_actions(rows):
    return action_store.open_actions(rows)


def update_action_status(action_id, status):
    return action_store.update_action_status(action_id, status)


def upsert_action(rows, action):
    return action_store.upsert_action(rows, action)


def catalog_action(app, action_type, title, description, source, priority=None, due_date=None):
    definition = workflow.action_type_by_id(action_type) if repository.using_sqlite() else None
    return {
        "application_id": app.get("id", ""),
        "company": app.get("company", ""),
        "role": app.get("role", ""),
        "type": action_type,
        "title": title,
        "description": description or (definition or {}).get("description", ""),
        "status": "open",
        "priority": priority or (definition or {}).get("default_priority") or app.get("priority") or tracker.DEFAULT_PRIORITY,
        "due_date": due_date or app.get("next_action_date") or default_due_date(),
        "source": source,
        "related_url": app.get("source_url", ""),
    }


def base_actions_for_application(app, warnings=None):
    warnings = warnings or []
    app_id = app.get("id", "")
    company = app.get("company", "")
    role = app.get("role", "")
    due = app.get("next_action_date") or default_due_date()
    actions = []

    if app.get("stage", "").lower() == "closed":
        return actions

    actions.append(
        catalog_action(
            app,
            "review-fit",
            f"Review fit and tailor resume for {company}",
            f"Review {role}, decide positioning, and identify resume changes before applying.",
            "ingest",
            due_date=due,
        )
    )

    warning_text = " ".join(warnings + [app.get("notes", "")]).lower()
    source = app.get("source", "").lower()
    if "browser verification" in warning_text or "javascript" in warning_text or "snowflake" in warning_text:
        actions.append(
            catalog_action(
                app,
                "verify-source",
                f"Verify source page in browser for {company}",
                "Open the posting in the browser and confirm active status, location, compensation, and apply button.",
                "ingest-warning",
                priority="high" if "snowflake" in warning_text else None,
                due_date=due,
            )
        )

    if "talent.com" in source or "job board" in source and "greenhouse" not in source:
        actions.append(
            catalog_action(
                app,
                "find-canonical-posting",
                f"Find canonical posting for {company}",
                "Find the employer's direct careers-page posting before applying from an aggregator.",
                "ingest",
                due_date=due,
            )
        )

    if company.lower() == "anthropic":
        actions.append(
            catalog_action(
                app,
                "draft-application-answer",
                "Draft Why Anthropic response",
                "Anthropic says they read this answer carefully. Draft a specific response tied to the role.",
                "rule",
                priority="high",
                due_date=due,
            )
        )

    return actions


def load_settings():
    return settings_store.load_settings()


def settings_status():
    return settings_store.settings_status()


def save_settings(provider, model, api_base, token, search_goals=None, fit_signals=None):
    return settings_store.save_settings(
        provider,
        model,
        api_base,
        token,
        search_goals=search_goals,
        fit_signals=fit_signals,
    )


def ai_actions_for_application(app):
    settings = load_settings()
    provider = settings.get("provider", "").lower()
    token = settings.get("api_token", "")
    model = settings.get("model", "")
    api_base = settings.get("api_base", "").rstrip("/")
    if not provider or not token:
        return [], "AI settings are not configured."
    if provider not in {"openai", "anthropic"}:
        return [], f"AI provider '{provider}' is not supported yet."

    prompt = (
        "Create 2-4 concise job-application actions as JSON. "
        "Each item must have type, title, description, priority. "
        "The type must be one of the active Hunter action type ids.\n\n"
        f"Active action type ids: {', '.join(sorted(workflow.active_action_type_ids()))}\n"
        f"Company: {app.get('company')}\n"
        f"Role: {app.get('role')}\n"
        f"Location: {app.get('location')}\n"
        f"Compensation: {app.get('compensation')}\n"
        f"Notes: {app.get('notes')}\n"
    )

    if provider == "openai":
        return call_openai(token, model or "gpt-4.1-mini", prompt, app, api_base)
    return call_anthropic(token, model or "claude-3-5-haiku-latest", prompt, app, api_base)


def request_json(url, headers, payload):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    context = ssl.create_default_context(cafile=certifi_ca_file())
    try:
        with urlopen(request, timeout=30, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def certifi_ca_file():
    try:
        import certifi  # type: ignore
    except Exception:  # noqa: BLE001 - fall back to Python's default trust store.
        return None
    return certifi.where()


def parse_json_actions(text, app):
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        return []
    try:
        raw_actions = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    actions = []
    active_types = workflow.active_action_type_ids() if repository.using_sqlite() else set()
    for item in raw_actions[:4]:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        action_type = action_store.normalize_action_type(item.get("type", ""))
        if active_types and action_type not in active_types:
            continue
        actions.append(
            {
                "application_id": app.get("id", ""),
                "company": app.get("company", ""),
                "role": app.get("role", ""),
                "type": action_type,
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "status": "open",
                "priority": item.get("priority", app.get("priority") or tracker.DEFAULT_PRIORITY),
                "due_date": app.get("next_action_date") or default_due_date(),
                "source": "ai",
                "related_url": app.get("source_url", ""),
            }
        )
    return actions


def call_openai(token, model, prompt, app, api_base=""):
    payload = {
        "model": model,
        "input": prompt,
    }
    try:
        data = request_json(
            f"{api_base or 'https://api.openai.com/v1'}/responses",
            {"Authorization": f"Bearer {token}"},
            payload,
        )
    except Exception as exc:  # noqa: BLE001 - surface provider failure as a warning.
        return [], f"OpenAI action generation failed: {exc}"
    text = data.get("output_text", "")
    if not text:
        chunks = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    chunks.append(content.get("text", ""))
        text = "\n".join(chunks)
    return parse_json_actions(text, app), ""


def call_anthropic(token, model, prompt, app, api_base=""):
    payload = {
        "model": model,
        "max_tokens": 900,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        data = request_json(
            f"{api_base or 'https://api.anthropic.com/v1'}/messages",
            {"x-api-key": token, "anthropic-version": "2023-06-01"},
            payload,
        )
    except Exception as exc:  # noqa: BLE001
        return [], f"Anthropic action generation failed: {exc}"
    text = "\n".join(
        item.get("text", "")
        for item in data.get("content", [])
        if item.get("type") == "text"
    )
    return parse_json_actions(text, app), ""


def create_actions_for_application(app, warnings=None, use_ai=False):
    rows = repository.read_actions()
    created = []
    for action in base_actions_for_application(app, warnings=warnings):
        was_created, row = upsert_action(rows, action)
        if was_created:
            created.append(row)

    ai_warning = ""
    if use_ai:
        ai_actions, ai_warning = ai_actions_for_application(app)
        for action in ai_actions:
            was_created, row = upsert_action(rows, action)
            if was_created:
                created.append(row)

    repository.write_actions(rows)
    action_store.sync_next_action(app.get("id", ""))
    return created, ai_warning


def generate_actions(application_ids=None, use_ai=False):
    tracker.ensure_workspace()
    apps = repository.read_applications()
    wanted = {item.upper() for item in application_ids or []}
    created = []
    warnings = []
    for app in apps:
        if wanted and app.get("id", "").upper() not in wanted:
            continue
        new_actions, warning = create_actions_for_application(app, use_ai=use_ai)
        created.extend(new_actions)
        if warning:
            warnings.append(f"{app.get('id')}: {warning}")
    return created, warnings


def build_parser():
    parser = argparse.ArgumentParser(description="Generate actions for tracked applications.")
    parser.add_argument("application_ids", nargs="*")
    parser.add_argument("--use-ai", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    created, warnings = generate_actions(args.application_ids, use_ai=args.use_ai)
    print(f"Created {len(created)} actions.")
    for warning in warnings:
        print(f"warning: {warning}")


if __name__ == "__main__":
    main()
