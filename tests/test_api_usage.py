import json
import tempfile
import unittest
from pathlib import Path

from hunter import api_usage, paths


class HunterApiUsageTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_data_dir = paths.DATA_DIR
        paths.DATA_DIR = Path(self.tempdir.name) / "data"

    def tearDown(self):
        paths.DATA_DIR = self.original_data_dir
        self.tempdir.cleanup()

    def test_usage_is_aggregated_by_feature_and_preserves_legacy_rows(self):
        response = {
            "id": "resp_123",
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 40},
                "output_tokens": 20,
                "output_tokens_details": {"reasoning_tokens": 5},
                "total_tokens": 120,
            },
            "output": [{"type": "web_search_call"}],
        }
        api_usage.log_usage(
            "company-discovery",
            "gpt-test",
            response,
            operation="startup-directories",
            context={"search_id": "DS0005", "role_family_id": "product-platform"},
        )
        with (paths.DATA_DIR / api_usage.USAGE_LOG_FILE).open(encoding="utf-8") as handle:
            logged = json.loads(handle.readline())
        self.assertEqual(logged["response_id"], "resp_123")
        self.assertEqual(
            logged["context"],
            {"role_family_id": "product-platform", "search_id": "DS0005"},
        )
        with (paths.DATA_DIR / api_usage.USAGE_LOG_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}) + "\n")

        summary = api_usage.usage_summary()

        self.assertEqual(summary["totals"]["request_count"], 2)
        self.assertEqual(summary["totals"]["total_tokens"], 132)
        self.assertEqual(summary["features"][0]["feature"], "company-discovery")
        self.assertEqual(summary["features"][0]["web_search_call_count"], 1)
        self.assertEqual(summary["features"][1]["feature"], "unattributed")


if __name__ == "__main__":
    unittest.main()
