import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunter import discovery_jobs, paths


class HunterDiscoveryJobsTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_data_dir = paths.DATA_DIR
        paths.DATA_DIR = Path(self.tempdir.name) / "data"
        discovery_jobs._active_thread = None

    def tearDown(self):
        thread = discovery_jobs._active_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        discovery_jobs._active_thread = None
        paths.DATA_DIR = self.original_data_dir
        self.tempdir.cleanup()

    def test_background_enrichment_persists_progress_and_result(self):
        result = {
            "target_count": 2,
            "processed_count": 2,
            "changed_count": 2,
            "ready_count": 1,
            "needs_input_count": 1,
            "remaining_count": 0,
            "manual_review_count": 1,
            "state_counts": {
                "ready": 1,
                "pending-enrichment": 0,
                "source-verification": 0,
                "needs-input": 1,
            },
            "errors": [],
        }

        def fake_enrichment(**kwargs):
            kwargs["progress"](
                {
                    "phase": "enriching",
                    "message": "Resolving candidate details 1 of 2…",
                    "completed_steps": 1,
                    "total_steps": 2,
                    "source": "greenhouse",
                }
            )
            return result

        with (
            patch("hunter.discovery_jobs.discovery.detail_enrichment_targets", return_value=[{}, {}]),
            patch("hunter.discovery_jobs.discovery.enrich_candidate_backlog", side_effect=fake_enrichment),
            patch(
                "hunter.company_discovery_jobs.enqueue_pending_evaluation",
                side_effect=RuntimeError("company worker unavailable"),
            ),
        ):
            started = discovery_jobs.start_job({"search_id": "DS0001", "limit": 100})
            discovery_jobs._active_thread.join(timeout=2)

        completed = discovery_jobs.current_job()
        self.assertEqual(started["status"], "queued")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"]["ready_count"], 1)
        self.assertIn("company worker unavailable", completed["result"]["errors"][0])
        self.assertIn("1 need manual review", completed["message"])
        self.assertTrue(discovery_jobs.job_path().exists())

    def test_background_enrichment_defaults_to_complete_backlog(self):
        target_count = 300
        result = {
            "target_count": target_count,
            "processed_count": target_count,
            "changed_count": target_count,
            "ready_count": target_count,
            "needs_input_count": 0,
            "remaining_count": 0,
            "state_counts": {"needs-input": 0},
            "errors": [],
        }

        def fake_enrichment(**kwargs):
            self.assertEqual(kwargs["candidate_id"], "")
            self.assertEqual(kwargs["limit"], 0)
            return result

        with (
            patch(
                "hunter.discovery_jobs.discovery.detail_enrichment_targets",
                return_value=[{} for _ in range(target_count)],
            ),
            patch(
                "hunter.discovery_jobs.discovery.enrich_candidate_backlog",
                side_effect=fake_enrichment,
            ),
            patch("hunter.company_discovery_jobs.enqueue_pending_evaluation"),
        ):
            started = discovery_jobs.start_job({})
            discovery_jobs._active_thread.join(timeout=2)

        self.assertEqual(started["request"]["limit"], 0)
        self.assertEqual(started["total_steps"], target_count)
        self.assertEqual(discovery_jobs.current_job()["status"], "completed")

    def test_background_search_uses_api_providers_and_persists_result(self):
        result = {
            "search": {"id": "DS0001"},
            "evaluated_count": 8,
            "new_count": 3,
            "updated_count": 1,
            "errors": [],
        }

        def fake_discovery(search_id, **kwargs):
            self.assertEqual(search_id, "DS0001")
            self.assertEqual(set(kwargs), {"enrichment_limit", "progress"})
            kwargs["progress"](
                {
                    "phase": "searching",
                    "message": "Searching direct company career sources…",
                    "completed_steps": 1,
                    "total_steps": 3,
                    "source": "direct-ats",
                }
            )
            return result

        with (
            patch("hunter.discovery_jobs.discovery.get_search", return_value={"id": "DS0001"}),
            patch("hunter.discovery_jobs.discovery.continue_discovery", side_effect=fake_discovery),
            patch("hunter.company_discovery_jobs.enqueue_pending_evaluation"),
        ):
            started = discovery_jobs.start_search_job({"search_id": "DS0001"})
            discovery_jobs._active_thread.join(timeout=2)

        completed = discovery_jobs.current_job()
        self.assertEqual(started["job_type"], "candidate-discovery")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"]["new_count"], 3)
        self.assertIn("3 new roles", completed["message"])


if __name__ == "__main__":
    unittest.main()
