#!/usr/bin/env python3
"""Serve Hunter's frontend with local API endpoints for settings and actions."""

import json
import mimetypes
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORTS))

from hunter import paths as hunter_paths
from hunter import agent as hunter_agent
from hunter import app_state
from hunter import actions as action_store
from hunter import applications as application_store
from hunter import companies as company_store
from hunter import contacts as contact_store
from hunter import settings as settings_store
from hunter import workflow as workflow_store

import action_engine


ROOT = hunter_paths.ROOT
FRONTEND_DIST = hunter_paths.FRONTEND_DIST


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not path.exists() or not path.is_file():
            self.send_error(404, "File not found")
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_download_file(self, path):
        if not path.exists() or not path.is_file():
            self.send_error(404, "File not found")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_frontend(self, request_path):
        if not FRONTEND_DIST.exists():
            self.send_error(503, "Frontend build is missing. Run: make frontend-build")
            return
        relative = request_path.lstrip("/")
        dist_root = FRONTEND_DIST.resolve()
        asset_path = (FRONTEND_DIST / relative).resolve() if relative else FRONTEND_DIST / "index.html"
        is_inside_dist = asset_path == dist_root or dist_root in asset_path.parents
        if relative and is_inside_dist and asset_path.exists() and asset_path.is_file():
            self.send_file(asset_path)
            return
        if "." in Path(relative).name:
            self.send_error(404, "File not found")
            return
        self.send_file(FRONTEND_DIST / "index.html")

    def do_GET(self):  # noqa: N802 - stdlib API name.
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/app-state":
            self.send_json(app_state.build_payload())
            return
        if path == "/api/companies/export":
            try:
                result = company_store.write_company_export((query.get("id") or [""])[0])
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_download_file(result["path"])
            return
        if path == "/api/settings":
            self.send_json(action_engine.settings_status())
            return
        if path == "/api/settings/resume/text":
            self.send_json(settings_store.resume_text_payload())
            return
        if path == "/api/workflow":
            self.send_json(workflow_store.read_workflow())
            return
        if path == "/dashboard" or path.startswith("/dashboard/"):
            self.send_error(404, "Legacy dashboard path removed. Use /")
            return
        self.send_frontend(path)

    def do_POST(self):  # noqa: N802 - stdlib API name.
        path = urlparse(self.path).path
        if path == "/api/settings":
            payload = self.read_json()
            status = action_engine.save_settings(
                provider=payload.get("provider"),
                model=payload.get("model"),
                api_base=payload.get("api_base"),
                token=payload.get("api_token"),
                search_goals=payload.get("search_goals"),
                fit_signals=payload.get("fit_signals"),
            )
            self.send_json(status)
            return

        if path == "/api/settings/resume":
            payload = self.read_json()
            try:
                status = settings_store.save_resume_upload(
                    filename=payload.get("filename", ""),
                    content_base64=payload.get("content_base64", ""),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json(status)
            return

        if path == "/api/settings/resume/delete":
            self.send_json(settings_store.delete_resume())
            return

        if path == "/api/actions/generate":
            payload = self.read_json()
            created, warnings = action_engine.generate_actions(use_ai=bool(payload.get("use_ai")))
            self.send_json({"created": len(created), "warnings": warnings})
            return

        if path == "/api/agent/chat":
            payload = self.read_json()
            try:
                result = hunter_agent.chat(payload.get("messages", []))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            except Exception as exc:  # noqa: BLE001 - local API should surface provider failures.
                self.send_json({"error": f"Hunter chat failed: {exc}"}, status=502)
                return
            self.send_json(result)
            return

        if path == "/api/actions/update":
            payload = self.read_json()
            try:
                action = action_engine.update_action_status(
                    action_id=payload.get("id", ""),
                    status=payload.get("status", ""),
                )
                posting = action_store.sync_next_action(action.get("application_id", ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"action": action, "posting": posting})
            return

        if path == "/api/actions/update-fields":
            payload = self.read_json()
            try:
                action = action_store.update_action_fields(
                    action_id=payload.get("id", ""),
                    updates=payload.get("updates", {}),
                )
                posting = action_store.sync_next_action(action.get("application_id", ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"action": action, "posting": posting})
            return

        if path == "/api/actions/make-next":
            payload = self.read_json()
            try:
                posting = action_store.make_next_action(payload.get("id", ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"posting": posting})
            return

        if path == "/api/applications/update":
            payload = self.read_json()
            try:
                application = application_store.update_application(
                    application_id=payload.get("id", ""),
                    updates=payload.get("updates", {}),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"application": application})
            return

        if path == "/api/workflow/stages/upsert":
            payload = self.read_json()
            try:
                stage = workflow_store.upsert_stage(payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"stage": stage, "workflow": workflow_store.read_workflow()})
            return

        if path == "/api/workflow/stages/archive":
            payload = self.read_json()
            try:
                stage = workflow_store.archive_stage(payload.get("id", ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"stage": stage, "workflow": workflow_store.read_workflow()})
            return

        if path == "/api/workflow/action-types/upsert":
            payload = self.read_json()
            try:
                action_type = workflow_store.upsert_action_type(payload)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"action_type": action_type, "workflow": workflow_store.read_workflow()})
            return

        if path == "/api/workflow/action-types/archive":
            payload = self.read_json()
            try:
                action_type = workflow_store.archive_action_type(payload.get("id", ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"action_type": action_type, "workflow": workflow_store.read_workflow()})
            return

        if path == "/api/contacts/upsert":
            payload = self.read_json()
            try:
                contact = contact_store.upsert_contact(
                    contact_id=payload.get("id", ""),
                    updates=payload.get("updates", {}),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"contact": contact})
            return

        if path == "/api/contacts/link":
            payload = self.read_json()
            try:
                link = contact_store.link_contact(
                    application_id=payload.get("application_id", ""),
                    contact_id=payload.get("contact_id", ""),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"link": link})
            return

        if path == "/api/contacts/unlink":
            payload = self.read_json()
            try:
                link = contact_store.unlink_contact(
                    application_id=payload.get("application_id", ""),
                    contact_id=payload.get("contact_id", ""),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"link": link})
            return

        if path == "/api/companies/upsert":
            payload = self.read_json()
            try:
                company = company_store.upsert_company(
                    company_id=payload.get("id", ""),
                    updates=payload.get("updates", {}),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"company": company})
            return

        if path == "/api/companies/archive":
            payload = self.read_json()
            try:
                company = company_store.archive_company(payload.get("id", ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"company": company})
            return

        if path == "/api/companies/restore":
            payload = self.read_json()
            try:
                company = company_store.restore_company(
                    company_id=payload.get("id", ""),
                    interest_status=payload.get("interest_status", "neutral"),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"company": company})
            return

        if path == "/api/companies/check":
            payload = self.read_json()
            try:
                result = company_store.check_company_postings(payload.get("id", ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json(result)
            return

        if path == "/api/companies/check-all":
            self.send_json(company_store.check_all_company_postings())
            return

        if path == "/api/companies/link-contact":
            payload = self.read_json()
            try:
                link = company_store.link_contact(
                    company_id=payload.get("company_id", ""),
                    contact_id=payload.get("contact_id", ""),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"link": link})
            return

        if path == "/api/companies/unlink-contact":
            payload = self.read_json()
            try:
                link = company_store.unlink_contact(
                    company_id=payload.get("company_id", ""),
                    contact_id=payload.get("contact_id", ""),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"link": link})
            return

        if path == "/api/companies/candidates/update":
            payload = self.read_json()
            try:
                candidate = company_store.update_candidate_status(
                    candidate_id=payload.get("id", ""),
                    status=payload.get("status", ""),
                )
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"candidate": candidate})
            return

        if path == "/api/companies/candidates/ingest":
            payload = self.read_json()
            try:
                result = company_store.ingest_candidate(payload.get("id", ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json(result)
            return

        self.send_json({"error": "Not found"}, status=404)


def main(argv=None):
    argv = argv or sys.argv[1:]
    port = int(argv[0]) if argv else 8010
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"Serving Hunter at http://127.0.0.1:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
