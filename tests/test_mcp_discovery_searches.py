import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hunter import mcp_server


class McpDiscoverySearchesTest(unittest.TestCase):
    def test_candidate_status_label_uses_considering_for_legacy_storage_value(self):
        self.assertEqual(
            mcp_server.compact_discovery_candidate({"status": "pursued"})["status_label"],
            "Considering",
        )

    def test_list_searches_returns_configured_searches(self):
        search = {
            "id": "DS0001",
            "name": "Product strategy",
            "keywords": "product strategy",
            "role_family_ids": ["product-strategy"],
            "lanes": [{"id": "remote", "label": "US remote"}],
            "last_run_at": "2026-09-01T08:00:00",
            "last_run_summary": {"new_count": 2},
        }
        with patch("hunter.mcp_server.discovery_store.list_searches", return_value=[search]):
            result = mcp_server.call_named_tool("hunter_list_discovery_searches", {})

        payload = json.loads(result["content"][0]["text"])
        self.assertIn("hunter_list_discovery_searches", mcp_server.TOOLS)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["searches"][0]["id"], "DS0001")

    def test_run_search_uses_saved_configuration_and_returns_compact_result(self):
        search = {"id": "DS0001", "name": "Product strategy", "lanes": []}
        result = {
            "search": search,
            "new_count": 2,
            "updated_count": 1,
            "associated_count": 0,
            "duplicate_count": 1,
            "evaluated_count": 5,
            "known_count": 2,
            "screened_count": 1,
            "needs_details_count": 1,
            "enrichment": {"ready_count": 1},
            "sources": [{"source": "ats-inventory"}],
            "errors": [],
            "captured": [{"id": "DC0001", "title": "Product Strategist"}],
        }
        with patch("hunter.mcp_server.discovery_store.continue_discovery", return_value=result) as run:
            response = mcp_server.call_named_tool(
                "hunter_run_discovery_search",
                {"id": "DS0001", "enrichment_limit": 25},
            )

        payload = json.loads(response["content"][0]["text"])
        self.assertIn("hunter_run_discovery_search", mcp_server.TOOLS)
        run.assert_called_once_with(
            "DS0001",
            enrichment_limit=25,
        )
        self.assertEqual(payload["new_count"], 2)
        self.assertEqual(payload["captured"][0]["id"], "DC0001")

    def test_run_searches_isolates_timeout_retries_and_continues(self):
        searches = [
            {"id": "DS0001", "name": "Stuck search"},
            {"id": "DS0002", "name": "Healthy search"},
        ]
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if "DS0001" in command:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "id": "DS0002",
                    "name": "Healthy search",
                    "status": "completed",
                    "evaluated_count": 4,
                    "new_count": 1,
                    "errors": [],
                }),
                stderr="",
            )

        with (
            patch("hunter.mcp_server.discovery_store.list_searches", return_value=searches),
            patch("hunter.mcp_server.subprocess.run", side_effect=run),
        ):
            response = mcp_server.call_named_tool(
                "hunter_run_discovery_searches",
                {"timeout_seconds": 5, "retry_count": 1},
            )

        payload = json.loads(response["content"][0]["text"])
        self.assertEqual(payload["search_count"], 2)
        self.assertEqual(payload["completed_count"], 1)
        self.assertEqual(payload["failed_count"], 1)
        self.assertEqual(payload["results"][0]["status"], "timed-out")
        self.assertEqual(payload["results"][0]["attempt_count"], 2)
        self.assertEqual(payload["results"][1]["status"], "completed")
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call[1]["timeout"] == 5 for call in calls))

    def test_run_searches_does_not_replay_a_timed_out_paid_search_by_default(self):
        searches = [{"id": "DS0001", "name": "Stuck search"}]
        with (
            patch("hunter.mcp_server.discovery_store.list_searches", return_value=searches),
            patch(
                "hunter.mcp_server.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["hunter"], 5),
            ) as run,
        ):
            response = mcp_server.call_named_tool(
                "hunter_run_discovery_searches",
                {"timeout_seconds": 5},
            )

        payload = json.loads(response["content"][0]["text"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(payload["results"][0]["attempt_count"], 1)

    def test_refresh_existing_candidates_does_not_run_acquisition_and_defaults_to_full_backlog(self):
        result = {
            "target_count": 12,
            "processed_count": 12,
            "ready_count": 9,
            "remaining_count": 3,
            "errors": [],
        }
        with patch(
            "hunter.mcp_server.discovery_store.enrich_candidate_backlog",
            return_value=result,
        ) as refresh:
            response = mcp_server.call_named_tool(
                "hunter_refresh_discovery_candidates",
                {},
            )

        payload = json.loads(response["content"][0]["text"])
        refresh.assert_called_once_with(candidate_id="", limit=0)
        self.assertEqual(payload["processed_count"], 12)
        self.assertIn("hunter_consider_discovery_candidate", mcp_server.TOOLS)
        self.assertIn("hunter_consider_company_candidate", mcp_server.TOOLS)

    def test_refresh_existing_candidates_can_target_one_candidate(self):
        with patch(
            "hunter.mcp_server.discovery_store.enrich_candidate_backlog",
            return_value={"target_count": 1, "processed_count": 1, "errors": []},
        ) as refresh:
            mcp_server.call_named_tool(
                "hunter_refresh_discovery_candidates",
                {"id": "dc0042", "limit": 1},
            )

        refresh.assert_called_once_with(candidate_id="DC0042", limit=1)


if __name__ == "__main__":
    unittest.main()
