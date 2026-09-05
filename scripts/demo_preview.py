#!/usr/bin/env python3
"""Serve fictional data in a disposable workspace for integration tests and screenshots."""

import argparse
import os
import signal
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))


def refresh_demo_dates():
    """Keep fictional actions useful when the committed fixture dates get old."""
    from hunter import repository

    today = date.today()
    rows = repository.read_applications()
    stages = ["considering", "applied", "applied", "interviewing", "interviewing", "considering", "offer", "closed"]
    for index, row in enumerate(rows):
        row["stage"] = stages[index % len(stages)]
        row["outcome"] = "archived" if row["stage"] == "closed" else ""
        row["date_found"] = (today - timedelta(days=14-index)).isoformat()
        row["date_applied"] = (today - timedelta(days=index)).isoformat() if row["stage"] not in {"considering", "closed"} else ""
    repository.save_applications_changes(rows)
    actions = repository.read_actions()
    considering = {row["id"] for row in rows if row["stage"] == "considering"}
    selected = set()
    for index, row in enumerate(actions):
        row["due_date"] = (today + timedelta(days=index-2)).isoformat()
        if row["application_id"] in considering and row["status"] not in {"done", "cancelled"}:
            if row["application_id"] in selected:
                row["status"] = "done"
                row["completed_date"] = today.isoformat()
            selected.add(row["application_id"])
    repository.save_actions_changes(actions)


def seed_review_queue():
    from hunter import discovery, repository, schema

    search = discovery.upsert_search(updates={
        "name": "Product and platform", "keywords": "product platform",
        "lanes": [{"label": "Remote", "location": "United States", "work_modes": ["remote"]}],
    })
    companies = []
    candidates = []
    for index in range(60):
        company = {field: "" for field in schema.COMPANY_FIELDS}
        company.update(id=f"CO{9000 + index}", name=f"Example Studio {index + 1:02d}",
                       interest_status="interested", tracking_status="watch", industry="Software",
                       company_size="51-200", website=f"https://studio{index}.example.invalid")
        companies.append(company)
        candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        candidate.update(id=f"DC{9000 + index}", company_id=company["id"], company=company["name"],
                         title=["Principal Product Manager", "Platform Product Lead", "Product Operations Lead"][index % 3],
                         url=f"https://studio{index}.example.invalid/jobs/{index}",
                         canonical_url=f"https://studio{index}.example.invalid/jobs/{index}",
                         location="United States", work_mode="remote", status="new", source_platform="manual",
                         search_id=search["id"], search_ids_json='["' + search["id"] + '"]',
                         processing_status="ready", qualification_status="eligible", fit_score=str(95-index//3),
                         fit_summary="Platform strategy, product systems, and cross-functional delivery.",
                         freshness_status="confirmed-open", freshness_checked_at=datetime.now().isoformat(timespec="seconds"),
                         description_text="Lead product strategy for a developer platform. Partner with engineering and design. " * 20)
        candidates.append(candidate)
    repository.insert_companies(companies)
    repository.replace_discovery_candidates_for_import(candidates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4175)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="hunter-demo-preview-") as workspace:
        os.environ["HUNTER_ROOT"] = workspace
        os.environ.pop("JOB_HUNT_ROOT", None)
        from hunter import demo_data, paths
        paths.FRONTEND_DIST = SOURCE_ROOT / "app" / "dist"
        demo_data.load_demo_data()
        refresh_demo_dates()
        seed_review_queue()
        from http.server import ThreadingHTTPServer
        from scripts.serve_app import AppHandler
        server = ThreadingHTTPServer(("127.0.0.1", args.port), AppHandler)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        try:
            print(f"Fictional demo: http://127.0.0.1:{server.server_port}/", flush=True)
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
