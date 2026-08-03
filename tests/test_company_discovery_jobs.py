import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunter import company_discovery_jobs, paths


class HunterCompanyDiscoveryJobsTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_data_dir = paths.DATA_DIR
        paths.DATA_DIR = Path(self.tempdir.name) / "data"
        company_discovery_jobs._active_thread = None

    def tearDown(self):
        thread = company_discovery_jobs._active_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        company_discovery_jobs._active_thread = None
        paths.DATA_DIR = self.original_data_dir
        self.tempdir.cleanup()

    def test_background_job_persists_progress_and_custom_location_request(self):
        captured = {}

        def fake_discovery(**kwargs):
            captured.update(kwargs)
            kwargs["progress"](
                {
                    "phase": "searching",
                    "message": "Searching startup directory (1 of 1)…",
                    "completed_steps": 0,
                    "total_steps": 2,
                    "source": "startup-directories",
                }
            )
            return {
                "review_count": 1,
                "location_verification_count": 0,
                "companies": [],
                "location_verification_companies": [],
            }

        with patch("hunter.company_discovery_jobs.company_discovery.run_company_discovery", side_effect=fake_discovery):
            started = company_discovery_jobs.start_job(
                {
                    "focus": "workflow platforms",
                    "sizes": ["51–200 employees"],
                    "sources": ["startup-directories"],
                    "locations": ["us-remote", "metro-area"],
                    "remote_region": "Canada",
                    "metro_area": "Denver metro",
                }
            )
            company_discovery_jobs._active_thread.join(timeout=2)

        completed = company_discovery_jobs.current_job()
        self.assertEqual(started["status"], "queued")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"]["review_count"], 1)
        self.assertEqual(captured["remote_region"], "Canada")
        self.assertEqual(captured["metro_area"], "Denver metro")
        self.assertTrue(company_discovery_jobs.job_path().exists())


if __name__ == "__main__":
    unittest.main()
