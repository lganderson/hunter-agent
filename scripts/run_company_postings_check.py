#!/usr/bin/env python3
"""Run one tracked-company careers check as an isolated subprocess."""

import argparse
import json
import sys
from pathlib import Path


ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORTS))

from hunter import companies, sqlite_store  # noqa: E402


def compact_result(result):
    scan = result.get("scan", {})
    return {
        "id": result.get("company", {}).get("id", ""),
        "name": result.get("company", {}).get("name", ""),
        "status": "completed-with-errors" if scan.get("status") == "partial" else "completed",
        "run_id": scan.get("run_id", ""),
        "new_count": len(result.get("new", [])),
        "recommended_count": len(result.get("recommended", [])),
        "candidate_count": len(result.get("candidates", [])),
        "unavailable_count": int(result.get("unavailable_count") or 0),
        "verification_count": int(result.get("verification_count") or 0),
        "errors": json.loads(scan.get("errors_json") or "[]"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("company_id")
    args = parser.parse_args(argv)
    sqlite_store.initialize()
    try:
        print(json.dumps(compact_result(companies.check_company_postings(args.company_id)), sort_keys=True))
    except Exception as exc:  # noqa: BLE001 - parent process records this company independently.
        print(json.dumps({
            "id": args.company_id.upper(),
            "name": "",
            "status": "failed",
            "errors": [str(exc)],
        }, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
