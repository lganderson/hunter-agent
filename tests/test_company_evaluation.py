import tempfile
import unittest
from pathlib import Path

from hunter import companies, company_evaluation, paths, sqlite_store


class HunterCompanyEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.original_paths = {
            name: getattr(paths, name)
            for name in ["ROOT", "DATA_DIR", "SETTINGS_FILE", "SQLITE_DB"]
        }
        paths.ROOT = self.root
        paths.DATA_DIR = self.root / "data"
        paths.SETTINGS_FILE = paths.DATA_DIR / "settings.local.json"
        paths.SQLITE_DB = paths.DATA_DIR / "hunter.sqlite"
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        sqlite_store.initialize()

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        self.tempdir.cleanup()

    def test_shared_evaluator_persists_metadata_location_fit_and_version(self):
        company = companies.upsert_company(
            "",
            {
                "name": "Cobot",
                "tracking_status": "discovered",
            },
        )
        profile = {
            "focus": "robotics, customer deployment",
            "sizes": ["51–200 employees", "201–500 employees"],
            "locations": ["us-remote", "metro-area"],
            "remote_region": "United States",
            "metro_area": "Minneapolis-Saint Paul metro",
        }

        def evaluator(batch, current_profile, batch_number):
            self.assertEqual([row["id"] for row in batch], [company["id"]])
            self.assertEqual(current_profile["metro_area"], "Minneapolis-Saint Paul metro")
            self.assertEqual(batch_number, 1)
            return [
                {
                    "company_id": company["id"],
                    "name": "Cobot",
                    "website": "https://cobot.example/",
                    "careers_url": "https://cobot.example/careers",
                    "industry": "Automation Machinery Manufacturing",
                    "company_size": "51–200 employees",
                    "description": "Robotics deployment platform for technical customer teams.",
                    "location_fit": "us-remote",
                    "location": "United States",
                    "remote_policy": "Current engineering roles support U.S. remote work.",
                    "location_evidence": "The careers page lists remote roles in the United States.",
                    "source_urls": ["https://cobot.example/careers"],
                }
            ]

        result = company_evaluation.evaluate_companies(
            company_ids=[company["id"]],
            tracking_status="discovered",
            profile=profile,
            force=True,
            evaluator=evaluator,
            reason="test",
        )

        saved = companies.get_company(company["id"])
        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["ready_count"], 1)
        self.assertEqual(saved["website"], "https://cobot.example")
        self.assertEqual(saved["careers_url"], "https://cobot.example/careers")
        self.assertEqual(saved["company_size"], "51–200 employees")
        self.assertEqual(saved["company_location_fit"], "us-remote")
        self.assertGreaterEqual(int(saved["company_fit_score"]), 65)
        self.assertEqual(saved["company_evaluation_status"], "ready")
        self.assertEqual(saved["company_evaluation_version"], result["evaluation_version"])
        self.assertTrue(saved["company_evaluation_checked_at"])

    def test_current_evaluation_is_not_requeued_without_force(self):
        company = companies.upsert_company(
            "",
            {
                "name": "Ready Tools",
                "tracking_status": "discovered",
            },
        )
        profile = company_evaluation.save_profile(company_evaluation.default_profile())
        rows = sqlite_store.read_companies()
        rows[0]["company_evaluation_status"] = "ready"
        rows[0]["company_evaluation_version"] = company_evaluation.evaluation_version(profile)
        rows[0]["company_evaluation_checked_at"] = company_evaluation.now_iso()
        sqlite_store.write_companies(rows)

        self.assertEqual(company_evaluation.mark_pending([company["id"]], profile=profile), [])
        self.assertEqual(
            company_evaluation.mark_pending([company["id"]], profile=profile, force=True),
            [company["id"]],
        )
        self.assertEqual(companies.get_company(company["id"])["company_evaluation_status"], "pending")

    def test_evaluator_retries_a_failed_batch_once(self):
        company = companies.upsert_company(
            "",
            {"name": "Retry Labs", "tracking_status": "discovered"},
        )
        attempts = []

        def evaluator(_batch, _profile, _batch_number):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("temporary structured output failure")
            return [
                {
                    "company_id": company["id"],
                    "name": "Retry Labs",
                    "website": "https://retry.example",
                    "careers_url": "",
                    "industry": "Software Development",
                    "company_size": "51–200 employees",
                    "description": "Workflow software.",
                    "location_fit": "unknown",
                    "location": "",
                    "remote_policy": "",
                    "location_evidence": "",
                    "source_urls": ["https://retry.example"],
                }
            ]

        result = company_evaluation.evaluate_companies(
            company_ids=[company["id"]],
            profile=company_evaluation.default_profile(),
            force=True,
            evaluator=evaluator,
        )

        self.assertEqual(len(attempts), 2)
        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["failed_count"], 0)


if __name__ == "__main__":
    unittest.main()
