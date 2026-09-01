import json
import unittest
from unittest.mock import patch

from hunter import mcp_server


class McpDiscoverySearchesTest(unittest.TestCase):
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
            use_browser_fallback=False,
        )
        self.assertEqual(payload["new_count"], 2)
        self.assertEqual(payload["captured"][0]["id"], "DC0001")


if __name__ == "__main__":
    unittest.main()
