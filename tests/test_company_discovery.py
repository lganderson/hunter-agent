import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunter import companies, company_discovery, paths, repository, sqlite_store


class HunterCompanyDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.original_paths = {
            name: getattr(paths, name)
            for name in [
                "ROOT",
                "DATA_DIR",
                "FRONTEND_DIR",
                "FRONTEND_DIST",
                "OUTPUT_FILE",
                "EXPORTS_DIR",
                "SETTINGS_FILE",
                "SQLITE_DB",
                "APPLICATIONS",
                "CONTACTS",
                "INTERVIEWS",
                "ACTIONS",
            ]
        }
        paths.ROOT = self.root
        paths.DATA_DIR = self.root / "data"
        paths.FRONTEND_DIR = self.root / "app"
        paths.FRONTEND_DIST = paths.FRONTEND_DIR / "dist"
        paths.OUTPUT_FILE = paths.FRONTEND_DIST / "index.html"
        paths.EXPORTS_DIR = self.root / "exports"
        paths.SETTINGS_FILE = paths.DATA_DIR / "settings.local.json"
        paths.SQLITE_DB = paths.DATA_DIR / "hunter.sqlite"
        paths.APPLICATIONS = paths.DATA_DIR / "applications.csv"
        paths.CONTACTS = paths.DATA_DIR / "contacts.csv"
        paths.INTERVIEWS = paths.DATA_DIR / "interviews.csv"
        paths.ACTIONS = paths.DATA_DIR / "actions.csv"
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        sqlite_store.initialize()

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        self.tempdir.cleanup()

    def test_run_records_small_company_for_review_with_source_backed_fit(self):
        search_calls = []
        progress_events = []

        def searcher(engine, query, page):
            search_calls.append((engine, query, page))
            return [
                {
                    "url": "https://wellfound.com/company/cobot",
                    "title": "Cobot | Wellfound",
                    "snippet": "Robotics platform for customer deployment workflows.",
                }
            ]

        def researcher(name, profile_url):
            self.assertEqual(name, "Cobot")
            self.assertEqual(profile_url, "")
            return {
                "company": "Cobot, Inc.",
                "company_industry": "Automation Machinery Manufacturing",
                "company_size": "51-200 employees",
                "company_profile_url": "https://www.linkedin.com/company/cobot",
                "website": "https://www.cobot.co/",
                "company_location_fit": "us-remote",
                "company_location": "United States",
                "company_remote_policy": "Supports U.S. remote employees.",
                "company_location_evidence": "The company profile lists U.S. remote work.",
            }

        result = company_discovery.run_company_discovery(
            focus="robotics, customer deployment",
            sizes=["51–200 employees", "201–500 employees"],
            sources=["startup-directories"],
            searcher=searcher,
            researcher=researcher,
            progress=progress_events.append,
        )

        self.assertEqual(search_calls[0][0], "google")
        self.assertIn("wellfound.com/company", search_calls[0][1])
        self.assertEqual(result["new_count"], 1)
        self.assertEqual(result["review_count"], 1)
        company = repository.read_companies()[0]
        self.assertEqual(company["name"], "Cobot")
        self.assertEqual(company["tracking_status"], "discovered")
        self.assertEqual(company["company_size"], "51–200 employees")
        self.assertEqual(company["company_discovery_source"], "Startup directory")
        self.assertEqual(company["company_discovery_source_url"], "https://wellfound.com/company/cobot")
        self.assertGreaterEqual(int(company["company_fit_score"]), 70)
        self.assertIn("robotics", company["company_fit_summary"])
        self.assertEqual(progress_events[0]["phase"], "preparing")
        self.assertEqual(progress_events[-1]["phase"], "complete")

    def test_run_skips_larger_and_not_interested_companies(self):
        companies.upsert_company(
            "",
            {"name": "Nope Labs", "interest_status": "not-interested"},
        )

        def searcher(_engine, _query, _page):
            return [
                {
                    "url": "https://wellfound.com/company/huge-cloud",
                    "title": "Huge Cloud | Wellfound",
                    "snippet": "Developer platform.",
                },
                {
                    "url": "https://wellfound.com/company/nope-labs",
                    "title": "Nope Labs | Wellfound",
                    "snippet": "Developer tools.",
                },
            ]

        def researcher(name, _profile_url):
            if name == "Huge Cloud":
                return {"company_size": "10,001+ employees"}
            return {"company_size": "51–200 employees"}

        result = company_discovery.run_company_discovery(
            focus="developer tools",
            sizes=["51–200 employees"],
            sources=["startup-directories"],
            searcher=searcher,
            researcher=researcher,
        )

        self.assertEqual(result["review_count"], 0)
        self.assertEqual(result["skipped_size_count"], 1)
        self.assertEqual(result["skipped_not_interested_count"], 1)
        self.assertEqual([row["name"] for row in repository.read_companies()], ["Nope Labs"])

    def test_company_name_parser_rejects_collection_pages(self):
        self.assertEqual(
            company_discovery.company_name_from_result(
                {"title": "Jobs at Aalyria | Techstars"},
                "venture-portfolios",
            ),
            "Aalyria",
        )
        self.assertEqual(
            company_discovery.company_name_from_result(
                {"title": "Companies"},
                "startup-directories",
            ),
            "",
        )
        self.assertEqual(
            company_discovery.company_name_from_result(
                {"title": "Augmented Robotics Careers - Insights and Opportunities"},
                "startup-directories",
            ),
            "Augmented Robotics",
        )
        self.assertEqual(
            company_discovery.company_name_from_result(
                {"title": "Path Robotics, Inc (We're Hiring!) Careers"},
                "startup-directories",
            ),
            "Path Robotics, Inc",
        )
        self.assertFalse(
            company_discovery.likely_company_profile(
                "startup-directories",
                "https://wellfound.com/company/watney-robotics/jobs",
            )
        )

    def test_openai_search_uses_balanced_lanes_and_logs_feature_usage(self):
        captured = {}

        def fake_request(url, token, payload):
            captured.update({"url": url, "token": token, "payload": payload})
            return {
                "model": "gpt-test",
                "output_text": '{"companies":[]}',
                "output": [{"type": "web_search_call"}],
                "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
            }

        with patch("hunter.company_discovery.agent._request_json", side_effect=fake_request):
            rows = company_discovery.openai_source_search(
                {"api_base": "https://example.test/v1", "token": "private", "model": "gpt-test"},
                "startup-directories",
                ["games", "robotics"],
                ["51–200 employees"],
                ["us-remote", "metro-area"],
                "Canada",
                "Denver metro",
            )

        self.assertEqual(rows, [])
        self.assertEqual(captured["token"], "private")
        self.assertEqual(captured["payload"]["tools"][0]["type"], "web_search")
        self.assertEqual(captured["payload"]["model"], "gpt-5.6-luna")
        self.assertEqual(captured["payload"]["max_tool_calls"], 5)
        self.assertEqual(captured["payload"]["reasoning"], {"effort": "low"})
        self.assertEqual(captured["payload"]["tools"][0]["search_context_size"], "medium")
        self.assertIn("wellfound.com", captured["payload"]["tools"][0]["filters"]["allowed_domains"])
        self.assertIn("- games: at most 4", captured["payload"]["input"])
        self.assertIn("Remote in Canada", captured["payload"]["input"])
        self.assertIn("Denver metro", captured["payload"]["input"])
        usage = json.loads((paths.DATA_DIR / "agent_usage.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(usage["feature"], "company-discovery")
        self.assertEqual(usage["operation"], "startup-directories")

    def test_openai_focus_planning_expands_broad_input_and_logs_usage(self):
        captured = {}

        def fake_request(url, token, payload):
            captured.update({"url": url, "token": token, "payload": payload})
            return {
                "model": "gpt-test",
                "output_text": json.dumps(
                    {
                        "focus_lanes": [
                            "AI product platforms",
                            "AI infrastructure and tooling",
                            "Intelligent enterprise workflows",
                            "Automation and decision systems",
                        ]
                    }
                ),
                "usage": {"input_tokens": 70, "output_tokens": 30, "total_tokens": 100},
            }

        with patch("hunter.company_discovery.agent._request_json", side_effect=fake_request):
            lanes = company_discovery.openai_focus_lane_search(
                {"api_base": "https://example.test/v1", "token": "private"},
                ["ai"],
            )

        self.assertEqual(len(lanes), 4)
        self.assertEqual(lanes[0], "ai product platforms")
        self.assertNotIn("tools", captured["payload"])
        self.assertEqual(captured["payload"]["metadata"]["source"], "focus-planning")
        usage = json.loads((paths.DATA_DIR / "agent_usage.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(usage["feature"], "company-discovery")
        self.assertEqual(usage["operation"], "focus-planning")

    def test_direct_employer_source_accepts_official_and_ats_pages(self):
        self.assertTrue(
            company_discovery.likely_company_profile(
                "direct-employers",
                "https://example.com/careers",
            )
        )
        self.assertTrue(
            company_discovery.likely_company_profile(
                "direct-employers",
                "https://jobs.ashbyhq.com/example/role-id",
            )
        )
        self.assertFalse(
            company_discovery.likely_company_profile(
                "direct-employers",
                "https://www.indeed.com/jobs?q=ai",
            )
        )

    def test_candidate_batch_round_robins_sources_before_global_limit(self):
        candidates = [
            {"company": f"Direct {index}", "source_id": "direct-employers"}
            for index in range(5)
        ] + [
            {"company": f"Startup {index}", "source_id": "startup-directories"}
            for index in range(5)
        ]

        batch = company_discovery.balanced_candidate_batch(
            candidates,
            ["direct-employers", "startup-directories"],
            limit=4,
        )

        self.assertEqual(
            [item["company"] for item in batch],
            ["Direct 0", "Startup 0", "Direct 1", "Startup 1"],
        )

    def test_openai_website_lookup_keeps_only_official_company_sites(self):
        captured = {}

        def fake_request(url, token, payload):
            captured.update({"url": url, "token": token, "payload": payload})
            return {
                "model": "gpt-test",
                "output_text": json.dumps(
                    {
                        "companies": [
                            {"name": "Cobot", "website": "https://www.cobot.co/"},
                            {"name": "Unknown Place", "website": "https://www.linkedin.com/company/unknown-place"},
                        ]
                    }
                ),
                "output": [{"type": "web_search_call"}],
                "usage": {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100},
            }

        with patch("hunter.company_discovery.agent._request_json", side_effect=fake_request):
            websites = company_discovery.openai_company_website_search(
                {"api_base": "https://example.test/v1", "token": "private"},
                [{"company": "Cobot"}, {"company": "Unknown Place"}],
            )

        self.assertEqual(websites[companies.company_merge_key("Cobot")], "https://www.cobot.co")
        self.assertEqual(websites[companies.company_merge_key("Unknown Place")], "")
        self.assertIn("official public homepage", captured["payload"]["input"])
        usage = json.loads((paths.DATA_DIR / "agent_usage.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(usage["feature"], "company-discovery")
        self.assertEqual(usage["operation"], "website-lookup")

    def test_openai_location_research_is_source_backed_and_logs_separate_usage(self):
        captured = {}

        def fake_request(url, token, payload):
            captured.update({"url": url, "token": token, "payload": payload})
            return {
                "model": "gpt-test",
                "output_text": json.dumps(
                    {
                        "companies": [
                            {
                                "name": "Cobot",
                                "location_fit": "us-remote",
                                "location": "United States",
                                "remote_policy": "Current U.S.-remote roles.",
                                "location_evidence": "The careers page lists remote U.S. roles.",
                                "source_urls": ["https://www.cobot.co/careers"],
                            }
                        ]
                    }
                ),
                "output": [{"type": "web_search_call"}],
                "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
            }

        with patch("hunter.company_discovery.agent._request_json", side_effect=fake_request):
            rows = company_discovery.openai_company_location_search(
                {"api_base": "https://example.test/v1", "token": "private"},
                [{"name": "Cobot", "website": "https://www.cobot.co"}],
                "United States",
                "Minneapolis-Saint Paul metro",
                2,
            )

        self.assertEqual(rows[0]["location_fit"], "us-remote")
        self.assertEqual(captured["payload"]["model"], "gpt-5.6-luna")
        self.assertEqual(captured["payload"]["max_tool_calls"], 10)
        self.assertIn("Minneapolis-Saint Paul metro", captured["payload"]["input"])
        usage = json.loads((paths.DATA_DIR / "agent_usage.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(usage["feature"], "company-location-research")
        self.assertEqual(usage["operation"], "batch-2")

    def test_research_tracked_locations_preserves_other_company_evidence(self):
        remote = companies.upsert_company(
            "",
            {
                "name": "Remote Tools",
                "tracking_status": "tracked",
            },
        )
        company_discovery.update_discovery_evidence(
            remote["id"],
            {"company_discovery_evidence": "Existing fit evidence"},
        )
        known = companies.upsert_company(
            "",
            {
                "name": "Known Local",
                "tracking_status": "tracked",
            },
        )
        company_discovery.update_company_location_evidence(
            known["id"],
            {"company_location_fit": "metro-area"},
        )
        discovered = companies.upsert_company(
            "",
            {"name": "Discovered Only", "tracking_status": "discovered"},
        )

        def searcher(batch, remote_region, metro_area):
            self.assertEqual([row["name"] for row in batch], ["Remote Tools"])
            self.assertEqual(remote_region, "United States")
            self.assertEqual(metro_area, "Minneapolis-Saint Paul metro")
            return [
                {
                    "name": "Remote Tools",
                    "location_fit": "us-remote",
                    "location": "United States",
                    "remote_policy": "Remote across the United States.",
                    "location_evidence": "The official careers page supports U.S. remote work.",
                    "source_urls": ["https://remote-tools.example/careers"],
                }
            ]

        result = company_discovery.research_tracked_company_locations(searcher=searcher)

        self.assertEqual(result["tracked_count"], 2)
        self.assertEqual(result["target_count"], 1)
        self.assertEqual(result["updated_count"], 1)
        updated = companies.get_company(remote["id"])
        self.assertEqual(updated["company_location_fit"], "us-remote")
        self.assertEqual(updated["company_discovery_evidence"], "Existing fit evidence")
        self.assertIn("https://remote-tools.example/careers", updated["company_location_evidence"])
        self.assertEqual(companies.get_company(known["id"])["company_location_fit"], "metro-area")
        self.assertEqual(companies.get_company(discovered["id"])["company_location_fit"], "")

    def test_location_research_keeps_unsubstantiated_fit_in_verification(self):
        company = companies.upsert_company(
            "",
            {"name": "Unclear Company", "tracking_status": "tracked"},
        )

        result = company_discovery.research_tracked_company_locations(
            searcher=lambda *_args: [
                {
                    "name": "Unclear Company",
                    "location_fit": "us-remote",
                    "location": "",
                    "remote_policy": "",
                    "location_evidence": "A profile says remote.",
                    "source_urls": [],
                }
            ]
        )

        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(result["needs_verification_count"], 1)
        saved = companies.get_company(company["id"])
        self.assertEqual(saved["company_location_fit"], "")
        self.assertTrue(saved["company_location_checked_at"])

    def test_openai_run_requires_location_evidence_and_persists_fit(self):
        api_rows = [
            {
                "name": "Remote Tools",
                "source_url": "https://wellfound.com/company/remote-tools",
                "focus_lane": "builder productivity",
                "evidence": "Workflow platform for technical teams.",
                "company_size": "51-200 employees",
                "industry": "Software",
                "description": "Builder workflow platform.",
                "website": "https://remote-tools.example",
                "company_profile_url": "",
                "location_fit": "us-remote",
                "location": "United States",
                "remote_policy": "Remote-first across the United States.",
                "location_evidence": "The company profile says the team works remotely across the U.S.",
            },
            {
                "name": "Unknown Place",
                "source_url": "https://wellfound.com/company/unknown-place",
                "focus_lane": "builder productivity",
                "evidence": "Workflow platform.",
                "company_size": "51-200 employees",
                "industry": "Software",
                "description": "Workflow platform.",
                "website": "",
                "company_profile_url": "",
                "location_fit": "unknown",
                "location": "",
                "remote_policy": "",
                "location_evidence": "",
            },
        ]

        with patch(
            "hunter.company_discovery.agent._settings",
            return_value={"api_base": "https://example.test/v1", "token": "private", "model": "gpt-test"},
        ), patch(
            "hunter.company_discovery.openai_focus_lane_search",
            return_value=["builder productivity", "developer workflows", "technical collaboration"],
        ), patch("hunter.company_discovery.openai_source_search", return_value=api_rows), patch(
            "hunter.company_discovery.openai_company_website_search",
            return_value={companies.company_merge_key("Unknown Place"): "https://unknown-place.example/"},
        ):
            result = company_discovery.run_company_discovery(
                focus="builder productivity",
                sizes=["51–200 employees"],
                sources=["startup-directories"],
                locations=["us-remote", "metro-area"],
            )

        self.assertEqual(result["review_count"], 1)
        self.assertEqual(result["location_verification_count"], 1)
        self.assertEqual(result["locations"], ["us-remote", "metro-area"])
        self.assertEqual([row["name"] for row in result["location_verification_companies"]], ["Unknown Place"])
        company = next(row for row in repository.read_companies() if row["name"] == "Remote Tools")
        self.assertEqual(company["name"], "Remote Tools")
        self.assertEqual(company["company_location_fit"], "us-remote")
        self.assertEqual(company["company_location"], "United States")
        self.assertIn("remotely", company["company_location_evidence"])
        self.assertIn("Remote in United States", company["company_fit_summary"])
        unknown = next(row for row in repository.read_companies() if row["name"] == "Unknown Place")
        self.assertEqual(unknown["company_location_fit"], "")
        self.assertEqual(unknown["website"], "https://unknown-place.example")


if __name__ == "__main__":
    unittest.main()
