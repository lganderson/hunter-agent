import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote, unquote_plus

from hunter import app_state, browser_discovery, discovery, paths, repository, sqlite_store


class HunterDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.original_paths = {
            name: getattr(paths, name)
            for name in [
                "ROOT",
                "DATA_DIR",
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
        paths.SETTINGS_FILE = paths.DATA_DIR / "settings.local.json"
        paths.SQLITE_DB = paths.DATA_DIR / "hunter.sqlite"
        paths.APPLICATIONS = paths.DATA_DIR / "applications.csv"
        paths.CONTACTS = paths.DATA_DIR / "contacts.csv"
        paths.INTERVIEWS = paths.DATA_DIR / "interviews.csv"
        paths.ACTIONS = paths.DATA_DIR / "actions.csv"
        sqlite_store.initialize()

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        self.tempdir.cleanup()

    def test_hunter_chrome_paces_consecutive_searches(self):
        delays = []
        browser = browser_discovery.HunterChrome(
            sleeper=delays.append,
            min_delay_seconds=1.25,
            max_delay_seconds=2.25,
            randomizer=lambda minimum, maximum: 1.75,
        )
        browser.window_id = "123"
        browser.last_search_at = browser_discovery.time.monotonic()
        browser._open_tab = lambda url: "456"
        browser._wait_until_ready = lambda tab_id: {"ready": "complete"}
        browser._execute = lambda tab_id, script: '{"blocked": false, "items": []}'
        browser._close_tab = lambda tab_id: None

        browser._search_tab("https://www.google.com/search?q=test", "ignored")

        self.assertEqual(len(delays), 1)
        self.assertGreater(delays[0], 1.25)
        self.assertLessEqual(delays[0], 1.75)

    def test_hunter_chrome_builds_second_google_and_linkedin_pages(self):
        opened_urls = []
        browser = browser_discovery.HunterChrome()
        browser._search_tab = (
            lambda url, extraction_script, scroll=False: opened_urls.append((url, scroll)) or []
        )

        browser.google("technical program manager", page=1)
        browser.linkedin(
            "https://www.linkedin.com/jobs/search/?keywords=technical+program+manager&location=Minnesota",
            page=1,
        )

        self.assertIn("start=10", opened_urls[0][0])
        self.assertFalse(opened_urls[0][1])
        self.assertIn("start=25", opened_urls[1][0])
        self.assertTrue(opened_urls[1][1])

    def test_candidate_ranking_prefers_verified_details_before_provisional_fit(self):
        candidates = [
            {"title": "Lower", "fit_score": "45", "processing_status": "ready", "description_text": "complete"},
            {"title": "Partial", "fit_score": "60", "processing_status": "partial", "description_text": "short"},
            {"title": "Strong", "fit_score": "90", "processing_status": "ready", "description_text": "complete"},
        ]

        ranked = sorted(candidates, key=discovery.candidate_rank_key, reverse=True)

        self.assertEqual([candidate["title"] for candidate in ranked], ["Strong", "Lower", "Partial"])

    def save_search(self, name, keywords, lanes=None):
        return discovery.upsert_search(
            "",
            {
                "name": name,
                "keywords": keywords,
                "lanes": lanes
                or [
                    {
                        "id": "default",
                        "label": "United States",
                        "location": "United States",
                        "work_modes": ["on-site", "hybrid", "remote"],
                    }
                ],
            },
        )

    def test_saved_search_roundtrips_and_builds_linkedin_url(self):
        search = self.save_search(
            "Platform leadership",
            "technical program manager developer tools",
            lanes=[
                {
                    "id": "minnesota",
                    "label": "Minnesota",
                    "location": "Minnesota",
                    "work_modes": ["on-site", "hybrid", "remote"],
                },
                {
                    "id": "national-remote",
                    "label": "U.S. remote",
                    "location": "United States",
                    "work_modes": ["remote"],
                },
            ],
        )

        opened = discovery.open_linkedin_search(search["id"])

        self.assertEqual(search["id"], "DS0001")
        self.assertNotIn("location", search)
        self.assertEqual(search["lanes"][0]["label"], "Minnesota")
        decoded_url = unquote_plus(opened["url"])
        self.assertIn('"technical program manager"', decoded_url)
        self.assertIn("developer tools", decoded_url)
        families = discovery.search_keyword_families(search)
        self.assertEqual([family["id"] for family in families], ["exact", "senior", "adjacent"])
        self.assertIn('"staff technical program manager"', families[1]["query"])
        self.assertIn('"technical project manager"', families[2]["query"])
        self.assertIn("location=Minnesota", opened["url"])
        self.assertEqual(len(opened["lanes"]), 2)
        self.assertIn("location=United+States", opened["lanes"][1]["url"])
        self.assertIn("f_WT=2", opened["lanes"][1]["url"])
        self.assertEqual(opened["lanes"][1]["work_modes"], ["remote"])
        self.assertTrue(opened["search"]["last_opened_at"])
        self.assertEqual(app_state.build_payload()["discovery_searches"][0]["name"], "Platform leadership")

    def test_search_now_runs_all_lanes_and_builtin_sources_without_source_configuration(self):
        search = self.save_search(
            "Minnesota plus remote",
            "technical program manager",
            lanes=[
                {
                    "id": "minnesota",
                    "label": "Minnesota",
                    "location": "Minnesota",
                    "work_modes": ["on-site", "hybrid", "remote"],
                },
                {
                    "id": "national-remote",
                    "label": "U.S. remote",
                    "location": "United States",
                    "work_modes": ["remote"],
                },
            ],
        )
        ashby_url = "https://jobs.ashbyhq.com/example/4e79049a-0372-4692-a0a7-61906fb12676"
        linkedin_url = "https://www.linkedin.com/jobs/view/technical-program-manager-1234567890"
        search_page = f"""
        <html><body><ol>
          <li><div class="dd algo algo-sr">
            <a href="https://r.search.yahoo.com/RU={quote(ashby_url, safe='')}/RK=2/RS=test">
              <h3>Senior Technical Program Manager @ Example Labs</h3>
            </a>
            <p>Lead developer platform programs and cross-functional launches.</p>
          </div></li>
          <li><div class="dd algo algo-sr">
            <a href="https://r.search.yahoo.com/RU={quote(linkedin_url, safe='')}/RK=2/RS=test">
              <h3>Technical Program Manager @ Network Company | LinkedIn</h3>
            </a>
            <p>Own technical programs for platform and developer workflows. Remote in the United States.</p>
          </div></li>
        </ol></body></html>
        """
        search_requests = []

        def search_fetcher(url):
            search_requests.append(url)
            return {"status": 200, "final_url": url, "html": search_page, "error": ""}

        posting_page = """
        <html><head><script type="application/ld+json">
          {
            "@type": "JobPosting",
            "title": "Senior Technical Program Manager",
            "description": "Lead developer platform programs and cross-functional launches.",
            "hiringOrganization": {"name": "Example Labs"},
            "jobLocation": {"address": {"addressLocality": "Minneapolis", "addressRegion": "MN"}}
          }
        </script></head></html>
        """

        result = discovery.run_search(
            search["id"],
            search_fetcher=search_fetcher,
            posting_fetcher=lambda url: {"status": 200, "final_url": url, "html": posting_page, "error": ""},
        )

        self.assertEqual(
            len(search_requests),
            len(search["lanes"])
            * len(discovery.search_keyword_families(search))
            * len(discovery.BUILT_IN_SEARCH_STRATEGIES),
        )
        self.assertTrue(any("Minnesota" in source["query"] for source in result["sources"]))
        self.assertTrue(any("United States" in source["query"] and "remote" in source["query"] for source in result["sources"]))
        self.assertEqual(result["found_count"], 2)
        self.assertEqual(result["new_count"], 2)
        self.assertEqual({candidate["source_platform"] for candidate in result["captured"]}, {"ashby", "linkedin"})
        linkedin = next(candidate for candidate in result["captured"] if candidate["source_platform"] == "linkedin")
        self.assertEqual(linkedin["company"], "Network Company")
        self.assertEqual(linkedin["processing_status"], "partial")

        rerun = discovery.run_search(
            search["id"],
            search_fetcher=search_fetcher,
            posting_fetcher=lambda url: {"status": 200, "final_url": url, "html": posting_page, "error": ""},
        )
        self.assertEqual(rerun["new_count"], 0)
        self.assertEqual(rerun["updated_count"], 2)
        self.assertEqual(len(repository.read_discovery_candidates()), 2)

    def test_search_now_uses_hunter_chrome_google_and_linkedin_sources(self):
        search = self.save_search(
            "Minnesota plus remote",
            "technical program manager",
            lanes=[
                {
                    "id": "minnesota",
                    "label": "Minnesota",
                    "location": "Minnesota",
                    "work_modes": ["on-site", "hybrid", "remote"],
                },
                {
                    "id": "national-remote",
                    "label": "U.S. remote",
                    "location": "United States",
                    "work_modes": ["remote"],
                },
            ],
        )
        ashby_url = "https://jobs.ashbyhq.com/example/4e79049a-0372-4692-a0a7-61906fb12676"
        linkedin_url = "https://www.linkedin.com/jobs/view/1234567890"
        browser_requests = []

        def browser_searcher(engine, value, page):
            browser_requests.append((engine, value, page))
            if engine == "linkedin":
                return [
                    {
                        "url": linkedin_url,
                        "title": "Technical Program Manager at Network Company",
                        "snippet": "United States · Remote · Own platform programs.",
                    }
                ]
            return [
                {
                    "url": ashby_url,
                    "title": "Senior Technical Program Manager at Example Labs",
                    "snippet": "Minneapolis, MN · Lead developer platform programs.",
                }
            ]

        posting_page = """
        <html><head><script type="application/ld+json">
          {
            "@type": "JobPosting",
            "title": "Senior Technical Program Manager",
            "description": "Lead developer platform programs and cross-functional launches.",
            "hiringOrganization": {"name": "Example Labs"},
            "jobLocation": {"address": {"addressLocality": "Minneapolis", "addressRegion": "MN"}}
          }
        </script></head><body>Apply now</body></html>
        """

        result = discovery.run_search(
            search["id"],
            browser_searcher=browser_searcher,
            posting_fetcher=lambda url: {"status": 200, "final_url": url, "html": posting_page, "error": ""},
        )

        expected_requests_per_lane = (
            len(discovery.search_keyword_families(search))
            * len(discovery.BUILT_IN_SEARCH_STRATEGIES)
        )
        self.assertEqual(len(browser_requests), len(search["lanes"]) * expected_requests_per_lane)
        self.assertEqual({request[0] for request in browser_requests}, {"google", "linkedin"})
        self.assertEqual({request[2] for request in browser_requests}, {0})
        self.assertTrue(any("google.com" not in value and "Minnesota" in value for engine, value, _page in browser_requests if engine == "google"))
        self.assertTrue(any("linkedin.com/jobs/search" in value for engine, value, _page in browser_requests if engine == "linkedin"))
        self.assertEqual({source["engine"] for source in result["sources"]}, {"hunter-chrome-google", "hunter-chrome-linkedin"})
        self.assertEqual({source["page_count"] for source in result["sources"]}, {1})
        self.assertEqual(
            {source["query_family"] for source in result["sources"]},
            {"exact", "senior", "adjacent"},
        )
        self.assertEqual(result["found_count"], 2)
        self.assertEqual(result["qualified_count"], 2)
        self.assertEqual({candidate["source_platform"] for candidate in result["captured"]}, {"ashby", "linkedin"})

    def test_adaptive_paging_continues_on_high_yield_and_stops_after_yield_drops(self):
        search = self.save_search("Technical platforms", "platform delivery leader")
        browser_requests = []

        def browser_searcher(engine, value, page):
            browser_requests.append((engine, page))
            count = 10 if page == 0 else 2
            return [
                {
                    "url": f"https://jobs.example.com/jobs/{engine}/{page}/{index}",
                    "title": f"Platform Delivery Leader {page}-{index}",
                    "snippet": "Minnesota platform program leadership.",
                }
                for index in range(count)
            ]

        discovery.run_search(
            search["id"],
            browser_searcher=browser_searcher,
            posting_fetcher=lambda url: {
                "status": 200,
                "final_url": url,
                "html": (
                    "<html><head><script type='application/ld+json'>"
                    '{"@type":"JobPosting","title":"Platform Delivery Leader",'
                    '"description":"Lead platform delivery planning, technical dependencies, execution reviews, '
                    'risk management, stakeholder communication, release readiness, operational mechanisms, '
                    'customer workflows, measurable outcomes, roadmap governance, continuous improvement, '
                    'cross-functional alignment, and post-launch learning across distributed engineering teams.",'
                    '"hiringOrganization":{"name":"Example Labs"},'
                    '"jobLocation":{"address":{"addressLocality":"Minneapolis","addressRegion":"MN"}}}'
                    "</script></head><body>Apply now</body></html>"
                ),
                "error": "",
            },
        )

        self.assertEqual([page for _engine, page in browser_requests], [0, 1, 0, 1, 0, 1])

    def test_search_automatically_enriches_linkedin_details_before_ranking(self):
        search = self.save_search("Technical platforms", "technical program manager")
        linkedin_url = "https://www.linkedin.com/jobs/view/1234567890"

        def browser_searcher(engine, value, page):
            if engine != "linkedin":
                return []
            return [
                {
                    "url": linkedin_url,
                    "title": "Technical Program Manager",
                    "company": "Example Labs",
                    "location": "United States",
                    "snippet": "Remote technical program role.",
                }
            ]

        details_text = (
            "Lead technical platform programs across planning, execution, dependency management, risk reviews, "
            "stakeholder communication, launch readiness, operating mechanisms, and continuous improvement. "
            "Partner with product and engineering teams to translate customer needs into clear requirements, "
            "measurable milestones, and durable delivery systems. Own roadmap governance, technical decision "
            "forums, release coordination, post-launch learning, and cross-functional communication across "
            "distributed teams in the United States."
        )
        result = discovery.run_search(
            search["id"],
            browser_searcher=browser_searcher,
            browser_detailer=lambda url: {
                "title": "Technical Program Manager, Platform",
                "company": "Example Labs",
                "location": "Remote; United States",
                "description_text": details_text,
            },
        )

        candidate = result["captured"][0]
        self.assertEqual(result["enriched_count"], 1)
        self.assertEqual(candidate["processing_status"], "ready")
        self.assertEqual(candidate["location"], "Remote; United States")
        self.assertNotIn(discovery.LINKEDIN_DETAILS_WARNING, candidate["warnings"])
        self.assertTrue(result["search"]["last_run_at"])
        self.assertEqual(result["search"]["last_run_summary"]["enriched_count"], 1)

    def test_workday_redirect_payload_is_not_ready(self):
        candidate = {
            "company": "Example",
            "title": "Technical Program Manager",
            "location": "Minneapolis, MN",
            "work_mode": "",
            "source_platform": "workday",
            "description_text": '{"widget":"redirect","url":"/job/example","externalSpa":true}',
            "warnings": "",
        }

        self.assertEqual(discovery.processing_status(candidate), "partial")

    def test_requisition_identity_deduplicates_cross_domain_workday_role(self):
        direct = {
            "company": "Danaher",
            "title": "Technical Project Manager, Global Operations",
            "url": "https://jobs.danaher.com/global/en/job/DANAGLOBALR1308804EXTERNALENGLOBAL/technical-project-manager",
            "canonical_url": "",
        }
        workday = {
            "company": "Danaher",
            "title": "Technical Project Manager, Global Operations ...",
            "url": "https://danaher.wd1.myworkdayjobs.com/DanaherJobs/job/technical-project-manager_R1308804",
            "canonical_url": "",
        }

        self.assertIs(discovery.matching_candidate([direct], workday), direct)

    def test_search_now_rejects_blocked_or_collection_content_before_scoring(self):
        search = self.save_search("Technical platforms", "technical program manager")
        result_url = "https://example.com/jobs/senior-technical-program-manager"
        search_page = f"""
        <html><body><ol><li><div class="dd algo algo-sr">
          <a href="https://r.search.yahoo.com/RU={quote(result_url, safe='')}/RK=2/RS=test">
            <h3>Senior Technical Program Manager</h3>
          </a>
          <p>Minnesota hybrid technical program leadership role.</p>
        </div></li></ol></body></html>
        """
        blocked_page = "<html><title>Just a moment</title><body>Verify you are human</body></html>"

        result = discovery.run_search(
            search["id"],
            search_fetcher=lambda url: {
                "status": 200,
                "final_url": url,
                "html": search_page,
                "error": "",
            },
            posting_fetcher=lambda url: {
                "status": 200,
                "final_url": url,
                "html": blocked_page,
                "error": "",
            },
        )

        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["found_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(repository.read_discovery_candidates(), [])

    def test_search_now_applies_final_limit_after_qualification(self):
        search = self.save_search("Technical platforms", "technical program manager")

        def browser_searcher(engine, value, page):
            return [
                {
                    "url": f"https://www.linkedin.com/jobs/view/{1234567800 + index}",
                    "title": f"Technical Program Manager at Company {index}",
                    "snippet": "Remote in the United States. Own technical platform programs.",
                }
                for index in range(3)
            ]

        original_limit = discovery.DISCOVERY_RESULT_LIMIT
        discovery.DISCOVERY_RESULT_LIMIT = 2
        try:
            result = discovery.run_search(
                search["id"],
                browser_searcher=browser_searcher,
            )
        finally:
            discovery.DISCOVERY_RESULT_LIMIT = original_limit

        self.assertEqual(result["evaluated_count"], 3)
        self.assertEqual(result["qualified_count"], 3)
        self.assertEqual(result["found_count"], 2)
        self.assertEqual(result["limited_count"], 1)
        self.assertEqual(len(repository.read_discovery_candidates()), 2)

    def test_search_now_stops_when_a_paged_source_requests_verification(self):
        search = self.save_search("Technical platforms", "technical program manager")
        browser_requests = []

        def browser_searcher(engine, value, page):
            browser_requests.append((engine, page))
            if page == 1:
                raise browser_discovery.BrowserDiscoveryError("Google needs verification in Hunter Chrome.")
            return [
                {
                    "url": f"https://jobs.example.com/job/{index}",
                    "title": f"Technical Program Manager {index}",
                    "snippet": "Minnesota technical program role.",
                }
                for index in range(10)
            ]

        with self.assertRaisesRegex(RuntimeError, "needs verification"):
            discovery.run_search(search["id"], browser_searcher=browser_searcher)

        self.assertEqual(browser_requests, [("google", 0), ("google", 1)])
        self.assertEqual(repository.read_discovery_candidates(), [])

    def test_open_web_source_keeps_postings_but_rejects_career_indexes(self):
        self.assertFalse(discovery.likely_individual_posting("https://example.com/careers", "Careers"))
        self.assertFalse(discovery.likely_individual_posting("https://example.com/jobs/search?q=program", "Search jobs"))
        self.assertFalse(discovery.likely_individual_posting("https://example.com/jobs", "Jobs at Example"))
        self.assertTrue(
            discovery.likely_individual_posting(
                "https://example.com/careers/senior-technical-program-manager-123",
                "Senior Technical Program Manager",
            )
        )

    def test_location_signal_rejects_salary_disclosures_but_keeps_job_locations(self):
        salary_result = {
            "title": "Technical Program Manager",
            "snippet": "Annual Salary USD 92,600 - 213,500 in Minnesota and Wisconsin.",
        }
        local_result = {
            "title": "Program Manager",
            "snippet": "Locations: Medina, Minnesota and Bedford, Massachusetts.",
        }

        self.assertFalse(discovery.result_has_location_signal(salary_result, "Minnesota"))
        self.assertTrue(discovery.result_has_location_signal(local_result, "Minnesota"))

    def test_direct_employer_capture_extracts_scores_and_deduplicates(self):
        search = self.save_search("Technical platforms", "technical program manager")
        page = """
        <html><head>
          <link rel="canonical" href="https://jobs.example.com/jobs/123">
          <script type="application/ld+json">
          {
            "@type": "JobPosting",
            "title": "Senior Technical Program Manager, Developer Platform",
            "description": "Own the developer platform roadmap, customer workflows, requirements, and cross-functional launch. Lead planning, execution, risk management, technical dependency reviews, stakeholder communication, release readiness, operational mechanisms, and continuous improvement across multiple engineering teams. Translate customer needs into durable program requirements and measurable delivery outcomes. Partner with product and engineering leaders to define milestones, resolve ambiguity, and communicate progress. Build repeatable mechanisms for roadmap planning, launch governance, and post-launch learning.",
            "hiringOrganization": {"name": "Example Labs"},
            "jobLocation": {"address": {"addressLocality": "Minneapolis", "addressRegion": "MN", "addressCountry": "US"}}
          }
          </script>
        </head><body>Apply now</body></html>
        """

        first = discovery.capture_candidates(
            search["id"],
            "https://jobs.example.com/jobs/123?utm_source=linkedin",
            fetcher=lambda url: {"status": 200, "final_url": url, "html": page, "error": ""},
        )
        second = discovery.capture_candidates(
            search["id"],
            "https://jobs.example.com/jobs/123",
            fetcher=lambda url: {"status": 200, "final_url": url, "html": page, "error": ""},
        )

        candidate = first["captured"][0]
        self.assertEqual(candidate["company"], "Example Labs")
        self.assertEqual(candidate["title"], "Senior Technical Program Manager, Developer Platform")
        self.assertEqual(candidate["location"], "Minneapolis, MN, US")
        self.assertEqual(candidate["processing_status"], "ready")
        self.assertGreaterEqual(int(candidate["fit_score"]), 45)
        self.assertEqual(second["captured"][0]["id"], candidate["id"])
        self.assertEqual(len(repository.read_discovery_candidates()), 1)

    def test_lane_location_and_work_modes_are_fully_configurable(self):
        search = self.save_search(
            "Colorado hybrid",
            "program manager",
            lanes=[
                {
                    "id": "front-range",
                    "label": "Front Range hybrid",
                    "location": "Denver, Colorado",
                    "work_modes": ["hybrid"],
                }
            ],
        )

        opened = discovery.open_linkedin_search(search["id"])

        self.assertEqual(opened["search"]["lanes"][0]["label"], "Front Range hybrid")
        self.assertIn("location=Denver%2C+Colorado", opened["url"])
        self.assertIn("f_WT=3", opened["url"])

    def test_lane_requires_location_and_at_least_one_work_mode(self):
        with self.assertRaisesRegex(ValueError, "location"):
            self.save_search(
                "Invalid location",
                "program manager",
                lanes=[{"id": "invalid", "label": "Invalid", "location": "", "work_modes": ["remote"]}],
            )
        with self.assertRaisesRegex(ValueError, "work mode"):
            self.save_search(
                "Invalid work modes",
                "program manager",
                lanes=[{"id": "invalid", "label": "Invalid", "location": "Minnesota", "work_modes": []}],
            )

    def test_linkedin_capture_can_be_completed_with_copied_details_and_ingested(self):
        search = self.save_search("Adjacent roles", "developer experience program lead")
        linkedin_url = "https://www.linkedin.com/jobs/view/1234567890"

        def unexpected_fetch(_url):
            self.fail("LinkedIn captures must remain browser-assisted and must not be fetched by Hunter.")

        result = discovery.capture_candidates(
            search["id"],
            linkedin_url,
            fetcher=unexpected_fetch,
        )
        candidate = result["captured"][0]
        self.assertEqual(candidate["processing_status"], "needs-details")

        candidate = discovery.update_candidate_details(
            candidate["id"],
            {
                "company": "New Company",
                "title": "Technical Program Manager, Developer Experience",
                "location": "Remote, US",
                "work_mode": "remote",
                "canonical_url": "https://jobs.new-company.example/roles/tpm-devex",
                "description_text": (
                    "Lead the developer tools roadmap, cross-functional launches, requirements, and customer workflows. "
                    "Own planning, technical dependency management, execution reviews, risk mitigation, stakeholder "
                    "communication, release readiness, and operating mechanisms across engineering and product teams. "
                    "Translate customer needs into program requirements and measurable outcomes while improving delivery "
                    "systems, launch governance, and post-launch learning. Partner with senior leaders and distributed "
                    "teams to resolve ambiguity and maintain durable program health."
                ),
            },
        )
        ingested = discovery.ingest_candidate(candidate["id"])

        self.assertEqual(candidate["processing_status"], "ready")
        self.assertEqual(candidate["warnings"], "")
        self.assertEqual(ingested["posting"]["company"], "New Company")
        self.assertEqual(ingested["posting"]["role"], "Technical Program Manager, Developer Experience")
        self.assertEqual(ingested["posting"]["source_url"], "https://jobs.new-company.example/roles/tpm-devex")
        self.assertEqual(discovery.get_candidate(candidate["id"])["status"], "ingested")
        self.assertEqual(len(repository.read_posting_snapshots(ingested["posting"]["id"])), 1)

    def test_batch_capture_rejects_one_details_payload_for_multiple_links(self):
        search = self.save_search("Batch", "program manager")

        with self.assertRaisesRegex(ValueError, "one link"):
            discovery.capture_candidates(
                search["id"],
                "https://example.com/jobs/1\nhttps://example.com/jobs/2",
                details={"company": "Example"},
            )

    def test_same_role_is_global_across_saved_searches(self):
        first_search = self.save_search("Platforms", "platform program manager")
        second_search = self.save_search("Developer experience", "developer experience")
        details = {
            "company": "Example Labs",
            "title": "Technical Program Manager",
            "description_text": "Lead developer platform roadmap and cross-functional delivery.",
        }
        url = "https://www.linkedin.com/jobs/view/1234567890"

        first = discovery.capture_candidates(first_search["id"], url, details=details)
        second = discovery.capture_candidates(second_search["id"], url, details=details)

        self.assertEqual(first["captured"][0]["id"], second["captured"][0]["id"])
        self.assertEqual(len(repository.read_discovery_candidates()), 1)

    def test_legacy_location_fields_migrate_into_search_lanes(self):
        repository.write_discovery_searches(
            [
                {
                    "id": "DS0001",
                    "name": "Legacy coverage",
                    "keywords": "program manager",
                    "location": "Minnesota",
                    "remote_location": "United States",
                    "lanes_json": "",
                }
            ]
        )

        search = discovery.list_searches()[0]

        self.assertEqual([lane["location"] for lane in search["lanes"]], ["Minnesota", "United States"])
        self.assertEqual(search["lanes"][0]["work_modes"], ["on-site", "hybrid", "remote"])
        self.assertEqual(search["lanes"][1]["work_modes"], ["remote"])


if __name__ == "__main__":
    unittest.main()
