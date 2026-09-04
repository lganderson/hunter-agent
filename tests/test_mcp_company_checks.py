import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hunter import mcp_server


class McpCompanyChecksTest(unittest.TestCase):
    def test_bulk_company_check_isolates_timeout_and_continues_without_retry_by_default(self):
        companies = [
            {
                "id": "CO0001",
                "name": "Stuck",
                "tracking_status": "tracked",
                "interest_status": "interested",
                "careers_url": "https://stuck.example/jobs",
            },
            {
                "id": "CO0002",
                "name": "Healthy",
                "tracking_status": "tracked",
                "interest_status": "neutral",
                "careers_url": "https://healthy.example/jobs",
            },
            {
                "id": "CO0003",
                "name": "Excluded",
                "tracking_status": "tracked",
                "interest_status": "not-interested",
                "careers_url": "https://excluded.example/jobs",
            },
            {
                "id": "CO0004",
                "name": "Missing URL",
                "tracking_status": "tracked",
                "interest_status": "interested",
                "careers_url": "",
            },
            {
                "id": "CO0005",
                "name": "Archived",
                "tracking_status": "tracked",
                "interest_status": "archived",
                "careers_url": "https://archived.example/jobs",
            },
        ]
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if "CO0001" in command:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "id": "CO0002",
                    "name": "Healthy",
                    "status": "completed",
                    "new_count": 2,
                    "recommended_count": 1,
                    "errors": [],
                }),
                stderr="",
            )

        with (
            patch("hunter.mcp_server.company_store.list_companies", return_value=companies),
            patch("hunter.mcp_server.subprocess.run", side_effect=run),
        ):
            response = mcp_server.call_named_tool(
                "hunter_check_tracked_company_postings",
                {"timeout_seconds": 5},
            )

        payload = json.loads(response["content"][0]["text"])
        self.assertEqual(payload["completed_count"], 1)
        self.assertEqual(payload["failed_count"], 1)
        self.assertEqual(payload["excluded_company_count"], 2)
        self.assertEqual(payload["missing_careers_url_count"], 1)
        self.assertEqual(payload["new_count"], 2)
        self.assertEqual(payload["recommended_count"], 1)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[1]["timeout"] == 5 for call in calls))
        self.assertEqual(payload["results"][0]["attempt_count"], 1)

    def test_bulk_company_check_retries_only_when_requested(self):
        companies = [{
            "id": "CO0001",
            "name": "Retry",
            "tracking_status": "tracked",
            "interest_status": "interested",
            "careers_url": "https://retry.example/jobs",
        }]
        with (
            patch("hunter.mcp_server.company_store.list_companies", return_value=companies),
            patch(
                "hunter.mcp_server.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["hunter"], 5),
            ) as run,
        ):
            response = mcp_server.call_named_tool(
                "hunter_check_tracked_company_postings",
                {"timeout_seconds": 5, "retry_count": 1},
            )

        payload = json.loads(response["content"][0]["text"])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(payload["results"][0]["attempt_count"], 2)


if __name__ == "__main__":
    unittest.main()
