"""OpenAI-backed in-app chat for Hunter."""

import json
import os
import ssl
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import mcp_server, paths, settings as settings_store, storage


DEFAULT_MODEL = "gpt-5.5"
PROMPT_CACHE_KEY = "hunter-local-chat-v1"
PROMPT_CACHE_RETENTION_MODELS = (
    "gpt-5",
    "gpt-4.1",
)
USAGE_LOG_FILE = "agent_usage.jsonl"
MUTATING_TOOLS = {
    "hunter_update_action",
    "hunter_update_action_fields",
    "hunter_make_next_action",
    "hunter_update_application",
    "hunter_ingest_posting",
    "hunter_upsert_contact",
    "hunter_link_contact",
    "hunter_unlink_contact",
    "hunter_upsert_company",
    "hunter_archive_company",
    "hunter_restore_company",
    "hunter_check_company_postings",
    "hunter_link_company_contact",
    "hunter_unlink_company_contact",
    "hunter_ingest_company_candidate",
    "hunter_update_settings",
}
MAX_TOOL_ROUNDS = 6


INSTRUCTIONS = """You are Hunter, a practical job-hunt tracking assistant.
Use the provided Hunter tools when you need current local tracker data or when
the user clearly asks you to update the tracker. Do not invent application
history, contacts, dates, compensation, outcomes, or private details. If a
field is unknown or absent, say that it is not recorded. Keep answers concise
and action-oriented. You may apply tracker changes directly when the user's
request is clear. When updating a posting, include only the fields the user
intended to change. Do not send empty strings for unchanged fields. If the user
asks to associate a posting with a company, update that posting's company field
directly. If the user says they submitted or applied to a posting, set stage to
application-submitted and date_applied to today unless they provide a specific
date. Closed postings use stage=closed plus an outcome such as rejected,
withdrawn, accepted, declined, archived, or closed-posting.
Companies are managed records with careers URLs, interest status, contacts, and
posting candidates. Use company tools for company-level updates, archiving or
restoring companies, and manual careers-page checks.
If the user asks to update Search Goals or fit settings, use Hunter settings
tools instead of only remembering the preference in the conversation.
Use the compact local fit profile for ordinary fit/recommendation answers. If
the user asks for exact resume wording or resume-specific tailoring, call
hunter_get_resume_text instead of guessing.
Action due dates live on actions. To change a posting's next action date, update
the linked action's due_date. If multiple open actions exist and the user picks
one as next, use hunter_make_next_action instead of editing the posting."""


def _settings():
    settings = settings_store.load_settings()
    token = settings.get("api_token") or os.environ.get("OPENAI_API_KEY", "")
    provider = (settings.get("provider") or "openai").lower()
    if provider != "openai":
        raise ValueError("Hunter chat currently supports OpenAI only. Set Provider to OpenAI in Settings.")
    if not token:
        raise ValueError("No OpenAI API token is configured. Add one in Settings.")
    return {
        "token": token,
        "model": settings.get("model") or DEFAULT_MODEL,
        "api_base": (settings.get("api_base") or "https://api.openai.com/v1").rstrip("/"),
    }


