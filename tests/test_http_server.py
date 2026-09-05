import http.client
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts import serve_app


class QuietAppHandler(serve_app.AppHandler):
    def log_message(self, format, *args):  # noqa: A002 - stdlib handler API.
        return


class HunterHttpServerTest(unittest.TestCase):
    def test_get_and_provider_validation_share_json_client_errors(self):
        cases = [
            ("GET", "/api/companies/export", serve_app.company_store, "write_company_export", {}),
            ("POST", "/api/resumes/plan", serve_app.resume_store, "propose_changes", {"application_id": "missing"}),
        ]
        for method, path, module, name, body in cases:
            with self.subTest(path=path), patch.object(module, name, side_effect=ValueError("Invalid selection")):
                status, _, payload = self.json_request(method, path, json.dumps(body).encode(), {"Content-Type": "application/json"})
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"], "Invalid selection")

    def test_invalid_nested_updates_return_json_and_do_not_mutate(self):
        for updates in [["bad"], {"notes": []}, {"lanes": ["bad"]}]:
            with self.subTest(updates=updates), patch.object(serve_app.contact_store, "upsert_contact") as save:
                status, _, payload = self.json_request(
                    "POST", "/api/contacts/upsert", json.dumps({"updates": updates}).encode(),
                    {"Content-Type": "application/json"},
                )
                self.assertEqual(status, 400)
                self.assertIn("request.updates", payload["error"])
                save.assert_not_called()

    def test_invalid_contact_date_returns_json_validation_error(self):
        with patch.object(serve_app.repository, "read_contacts", return_value=[]), patch.object(serve_app.repository, "save_contacts_changes") as save:
            status, _, payload = self.json_request(
                "POST", "/api/contacts/upsert",
                json.dumps({"updates": {"name": "Synthetic Contact", "next_follow_up": "bad-date"}}).encode(),
                {"Content-Type": "application/json"},
            )
        self.assertEqual(status, 400)
        self.assertIn("Invalid date", payload["error"])
        save.assert_not_called()

    def test_expired_cursor_has_a_distinct_recovery_code(self):
        def expired(_query):
            raise serve_app.read_models.ReadModelError(409, "Reload the first page.", "cursor_expired")
        with patch.dict(serve_app.read_models.READ_MODEL_GET_ROUTES, {"/api/candidates/company": expired}):
            status, _, payload = self.json_request("GET", "/api/candidates/company?cursor=stale")
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "cursor_expired")

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietAppHandler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, body=b"", headers=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.putrequest(method, path, skip_host=True)
        connection.putheader("Host", host if host is not None else f"127.0.0.1:{self.port}")
        supplied_headers = dict(headers or {})
        if body and "Content-Length" not in supplied_headers:
            supplied_headers["Content-Length"] = str(len(body))
        for name, value in supplied_headers.items():
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        connection.close()
        return result

    def json_request(self, method, path, body=b"", headers=None, host=None):
        status, response_headers, payload = self.request(method, path, body, headers, host)
        return status, response_headers, json.loads(payload.decode("utf-8"))

    def test_health_is_data_free_and_sets_local_response_headers(self):
        status, headers, payload = self.json_request("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"service": "hunter", "status": "ok"})
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_json_responses_use_compact_wire_encoding(self):
        status, _, payload = self.request("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload, b'{"service":"hunter","status":"ok"}')

    def test_registered_read_model_route_receives_parsed_query(self):
        with patch.dict(
            serve_app.read_models.READ_MODEL_GET_ROUTES,
            {"/api/test-read-model": lambda query: {"query": query, "revision": 7}},
        ):
            status, _, payload = self.json_request(
                "GET", "/api/test-read-model?status=new&status=pursued"
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["revision"], 7)
        self.assertEqual(payload["query"]["status"], ["new", "pursued"])

    def test_registered_read_model_errors_are_structured(self):
        def fail(_query):
            raise serve_app.read_models.ReadModelError(409, "Reload the first page.")

        with patch.dict(
            serve_app.read_models.READ_MODEL_GET_ROUTES,
            {"/api/test-read-model-error": fail},
        ):
            status, _, payload = self.json_request("GET", "/api/test-read-model-error")

        self.assertEqual(status, 409)
        self.assertEqual(payload, {"error": "Reload the first page."})

    def test_unknown_api_get_returns_structured_json_404(self):
        status, headers, payload = self.json_request("GET", "/api/typo")

        self.assertEqual(status, 404)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(payload, {"error": "Unknown API endpoint: /api/typo"})

    def test_only_generated_ipv4_host_is_allowed(self):
        for host in [f"localhost:{self.port}", "attacker.invalid", "127.0.0.1", ""]:
            with self.subTest(host=host):
                status, _, payload = self.json_request("GET", "/api/health", host=host)
                self.assertEqual(status, 403)
                self.assertIn("generated local URL", payload["error"])

    def test_host_boundary_applies_before_frontend_serving(self):
        status, _, payload = self.json_request("GET", "/", host=f"localhost:{self.port}")

        self.assertEqual(status, 403)
        self.assertIn("generated local URL", payload["error"])

    def test_hostile_and_null_origins_are_rejected(self):
        for origin in ["https://attacker.invalid", "null", f"http://localhost:{self.port}"]:
            with self.subTest(origin=origin):
                status, _, payload = self.json_request(
                    "GET",
                    "/api/health",
                    headers={"Origin": origin},
                )
                self.assertEqual(status, 403)
                self.assertIn("Cross-origin", payload["error"])

    def test_expected_origin_is_allowed(self):
        status, _, payload = self.json_request(
            "GET",
            "/api/health",
            headers={"Origin": f"http://127.0.0.1:{self.port}"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_cross_site_fetch_metadata_is_rejected(self):
        status, _, payload = self.json_request(
            "GET",
            "/api/health",
            headers={"Sec-Fetch-Site": "cross-site"},
        )

        self.assertEqual(status, 403)
        self.assertIn("Cross-site", payload["error"])

    def test_origin_and_fetch_metadata_rejections_precede_mutation(self):
        cases = [
            {"Origin": "https://attacker.invalid"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
        ]
        with patch.object(serve_app.action_engine, "save_settings") as save_settings:
            for request_headers in cases:
                with self.subTest(headers=request_headers):
                    status, _, _ = self.json_request(
                        "POST",
                        "/api/settings",
                        body=b"{}",
                        headers={"Content-Type": "application/json", **request_headers},
                    )
                    self.assertEqual(status, 403)
        save_settings.assert_not_called()

    def test_all_posts_require_json_content_type_before_mutation(self):
        with patch.object(serve_app.action_engine, "save_settings") as save_settings:
            status, _, payload = self.json_request("POST", "/api/settings", body=b"{}")

        self.assertEqual(status, 415)
        self.assertIn("Content-Type", payload["error"])
        save_settings.assert_not_called()

    def test_json_media_type_allows_parameters(self):
        with patch.object(serve_app.action_engine, "save_settings", return_value={"saved": True}):
            status, _, payload = self.json_request(
                "POST",
                "/api/settings",
                body=b"{}",
                headers={"Content-Type": "application/json; charset=utf-8"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"saved": True})

    def test_malformed_invalid_utf8_and_non_object_json_return_400(self):
        cases = [b"{", b"\xff", b"[]"]
        with patch.object(serve_app.action_engine, "save_settings") as save_settings:
            for body in cases:
                with self.subTest(body=body):
                    status, _, payload = self.json_request(
                        "POST",
                        "/api/settings",
                        body=body,
                        headers={"Content-Type": "application/json"},
                    )
                    self.assertEqual(status, 400)
                    self.assertIn("error", payload)
        save_settings.assert_not_called()

    def test_invalid_content_lengths_return_structured_400(self):
        with patch.object(serve_app.action_engine, "save_settings") as save_settings:
            for content_length in ["invalid", "-1"]:
                with self.subTest(content_length=content_length):
                    status, _, payload = self.json_request(
                        "POST",
                        "/api/settings",
                        headers={"Content-Type": "application/json", "Content-Length": content_length},
                    )
                    self.assertEqual(status, 400)
                    self.assertIn("Content-Length", payload["error"])
        save_settings.assert_not_called()

    def test_default_body_limit_returns_413_before_reading(self):
        with patch.object(serve_app.action_engine, "save_settings") as save_settings:
            status, _, payload = self.json_request(
                "POST",
                "/api/settings",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(serve_app.DEFAULT_JSON_BODY_LIMIT + 1),
                },
            )

        self.assertEqual(status, 413)
        self.assertIn("exceeds", payload["error"])
        save_settings.assert_not_called()

    def test_resume_route_has_room_for_base64_encoded_upload(self):
        content_base64 = "A" * (serve_app.DEFAULT_JSON_BODY_LIMIT + 1)
        body = json.dumps({"filename": "resume.txt", "content_base64": content_base64}).encode("utf-8")
        with patch.object(
            serve_app.settings_store,
            "save_resume_upload",
            return_value={"resume": {"configured": True}},
        ) as save_resume_upload:
            status, _, payload = self.json_request(
                "POST",
                "/api/settings/resume",
                body=body,
                headers={"Content-Type": "application/json"},
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["resume"]["configured"])
        save_resume_upload.assert_called_once()

    def test_resume_route_still_has_a_bounded_request_size(self):
        with patch.object(serve_app.settings_store, "save_resume_upload") as save_resume_upload:
            status, _, payload = self.json_request(
                "POST",
                "/api/settings/resume",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(serve_app.RESUME_UPLOAD_JSON_BODY_LIMIT + 1),
                },
            )

        self.assertEqual(status, 413)
        self.assertIn("exceeds", payload["error"])
        save_resume_upload.assert_not_called()

    def test_server_remains_healthy_after_rejected_json(self):
        self.json_request(
            "POST",
            "/api/settings",
            body=b"{",
            headers={"Content-Type": "application/json"},
        )

        status, _, payload = self.json_request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
