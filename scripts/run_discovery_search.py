#!/usr/bin/env python3
"""Run one saved Discovery search as an isolated subprocess."""

import argparse
import json
import sys
from pathlib import Path


ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORTS))

from hunter import discovery, sqlite_store  # noqa: E402


def compact_result(result):
    errors = result.get("errors", [])
    evaluated_count = int(result.get("evaluated_count", 0) or 0)
    useful_count = sum(
        int(result.get(field, 0) or 0)
        for field in ["new_count", "updated_count", "associated_count", "known_count", "duplicate_count"]
    )
    status = "failed" if errors and not evaluated_count and not useful_count else (
        "completed-with-errors" if errors else "completed"
    )
    return {
        "id": result.get("search", {}).get("id", ""),
        "name": result.get("search", {}).get("name", ""),
        "status": status,
        "evaluated_count": evaluated_count,
        "new_count": int(result.get("new_count", 0) or 0),
        "updated_count": int(result.get("updated_count", 0) or 0),
        "associated_count": int(result.get("associated_count", 0) or 0),
        "duplicate_count": int(result.get("duplicate_count", 0) or 0),
        "known_count": int(result.get("known_count", 0) or 0),
        "screened_count": int(result.get("screened_count", 0) or 0),
        "needs_details_count": int(result.get("needs_details_count", 0) or 0),
        "errors": errors,
        "enrichment": result.get("enrichment", {}),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("search_id")
    parser.add_argument("--enrichment-limit", type=int, default=100)
    parser.add_argument("--use-browser-fallback", action="store_true")
    args = parser.parse_args(argv)
    sqlite_store.initialize()
    try:
        result = discovery.continue_discovery(
            args.search_id,
            enrichment_limit=max(0, min(250, args.enrichment_limit)),
            use_browser_fallback=args.use_browser_fallback,
        )
        print(json.dumps(compact_result(result), sort_keys=True))
    except Exception as exc:  # noqa: BLE001 - parent process records this search independently.
        print(
            json.dumps(
                {
                    "id": args.search_id.upper(),
                    "name": "",
                    "status": "failed",
                    "errors": [str(exc)],
                },
                sort_keys=True,
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