def _request_json(url, token, payload):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    context = ssl.create_default_context(cafile=_certifi_ca_file())
    try:
        with urlopen(request, timeout=60, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI request failed with HTTP {exc.code}: {body}") from exc


def _certifi_ca_file():
    try:
        import certifi  # type: ignore
    except Exception:  # noqa: BLE001 - fall back to Python's default trust store.
        return None
    return certifi.where()


def _openai_tools():
    tools = []
    for name, definition in mcp_server.TOOLS.items():
        tools.append(
            {
                "type": "function",
                "name": name,
                "description": definition["description"],
                "parameters": definition["inputSchema"],
            }
        )
    return tools


def _prompt_cache_options(model):
    normalized = storage.clean(model).lower()
    options = {"prompt_cache_key": PROMPT_CACHE_KEY}
    if normalized.startswith(PROMPT_CACHE_RETENTION_MODELS):
        options["prompt_cache_retention"] = "24h"
    return options


def _normalize_messages(messages):
    normalized = []
    for item in messages or []:
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        content = content.strip()
        if content:
            normalized.append({"role": role, "content": content})
    if not normalized:
        raise ValueError("A chat message is required.")
    return normalized[-20:]


def _output_text(response):
    if response.get("output_text"):
        return response["output_text"]
    chunks = []
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def _function_calls(response):
    return [item for item in response.get("output", []) if item.get("type") == "function_call"]


def _replayable_output(response):
    return [item for item in response.get("output", []) if item.get("type") != "reasoning"]


def _call_tool(call):
    name = call.get("name", "")
    try:
        arguments = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid arguments for {name}: {exc}") from exc
    arguments = _sanitize_tool_arguments(name, arguments)
    result = mcp_server.call_named_tool(name, arguments)
    return arguments, result


def _sanitize_tool_arguments(name, arguments):
    if name not in {"hunter_update_application", "hunter_upsert_contact", "hunter_upsert_company"}:
        return arguments
    updates = arguments.get("updates")
    if not isinstance(updates, dict):
        return arguments
    arguments = {**arguments}
    arguments["updates"] = {
        field: value
        for field, value in updates.items()
        if not (isinstance(value, str) and storage.clean(value) == "")
    }
    return arguments


def _usage_metrics(response):
    usage = response.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cached_input_tokens": int(input_details.get("cached_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def log_usage(model, response, tool_round, tool_call_count):
    metrics = _usage_metrics(response)
    if not any(metrics.values()):
        return
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    uncached = max(0, metrics["input_tokens"] - metrics["cached_input_tokens"])
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "tool_round": tool_round,
        "tool_call_count": tool_call_count,
        "prompt_cache_key": PROMPT_CACHE_KEY,
        **metrics,
        "uncached_input_tokens": uncached,
    }
    with (paths.DATA_DIR / USAGE_LOG_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def chat(messages):
    config = _settings()
    input_items = _normalize_messages(messages)
    fit_profile_context = settings_store.fit_profile_context()
    instructions = INSTRUCTIONS
    if fit_profile_context:
        instructions = (
            f"{INSTRUCTIONS}\n\n"
            "Use the following compact local fit profile when evaluating job fit, "
            "suggesting positioning, searching career pages, or selecting "
            "promising postings. Fetch full resume text only when exact resume "
            "detail is necessary.\n\n"
            f"{fit_profile_context}"
        )
    tool_calls = []
    mutated = False
    response = None

    for tool_round in range(1, MAX_TOOL_ROUNDS + 1):
        payload = {
            "model": config["model"],
            "instructions": instructions,
            "input": input_items,
            "tools": _openai_tools(),
            "tool_choice": "auto",
            "store": False,
            **_prompt_cache_options(config["model"]),
        }
        response = _request_json(f"{config['api_base']}/responses", config["token"], payload)
        calls = _function_calls(response)
        log_usage(config["model"], response, tool_round, len(calls))
        if not calls:
            break

        input_items.extend(_replayable_output(response))
        for call in calls:
            name = call.get("name", "")
            call_record = {"name": name, "ok": True}
            try:
                arguments, result = _call_tool(call)
                call_record["arguments"] = arguments
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.get("call_id"),
                        "output": json.dumps(result, sort_keys=True),
                    }
                )
                if name in MUTATING_TOOLS:
                    mutated = True
            except Exception as exc:  # noqa: BLE001 - return tool errors to the model.
                call_record["ok"] = False
                call_record["error"] = str(exc)
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.get("call_id"),
                        "output": json.dumps({"error": str(exc)}, sort_keys=True),
                    }
                )
            tool_calls.append(call_record)
    else:
        return {
            "message": "I reached Hunter's tool-call limit before finishing. Try a narrower request.",
            "tool_calls": tool_calls,
            "mutated": mutated,
        }

    message = _output_text(response or {})
    return {
        "message": message or "I could not produce a response.",
        "tool_calls": tool_calls,
        "mutated": mutated,
    }
