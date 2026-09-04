import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote, unquote_plus
from unittest.mock import patch

from hunter import applications, app_state, companies, discovery, paths, repository, schema, sqlite_store


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

    def test_browser_results_exclude_builtin_aggregator_network(self):
        results = discovery.normalize_browser_results(
            [
                {
                    "url": "https://builtin.com/job/technical-program-manager/8018128",
                    "title": "Technical Program Manager",
                },
                {
                    "url": "https://www.builtinchicago.org/job/platform-program-manager/7776618",
                    "title": "Platform Program Manager",
                },
                {
                    "url": "https://jobs.example.com/job/technical-program-manager",
                    "title": "Technical Program Manager",
                },
            ]
        )

        self.assertEqual(
            [result["url"] for result in results],
            ["https://jobs.example.com/job/technical-program-manager"],
        )

    def test_builtin_candidates_are_hidden_without_deleting_history(self):
        rows = []
        for candidate_id, url in [
            ("DC0001", "https://builtinboston.com/job/technical-program-manager/123"),
            ("DC0002", "https://jobs.example.com/job/technical-program-manager"),
        ]:
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": candidate_id,
                    "title": "Technical Program Manager",
                    "url": url,
                    "canonical_url": url,
                    "status": "new",
                    "processing_status": "ready",
                }
            )
            rows.append(row)
        repository.write_discovery_candidates(rows)

        self.assertEqual(
            [candidate["id"] for candidate in discovery.list_candidates()],
            ["DC0002"],
        )
        self.assertEqual(len(repository.read_discovery_candidates()), 2)

    def test_expired_structured_posting_date_marks_candidate_unavailable(self):
        candidate = {
            "company": "Example",
            "title": "Technical Program Manager",
            "status": "new",
            "source_platform": "employer",
            "description_text": "Detailed posting content " * 30,
            "warnings": "",
        }

        discovery.apply_browser_details(
            candidate,
            {
                "availability_status": "open",
                "valid_through": "2026-07-27",
            },
        )

        self.assertEqual(candidate["freshness_status"], "closed")
        self.assertEqual(candidate["status"], "unavailable")

    def test_valid_through_today_is_not_expired(self):
        self.assertFalse(
            discovery.posting_valid_through_expired(
                "2026-07-29",
                reference=discovery.datetime(2026, 7, 29, 18, 0),
            )
        )

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
        self.assertEqual(
            [family["id"] for family in families],
            [family["id"] for family in discovery.ROLE_QUERY_FAMILIES],
        )
        self.assertIn('"staff technical program manager"', families[0]["query"])
        self.assertIn('"technical project manager"', families[1]["query"])
        self.assertIn('"technical product manager"', families[2]["query"])
        self.assertIn('"product operations manager"', families[3]["query"])
        self.assertTrue(all("developer tools" in family["query"] for family in families))
        self.assertIn("location=Minnesota", opened["url"])
        self.assertEqual(len(opened["lanes"]), 2)
        self.assertIn("location=United+States", opened["lanes"][1]["url"])
        self.assertIn("f_WT=2", opened["lanes"][1]["url"])
        self.assertIn("f_TPR=r2592000", opened["lanes"][1]["url"])
        self.assertIn("sortBy=DD", opened["lanes"][1]["url"])
        self.assertEqual(opened["lanes"][1]["work_modes"], ["remote"])
        self.assertTrue(opened["search"]["last_opened_at"])
        self.assertEqual(app_state.build_payload()["discovery_searches"][0]["name"], "Platform leadership")

    def test_saved_search_can_select_adjacent_role_families_and_optional_focus(self):
        search = discovery.upsert_search(
            "",
            {
                "name": "Product delivery",
                "keywords": "",
                "role_family_ids": ["product-platform", "product-operations"],
                "lanes": [
                    {
                        "id": "remote",
                        "label": "U.S. remote",
                        "location": "United States",
                        "work_modes": ["remote"],
                    }
                ],
            },
        )

        self.assertEqual(search["role_family_ids"], ["product-platform", "product-operations"])
        self.assertEqual(
            [family["id"] for family in discovery.search_keyword_families(search)],
            ["product-platform", "product-operations"],
        )
        stored = repository.read_discovery_searches()[0]
        self.assertEqual(stored["role_family_ids_json"], '["product-platform", "product-operations"]')

    def test_adjacent_role_family_and_responsibilities_are_explained(self):
        product = {
            "title": "Senior Technical Product Manager",
            "description_text": "Own the roadmap and requirements across engineering teams and launch readiness.",
        }
        principal_product = {
            "title": "Principal Product Manager, Emerging Products",
            "description_text": "Shape product strategy through discovery, prototypes, and customer experiments.",
        }
        technology_product = {
            "title": "Sr Manager, Technology Product Management — Unified Experience Layer",
            "description_text": "Shape a shared platform across customers, product teams, developers, and APIs.",
        }
        ai_program = {
            "title": "Senior AI Program Manager",
            "description_text": "Build prototypes and agentic workflows with product and engineering teams.",
        }
        ai_operations = {
            "title": "Senior Manager, AI Performance & Operations",
            "description_text": "Own evaluation, observability, failure analysis, and continuous improvement.",
        }
        technologist = {
            "title": "Principal Product Technologist",
            "description_text": "Build prototypes with product teams and influence architecture decisions.",
        }
        game = {
            "title": "Development Director",
            "description_text": "Lead cross-functional game teams through dependencies and release readiness.",
        }

        self.assertEqual(discovery.candidate_role_family(product)["id"], "product-platform")
        self.assertEqual(discovery.candidate_role_family(principal_product)["id"], "product-platform")
        self.assertEqual(discovery.candidate_role_family(technology_product)["id"], "product-platform")
        self.assertEqual(discovery.candidate_role_family(ai_program)["id"], "technical-program")
        self.assertEqual(discovery.candidate_role_family(ai_operations)["id"], "product-operations")
        self.assertEqual(discovery.candidate_role_family(technologist)["id"], "technologist-prototyping")
        self.assertEqual(discovery.candidate_role_family(game)["id"], "games-interactive")
        self.assertEqual(
            discovery.candidate_responsibility_signals(product),
            ["Roadmap and requirements", "Technical delivery", "Launch and release"],
        )

    def test_generic_adjacent_title_requires_delivery_evidence(self):
        search = self.save_search("TPM", "technical program manager")
        candidate = {
            "title": "Product Lead",
            "canonical_url": "https://jobs.ashbyhq.com/example/4e79049a-0372-4692-a0a7-61906fb12676",
            "source_platform": "ashby",
            "processing_status": "ready",
            "fit_score": "70",
            "freshness_status": "confirmed-open",
            "description_text": "Own a product area and attend weekly meetings.",
        }

        admitted, reason = discovery.candidate_review_admission(candidate, search=search)
        self.assertFalse(admitted)
        self.assertIn("lacks enough relevant delivery responsibilities", reason)

        candidate["description_text"] = (
            "Lead cross-functional engineering teams, manage roadmap requirements and dependencies, "
            "and drive launch readiness."
        )
        admitted, reason = discovery.candidate_review_admission(candidate, search=search)
        self.assertTrue(admitted)
        self.assertEqual(reason, "")

    def test_result_selection_reserves_space_for_each_role_family(self):
        search = self.save_search("TPM", "technical program manager")
        candidates = []
        for index in range(20):
            candidates.append(
                {
                    "title": f"Senior Technical Program Manager {index}",
                    "status": "new",
                    "fit_score": str(90 - index),
                    "processing_status": "ready",
                    "_lane_matched": True,
                }
            )
        for index in range(5):
            candidates.append(
                {
                    "title": f"Product Operations Manager {index}",
                    "status": "new",
                    "fit_score": str(60 - index),
                    "processing_status": "ready",
                    "_lane_matched": True,
                }
            )

        selected = discovery.select_balanced_role_candidates(candidates, search, limit=10)

        counts = {}
        for candidate in selected:
            family_id = discovery.candidate_role_family(candidate, search)["id"]
            counts[family_id] = counts.get(family_id, 0) + 1
        self.assertEqual(counts, {"technical-program": 5, "product-operations": 5})

    def test_preference_suggestion_is_search_specific_and_resolves_when_saved(self):
        search = self.save_search("TPM", "technical program manager")
        other_search = self.save_search("Product", "product operations manager")
        rows = []
        for index, title in enumerate(
            ["Technical Project Manager", "Senior Technical Project Manager"],
            start=1,
        ):
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": f"DC000{index}",
                    "search_id": search["id"],
                    "title": title,
                    "url": f"https://example.com/jobs/{index}",
                    "status": "ignored",
                    "processing_status": "ready",
                }
            )
            rows.append(row)
        repository.write_discovery_candidates(rows)

        suggestions = discovery.preference_suggestions()

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["search_id"], search["id"])
        self.assertEqual(suggestions[0]["term"], "project")
        self.assertNotEqual(suggestions[0]["search_id"], other_search["id"])

        discovery.upsert_search(search["id"], {"excluded_terms": ["project"]})

        self.assertEqual(discovery.preference_suggestions(), [])
        self.assertEqual(app_state.build_payload()["discovery_preference_suggestions"], [])

    def test_exclusions_filter_titles_without_narrowing_search_or_descriptions(self):
        saved = self.save_search("TPM", "technical program manager")
        search = discovery.upsert_search(saved["id"], {"excluded_terms": ["project"]})
        candidate = {
            "title": "Senior Technical Program Manager",
            "description_text": "Lead complex projects across the platform organization.",
        }
        excluded_candidate = {
            "title": "Senior Technical Project Manager",
            "description_text": "Lead platform delivery.",
        }
        query = discovery.discovery_query(
            search,
            search["lanes"][0],
            discovery.BUILT_IN_SEARCH_STRATEGIES[0],
        )

        self.assertFalse(discovery.candidate_is_excluded(search, candidate))
        self.assertTrue(discovery.candidate_is_excluded(search, excluded_candidate))
        self.assertNotIn("-project", query)

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
            "hiringOrganization": {
              "name": "Example Labs",
              "industry": "Software Development",
              "numberOfEmployees": {"minValue": 201, "maxValue": 500},
              "sameAs": "https://www.linkedin.com/company/example-labs"
            },
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
        self.assertTrue(any("Minneapolis" in source["query"] for source in result["sources"]))
        self.assertTrue(any("after:" in source["query"] for source in result["sources"] if source["source"] != "linkedin"))
        self.assertTrue(all("after:" not in source["query"] for source in result["sources"] if source["source"] == "linkedin"))
        self.assertTrue(
            all(
                "-site:builtin.com" in source["query"]
                for source in result["sources"]
                if source["source"] == "employer-web"
            )
        )
        self.assertTrue(
            all(
                "-site:remotezest.up.railway.app" in source["query"]
                and "-theladders.com" in source["query"]
                for source in result["sources"]
                if source["source"] == "employer-web"
            )
        )
        self.assertEqual(result["found_count"], 2)
        self.assertEqual(result["new_count"], 2)
        self.assertEqual({candidate["source_platform"] for candidate in result["captured"]}, {"ashby", "linkedin"})
        linkedin = next(candidate for candidate in result["captured"] if candidate["source_platform"] == "linkedin")
        linkedin_company = companies.get_company(linkedin["company_id"])
        self.assertEqual(linkedin_company["name"], "Network Company")
        self.assertEqual(linkedin["processing_status"], "partial")
        employer = next(candidate for candidate in result["captured"] if candidate["source_platform"] == "ashby")
        employer_company = companies.get_company(employer["company_id"])
        self.assertEqual(employer_company["industry"], "Software Development")
        self.assertEqual(employer_company["company_size"], "201–500 employees")

        rerun = discovery.run_search(
            search["id"],
            search_fetcher=search_fetcher,
            posting_fetcher=lambda url: {"status": 200, "final_url": url, "html": posting_page, "error": ""},
        )
        self.assertEqual(rerun["new_count"], 0)
        self.assertEqual(rerun["updated_count"], 0)
        self.assertEqual(rerun["known_count"], 2)
        self.assertEqual(len(repository.read_discovery_candidates()), 2)

    def test_search_now_supports_injected_google_and_linkedin_sources(self):
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
        self.assertEqual(
            len(browser_requests),
            len(search["lanes"]) * expected_requests_per_lane,
        )
        self.assertEqual({request[0] for request in browser_requests}, {"google", "linkedin"})
        self.assertEqual(
            {page for engine, _value, page in browser_requests if engine == "google"},
            {0},
        )
        self.assertEqual(
            {page for engine, _value, page in browser_requests if engine == "linkedin"},
            {0},
        )
        self.assertTrue(any("google.com" not in value and "Minnesota" in value for engine, value, _page in browser_requests if engine == "google"))
        self.assertTrue(any("linkedin.com/jobs/search" in value for engine, value, _page in browser_requests if engine == "linkedin"))
        self.assertEqual({source["engine"] for source in result["sources"]}, {"injected-google", "injected-linkedin"})
        self.assertEqual({source["page_count"] for source in result["sources"]}, {1})
        self.assertEqual(
            {source["query_family"] for source in result["sources"]},
            {family["id"] for family in discovery.ROLE_QUERY_FAMILIES},
        )
        self.assertEqual(result["found_count"], 2)
        self.assertEqual(result["qualified_count"], 2)
        self.assertEqual({candidate["source_platform"] for candidate in result["captured"]}, {"ashby", "linkedin"})

    def test_adaptive_paging_continues_on_high_yield_and_stops_after_yield_drops(self):
        search = self.save_search("Technical platforms", "platform delivery leader")
        browser_requests = []

        def browser_searcher(engine, value, page):
            browser_requests.append((engine, page))
            count = (15 if engine == "linkedin" else 10) if page == 0 else 2
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

    def test_api_search_uses_provider_bundle_without_launching_chrome(self):
        search = self.save_search("Technical platforms", "technical program manager")
        posting_url = "https://boards.greenhouse.io/example/jobs/123456"
        bundle = {
            "results": [
                {
                    "provider": "openai",
                    "url": posting_url,
                    "title": "Senior Technical Program Manager",
                    "company": "Example Labs",
                    "location": "United States",
                    "work_mode": "Remote",
                    "snippet": "Lead platform delivery across product and engineering teams.",
                    "role_family_ids": ["technical-program"],
                    "lane_ids": ["default"],
                }
            ],
            "sources": [
                {
                    "source": "openai-web",
                    "label": "OpenAI source-backed web search",
                    "query_family": "all",
                    "query_family_label": "All selected role families",
                    "lane_id": "all",
                    "lane_label": "All configured locations",
                    "query": "Technical platforms",
                    "found_count": 1,
                    "page_count": 1,
                    "engine": "openai-web-search",
                }
            ],
            "errors": [],
        }
        description = (
            "Lead technical platform programs across planning, dependencies, execution reviews, risk management, "
            "stakeholder communication, launch readiness, requirements, roadmap governance, and continuous "
            "improvement with product and engineering teams."
        )

        with patch("hunter.discovery.candidate_sources.provider_bundle", return_value=bundle):
            result = discovery.run_search(
                search["id"],
                posting_fetcher=lambda url: {
                    "status": 200,
                    "final_url": url,
                    "html": (
                        "<script type='application/ld+json'>"
                        + json.dumps(
                            {
                                "@type": "JobPosting",
                                "title": "Senior Technical Program Manager",
                                "description": description,
                                "hiringOrganization": {"name": "Example Labs"},
                                "jobLocationType": "TELECOMMUTE",
                            }
                        )
                        + "</script>"
                    ),
                    "error": "",
                },
            )

        self.assertEqual(result["new_count"], 1)
        self.assertEqual(result["captured"][0]["source_platform"], "greenhouse")
        self.assertTrue(result["run_id"].startswith("DR-"))
        provenance = discovery.candidate_acquisition_provenance(result["captured"][0])
        self.assertEqual(provenance[0]["provider"], "openai")
        self.assertEqual(provenance[0]["run_id"], result["run_id"])

    def test_ats_inventory_without_verification_timestamp_is_not_confirmed_open(self):
        search = self.save_search("Technical platforms", "technical program manager")
        posting_url = "https://jobs.example.com/roles/123"
        bundle = {
            "results": [
                {
                    "provider": "ats",
                    "url": posting_url,
                    "title": "Technical Program Manager",
                    "company": "Example Labs",
                    "location": "United States",
                    "work_mode": "Remote",
                    "description_text": "Lead technical delivery, dependencies, risk management, and launch readiness. " * 12,
                    "snippet": "Lead technical delivery and launch readiness.",
                    "fit_score": "80",
                    "role_family_ids": ["technical-program"],
                    "lane_ids": ["default"],
                    "last_verified_at": "",
                }
            ],
            "sources": [],
            "errors": [],
        }

        with patch("hunter.discovery.candidate_sources.provider_bundle", return_value=bundle):
            result = discovery.run_search(search["id"])

        candidate = result["captured"][0]
        self.assertEqual(candidate["freshness_status"], "")
        self.assertEqual(candidate["freshness_checked_at"], "")
        self.assertEqual(discovery.candidate_review_state(candidate), "needs-freshness")

    def test_api_search_measures_cheap_source_coverage_by_eligible_unseen_results(self):
        search = self.save_search("Technical platforms", "technical program manager")
        excluded_company = companies.upsert_company(
            "",
            {"name": "Excluded Labs", "interest_status": "not-interested"},
        )
        known = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        known.update(
            {
                "id": "DC0001",
                "title": "Technical Program Manager",
                "url": "https://jobs.example.com/jobs/known",
                "canonical_url": "https://jobs.example.com/jobs/known",
                "status": "new",
            }
        )
        repository.write_discovery_candidates([known])
        novelty = {}

        def provider_bundle(_search, _families, **kwargs):
            result_is_novel = kwargs["result_is_novel"]
            common = {
                "provider": "adzuna",
                "location": "United States",
                "work_mode": "Remote",
                "snippet": "Technical program manager leading platform delivery.",
                "role_family_ids": ["technical-program"],
                "lane_ids": ["default"],
            }
            novelty["known"] = result_is_novel(
                {
                    **common,
                    "url": known["url"],
                    "title": known["title"],
                    "company": "Known Labs",
                }
            )
            novelty["excluded"] = result_is_novel(
                {
                    **common,
                    "url": "https://jobs.excluded.example/jobs/tpm",
                    "title": "Technical Program Manager",
                    "company": excluded_company["name"],
                }
            )
            novelty["irrelevant"] = result_is_novel(
                {
                    **common,
                    "url": "https://jobs.example.com/jobs/marketing",
                    "title": "Marketing Manager",
                    "company": "Marketing Labs",
                }
            )
            novelty["novel"] = result_is_novel(
                {
                    **common,
                    "url": "https://jobs.example.com/jobs/novel",
                    "title": "Senior Technical Program Manager",
                    "company": "Novel Labs",
                }
            )
            return {"results": [], "sources": [], "errors": []}

        with patch(
            "hunter.discovery.candidate_sources.provider_bundle",
            side_effect=provider_bundle,
        ):
            result = discovery.run_search(search["id"])

        self.assertEqual(
            novelty,
            {
                "known": False,
                "excluded": False,
                "irrelevant": False,
                "novel": True,
            },
        )
        self.assertEqual(result["new_count"], 0)

    def test_adzuna_provider_results_remain_reviewable_with_attribution_url(self):
        search = self.save_search("Technical platforms", "technical program manager")
        redirect_url = "https://www.adzuna.com/details/123?utm_medium=api"
        description = (
            "Lead technical platform programs across planning, dependencies, execution reviews, risk management, "
            "stakeholder communication, launch readiness, requirements, roadmap governance, release coordination, "
            "and continuous improvement with product and engineering teams in a remote environment."
        )
        bundle = {
            "results": [
                {
                    "provider": "adzuna",
                    "provider_record_id": "123",
                    "url": redirect_url,
                    "title": "Senior Technical Program Manager",
                    "company": "Example Labs",
                    "location": "United States",
                    "work_mode": "Remote",
                    "snippet": description,
                    "role_family_ids": ["technical-program"],
                    "lane_ids": ["default"],
                }
            ],
            "sources": [{
                "source": "adzuna",
                "label": "Jobs by Adzuna",
                "query_family": "technical-program",
                "query_family_label": "Technical program leadership",
                "lane_id": "default",
                "lane_label": "United States",
                "query": "technical program manager · United States",
                "found_count": 1,
                "page_count": 1,
                "engine": "adzuna-api",
            }],
            "errors": [],
        }

        with patch("hunter.discovery.candidate_sources.provider_bundle", return_value=bundle):
            result = discovery.run_search(
                search["id"],
                posting_fetcher=lambda url: {
                    "status": 403,
                    "final_url": url,
                    "html": "",
                    "error": "Redirect page blocks lightweight extraction",
                },
            )

        self.assertEqual(result["new_count"], 1)
        self.assertEqual(result["screened_count"], 0)
        self.assertEqual(result["captured"][0]["source_platform"], "adzuna")
        self.assertEqual(result["captured"][0]["canonical_url"], redirect_url)

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
                "company_industry": "Software Development",
                "company_size": "51-200 employees",
                "company_profile_url": "https://www.linkedin.com/company/example-labs",
                "company_metadata_source": linkedin_url,
                "description_text": details_text,
            },
        )

        candidate = result["captured"][0]
        self.assertEqual(result["enriched_count"], 1)
        self.assertEqual(candidate["processing_status"], "ready")
        self.assertEqual(candidate["location"], "Remote; United States")
        company = companies.get_company(candidate["company_id"])
        self.assertEqual(company["industry"], "Software Development")
        self.assertEqual(company["company_size"], "51–200 employees")
        self.assertNotIn(discovery.LINKEDIN_DETAILS_WARNING, candidate["warnings"])
        self.assertTrue(result["search"]["last_run_at"])
        self.assertEqual(result["search"]["last_run_summary"]["enriched_count"], 1)

    def test_search_recognizes_existing_role_before_posting_lookup(self):
        search = self.save_search("Technical platforms", "technical program manager")
        linkedin_url = "https://www.linkedin.com/jobs/view/1234567890"
        row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        row.update(
            {
                "id": "DC0001",
                "search_id": search["id"],
                "company_id": companies.upsert_company(
                    "",
                    {"name": "Example Labs", "tracking_status": "discovered"},
                )["id"],
                "title": "Technical Program Manager",
                "url": linkedin_url,
                "status": "new",
                "processing_status": "ready",
            }
        )
        repository.write_discovery_candidates([row])
        posting_calls = []
        detail_calls = []

        result = discovery.run_search(
            search["id"],
            posting_fetcher=lambda url: posting_calls.append(url) or self.fail(
                "Known roles should not be fetched again"
            ),
            browser_searcher=lambda engine, value, page: (
                [
                    {
                        "url": linkedin_url,
                        "title": "Technical Program Manager",
                        "company": "Example Labs",
                        "location": "United States",
                        "snippet": "Remote technical program role.",
                    }
                ]
                if engine == "linkedin"
                else []
            ),
            browser_detailer=lambda url: detail_calls.append(url) or {},
        )

        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["known_count"], 1)
        self.assertEqual(result["found_count"], 0)
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(posting_calls, [])
        self.assertEqual(detail_calls, [])

    def test_search_retains_browser_verifiable_posting_when_lightweight_fetch_fails(self):
        search = self.save_search("Technical platforms", "technical program manager")
        posting_url = "https://jobs.example.com/roles/technical-program-manager"
        detail_calls = []

        result = discovery.run_search(
            search["id"],
            posting_fetcher=lambda url: {
                "status": 403,
                "final_url": url,
                "html": "",
                "error": "Access denied",
            },
            browser_searcher=lambda engine, value, page: (
                [
                    {
                        "url": posting_url,
                        "title": "Technical Program Manager",
                        "company": "Example Labs",
                        "location": "Remote, United States",
                        "snippet": "Remote technical program role.",
                    }
                ]
                if engine == "google"
                else []
            ),
            browser_detailer=lambda url: detail_calls.append(url) or {},
        )

        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["known_count"], 0)
        self.assertEqual(result["found_count"], 1)
        self.assertEqual(result["new_count"], 1)
        self.assertEqual(result["needs_details_count"], 1)
        self.assertEqual(detail_calls, [posting_url])
        self.assertIn(
            discovery.SOURCE_VALIDATION_WARNING,
            result["captured"][0]["warnings"],
        )

    def test_search_uses_lane_match_for_ranking_without_discarding_role(self):
        search = self.save_search(
            "Technical platforms",
            "technical program manager",
            lanes=[
                {
                    "id": "minnesota",
                    "label": "Minnesota",
                    "location": "Minnesota",
                    "work_modes": ["on-site", "hybrid", "remote"],
                }
            ],
        )
        matching_url = "https://www.linkedin.com/jobs/view/1234567890"
        review_url = "https://www.linkedin.com/jobs/view/1234567891"

        result = discovery.run_search(
            search["id"],
            browser_searcher=lambda engine, value, page: (
                [
                    {
                        "url": review_url,
                        "title": "Technical Program Manager",
                        "company": "London Example",
                        "location": "London, United Kingdom",
                        "work_mode": "On-site",
                        "snippet": "On-site technical program role in London.",
                    },
                    {
                        "url": matching_url,
                        "title": "Technical Program Manager",
                        "company": "Minnesota Example",
                        "location": "Minneapolis, Minnesota",
                        "work_mode": "Hybrid",
                        "snippet": "Hybrid technical program role in Minnesota.",
                    },
                ]
                if engine == "linkedin"
                else []
            ),
        )

        self.assertEqual(result["found_count"], 2)
        self.assertEqual(result["lane_unmatched_count"], 1)
        self.assertNotIn("lane-mismatch", result["skip_reasons"])
        self.assertEqual(result["captured"][0]["url"], matching_url)
        self.assertIn(
            discovery.LANE_REVIEW_WARNING,
            result["captured"][1]["warnings"],
        )

    def test_search_skips_not_interested_company_before_enrichment_or_storage(self):
        search = self.save_search("Technical platforms", "technical program manager")
        company = companies.upsert_company(
            "",
            {"name": "Example Labs", "interest_status": "not-interested"},
        )
        detail_calls = []

        result = discovery.run_search(
            search["id"],
            browser_searcher=lambda engine, value, page: (
                [{
                    "url": "https://www.linkedin.com/jobs/view/1234567890",
                    "title": "Technical Program Manager",
                    "company": "Example Labs",
                    "location": "United States",
                    "snippet": "Remote technical program role.",
                }]
                if engine == "linkedin"
                else []
            ),
            browser_detailer=lambda url: detail_calls.append(url) or {},
        )

        self.assertEqual(result["found_count"], 0)
        self.assertEqual(result["qualified_count"], 0)
        self.assertGreaterEqual(result["skipped_count"], 1)
        self.assertEqual(detail_calls, [])
        self.assertEqual(repository.read_discovery_candidates(), [])
        self.assertFalse(
            discovery.enrichment_needed(
                {
                    "company_id": company["id"],
                    "status": "new",
                    "processing_status": "needs-details",
                },
                company,
            )
        )

    def test_search_infers_not_interested_company_from_employer_hostname(self):
        search = self.save_search("Technical platforms", "technical program manager")
        companies.upsert_company(
            "",
            {"name": "Walmart", "interest_status": "not-interested"},
        )
        posting_url = "https://careers.walmart.com/us/en/jobs/R-2496298"
        detail_calls = []

        result = discovery.run_search(
            search["id"],
            browser_searcher=lambda engine, value, page: (
                [{
                    "url": posting_url,
                    "title": "(USA) Principal, Technical Program Manager",
                    "snippet": "Lead large technical programs across multiple teams.",
                }]
                if engine == "google"
                else []
            ),
            posting_fetcher=lambda url: {
                "html": (
                    "<html><head><title>(USA) Principal, Technical Program Manager</title></head>"
                    "<body><h1>(USA) Principal, Technical Program Manager</h1>"
                    "<p>What you'll do: lead complex technical programs, manage dependencies, "
                    "coordinate engineering teams, define milestones, communicate risks, and "
                    "deliver durable systems for a large organization. Apply now for this role.</p>"
                    "</body></html>"
                ),
                "final_url": url,
                "error": "",
            },
            browser_detailer=lambda url: detail_calls.append(url) or {},
        )

        self.assertEqual(result["found_count"], 0)
        self.assertEqual(result["skip_reasons"]["not-interested-company"], 1)
        self.assertEqual(detail_calls, [])
        self.assertEqual(repository.read_discovery_candidates(), [])

    def test_search_researches_missing_company_information_and_links_company(self):
        search = self.save_search("Technical platforms", "technical program manager")
        linkedin_url = "https://www.linkedin.com/jobs/view/1234567890"
        research_calls = []
        details_text = (
            "Lead technical platform programs across planning, execution, dependency management, risk reviews, "
            "stakeholder communication, launch readiness, operating mechanisms, and continuous improvement. "
            "Partner with product and engineering teams to translate customer needs into clear requirements, "
            "measurable milestones, and durable delivery systems across distributed engineering teams."
        )

        result = discovery.run_search(
            search["id"],
            browser_searcher=lambda engine, value, page: (
                [{
                    "url": linkedin_url,
                    "title": "Technical Program Manager",
                    "company": "Example Labs",
                    "location": "United States",
                    "snippet": "Remote technical program role.",
                }]
                if engine == "linkedin"
                else []
            ),
            browser_detailer=lambda url: {
                "title": "Technical Program Manager, Platform",
                "company": "Example Labs",
                "location": "Remote; United States",
                "description_text": details_text,
            },
            company_researcher=lambda name, profile: (
                research_calls.append((name, profile))
                or {
                    "company_industry": "Software Development",
                    "company_size": "201-500 employees",
                    "company_profile_url": "https://www.linkedin.com/company/example-labs/about/",
                    "company_metadata_source": "https://www.linkedin.com/company/example-labs/about/",
                }
            ),
        )

        candidate = result["captured"][0]
        company = companies.get_company(candidate["company_id"])
        self.assertEqual(research_calls, [("Example Labs", "")])
        self.assertEqual(result["company_researched_count"], 1)
        self.assertEqual(result["company_suggestion_count"], 0)
        self.assertEqual(company["tracking_status"], "discovered")
        self.assertEqual(company["industry"], "Software Development")
        self.assertEqual(company["company_size"], "201–500 employees")
        self.assertEqual(
            company["company_profile_url"],
            "https://www.linkedin.com/company/example-labs",
        )

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

    def test_manual_details_clear_linkedin_warning_for_managed_company(self):
        candidate = {
            "company_id": "CO0001",
            "title": "Technical Program Manager",
            "location": "United States",
            "work_mode": "Remote",
            "source_platform": "linkedin",
            "description_text": "Detailed posting content " * 30,
            "warnings": discovery.LINKEDIN_DETAILS_WARNING,
        }

        discovery.apply_manual_details(candidate, {"notes": "Reviewed manually."})

        self.assertEqual(candidate["warnings"], "")
        self.assertEqual(discovery.processing_status(candidate), "ready")

    def test_browser_details_clear_linkedin_warning_for_linked_company(self):
        candidate = {
            "company_id": "CO0001",
            "title": "Technical Program Manager",
            "location": "United States",
            "work_mode": "Remote",
            "source_platform": "linkedin",
            "description_text": "",
            "warnings": discovery.LINKEDIN_DETAILS_WARNING,
        }

        discovery.apply_browser_details(
            candidate,
            {"description_text": "Detailed posting content " * 30},
        )

        self.assertEqual(candidate["warnings"], "")
        self.assertEqual(discovery.processing_status(candidate), "ready")

    def test_manual_details_requeue_automatic_enrichment_after_user_input(self):
        candidate = {
            "company": "Example Labs",
            "title": "Technical Program Manager",
            "url": "https://www.linkedin.com/jobs/view/1234567890",
            "canonical_url": "",
            "description_text": "",
            "detail_attempt_count": str(discovery.MAX_DETAIL_ATTEMPTS),
            "detail_last_attempt_at": "2026-08-03T10:00:00",
            "detail_last_error": "Could not read LinkedIn.",
        }

        discovery.apply_manual_details(
            candidate,
            {"canonical_url": "https://jobs.example.com/technical-program-manager"},
        )

        self.assertEqual(candidate["detail_attempt_count"], "0")
        self.assertEqual(candidate["detail_last_attempt_at"], "")
        self.assertEqual(candidate["detail_last_error"], "")
        self.assertEqual(discovery.candidate_detail_state(candidate), "pending-enrichment")

    def test_detail_state_separates_automatic_work_from_user_input(self):
        automatic = {
            "company": "Example Labs",
            "title": "Technical Program Manager",
            "url": "https://jobs.example.com/tpm",
            "description_text": "Short summary",
            "warnings": "",
            "detail_attempt_count": "0",
        }
        source_verification = {
            **automatic,
            "source_platform": "linkedin",
            "description_text": "Lead technical programs across engineering and product teams. " * 10,
            "location": "United States",
            "work_mode": "Remote",
            "warnings": discovery.LINKEDIN_DETAILS_WARNING,
        }
        exhausted = {**automatic, "detail_attempt_count": str(discovery.MAX_DETAIL_ATTEMPTS)}

        self.assertEqual(discovery.candidate_detail_state(automatic), "pending-enrichment")
        self.assertEqual(discovery.candidate_detail_state(source_verification), "source-verification")
        self.assertEqual(discovery.candidate_detail_state(exhausted), "needs-input")
        self.assertTrue(all(gap["automatic"] for gap in discovery.candidate_detail_gaps(automatic)))

    def test_greenhouse_provider_resolves_job_without_browser(self):
        requested = []
        details, error = discovery.provider_candidate_details(
            "https://job-boards.greenhouse.io/example/jobs/1234567",
            fetcher=lambda url: requested.append(url) or {
                "html": (
                    '{"id":1234567,"title":"Senior Technical Program Manager",'
                    '"absolute_url":"https://job-boards.greenhouse.io/example/jobs/1234567",'
                    '"location":{"name":"Remote, United States"},'
                    '"content":"<p>Lead complex technical programs across engineering, product, and operations. '
                    'Manage dependencies, risks, milestones, launches, and durable delivery mechanisms.</p>"}'
                ),
                "error": "",
            },
        )

        self.assertEqual(error, "")
        self.assertEqual(details["title"], "Senior Technical Program Manager")
        self.assertEqual(details["location"], "Remote, United States")
        self.assertEqual(details["work_mode"], "Remote")
        self.assertEqual(details["availability_status"], "open")
        self.assertEqual(requested, ["https://boards-api.greenhouse.io/v1/boards/example/jobs/1234567?content=true"])

    def test_ashby_provider_trusts_structured_job_despite_captcha_script(self):
        page = """
        <html><head>
          <title>Principal Product Manager @ Example</title>
          <script type="application/ld+json">{
            "@type": "JobPosting",
            "title": "Principal Product Manager",
            "description": "Lead product strategy, requirements, delivery, and cross-functional execution for a complex platform role.",
            "jobLocation": {"address": {"addressLocality": "Remote", "addressCountry": "US"}}
          }</script>
          <script>window.captchaProvider = "available";</script>
        </head><body><p>Apply now</p></body></html>
        """

        details, error = discovery.provider_candidate_details(
            "https://jobs.ashbyhq.com/example/59a468ef-aef4-41a1-8bf2-5c920524e5d0",
            fetcher=lambda _url: {"html": page, "error": ""},
        )

        self.assertEqual(error, "")
        self.assertEqual(details["title"], "Principal Product Manager")
        self.assertEqual(details["availability_status"], "open")

    def test_lever_provider_uses_individual_posting_api(self):
        requested = []
        details, error = discovery.provider_candidate_details(
            "https://jobs.lever.co/arcadia/8d01e985-fc84-4097-ab31-ca2a328d8e11",
            fetcher=lambda url: requested.append(url) or {
                "html": json.dumps({
                    "id": "8d01e985-fc84-4097-ab31-ca2a328d8e11",
                    "text": "Principal Product Manager, AI Product",
                    "hostedUrl": "https://jobs.lever.co/arcadia/8d01e985-fc84-4097-ab31-ca2a328d8e11",
                    "categories": {"location": "Remote (USA)"},
                    "workplaceType": "remote",
                    "descriptionPlain": "Lead product strategy and delivery across engineering and design teams. " * 8,
                    "lists": [{"text": "Requirements", "content": "<li>Own complex platform programs.</li>"}],
                }),
                "error": "",
            },
        )

        self.assertEqual(error, "")
        self.assertEqual(details["title"], "Principal Product Manager, AI Product")
        self.assertEqual(details["location"], "Remote (USA)")
        self.assertEqual(details["work_mode"], "Remote")
        self.assertEqual(details["availability_status"], "open")
        self.assertIn("Requirements", details["description_text"])
        self.assertEqual(
            requested,
            ["https://api.lever.co/v0/postings/arcadia/8d01e985-fc84-4097-ab31-ca2a328d8e11"],
        )

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

    def test_search_preserves_partial_results_when_an_injected_provider_fails(self):
        search = self.save_search("Technical platforms", "technical program manager")
        browser_requests = []

        def browser_searcher(engine, value, page):
            browser_requests.append((engine, page))
            if engine == "linkedin":
                return []
            if page == 1:
                raise RuntimeError("Injected search provider failed.")
            return [
                {
                    "url": f"https://www.linkedin.com/jobs/view/{1234567800 + index}",
                    "title": f"Technical Program Manager {index}",
                    "snippet": "Minnesota technical program role.",
                }
                for index in range(10)
            ]

        result = discovery.run_search(search["id"], browser_searcher=browser_searcher)

        self.assertEqual(
            [(engine, page) for engine, page in browser_requests if engine == "google"],
            [("google", 0), ("google", 1)],
        )
        self.assertEqual(result["evaluated_count"], 10)
        self.assertEqual(result["found_count"], 10)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("provider failed", result["errors"][0])

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

    def test_search_rejects_ats_redirect_to_company_board(self):
        search = self.save_search("Technical platforms", "technical program manager")
        result_url = "https://job-boards.greenhouse.io/example/jobs/1234567"
        board_url = "https://job-boards.greenhouse.io/example?error=true"
        page = """
        <html><head><script type="application/ld+json">
          {
            "@type": "JobPosting",
            "title": "Senior Technical Program Manager",
            "description": "Lead platform programs, planning, delivery, risk management, and launches.",
            "hiringOrganization": {"name": "Example Labs"},
            "jobLocationType": "TELECOMMUTE"
          }
        </script></head><body>Apply now</body></html>
        """

        result = discovery.run_search(
            search["id"],
            browser_searcher=lambda engine, value, page: (
                [{
                    "url": result_url,
                    "title": "Senior Technical Program Manager at Example Labs",
                    "snippet": "United States · Remote · Lead platform programs.",
                }]
                if engine == "google"
                else []
            ),
            posting_fetcher=lambda url: {
                "status": 200,
                "final_url": board_url,
                "html": page,
                "error": "",
            },
        )

        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["found_count"], 0)
        self.assertGreaterEqual(result["skipped_count"], 1)
        self.assertEqual(result["skip_reasons"]["invalid-posting-page"], 1)
        self.assertEqual(repository.read_discovery_candidates(), [])

    def test_search_screens_aggregator_from_new_but_keeps_diagnostic_record(self):
        search = self.save_search("Technical platforms", "technical program manager")
        result_url = "https://jobright.ai/jobs/info/123"
        description = (
            "Lead technical platform programs across planning, execution, dependency management, risk reviews, "
            "stakeholder communication, launch readiness, operating mechanisms, and continuous improvement. "
            "Partner with product and engineering leaders to define milestones, resolve ambiguity, and deliver "
            "measurable outcomes across distributed teams."
        )
        page = f"""
        <html><head><script type="application/ld+json">
          {{
            "@type": "JobPosting",
            "title": "Principal Technical Program Manager",
            "description": "{description}",
            "hiringOrganization": {{"name": "Providence Health & Services"}},
            "jobLocationType": "TELECOMMUTE"
          }}
        </script></head><body>Apply now</body></html>
        """

        result = discovery.run_search(
            search["id"],
            browser_searcher=lambda engine, value, page: (
                [{
                    "url": result_url,
                    "title": "Principal Technical Program Manager at Providence Health & Services | Jobright.ai",
                    "snippet": "United States · Remote · Lead technical platform programs.",
                }]
                if engine == "google"
                else []
            ),
            posting_fetcher=lambda url: {
                "status": 200,
                "final_url": result_url,
                "html": page,
                "error": "",
            },
        )

        candidate = result["captured"][0]
        self.assertEqual(result["qualified_count"], 0)
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(result["screened_count"], 1)
        self.assertEqual(
            result["screened_reasons"][
                "the posting is from an aggregator without a verified employer source"
            ],
            1,
        )
        self.assertEqual(candidate["status"], discovery.SCREENED_STATUS)
        self.assertIn("aggregator", candidate["warnings"].lower())
        extracted = discovery.extracted_candidate(
            result_url,
            fetcher=lambda url: {
                "status": 200,
                "final_url": result_url,
                "html": page,
                "error": "",
            },
        )
        self.assertEqual(extracted["company"], "Providence Health & Services")

    def test_source_trust_checks_low_trust_host_before_company_domain(self):
        candidate = {
            "canonical_url": "https://jobright.ai/jobs/info/123",
            "source_platform": "employer",
            "freshness_status": "",
        }
        company = {"website": "https://jobright.ai"}

        trust = discovery.candidate_source_trust(candidate, company)

        self.assertEqual(trust["id"], "aggregator")
        self.assertFalse(trust["is_direct_employer_source"])

    def test_source_trust_redetects_workday_from_canonical_url(self):
        candidate = {
            "canonical_url": (
                "https://zillow.wd5.myworkdayjobs.com/en-US/"
                "Zillow_Group_External/job/Remote-United-States/"
                "Principal-Technical-Program-Manager_P750335-1"
            ),
            "source_platform": "employer",
            "freshness_status": "",
        }

        trust = discovery.candidate_source_trust(candidate)

        self.assertEqual(trust["id"], "employer")
        self.assertTrue(trust["is_direct_employer_source"])

    def test_work_mode_requires_role_level_remote_evidence(self):
        self.assertEqual(
            discovery.work_mode_from_text(
                "",
                "Collaborate with remote teams and support remote monitoring tools.",
            ),
            "",
        )
        self.assertEqual(
            discovery.work_mode_from_text("", "This position is fully remote."),
            "Remote",
        )
        self.assertEqual(
            discovery.work_mode_from_text("", "United States · Remote · Own platform programs."),
            "Remote",
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
            "hiringOrganization": {
              "name": "Example Labs",
              "industry": "Software Development",
              "numberOfEmployees": "201-500",
              "sameAs": "https://www.linkedin.com/company/example-labs"
            },
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
        discovered_company = companies.get_company(candidate["company_id"])
        self.assertEqual(discovered_company["name"], "Example Labs")
        self.assertEqual(candidate["title"], "Senior Technical Program Manager, Developer Platform")
        self.assertEqual(candidate["location"], "Minneapolis, MN, US")
        self.assertEqual(discovered_company["industry"], "Software Development")
        self.assertEqual(discovered_company["company_size"], "201–500 employees")
        self.assertTrue(candidate["company_id"])
        self.assertEqual(discovered_company["tracking_status"], "discovered")
        self.assertEqual(candidate["processing_status"], "ready")
        self.assertGreaterEqual(int(candidate["fit_score"]), 45)
        self.assertEqual(second["captured"][0]["id"], candidate["id"])
        self.assertEqual(len(repository.read_discovery_candidates()), 1)
        ingested = discovery.ingest_candidate(candidate["id"])
        company = next(
            row for row in repository.read_companies()
            if row["id"] == ingested["posting"]["company_id"]
        )
        self.assertEqual(company["industry"], "Software Development")
        self.assertEqual(company["company_size"], "201–500 employees")
        self.assertEqual(company["company_profile_url"], "https://www.linkedin.com/company/example-labs")
        undone = discovery.undo_candidate_decision(
            candidate["id"],
            "pursued",
            application_id=ingested["posting"]["id"],
            remove_posting=True,
        )
        self.assertTrue(undone["posting_removed"])
        self.assertEqual(undone["candidate"]["status"], "new")
        self.assertEqual(repository.read_applications(), [])

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
        discovery.continue_enrichment(
            limit=1,
            browser_detailer=lambda _url: {"availability_status": "open"},
            company_researcher=lambda *_args: {},
        )
        candidate = discovery.get_candidate(candidate["id"])
        ingested = discovery.ingest_candidate(candidate["id"])

        self.assertEqual(candidate["processing_status"], "ready")
        self.assertEqual(candidate["warnings"], "")
        self.assertEqual(ingested["posting"]["company"], "New Company")
        self.assertEqual(ingested["posting"]["role"], "Technical Program Manager, Developer Experience")
        self.assertEqual(ingested["posting"]["source_url"], "https://jobs.new-company.example/roles/tpm-devex")
        self.assertEqual(discovery.get_candidate(candidate["id"])["status"], "pursued")
        self.assertEqual(len(repository.read_posting_snapshots(ingested["posting"]["id"])), 1)

    def test_candidate_details_can_select_existing_or_create_new_company(self):
        search = self.save_search("Flexible company", "technical program manager")
        original = companies.upsert_company("", {"name": "Original Labs"})
        existing = companies.upsert_company("", {"name": "Existing Systems"})
        candidate = discovery.capture_candidates(
            search["id"],
            "https://www.linkedin.com/jobs/view/2468013579",
            details={
                "company_id": original["id"],
                "company_name": original["name"],
                "title": "Technical Program Manager",
            },
        )["captured"][0]

        selected = discovery.update_candidate_details(
            candidate["id"],
            {
                "company_id": existing["id"],
                "company_name": existing["name"],
            },
        )
        self.assertEqual(selected["company_id"], existing["id"])

        created = discovery.update_candidate_details(
            candidate["id"],
            {
                "company_id": "",
                "company_name": "Newly Discovered Company",
            },
        )
        created_company = companies.get_company(created["company_id"])
        self.assertNotIn(created["company_id"], {original["id"], existing["id"]})
        self.assertEqual(created_company["name"], "Newly Discovered Company")
        self.assertEqual(created_company["tracking_status"], "discovered")

    def test_mark_duplicate_associates_existing_posting_and_preserves_decision(self):
        search = self.save_search("Platforms", "technical program manager")
        company = companies.upsert_company("", {"name": "Example Labs"})
        posting = applications.create_application(
            {
                "company_id": company["id"],
                "company": company["name"],
                "role": "Technical Program Manager, Platform",
                "source_url": "https://jobs.example.com/roles/platform-tpm",
            }
        )
        candidate = {field: "" for field in discovery.schema.DISCOVERY_CANDIDATE_FIELDS}
        candidate.update(
            {
                "id": "DC0001",
                "search_id": search["id"],
                "company_id": company["id"],
                "title": "Technical Program Manager - Platform",
                "url": "https://www.linkedin.com/jobs/view/1234567890",
                "status": "new",
                "processing_status": "ready",
            }
        )
        repository.write_discovery_candidates([candidate])

        result = discovery.mark_candidate_duplicate(candidate["id"], posting["id"])

        self.assertEqual(result["candidate"]["status"], "duplicate")
        self.assertEqual(result["candidate"]["ingested_application_id"], posting["id"])
        self.assertEqual(result["posting"]["id"], posting["id"])
        self.assertFalse(discovery.enrichment_needed(result["candidate"], company))

        rediscovered = dict(candidate)
        rediscovered.update({"id": "DC0002", "status": "new", "last_seen_at": "2026-07-26T12:00:00"})
        repository.write_discovery_candidates([result["candidate"], rediscovered])
        discovery.canonicalize_candidates()
        preserved = discovery.list_candidates()[0]

        self.assertEqual(preserved["status"], "duplicate")
        self.assertEqual(preserved["ingested_application_id"], posting["id"])

    def test_mark_duplicate_rejects_unknown_posting(self):
        candidate = {field: "" for field in discovery.schema.DISCOVERY_CANDIDATE_FIELDS}
        candidate.update({"id": "DC0001", "status": "new"})
        repository.write_discovery_candidates([candidate])

        with self.assertRaisesRegex(ValueError, "No application found"):
            discovery.mark_candidate_duplicate(candidate["id"], "A9999")

        self.assertEqual(discovery.get_candidate(candidate["id"])["status"], "new")

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
        candidate = discovery.list_candidates()[0]
        self.assertEqual(candidate["search_ids"], [first_search["id"], second_search["id"]])

    def test_generic_career_page_titles_do_not_merge_distinct_postings(self):
        company = companies.upsert_company("", {"name": "Microsoft"})
        rows = []
        for candidate_id, job_id in [("DC0001", "100"), ("DC0002", "200")]:
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": candidate_id,
                    "company_id": company["id"],
                    "title": "| Microsoft Careers",
                    "url": f"https://apply.careers.microsoft.com/careers/job/{job_id}",
                    "canonical_url": f"https://apply.careers.microsoft.com/careers/job/{job_id}",
                    "status": discovery.SCREENED_STATUS,
                }
            )
            rows.append(row)

        self.assertEqual(len(discovery.canonicalize_candidate_rows(rows)), 2)

    def test_location_mismatch_is_screened_before_review(self):
        search = self.save_search(
            "Minnesota TPM",
            "technical program manager",
            lanes=[
                {
                    "id": "minnesota",
                    "label": "Minnesota",
                    "location": "Minnesota",
                    "work_modes": ["on-site", "hybrid"],
                },
                {
                    "id": "remote-us",
                    "label": "United States remote",
                    "location": "United States",
                    "work_modes": ["remote"],
                },
            ],
        )
        candidate = {
            "title": "Technical Program Manager",
            "canonical_url": "https://jobs.ashbyhq.com/example/4e79049a-0372-4692-a0a7-61906fb12676",
            "source_platform": "ashby",
            "processing_status": "ready",
            "fit_score": "80",
            "freshness_status": "confirmed-open",
            "description_text": "Lead technical delivery, dependencies, roadmap planning, and launch readiness.",
            "location": "Los Angeles, CA",
            "work_mode": "on-site",
        }

        admitted, reason = discovery.candidate_review_admission(candidate, search=search)

        self.assertFalse(admitted)
        self.assertIn("outside the configured location lanes", reason)

    def test_unknown_location_remains_recoverable_without_overwriting_user_decisions(self):
        search = self.save_search(
            "US remote TPM",
            "technical program manager",
            lanes=[
                {
                    "id": "remote-us",
                    "label": "United States remote",
                    "location": "United States",
                    "work_modes": ["remote"],
                }
            ],
        )
        base = {
            "title": "Technical Program Manager",
            "canonical_url": "https://jobs.example.com/roles/123",
            "source_platform": "employer",
            "processing_status": "partial",
            "fit_score": "80",
            "freshness_status": "",
            "description_text": "",
            "location": "",
            "work_mode": "",
        }
        candidate = {**base, "status": discovery.SCREENED_STATUS}
        ignored = {**base, "status": "ignored"}

        self.assertFalse(discovery.apply_candidate_review_admission(candidate, search=search))
        self.assertEqual(candidate["status"], "new")
        self.assertEqual(candidate["qualification_status"], "needs-verification")
        self.assertEqual(discovery.candidate_review_state(candidate), "needs-qualification")
        self.assertEqual(discovery.detail_enrichment_targets(rows=[candidate]), [candidate])
        self.assertFalse(discovery.apply_candidate_review_admission(ignored, search=search))
        self.assertEqual(ignored["status"], "ignored")

    def test_adjacent_title_requires_responsibility_evidence(self):
        search = self.save_search("TPM", "technical program manager")
        relevant = {
            "title": "Director, Platform Delivery",
            "description_text": "Own cross-functional technical delivery, dependencies, risk management, and launch readiness.",
        }
        unrelated = {
            "title": "Director, Sales",
            "description_text": "Own pipeline development and account growth.",
        }

        self.assertTrue(discovery.candidate_title_matches_search(relevant, search))
        self.assertFalse(discovery.candidate_title_matches_search(unrelated, search))

    def test_us_remote_lane_rejects_foreign_remote_role(self):
        lane = {
            "id": "remote-us",
            "label": "United States remote",
            "location": "United States",
            "work_modes": ["remote"],
        }

        self.assertFalse(
            discovery.candidate_matches_lane(
                {
                    "location": "Remote; Bengaluru, India; India",
                    "work_mode": "Remote",
                    "description_text": "This role is remote within India.",
                },
                {},
                lane,
            )
        )

    def test_us_remote_lane_ignores_pronoun_us_in_foreign_role_description(self):
        lane = {
            "id": "remote-us",
            "label": "United States remote",
            "location": "United States",
            "work_modes": ["remote"],
        }

        self.assertFalse(
            discovery.candidate_matches_lane(
                {
                    "location": "Remote; Newcastle, New South Wales, AU; AU",
                    "work_mode": "Remote",
                    "description_text": (
                        "We are an Australian company with a presence across North America, "
                        "and we are looking for someone to help us improve product operations."
                    ),
                },
                {},
                lane,
            )
        )

    def test_us_remote_lane_accepts_explicit_us_remote_role(self):
        lane = {
            "id": "remote-us",
            "label": "United States remote",
            "location": "United States",
            "work_modes": ["remote"],
        }

        self.assertTrue(
            discovery.candidate_matches_lane(
                {
                    "location": "Remote; US",
                    "work_mode": "Remote",
                    "description_text": "This role is remote within the United States.",
                },
                {},
                lane,
            )
        )

    def test_lane_matching_prefers_explicit_hybrid_policy_over_remote_flag(self):
        lane = {
            "id": "remote-us",
            "label": "United States remote",
            "location": "United States",
            "work_modes": ["remote"],
        }

        self.assertFalse(
            discovery.candidate_matches_lane(
                {
                    "location": "Livermore, CA, US",
                    "work_mode": "Remote",
                    "description_text": (
                        "Our hybrid roles combine on-site collaboration with flexibility. "
                        "You will work 3+ days per week on-site and remotely for the balance."
                    ),
                },
                {},
                lane,
            )
        )

    def test_optional_telecommuting_does_not_override_fixed_office_location(self):
        self.assertEqual(
            discovery.work_mode_from_text(
                "Newark, New Jersey, USA",
                (
                    "Position is fixed location based in the Newark office; however, "
                    "telecommuting from a home office may also be allowed."
                ),
            ),
            "On-site",
        )

    def test_reclassify_memberships_preserves_decisions_and_unassigns_bad_active_rows(self):
        tpm = discovery.upsert_search(
            "",
            {
                "name": "TPM",
                "keywords": "",
                "role_family_ids": ["technical-program"],
                "lanes": [{"id": "remote-us", "label": "US remote", "location": "United States", "work_modes": ["remote"]}],
            },
        )
        product = discovery.upsert_search(
            "",
            {
                "name": "Product",
                "keywords": "",
                "role_family_ids": ["product-platform"],
                "lanes": [{"id": "remote-us", "label": "US remote", "location": "United States", "work_modes": ["remote"]}],
            },
        )
        rows = []
        for candidate_id, title, status in [
            ("DC0001", "Senior Technical Product Manager", "new"),
            ("DC0002", "Account Executive", "new"),
            ("DC0003", "Senior Technical Product Manager", "ignored"),
        ]:
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": candidate_id,
                    "search_id": tpm["id"],
                    "title": title,
                    "status": status,
                    "location": "United States",
                    "work_mode": "remote",
                    "fit_score": "80",
                    "processing_status": "ready",
                    "freshness_status": "confirmed-open",
                    "canonical_url": f"https://jobs.example.com/{candidate_id}",
                    "url": f"https://jobs.example.com/{candidate_id}",
                    "description_text": "Own product strategy, roadmap, technical delivery, requirements, and launches.",
                }
            )
            rows.append(row)
        repository.write_discovery_candidates(rows)

        result = discovery.reclassify_candidate_search_memberships()
        candidates = {candidate["id"]: candidate for candidate in discovery.list_candidates()}

        self.assertGreaterEqual(result["changed_count"], 2)
        self.assertEqual(candidates["DC0001"]["search_ids"], [product["id"]])
        self.assertEqual(candidates["DC0002"]["search_ids"], [])
        self.assertEqual(candidates["DC0002"]["status"], discovery.SCREENED_STATUS)
        self.assertEqual(candidates["DC0003"]["status"], "ignored")
        self.assertIn(tpm["id"], candidates["DC0003"]["search_ids"])
        self.assertIn(product["id"], candidates["DC0003"]["search_ids"])

    def test_canonicalization_merges_source_urls_and_preserves_decision(self):
        company = companies.upsert_company("", {"name": "Example Labs"})
        rows = []
        for candidate_id, url, status in [
            ("DC0001", "https://www.linkedin.com/jobs/view/1234567890", "new"),
            ("DC0002", "https://jobs.example.com/roles/platform-tpm", "ingested"),
        ]:
            row = {field: "" for field in discovery.schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": candidate_id,
                    "company_id": company["id"],
                    "title": "Technical Program Manager, Platform",
                    "url": url,
                    "canonical_url": url if candidate_id == "DC0002" else "",
                    "status": status,
                    "processing_status": "ready",
                    "last_seen_at": f"2026-07-2{1 if candidate_id == 'DC0001' else 2}T10:00:00",
                }
            )
            rows.append(row)
        repository.write_discovery_candidates(rows)

        result = discovery.canonicalize_candidates()
        candidate = discovery.list_candidates()[0]

        self.assertEqual(result["merged_count"], 1)
        self.assertEqual(candidate["status"], "pursued")
        self.assertEqual(len(candidate["source_urls"]), 2)
        self.assertEqual(len(repository.read_discovery_candidates()), 1)

    def test_canonicalization_uses_preserved_source_urls_for_deduplication(self):
        shared = "https://www.linkedin.com/jobs/view/1234567890"
        rows = []
        for candidate_id, direct_url, source_urls in [
            ("DC0001", "https://jobs.example.com/roles/tpm", [shared, "https://jobs.example.com/roles/tpm"]),
            ("DC0002", "https://jobs.example.com/roles/tpm-new", [shared, "https://jobs.example.com/roles/tpm-new"]),
        ]:
            row = {field: "" for field in discovery.schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": candidate_id,
                    "company": "Example Labs",
                    "title": "Technical Program Manager",
                    "url": direct_url,
                    "canonical_url": direct_url,
                    "source_urls_json": discovery.json.dumps(source_urls),
                    "status": "new",
                    "processing_status": "ready",
                }
            )
            rows.append(row)
        repository.write_discovery_candidates(rows)

        result = discovery.canonicalize_candidates()
        candidate = discovery.list_candidates()[0]

        self.assertEqual(result["merged_count"], 1)
        self.assertEqual(len(candidate["source_urls"]), 3)

    def test_backlog_uses_provider_before_browser_and_becomes_ready(self):
        search = self.save_search("Platforms", "technical program manager")
        company = companies.upsert_company("", {"name": "Example Labs"})
        row = {field: "" for field in discovery.schema.DISCOVERY_CANDIDATE_FIELDS}
        row.update(
            {
                "id": "DC0001",
                "search_id": search["id"],
                "company_id": company["id"],
                "company": company["name"],
                "title": "Senior Technical Program Manager",
                "url": "https://job-boards.greenhouse.io/example/jobs/1234567",
                "canonical_url": "https://job-boards.greenhouse.io/example/jobs/1234567",
                "source_platform": "greenhouse",
                "status": "new",
                "processing_status": "partial",
                "warnings": "",
            }
        )
        repository.write_discovery_candidates([row])
        browser_calls = []

        result = discovery.enrich_candidate_backlog(
            search_id=search["id"],
            fetcher=lambda _url: {
                "html": (
                    '{"id":1234567,"title":"Senior Technical Program Manager",'
                    '"absolute_url":"https://job-boards.greenhouse.io/example/jobs/1234567",'
                    '"location":{"name":"Remote, United States"},'
                    '"content":"<p>Lead complex technical programs across engineering and product teams. '
                    'Own dependencies, risks, milestones, launches, and delivery mechanisms for platform work. '
                    'Partner with senior leaders to define strategy, sequence roadmaps, communicate tradeoffs, '
                    'and improve the operating system used by multiple teams. Build planning mechanisms, define '
                    'success criteria, identify delivery risks early, and guide programs from definition through '
                    'launch. Work closely with engineering managers, product managers, design, operations, and '
                    'customer teams to turn ambiguous needs into clear plans. Establish durable reporting, review '
                    'quality and readiness, resolve cross-team blockers, and continuously improve execution.</p>"}'
                ),
                "error": "",
            },
            browser_detailer=lambda url: browser_calls.append(url) or {},
        )

        candidate = discovery.list_candidates()[0]
        self.assertEqual(result["ready_count"], 1)
        self.assertEqual(candidate["detail_state"], "ready")
        self.assertEqual(candidate["detail_attempt_count"], "1")
        self.assertEqual(browser_calls, [])

    def test_backlog_skips_candidates_from_not_interested_companies(self):
        company = companies.upsert_company("", {"name": "Example Labs", "interest_status": "not-interested"})
        row = {field: "" for field in discovery.schema.DISCOVERY_CANDIDATE_FIELDS}
        row.update(
            {
                "id": "DC0001",
                "company_id": company["id"],
                "company": company["name"],
                "title": "Technical Program Manager",
                "url": "https://jobs.example.com/roles/tpm",
                "status": "new",
                "processing_status": "partial",
            }
        )
        repository.write_discovery_candidates([row])

        self.assertEqual(discovery.detail_enrichment_targets(), [])
        result = discovery.enrich_candidate_backlog(browser_detailer=lambda _url: self.fail("Excluded role should not be opened"))
        self.assertEqual(result["remaining_count"], 0)
        self.assertEqual(result["state_counts"]["pending-enrichment"], 0)

    def test_continue_enrichment_finishes_posting_and_company_details(self):
        company = companies.upsert_company("", {"name": "Example Labs", "tracking_status": "discovered"})
        row = {field: "" for field in discovery.schema.DISCOVERY_CANDIDATE_FIELDS}
        row.update(
            {
                "id": "DC0001",
                "company_id": company["id"],
                "title": "Technical Program Manager",
                "url": "https://jobs.example.com/roles/platform-tpm",
                "status": "new",
                "processing_status": "partial",
                "captured_at": "2026-07-20T10:00:00",
                "last_seen_at": "2026-07-20T10:00:00",
            }
        )
        repository.write_discovery_candidates([row])

        result = discovery.continue_enrichment(
            limit=10,
            browser_detailer=lambda _url: {
                "company": "Example Labs",
                "title": "Technical Program Manager",
                "canonical_url": "https://jobs.example.com/roles/platform-tpm",
                "location": "Remote, United States",
                "description_text": "Remote role. " + "Lead technical program delivery across engineering and product teams. " * 20,
                "company_industry": "Software Development",
                "company_size": "201-500 employees",
                "availability_status": "open",
            },
            company_researcher=lambda *_args: {},
        )
        candidate = discovery.list_candidates()[0]
        enriched_company = companies.get_company(company["id"])

        self.assertEqual(result["processed_count"], 1)
        self.assertEqual(candidate["processing_status"], "ready")
        self.assertEqual(candidate["freshness_status"], "confirmed-open")
        self.assertEqual(candidate["source_confidence"], "High")
        self.assertEqual(enriched_company["industry"], "Software Development")
        self.assertEqual(enriched_company["company_size"], "201–500 employees")

    def test_source_trust_distinguishes_employer_network_and_aggregator(self):
        base = {
            "status": "new",
            "processing_status": "ready",
            "fit_score": "80",
            "freshness_status": "confirmed-open",
            "company": "Example",
            "title": "Senior Product Manager",
            "location": "Remote; United States",
            "description_text": "Responsibilities and requirements. " * 30,
        }
        employer = discovery.candidate_payload(
            {
                **base,
                "canonical_url": "https://jobs.ashbyhq.com/example/role",
                "source_platform": "ashby",
            }
        )
        network = discovery.candidate_payload(
            {
                **base,
                "canonical_url": "https://www.linkedin.com/jobs/view/123",
                "source_platform": "linkedin",
            }
        )
        aggregator = discovery.candidate_payload(
            {
                **base,
                "canonical_url": "https://infosecjobboard.com/job/123",
                "source_platform": "employer",
            }
        )

        self.assertEqual(employer["source_trust"], "employer")
        self.assertTrue(employer["recommendation_eligible"])
        self.assertEqual(network["source_trust"], "network")
        self.assertTrue(network["recommendation_eligible"])
        self.assertEqual(aggregator["source_trust"], "aggregator")
        self.assertFalse(aggregator["recommendation_eligible"])
        self.assertNotIn("Direct employer posting available", aggregator["fit_strengths"])

    def test_bulk_candidate_status_transition_updates_only_selected_results(self):
        rows = []
        for index in range(1, 4):
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update({
                "id": f"DC{index:04d}",
                "title": f"Role {index}",
                "url": f"https://example.com/jobs/{index}",
                "status": "new",
                "processing_status": "ready",
            })
            rows.append(row)
        repository.write_discovery_candidates(rows)

        result = discovery.update_candidate_statuses(
            ["DC0001", "DC0003"],
            "ignored",
            ignore_reason="wrong-role",
        )
        stored = {
            row["id"]: (row["status"], row["ignore_reason"])
            for row in repository.read_discovery_candidates()
        }

        self.assertEqual(result["count"], 2)
        self.assertEqual(stored, {
            "DC0001": ("ignored", "wrong-role"),
            "DC0002": ("new", ""),
            "DC0003": ("ignored", "wrong-role"),
        })

    def test_remote_reposting_sites_are_low_trust_aggregators(self):
        for url in [
            "https://remotezest.up.railway.app/job/remote-senior-technical-program-manager-25",
            "https://remoteclickjobs-production.up.railway.app/job/remote-technical-program-manager-14",
            "https://remowork.life/jobs/example",
            "https://remoteineurope.com/job/lead-technical-program-manager",
            "https://www.theladders.com/job/example",
            "https://jobs.usvetjobs.com/job/program-manager/85207456",
        ]:
            with self.subTest(url=url):
                trust = discovery.candidate_source_trust(
                    {
                        "canonical_url": url,
                        "source_platform": "employer",
                    }
                )
                self.assertEqual(trust["id"], "aggregator")

    def test_apply_and_undo_search_exclusions_only_changes_matching_new_roles(self):
        search = {field: "" for field in schema.DISCOVERY_SEARCH_FIELDS}
        search.update(
            {
                "id": "DS0001",
                "name": "TPM",
                "keywords": "technical program manager",
                "lanes_json": '[{"id":"lane-1","label":"US","location":"United States","work_modes":["remote"]}]',
                "excluded_terms_json": '["infrastructure"]',
            }
        )
        repository.write_discovery_searches([search])
        rows = []
        for candidate_id, title, status in [
            ("DC0001", "Technical Program Manager, Infrastructure", "new"),
            ("DC0002", "Technical Program Manager, Product", "new"),
            ("DC0003", "Infrastructure Program Manager", "ingested"),
        ]:
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": candidate_id,
                    "search_id": "DS0001",
                    "title": title,
                    "status": status,
                }
            )
            rows.append(row)
        repository.write_discovery_candidates(rows)

        applied = discovery.apply_search_exclusions("DS0001")
        changed = {row["id"]: row for row in repository.read_discovery_candidates()}

        self.assertEqual(applied["candidate_ids"], ["DC0001"])
        self.assertEqual(changed["DC0001"]["status"], "ignored")
        self.assertEqual(changed["DC0001"]["ignore_reason"], "search-exclusion")
        self.assertEqual(changed["DC0002"]["status"], "new")
        self.assertEqual(changed["DC0003"]["status"], "pursued")

        restored = discovery.undo_search_exclusions(applied["candidate_ids"])
        changed = {row["id"]: row for row in repository.read_discovery_candidates()}

        self.assertEqual(restored["candidate_ids"], ["DC0001"])
        self.assertEqual(changed["DC0001"]["status"], "new")
        self.assertEqual(changed["DC0001"]["ignore_reason"], "")

    def test_ignore_reasons_keep_source_quality_feedback_out_of_keyword_suggestions(self):
        search = {field: "" for field in schema.DISCOVERY_SEARCH_FIELDS}
        search.update(
            {
                "id": "DS0001",
                "name": "TPM",
                "keywords": "technical program manager",
                "lanes_json": '[{"id":"lane-1","label":"US","location":"United States","work_modes":["remote"]}]',
            }
        )
        repository.write_discovery_searches([search])
        rows = []
        for index in range(2):
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": f"DC000{index + 1}",
                    "search_id": "DS0001",
                    "title": "Technical Program Manager, Infrastructure",
                    "status": "ignored",
                    "ignore_reason": "poor-source",
                }
            )
            rows.append(row)
        repository.write_discovery_candidates(rows)

        self.assertEqual(discovery.preference_suggestions(), [])

        discovery.update_candidate_status("DC0001", "ignored", "wrong-role")
        discovery.update_candidate_status("DC0002", "ignored", "wrong-role")

        self.assertEqual(discovery.preference_suggestions()[0]["term"], "infrastructure")

    def test_cleanup_candidates_reconciles_active_queue_without_changing_reviewed_history(self):
        search = self.save_search("TPM", "technical program manager")
        discovery.upsert_search(search["id"], {"excluded_terms": ["infrastructure"]})
        excluded_company = companies.upsert_company(
            "",
            {"name": "No Thanks Inc", "interest_status": "not-interested"},
        )
        rows = []
        for candidate_id, title, status, company_id, fit_score, url in [
            (
                "DC0001",
                "Technical Program Manager",
                "new",
                excluded_company["id"],
                "90",
                "https://www.linkedin.com/jobs/view/1000000001",
            ),
            (
                "DC0002",
                "Technical Program Manager, Infrastructure",
                "screened",
                "",
                "90",
                "https://www.linkedin.com/jobs/view/1000000002",
            ),
            (
                "DC0003",
                "Technical Program Manager",
                "new",
                "",
                "20",
                "https://www.linkedin.com/jobs/view/1000000003",
            ),
            (
                "DC0004",
                "Technical Program Manager",
                "ignored",
                "",
                "90",
                "https://www.linkedin.com/jobs/view/1000000004",
            ),
            (
                "DC0005",
                "Partner Program Manager",
                "new",
                "",
                "90",
                "https://www.linkedin.com/jobs/view/1000000005",
            ),
            (
                "DC0006",
                "Engineering Program Manager",
                "new",
                "",
                "90",
                "https://www.linkedin.com/jobs/view/1000000006",
            ),
        ]:
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": candidate_id,
                    "search_id": search["id"],
                    "title": title,
                    "status": status,
                    "company_id": company_id,
                    "fit_score": fit_score,
                    "location": "United States",
                    "work_mode": "on-site",
                    "url": url,
                    "canonical_url": url,
                }
            )
            rows.append(row)
        repository.write_discovery_candidates(rows)

        result = discovery.cleanup_candidates()
        changed = {
            candidate["id"]: candidate
            for candidate in repository.read_discovery_candidates()
        }

        self.assertEqual(result["ignored_company_count"], 1)
        self.assertEqual(result["ignored_exclusion_count"], 1)
        self.assertEqual(result["screened_count"], 2)
        self.assertEqual(changed["DC0001"]["status"], "new")
        self.assertEqual(changed["DC0001"]["ignore_reason"], "")
        self.assertEqual(changed["DC0002"]["status"], "ignored")
        self.assertEqual(changed["DC0002"]["ignore_reason"], "search-exclusion")
        self.assertEqual(changed["DC0003"]["status"], discovery.SCREENED_STATUS)
        self.assertEqual(changed["DC0004"]["status"], "ignored")
        self.assertEqual(changed["DC0005"]["status"], discovery.SCREENED_STATUS)
        self.assertEqual(changed["DC0006"]["status"], "new")

        second_result = discovery.cleanup_candidates()
        self.assertEqual(second_result["ignored_company_count"], 1)
        self.assertEqual(second_result["ignored_exclusion_count"], 0)
        self.assertEqual(second_result["screened_count"], 0)

    def test_cleanup_does_not_mutate_candidate_from_excluded_employer_hostname(self):
        company = companies.upsert_company(
            "",
            {"name": "Walmart", "interest_status": "not-interested"},
        )
        row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        row.update(
            {
                "id": "DC0001",
                "title": "(USA) Principal, Technical Program Manager",
                "url": "https://careers.walmart.com/us/en/jobs/R-2496298",
                "canonical_url": "https://careers.walmart.com/us/en/jobs/R-2496298",
                "status": "new",
            }
        )
        repository.write_discovery_candidates([row])

        result = discovery.cleanup_candidates()
        candidate = repository.read_discovery_candidates()[0]

        self.assertEqual(result["linked_count"], 1)
        self.assertEqual(result["ignored_company_count"], 1)
        self.assertEqual(candidate["company_id"], company["id"])
        self.assertEqual(candidate["status"], "new")
        self.assertEqual(candidate["ignore_reason"], "")

    def test_excluded_discovery_candidate_is_not_fetched_or_mutated(self):
        search = self.save_search("TPM", "technical program manager")
        company = companies.upsert_company(
            "",
            {
                "name": "Excluded Inc",
                "interest_status": "not-interested",
                "website": "https://excluded.example",
            },
        )
        called = []

        with self.assertRaisesRegex(ValueError, "not-interested"):
            discovery.capture_candidates(
                search["id"],
                "https://excluded.example/jobs/tpm",
                fetcher=lambda url: called.append(url),
            )

        self.assertEqual(called, [])
        self.assertEqual(repository.read_discovery_candidates(), [])
        self.assertEqual(companies.get_company(company["id"])["interest_status"], "not-interested")

    def test_excluded_discovery_candidate_cannot_be_opened_reopened_or_pursued(self):
        company = companies.upsert_company(
            "", {"name": "Archived Inc", "interest_status": "archived"}
        )
        candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        candidate.update(
            {
                "id": "DC0001",
                "company_id": company["id"],
                "company": company["name"],
                "title": "Technical Program Manager",
                "status": "ignored",
                "description_text": "This full posting must not enter default review.",
            }
        )
        repository.write_discovery_candidates([candidate])

        with self.assertRaisesRegex(ValueError, "archived"):
            discovery.get_candidate("DC0001")
        with self.assertRaisesRegex(ValueError, "archived"):
            discovery.update_candidate_status("DC0001", "new")
        with self.assertRaisesRegex(ValueError, "archived"):
            discovery.pursue_candidate("DC0001")

        opted_in = discovery.get_candidate("DC0001", include_excluded_companies=True)
        self.assertEqual(opted_in["id"], "DC0001")
        self.assertFalse(opted_in["recommendation_eligible"])
        self.assertEqual(repository.read_discovery_candidates()[0]["status"], "ignored")

    def test_cleanup_repairs_company_misattributed_through_shared_greenhouse_host(self):
        anthropic = companies.upsert_company(
            "",
            {
                "name": "Anthropic",
                "careers_url": "https://job-boards.greenhouse.io/anthropic",
            },
        )
        oura = companies.upsert_company("", {"name": "OURA"})
        row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        row.update(
            {
                "id": "DC0001",
                "company_id": anthropic["id"],
                "title": "Staff Engineering Program Manager, Core Tech",
                "url": "https://job-boards.greenhouse.io/oura/jobs/4360455009",
                "canonical_url": "https://job-boards.greenhouse.io/oura/jobs/4360455009",
                "description_text": (
                    "Job Application for Staff Engineering Program Manager, Core Tech at Ōura "
                    "Back to jobs New Staff Engineering Program Manager, Core Tech"
                ),
                "status": "new",
            }
        )
        repository.write_discovery_candidates([row])

        result = discovery.cleanup_candidates()
        candidate = repository.read_discovery_candidates()[0]

        self.assertEqual(result["linked_count"], 1)
        self.assertEqual(candidate["company_id"], oura["id"])
        self.assertNotEqual(candidate["company_id"], anthropic["id"])

    def test_connect_candidate_prefers_explicit_company_over_unrelated_shared_ats_company(self):
        anthropic = companies.upsert_company(
            "",
            {
                "name": "Anthropic",
                "careers_url": "https://job-boards.greenhouse.io/anthropic",
            },
        )
        candidate = {
            "company": "Keeper Security",
            "company_id": "",
            "title": "Senior Technical Product Manager",
            "url": "https://job-boards.greenhouse.io/keepersecurity/jobs/4364187009",
            "canonical_url": "https://job-boards.greenhouse.io/keepersecurity/jobs/4364187009",
        }

        linked = discovery.connect_candidate_company(candidate)

        self.assertEqual(linked["name"], "Keeper Security")
        self.assertNotEqual(linked["id"], anthropic["id"])

    def test_company_research_queue_fills_missing_active_companies_beyond_posting_batch(self):
        candidate_rows = []
        for index in range(5):
            company = companies.upsert_company(
                "",
                {"name": f"Example {index}", "tracking_status": "discovered"},
            )
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": f"DC000{index + 1}",
                    "company_id": company["id"],
                    "title": "Technical Program Manager",
                    "url": f"https://jobs.example{index}.com/role",
                    "canonical_url": f"https://jobs.example{index}.com/role",
                    "status": "new",
                    "processing_status": "ready",
                    "freshness_status": "confirmed-open",
                    "freshness_checked_at": discovery.now_iso(),
                }
            )
            candidate_rows.append(row)
        repository.write_discovery_candidates(candidate_rows)
        researched = []

        result = discovery.continue_enrichment(
            limit=1,
            browser_detailer=lambda _url: {},
            company_researcher=lambda name, _profile: researched.append(name) or {
                "company_industry": "Software Development",
                "company_size": "51-200 employees",
            },
        )

        self.assertEqual(result["company_researched_count"], discovery.COMPANY_RESEARCH_LIMIT)
        self.assertEqual(len(researched), discovery.COMPANY_RESEARCH_LIMIT)
        self.assertEqual(result["company_research_remaining_count"], 2)

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

    def test_review_state_distinguishes_detail_freshness_and_extraction_failure(self):
        base = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        base.update({
            "company_id": "CO0001",
            "title": "Senior Product Manager",
            "canonical_url": "https://example.com/jobs/123",
            "location": "Remote; United States",
            "description_text": "Responsibilities and requirements. " * 30,
            "processing_status": "ready",
            "freshness_status": "confirmed-open",
        })
        ready = dict(base)
        needs_detail = {**base, "description_text": "Too short", "processing_status": "partial"}
        needs_freshness = {**base, "freshness_status": "", "freshness_checked_at": ""}
        failed = {
            **needs_detail,
            "detail_attempt_count": str(discovery.MAX_DETAIL_ATTEMPTS),
            "detail_last_error": "No readable posting details were returned.",
        }

        self.assertEqual(discovery.candidate_review_state(ready), "ready")
        self.assertEqual(discovery.candidate_review_state(needs_detail), "needs-detail")
        self.assertEqual(discovery.candidate_review_state(needs_freshness), "needs-freshness")
        self.assertEqual(discovery.candidate_review_state(failed), "failed-extraction")

    def test_failed_freshness_check_is_recorded_for_review_ready_detail(self):
        company = companies.upsert_company("", {"name": "Example"})
        candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        candidate.update({
            "id": "DC0001",
            "company_id": company["id"],
            "title": "Senior Product Manager",
            "canonical_url": "https://jobs.example.com/roles/123",
            "location": "Remote; United States",
            "description_text": "Responsibilities and requirements. " * 30,
            "processing_status": "ready",
            "status": "new",
        })

        result = discovery.resolve_candidate_details(
            candidate,
            fetcher=lambda _url: {"html": "<html><title>Jobs</title></html>", "error": ""},
            company_rows=[company],
            company_candidates=[],
        )

        self.assertEqual(result["after_review_state"], "needs-freshness")
        self.assertEqual(candidate["freshness_status"], "needs-review")
        self.assertTrue(candidate["freshness_checked_at"])
        self.assertEqual(
            candidate["detail_last_error"],
            "The posting page did not provide verifiable job details.",
        )

    def test_ready_candidate_missing_freshness_enters_enrichment_backlog(self):
        company = companies.upsert_company("", {"name": "Example"})
        candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        candidate.update({
            "id": "DC0001",
            "company_id": company["id"],
            "title": "Senior Product Manager",
            "canonical_url": "https://example.com/jobs/123",
            "location": "Remote; United States",
            "description_text": "Responsibilities and requirements. " * 30,
            "processing_status": "ready",
            "status": "new",
        })

        targets = discovery.detail_enrichment_targets(
            rows=[candidate],
            company_rows=[company],
        )

        self.assertEqual([row["id"] for row in targets], ["DC0001"])

    def test_enrichment_backlog_can_target_one_existing_candidate(self):
        company = companies.upsert_company("", {"name": "Example"})
        candidates = []
        for candidate_id in ["DC0001", "DC0002"]:
            candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            candidate.update({
                "id": candidate_id,
                "company_id": company["id"],
                "title": "Senior Product Manager",
                "canonical_url": f"https://example.com/jobs/{candidate_id.lower()}",
                "location": "Remote; United States",
                "description_text": "Responsibilities and requirements. " * 30,
                "processing_status": "ready",
                "status": "new",
            })
            candidates.append(candidate)

        targets = discovery.detail_enrichment_targets(
            rows=candidates,
            candidate_id="dc0002",
            company_rows=[company],
        )

        self.assertEqual([row["id"] for row in targets], ["DC0002"])

    def test_targeted_enrichment_reports_only_the_target_remaining(self):
        company = companies.upsert_company("", {"name": "Example"})
        candidates = []
        for candidate_id in ["DC0001", "DC0002"]:
            candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            candidate.update({
                "id": candidate_id,
                "company_id": company["id"],
                "title": "Senior Product Manager",
                "canonical_url": f"https://jobs.example.com/roles/{candidate_id.lower()}",
                "location": "Remote; United States",
                "description_text": "Responsibilities and requirements. " * 30,
                "processing_status": "ready",
                "status": "new",
            })
            candidates.append(candidate)
        repository.write_discovery_candidates(candidates)

        result = discovery.enrich_candidate_backlog(
            candidate_id="DC0001",
            limit=1,
            fetcher=lambda _url: {"html": "<html><title>Jobs</title></html>", "error": ""},
        )

        self.assertEqual(result["target_count"], 1)
        self.assertEqual(result["remaining_count"], 1)
        self.assertEqual(result["manual_review_count"], 0)
        self.assertEqual(result["review_state_counts"]["needs-freshness"], 1)

    def test_distinct_requisitions_are_not_canonicalized_or_matched_to_posting(self):
        company = companies.upsert_company("", {"name": "Waymo"})
        posting = {field: "" for field in schema.APPLICATION_FIELDS}
        posting.update({
                "id": "A0064",
                "company": "Waymo",
                "company_id": company["id"],
                "role": "Senior Technical Program Manager, Simulation",
                "source_url": "https://careers.withwaymo.com/jobs?gh_jid=8026543",
                "stage": "considering",
        })
        repository.write_applications([posting])
        rows = []
        for candidate_id, requisition, title in [
            ("DC0001", "8109626", "Senior Product Manager, Autonomous Vehicle Reliability"),
            ("DC0002", "8026543", "Senior Technical Program Manager, Simulation"),
        ]:
            candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            candidate.update({
                "id": candidate_id,
                "company_id": company["id"],
                "title": title,
                "url": "https://careers.withwaymo.com/jobs",
                "canonical_url": f"https://careers.withwaymo.com/jobs?gh_jid={requisition}",
                "source_urls_json": json.dumps([
                    "https://careers.withwaymo.com/jobs",
                    f"https://careers.withwaymo.com/jobs?gh_jid={requisition}",
                ]),
                "status": "new",
            })
            rows.append(candidate)

        canonical = discovery.canonicalize_candidate_rows(rows)

        self.assertEqual(len(canonical), 2)
        self.assertIsNone(discovery.matching_application(rows[0]))
        self.assertEqual(discovery.matching_application(rows[1])["id"], "A0064")

    def test_unavailable_status_synchronizes_freshness_and_reopen_clears_closed(self):
        company = companies.upsert_company("", {"name": "GitHub"})
        candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        candidate.update({
            "id": "DC0570",
            "company_id": company["id"],
            "title": "Senior Product Operations Manager",
            "status": "new",
            "freshness_status": "confirmed-open",
            "freshness_checked_at": "2026-08-20T11:04:12",
        })
        repository.write_discovery_candidates([candidate])

        unavailable = discovery.update_candidate_status("DC0570", "unavailable")
        reopened = discovery.update_candidate_status("DC0570", "new")

        self.assertEqual(unavailable["freshness_status"], "closed")
        self.assertTrue(unavailable["freshness_checked_at"])
        self.assertEqual(reopened["freshness_status"], "")
        self.assertEqual(reopened["freshness_checked_at"], "")


if __name__ == "__main__":
    unittest.main()
