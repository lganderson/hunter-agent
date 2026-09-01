"""Private local API usage logging and feature-level summaries."""

import json
from datetime import datetime

from . import paths, storage


USAGE_LOG_FILE = "agent_usage.jsonl"
DEFAULT_FEATURE = "unattributed"


def usage_metrics(response):
    usage = (response or {}).get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cached_input_tokens": int(input_details.get("cached_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def web_search_call_count(response):
    return sum(
        1
        for item in (response or {}).get("output", [])
        if item.get("type") == "web_search_call"
    )


def log_usage(
    feature,
    model,
    response,
    *,
    provider="openai",
    operation="",
    tool_round=0,
    tool_call_count=0,
    prompt_cache_key="",
    context=None,
):
    metrics = usage_metrics(response)
    search_calls = web_search_call_count(response)
    if not any(metrics.values()) and not search_calls:
        return
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    uncached = max(0, metrics["input_tokens"] - metrics["cached_input_tokens"])
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "provider": storage.clean(provider).lower() or "openai",
        "feature": storage.clean(feature).lower() or DEFAULT_FEATURE,
        "operation": storage.clean(operation).lower(),
        "model": storage.clean(model),
        "request_count": 1,
        "tool_round": int(tool_round or 0),
        "tool_call_count": int(tool_call_count or 0),
        "web_search_call_count": search_calls,
        "prompt_cache_key": storage.clean(prompt_cache_key),
        "response_id": storage.clean((response or {}).get("id", "")),
        "context": {
            storage.clean(key): storage.clean(value)
            for key, value in (context or {}).items()
            if storage.clean(key) and storage.clean(value)
        },
        **metrics,
        "uncached_input_tokens": uncached,
    }
    with (paths.DATA_DIR / USAGE_LOG_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def usage_summary():
    totals = _empty_totals()
    features = {}
    log_path = paths.DATA_DIR / USAGE_LOG_FILE
    if not log_path.exists():
        return {"totals": totals, "features": []}

    with log_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            feature = storage.clean(row.get("feature", "")).lower() or DEFAULT_FEATURE
            feature_totals = features.setdefault(feature, _empty_totals())
            for field in totals:
                try:
                    value = int(row.get(field) or (1 if field == "request_count" else 0))
                except (TypeError, ValueError):
                    value = 0
                totals[field] += value
                feature_totals[field] += value

    ordered = [
        {"feature": feature, **values}
        for feature, values in sorted(
            features.items(),
            key=lambda item: (-item[1]["total_tokens"], item[0]),
        )
    ]
    return {"totals": totals, "features": ordered}


def _empty_totals():
    return {
        "request_count": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "tool_call_count": 0,
        "web_search_call_count": 0,
    }
