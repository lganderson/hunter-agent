import base64
import html as html_lib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from hunter import app_state, companies, discovery, mcp_server, paths, repository, schema, settings, sqlite_store


class HunterCompaniesTest(unittest.TestCase):
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

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        self.tempdir.cleanup()

    def test_initialize_adds_company_tables_and_company_id_without_losing_postings(self):
        legacy_fields = [field for field in schema.APPLICATION_FIELDS if field != "company_id"]
        with sqlite_store.connect() as connection:
            columns = ", ".join(f'"{field}" TEXT NOT NULL DEFAULT ""' for field in legacy_fields)
            connection.execute(f"CREATE TABLE applications ({columns}, PRIMARY KEY(id))")
            row = {field: "" for field in legacy_fields}
            row.update({"id": "A0001", "company": "Apple", "role": "Engineer", "stage": "posting-review"})
            connection.execute(
                f"INSERT INTO applications ({', '.join(legacy_fields)}) VALUES ({', '.join('?' for _ in legacy_fields)})",
                [row[field] for field in legacy_fields],
            )

        sqlite_store.initialize()

        app = repository.read_applications()[0]
        self.assertEqual(app["company"], "Apple")
        self.assertIn("company_id", app)
        with sqlite_store.connect() as connection:
            self.assertIn("companies", table_names(connection))
            self.assertIn("company_contacts", table_names(connection))
            self.assertIn("company_career_sources", table_names(connection))
            self.assertIn("company_posting_candidates", table_names(connection))
            self.assertIn("company_career_scans", table_names(connection))
            candidate_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(company_posting_candidates)").fetchall()
            }
            company_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(companies)").fetchall()
            }
            self.assertIn("location", candidate_columns)
            self.assertIn("source_platform", candidate_columns)
            self.assertIn("scan_state", candidate_columns)
            self.assertIn("industry", company_columns)
            self.assertIn("company_size", company_columns)
            self.assertIn("company_metadata_source", company_columns)
            self.assertIn("company_fit_score", company_columns)
            self.assertIn("company_discovery_source_url", company_columns)
            self.assertIn("company_location_fit", company_columns)
            self.assertIn("company_location_evidence", company_columns)

    def test_upsert_company_auto_associates_exact_posting_and_syncs_action_company(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "company": "Apple", "company_id": ""}),
        ])
        repository.write_actions([
            action_row({"id": "T0001", "application_id": "A0001", "company": ""}),
        ])

        company = companies.upsert_company("", {"name": "Apple", "interest_status": "interested"})

        app = repository.read_applications()[0]
        action = repository.read_actions()[0]
        self.assertEqual(company["id"], "CO0001")
        self.assertEqual(app["company_id"], "CO0001")
        self.assertEqual(app["company"], "Apple")
        self.assertEqual(action["company"], "Apple")

    def test_shared_job_board_matching_uses_employer_tenant_not_ats_hostname(self):
        sqlite_store.initialize()
        anthropic = companies.upsert_company(
            "",
            {
                "name": "Anthropic",
                "careers_url": "https://job-boards.greenhouse.io/anthropic",
            },
        )
        oura = companies.upsert_company("", {"name": "OURA"})

        self.assertEqual(
            companies.matching_company_record_from_url(
                "https://job-boards.greenhouse.io/anthropic/jobs/123"
            )["id"],
            anthropic["id"],
        )
        self.assertEqual(
            companies.matching_company_record_from_url(
                "https://job-boards.greenhouse.io/oura/jobs/456"
            )["id"],
            oura["id"],
        )
        self.assertIsNone(
            companies.matching_company_record_from_url(
                "https://job-boards.greenhouse.io/keepersecurity/jobs/789"
            )
        )

    def test_shared_job_board_matching_accepts_equivalent_tenant_suffix(self):
        sqlite_store.initialize()
        company = companies.upsert_company(
            "",
            {
                "name": "Rivian and Volkswagen Group Technologies",
                "careers_url": "https://jobs.ashbyhq.com/rivianvw",
            },
        )

        matched = companies.matching_company_record_from_url(
            "https://jobs.ashbyhq.com/rivianvw.tech/123"
        )

        self.assertEqual(matched["id"], company["id"])

    def test_initialize_adds_company_metadata_columns_without_losing_existing_company(self):
        with sqlite_store.connect() as connection:
            connection.execute(
                "CREATE TABLE companies ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', aliases TEXT NOT NULL DEFAULT '', "
                "interest_status TEXT NOT NULL DEFAULT 'neutral', website TEXT NOT NULL DEFAULT '', "
                "careers_url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', "
                "last_checked_at TEXT NOT NULL DEFAULT '', last_check_status TEXT NOT NULL DEFAULT '')"
            )
            connection.execute(
                "INSERT INTO companies(id, name, interest_status) VALUES('CO0001', 'Example Labs', 'interested')"
            )

        sqlite_store.initialize()

        company = repository.read_companies()[0]
        self.assertEqual(company["name"], "Example Labs")
        self.assertEqual(company["interest_status"], "interested")
        self.assertEqual(company["industry"], "")
        self.assertEqual(company["company_size"], "")
        self.assertEqual(company["tracking_status"], "tracked")

    def test_initialize_moves_discovery_company_details_to_linked_company_records(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example Labs"})
        legacy_company_fields = [
            "company",
            "company_industry",
            "company_size",
            "company_profile_url",
            "company_metadata_source",
        ]
        legacy_fields = [
            *schema.DISCOVERY_CANDIDATE_FIELDS[:3],
            *legacy_company_fields,
            *schema.DISCOVERY_CANDIDATE_FIELDS[3:],
        ]
        with sqlite_store.connect() as connection:
            connection.execute("DROP TABLE discovery_candidates")
            columns = ", ".join(
                f'"{field}" TEXT NOT NULL DEFAULT ""'
                for field in legacy_fields
            )
            connection.execute(
                f"CREATE TABLE discovery_candidates ({columns}, PRIMARY KEY(id), UNIQUE(search_id, url))"
            )
            row = {field: "" for field in legacy_fields}
            row.update(
                {
                    "id": "DC0001",
                    "search_id": "DS0001",
                    "company_id": company["id"],
                    "company": "Example Labs",
                    "company_industry": "Software Development",
                    "company_size": "201–500 employees",
                    "title": "Technical Program Manager",
                    "url": "https://example.com/jobs/1",
                }
            )
            connection.execute(
                f"INSERT INTO discovery_candidates ({', '.join(legacy_fields)}) "
                f"VALUES ({', '.join('?' for _ in legacy_fields)})",
                [row[field] for field in legacy_fields],
            )

        sqlite_store.initialize()

        candidate = repository.read_discovery_candidates()[0]
        self.assertEqual(candidate["company_id"], company["id"])
        self.assertEqual(candidate["title"], "Technical Program Manager")
        self.assertTrue(set(legacy_company_fields).isdisjoint(candidate))
        migrated_company = companies.get_company(company["id"])
        self.assertEqual(migrated_company["industry"], "Software Development")
        self.assertEqual(migrated_company["company_size"], "201–500 employees")
        with sqlite_store.connect() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(discovery_candidates)").fetchall()
            }
        self.assertEqual(columns, set(schema.DISCOVERY_CANDIDATE_FIELDS))

    def test_manual_company_metadata_is_normalized_and_preserved_from_automatic_updates(self):
        sqlite_store.initialize()
        company = companies.upsert_company(
            "",
            {
                "name": "Example Labs",
                "industry": "Software Development",
                "company_size": "201 - 500 people",
                "company_profile_url": "https://www.linkedin.com/company/example-labs/",
            },
        )

        refreshed = companies.update_company_metadata(
            company["id"],
            {
                "company_industry": "Technology, Information and Internet",
                "company_size": "501-1,000 employees",
            },
            source_url="https://www.linkedin.com/jobs/view/123",
        )

        self.assertEqual(refreshed["industry"], "Software Development")
        self.assertEqual(refreshed["company_size"], "201–500 employees")
        self.assertEqual(refreshed["company_metadata_source"], "manual")
        suggestions = companies.company_metadata_suggestions(refreshed)
        self.assertEqual(
            {suggestion["field"] for suggestion in suggestions},
            {"industry", "company_size"},
        )

    def test_discovery_records_company_without_enabling_career_tracking(self):
        sqlite_store.initialize()

        discovered = companies.record_discovered_company(
            {
                "company": "Example Labs",
                "company_industry": "Software Development",
                "company_size": "51-200 employees",
                "company_metadata_source": "https://www.linkedin.com/jobs/view/123",
            },
            seen_at="2026-07-25T10:00:00",
        )
        seen_again = companies.record_discovered_company(
            {"company": "Example Labs"},
            seen_at="2026-07-25T11:00:00",
        )

        self.assertEqual(discovered["tracking_status"], "discovered")
        self.assertEqual(discovered["industry"], "Software Development")
        self.assertEqual(discovered["company_size"], "51–200 employees")
        self.assertEqual(seen_again["id"], discovered["id"])
        self.assertEqual(seen_again["last_seen_at"], "2026-07-25T11:00:00")
        self.assertEqual(companies.check_all_company_postings()["checked_count"], 0)
        self.assertEqual(companies.check_all_company_postings()["skipped"][0]["reason"], "not tracked")

    def test_research_fills_blanks_and_saves_conflicts_for_review(self):
        sqlite_store.initialize()
        company = companies.upsert_company(
            "",
            {
                "name": "Example Labs",
                "industry": "Software Development",
                "tracking_status": "discovered",
            },
        )

        updated = companies.update_company_metadata(
            company["id"],
            {
                "company_industry": "Technology, Information and Internet",
                "company_size": "201-500 employees",
                "company_profile_url": "https://www.linkedin.com/company/example-labs/about/",
                "company_metadata_source": "https://www.linkedin.com/company/example-labs/about/",
            },
            source_url="https://www.linkedin.com/company/example-labs/about/",
        )
        suggestions = companies.company_metadata_suggestions(updated)

        self.assertEqual(updated["industry"], "Software Development")
        self.assertEqual(updated["company_size"], "201–500 employees")
        self.assertEqual(
            updated["company_profile_url"],
            "https://www.linkedin.com/company/example-labs",
        )
        self.assertEqual(suggestions[0]["field"], "industry")

        resolved = companies.resolve_company_metadata_suggestion(
            company["id"],
            suggestions[0]["id"],
            "apply",
        )
        self.assertEqual(resolved["industry"], "Technology, Information and Internet")
        self.assertEqual(companies.company_metadata_suggestions(resolved), [])

    def test_openai_company_research_updates_evaluation_and_returns_attribution(self):
        sqlite_store.initialize()
        company = companies.upsert_company(
            "",
            {
                "name": "Example Labs",
                "tracking_status": "tracked",
                "interest_status": "neutral",
            },
        )

        result = companies.research_company_with_openai(
            company["id"],
            evaluator=lambda batch, profile, batch_number: [{
                "company_id": company["id"],
                "name": company["name"],
                "website": "https://example.com",
                "careers_url": "https://example.com/careers",
                "industry": "Software Development",
                "company_size": "201–500 employees",
                "description": "Builds workflow software.",
                "location_fit": "us-remote",
                "location": "United States",
                "remote_policy": "Remote roles available in the United States.",
                "location_evidence": "The official careers page lists US remote roles.",
                "source_urls": ["https://example.com", "https://example.com/careers"],
            }],
        )

        self.assertEqual(result["provider"], "openai")
        self.assertTrue(result["run_id"].startswith("company-evaluation-"))
        self.assertEqual(result["evaluation_status"], "ready")
        self.assertEqual(result["company"]["careers_url"], "https://example.com/careers")
        self.assertEqual(result["company"]["company_location_fit"], "us-remote")
        self.assertIn("careers_url", result["applied_fields"])

    def test_mcp_company_research_defaults_to_openai_provider(self):
        result = {
            "company": {field: "" for field in schema.COMPANY_FIELDS} | {
                "id": "CO0001",
                "name": "Example",
            },
            "applied_fields": ["industry"],
            "suggestions": [],
            "source_url": "https://example.com",
            "provider": "openai",
            "run_id": "company-evaluation-test",
            "evaluation_status": "ready",
        }
        with patch("hunter.mcp_server.company_store.research_company", return_value=result) as research:
            response = mcp_server.call_named_tool("hunter_research_company", {"id": "CO0001"})

        payload = json.loads(response["content"][0]["text"])
        research.assert_called_once_with("CO0001")
        self.assertEqual(payload["provider"], "openai")
        self.assertEqual(payload["run_id"], "company-evaluation-test")

    def test_company_recommendation_uses_discovery_fit_without_changing_tracking(self):
        sqlite_store.initialize()
        company = companies.record_discovered_company({"company": "Example Labs"})
        rows = []
        titles = ["Technical Program Manager, Platform", "Senior Program Manager, Security"]
        for index in range(2):
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": f"DC000{index + 1}",
                    "company_id": company["id"],
                    "company": company["name"],
                    "title": titles[index],
                    "url": f"https://jobs.example.com/{index + 1}",
                    "status": "new",
                    "processing_status": "ready",
                    "fit_score": "70",
                    "location": "Remote; United States",
                    "description_text": "Responsibilities and requirements. " * 30,
                    "freshness_status": "confirmed-open",
                }
            )
            rows.append(row)
        repository.write_discovery_candidates(rows)

        payload_company = app_state.build_payload()["companies"][0]

        self.assertEqual(payload_company["tracking_status"], "discovered")
        self.assertEqual(payload_company["discovery_role_count"], 2)
        self.assertEqual(payload_company["recommended_discovery_role_count"], 2)
        self.assertIn("Hunter suggests tracking", payload_company["tracking_recommendation"])

    def test_company_recommendation_learns_from_repeated_ignored_roles(self):
        sqlite_store.initialize()
        company = companies.upsert_company(
            "",
            {"name": "Example Labs", "interest_status": "neutral"},
        )
        rows = []
        for index, function in enumerate(["Finance", "Operations"]):
            row = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            row.update(
                {
                    "id": f"DC000{index + 1}",
                    "company_id": company["id"],
                    "title": f"Project Manager, {function}",
                    "url": f"https://jobs.example.com/{index + 1}",
                    "status": "ignored",
                    "processing_status": "ready",
                }
            )
            rows.append(row)
        repository.write_discovery_candidates(rows)

        payload_company = app_state.build_payload()["companies"][0]

        self.assertEqual(payload_company["ignored_role_count"], 2)
        self.assertEqual(payload_company["pursued_role_count"], 0)
        self.assertIn("mark it Not interested", payload_company["decision_recommendation"])

        companies.upsert_company(company["id"], {"interest_status": "not-interested"})

        self.assertEqual(
            app_state.build_payload()["companies"][0]["decision_recommendation"],
            "",
        )

    def test_link_and_unlink_company_contact(self):
        sqlite_store.initialize()
        repository.write_contacts([contact_row({"id": "C0001", "name": "Ada"})])
        company = companies.upsert_company("", {"name": "Apple"})

        companies.link_contact(company["id"], "C0001")
        self.assertEqual(len(repository.read_company_contacts()), 1)

        companies.unlink_contact(company["id"], "C0001")
        self.assertEqual(repository.read_company_contacts(), [])

    def test_archive_and_restore_company_preserves_associations(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "company": "Apple", "company_id": ""}),
        ])
        repository.write_contacts([contact_row({"id": "C0001", "name": "Ada"})])
        company = companies.upsert_company("", {"name": "Apple", "interest_status": "interested"})
        companies.link_contact(company["id"], "C0001")

        archived = companies.archive_company(company["id"])
        restored = companies.restore_company(company["id"])

        self.assertEqual(archived["interest_status"], "archived")
        self.assertEqual(restored["interest_status"], "neutral")
        self.assertEqual(repository.read_applications()[0]["company_id"], company["id"])
        self.assertEqual(repository.read_company_contacts()[0]["company_id"], company["id"])

    def test_untrack_company_returns_to_discovery_without_losing_associations(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "company": "Apple", "company_id": ""}),
        ])
        repository.write_contacts([contact_row({"id": "C0001", "name": "Ada"})])
        company = companies.upsert_company(
            "",
            {
                "name": "Apple",
                "careers_url": "https://jobs.apple.com",
                "tracking_status": "tracked",
            },
        )
        companies.link_contact(company["id"], "C0001")

        updated = companies.untrack_company(company["id"])

        self.assertEqual(updated["tracking_status"], "discovered")
        self.assertEqual(updated["careers_url"], "https://jobs.apple.com")
        self.assertEqual(repository.read_applications()[0]["company_id"], company["id"])
        self.assertEqual(repository.read_company_contacts()[0]["company_id"], company["id"])

    def test_company_can_be_marked_not_interested_without_archiving(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Apple"})

        updated = companies.upsert_company(
            company["id"],
            {"interest_status": "not-interested"},
        )

        self.assertEqual(updated["interest_status"], "not-interested")
        self.assertEqual(updated["tracking_status"], "tracked")

    def test_restore_company_rejects_archived_status(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Apple", "interest_status": "archived"})

        with self.assertRaisesRegex(ValueError, "Restore status"):
            companies.restore_company(company["id"], "archived")

    def test_check_company_postings_records_new_candidates_and_skips_tracked_urls(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "source_url": "https://example.com/jobs/old-role"}),
        ])
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        html = """
        <a href="/jobs/old-role?utm_source=test">Old Role</a>
        <a href="/jobs/new-role?utm_source=test">New Role</a>
        <a href="/jobs/new-role">New Role Duplicate</a>
        <a href="/about">About</a>
        <a href="https://outside.example/jobs/nope">External Role</a>
        <a href="/blog/breaking-in-a-guide-to-landing-your-first-product-design-role">Read article</a>
        <a href="/careers?context=localeChange">English</a>
        """

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {"status": 200, "final_url": "https://example.com/careers", "html": html, "error": ""},
        )

        candidates = result["candidates"]
        self.assertEqual(len(result["new"]), 1)
        self.assertEqual(result["recommended"], [])
        self.assertEqual(candidates[0]["title"], "New Role")
        self.assertEqual(candidates[0]["url"], "https://example.com/jobs/new-role")
        self.assertEqual(candidates[0]["source_platform"], "generic_html")
        self.assertEqual(candidates[0]["source_job_id"], "")
        self.assertEqual(candidates[0]["scan_state"], "current")
        self.assertEqual(result["scan"]["unique_candidate_count"], "2")
        self.assertIn("fit_score", candidates[0])

    def test_target_check_uses_jobsearch_api_and_retires_generic_navigation_candidates(self):
        sqlite_store.initialize()
        careers_url = "https://corporate.target.com/careers/job-search"
        company = companies.upsert_company("", {"name": "Target", "careers_url": careers_url})
        old_candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        old_candidate.update(
            {
                "id": "CP0001",
                "company_id": company["id"],
                "title": "Careers FAQs",
                "url": "https://corporate.target.com/careers/faqs",
                "source_platform": "generic_html",
                "status": "new",
                "scan_state": "current",
            }
        )
        repository.write_company_posting_candidates([old_candidate])
        payload = {
            "count": 2,
            "results": [
                {
                    "document": {
                        "title": "Lead UX Design Operations Program Manager",
                        "url": "/jobs/w77/02/lead-ux-design-operations-program-manager",
                        "jobaddress": "1000 Nicollet Mall, Minneapolis, MN",
                        "remotetype": "Hybrid",
                        "jobarea": "User Experience",
                        "jobfamily": "User Experience",
                        "jobcategories": ["UX Design, Research & Accessibility"],
                        "jobskills": ["Cross-Functional Partnerships", "Project Management"],
                        "requisitionid": "R0000447702",
                    }
                },
                {
                    "document": {
                        "title": "Full Time Hourly Warehouse Operations Openings",
                        "url": "/jobs/w76/40/full-time-hourly-warehouse-operations-openings",
                        "jobaddress": "2200 Viking Rd, Cedar Falls, IA",
                        "jobarea": "Distribution Center Hourly",
                        "jobcategories": ["Supply Chain Hourly"],
                        "requisitionid": "R0000440000",
                    }
                },
            ],
        }
        calls = []

        def fetcher(url, headers=None, method="GET", data=None):
            calls.append({"url": url, "headers": headers or {}, "method": method, "data": data})
            if url == "https://corporate.target.com/api/jobsearch" and method == "POST":
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            return {"status": 404, "final_url": url, "html": "", "error": "HTTP Error 404"}

        with patch.object(companies, "resume_search_terms", return_value=["technical program manager"]):
            result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = result["career_source"]
        self.assertEqual(source["platform_type"], "target_jobsearch")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["headers"]["Content-Type"], "application/x-www-form-urlencoded")
        self.assertIn(b"q=technical+program+manager", calls[0]["data"])
        self.assertEqual([row["title"] for row in result["new"]], ["Lead UX Design Operations Program Manager"])
        candidate = result["new"][0]
        self.assertEqual(candidate["location"], "1000 Nicollet Mall, Minneapolis, MN")
        self.assertEqual(candidate["work_mode"], "Hybrid")
        self.assertEqual(candidate["source_job_id"], "R0000447702")
        self.assertEqual(candidate["source_platform"], "target_jobsearch")
        retired = next(row for row in result["candidates"] if row["id"] == "CP0001")
        self.assertEqual(retired["status"], "unavailable")
        self.assertEqual(retired["scan_state"], "unavailable")
        self.assertEqual(result["scan"]["unavailable_count"], "1")

    def test_check_company_postings_records_partial_scan_and_query_provenance(self):
        sqlite_store.initialize()
        careers_url = "https://www.google.com/about/careers/applications/jobs/results"
        company = companies.upsert_company("", {"name": "Google", "careers_url": careers_url})
        companies.save_company_career_source(
            company["id"],
            careers_url,
            "google_careers",
            status="verified",
        )
        urls = [
            f"{careers_url}?q=technical+program+manager",
            f"{careers_url}?q=product+manager",
        ]
        html = """
        <base href="https://www.google.com/about/careers/applications/">
        <li class="lLd3Je">
          <h3 class="QJPWVe">Technical Program Manager</h3>
          <span class="r0wTof">Chicago, IL, USA</span>
          <a href="jobs/results/123456789012345678-technical-program-manager"></a>
        </li>
        """

        def fetcher(url):
            if "technical+program+manager" in url:
                return {"status": 200, "final_url": url, "html": html, "error": ""}
            return {"status": 503, "final_url": url, "html": "", "error": "HTTP Error 503"}

        with patch.object(companies, "google_careers_search_urls", return_value=urls):
            result = companies.check_company_postings(company["id"], fetcher=fetcher)

        candidate = result["new"][0]
        self.assertEqual(candidate["matched_queries"], "technical program manager")
        self.assertEqual(candidate["source_platform"], "google_careers")
        self.assertEqual(candidate["source_job_id"], "external-job-id:123456789012345678")
        self.assertEqual(candidate["location"], "Chicago, IL, USA")
        self.assertTrue(candidate["score_inputs_hash"])
        self.assertEqual(result["scan"]["status"], "partial")
        self.assertEqual(result["scan"]["requests_succeeded"], "1")
        self.assertEqual(result["scan"]["requests_failed"], "1")
        self.assertTrue(result["company"]["last_check_status"].startswith("partial:"))
        self.assertEqual(repository.read_company_career_scans(company["id"])[0], result["scan"])

    def test_check_company_postings_rediscoveries_successful_zero_candidate_source(self):
        sqlite_store.initialize()
        careers_url = "https://example.com/careers"
        company = companies.upsert_company("", {"name": "Example", "careers_url": careers_url})
        original_source = companies.save_company_career_source(
            company["id"], careers_url, "next_static_jobs", status="verified"
        )
        rediscovered_source = {
            **original_source,
            "platform_type": "endpoint_json_jobs",
            "config_json": '{"endpoint_url":"https://example.com/jobs.json"}',
        }
        extracted = [
            {
                "title": "Technical Program Manager",
                "url": "https://example.com/jobs/12345678-technical-program-manager",
                "location": "Remote",
            }
        ]

        with (
            patch.object(
                companies,
                "fetch_career_candidates_with_source",
                side_effect=[([], 1, []), (extracted, 1, [])],
            ),
            patch.object(companies, "discover_company_career_source", return_value=rediscovered_source),
        ):
            result = companies.check_company_postings(company["id"], fetcher=Mock())

        self.assertEqual([row["title"] for row in result["new"]], ["Technical Program Manager"])
        self.assertEqual(result["scan"]["requests_succeeded"], "2")
        self.assertEqual(result["scan"]["unique_candidate_count"], "1")

    def test_check_company_postings_persists_structured_job_location(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Technical Program Manager",
          "url": "https://example.com/jobs/technical-program-manager",
          "jobLocationType": "TELECOMMUTE",
          "applicantLocationRequirements": {"@type": "Country", "name": "United States"}
        }
        </script>
        <a href="/jobs/technical-program-manager">Technical Program Manager</a>
        """

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {"status": 200, "final_url": "https://example.com/careers", "html": html, "error": ""},
        )

        self.assertEqual(result["new"][0]["location"], "Remote; United States")
        self.assertEqual(repository.read_company_posting_candidates()[0]["location"], "Remote; United States")

    def test_check_company_postings_marks_existing_candidate_ingested_by_job_board_identity(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Figma", "careers_url": "https://www.figma.com/careers"})
        repository.write_applications([
            application_row({
                "id": "A0001",
                "company": "Figma",
                "company_id": company["id"],
                "role": "Technical Program Manager, AI Performance",
                "source_url": "https://job-boards.greenhouse.io/figma/jobs/5837760004?gh_jid=5837760004",
            }),
        ])
        candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        candidate.update({
            "id": "CP0001",
            "company_id": company["id"],
            "title": "Technical Program Manager, AI Performance",
            "url": "https://boards.greenhouse.io/figma/jobs/5837760004?gh_jid=5837760004",
            "status": "new",
        })
        repository.write_company_posting_candidates([candidate])
        html = '<a href="https://boards.greenhouse.io/figma/jobs/5837760004?gh_jid=5837760004">Technical Program Manager, AI Performance</a>'

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {"status": 200, "final_url": "https://www.figma.com/careers", "html": html, "error": ""},
        )

        self.assertEqual(result["new"], [])
        self.assertEqual(result["recommended"], [])
        self.assertEqual(result["candidates"][0]["status"], "pursued")

    def test_ingest_candidate_passes_candidate_title_as_role(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        candidate.update({
            "id": "CP0001",
            "company_id": company["id"],
            "title": "Product Manager, AI Platform",
            "url": "https://example.com/jobs/product-manager-ai-platform",
            "location": "Remote; United States",
            "status": "new",
            "description_excerpt": "Responsibilities and requirements. " * 30,
            "scan_state": "current",
        })
        repository.write_company_posting_candidates([candidate])

        with patch.object(companies.subprocess, "run", return_value=Mock(returncode=0, stdout="ingested", stderr="")) as run:
            result = companies.ingest_candidate("CP0001")

        command = run.call_args.args[0]
        self.assertNotIn("cwd", run.call_args.kwargs)
        self.assertIn("--role", command)
        self.assertEqual(command[command.index("--role") + 1], "Product Manager, AI Platform")
        self.assertEqual(command[command.index("--location") + 1], "Remote; United States")
        self.assertEqual(command[-1], "https://example.com/jobs/product-manager-ai-platform")
        self.assertEqual(result["candidate"]["status"], "pursued")

    def test_check_company_postings_does_not_mark_missing_search_results_unavailable(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        first_html = '<a href="https://example.com/jobs/old-role">Old Role</a>'
        second_html = '<a href="https://example.com/jobs/new-role">New Role</a>'

        companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {"status": 200, "final_url": "https://example.com/careers", "html": first_html, "error": ""},
        )
        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {"status": 200, "final_url": "https://example.com/careers", "html": second_html, "error": ""},
        )

        statuses = {row["title"]: row["status"] for row in result["candidates"]}
        scan_states = {row["title"]: row["scan_state"] for row in result["candidates"]}
        self.assertEqual(statuses["Old Role"], "new")
        self.assertEqual(statuses["New Role"], "new")
        self.assertEqual(scan_states["Old Role"], "not-seen")
        self.assertEqual(scan_states["New Role"], "current")
        self.assertNotIn("unavailable", result["company"]["last_check_status"])
        self.assertEqual(result["verification_count"], 1)
        self.assertEqual(result["unavailable_count"], 0)

    def test_check_company_postings_marks_unseen_candidate_unavailable_after_direct_404(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        candidate.update({
            "id": "CP0001",
            "company_id": company["id"],
            "title": "Old Role",
            "url": "https://example.com/jobs/old-role",
            "status": "new",
            "last_seen_at": "2026-06-01T00:00:00",
        })
        repository.write_company_posting_candidates([candidate])
        careers_html = '<a href="https://example.com/jobs/new-role">New Role</a>'

        def fetch(url, **_kwargs):
            if url == "https://example.com/jobs/old-role":
                return {"status": 404, "final_url": url, "html": "Not found", "error": "HTTP Error 404"}
            return {"status": 200, "final_url": "https://example.com/careers", "html": careers_html, "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetch)

        statuses = {row["title"]: row["status"] for row in result["candidates"]}
        self.assertEqual(statuses["Old Role"], "unavailable")
        self.assertEqual(statuses["New Role"], "new")
        self.assertEqual(result["verification_count"], 1)
        self.assertEqual(result["unavailable_count"], 1)
        self.assertIn("1 unavailable", result["company"]["last_check_status"])

    def test_check_company_postings_marks_unseen_candidate_unavailable_after_closed_detail_page(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        candidate.update({
            "id": "CP0001",
            "company_id": company["id"],
            "title": "Old Role",
            "url": "https://example.com/jobs/old-role",
            "status": "new",
            "last_seen_at": "2026-06-01T00:00:00",
        })
        repository.write_company_posting_candidates([candidate])
        careers_html = '<a href="https://example.com/jobs/new-role">New Role</a>'

        def fetch(url, **_kwargs):
            if url == "https://example.com/jobs/old-role":
                return {
                    "status": 200,
                    "final_url": url,
                    "html": "<main>This job is no longer available.</main>",
                    "error": "",
                }
            return {"status": 200, "final_url": "https://example.com/careers", "html": careers_html, "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetch)

        statuses = {row["title"]: row["status"] for row in result["candidates"]}
        self.assertEqual(statuses["Old Role"], "unavailable")
        self.assertEqual(statuses["New Role"], "new")
        self.assertEqual(result["unavailable_count"], 1)

    def test_check_company_postings_restores_unavailable_candidate_when_seen_again(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        candidate.update({
            "id": "CP0001",
            "company_id": company["id"],
            "title": "Product Manager",
            "url": "https://example.com/jobs/product-manager",
            "status": "unavailable",
            "last_seen_at": "2026-06-01T00:00:00",
        })
        repository.write_company_posting_candidates([candidate])
        html = '<a href="https://example.com/jobs/product-manager">Product Manager</a>'

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {"status": 200, "final_url": "https://example.com/careers", "html": html, "error": ""},
        )

        self.assertEqual(result["candidates"][0]["status"], "new")
        self.assertEqual(result["new"], [])

    def test_smartrecruiters_identity_ignores_company_path_segment(self):
        keys = companies.posting_identity_keys(
            "https://jobs.smartrecruiters.com/Ubisoft2/744000133930119-technical-program-manager-ai-initiatives"
        )

        self.assertIn("smartrecruiters:744000133930119", keys)
        self.assertIn("external-job-id:744000133930119", keys)
        self.assertIn("path:smartrecruiters:744000133930119-technical-program-manager-ai-initiatives", keys)
        self.assertNotIn("path:smartrecruiters:ubisoft2", keys)

    def test_posting_identity_cache_is_normalized_and_mutation_isolated(self):
        companies._posting_identity_keys_cached.cache_clear()
        first = companies.posting_identity_keys(
            "https://job-boards.greenhouse.io/example/jobs/6135395004"
            "?gh_jid=6135395004&utm_source=first"
        )
        first.add("caller-only")
        second = companies.posting_identity_keys(
            "https://job-boards.greenhouse.io/example/jobs/6135395004"
            "?gh_jid=6135395004&utm_source=second"
        )

        self.assertNotIn("caller-only", second)
        self.assertIn("greenhouse:6135395004", second)
        self.assertEqual(companies._posting_identity_keys_cached.cache_info().misses, 1)
        self.assertGreaterEqual(companies._posting_identity_keys_cached.cache_info().hits, 1)

    def test_identity_caches_are_safe_under_concurrent_callers(self):
        companies._posting_identity_keys_cached.cache_clear()
        companies._normalized_requisition_ids_cached.cache_clear()
        url = (
            "https://jobs.smartrecruiters.com/Example/"
            "744000133930119-technical-program-manager"
        )

        def identity(_index):
            keys = companies.posting_identity_keys(url)
            requisitions = companies.normalized_requisition_ids(url)
            keys.add(f"local:{_index}")
            requisitions.add(f"local:{_index}")
            return keys, requisitions

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(identity, range(64)))

        for index, (keys, requisitions) in enumerate(results):
            self.assertIn(f"local:{index}", keys)
            self.assertIn(f"local:{index}", requisitions)
            self.assertIn("smartrecruiters:744000133930119", keys)
            self.assertEqual(
                requisitions - {f"local:{index}"},
                {"744000133930119"},
            )
        self.assertEqual(companies._posting_identity_keys_cached.cache_info().currsize, 1)
        self.assertEqual(companies._normalized_requisition_ids_cached.cache_info().currsize, 1)

    def test_smartrecruiters_candidate_matches_branded_careers_url_by_job_id(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Ubisoft"})
        repository.write_applications([
            application_row({
                "id": "A0001",
                "company": "Ubisoft",
                "company_id": company["id"],
                "role": "Older title",
                "source_url": "https://www.ubisoft.com/en-us/company/careers/search/744000133930119-technical-program-manager-ai-initiatives",
            }),
        ])

        tracked = companies.tracked_posting_context(company)

        self.assertTrue(companies.candidate_is_tracked({
            "title": "Technical Program Manager - AI initiatives",
            "url": "https://jobs.smartrecruiters.com/Ubisoft2/744000133930119-technical-program-manager-ai-initiatives",
        }, tracked))

    def test_distinct_requisitions_override_shared_company_and_careers_url(self):
        sqlite_store.initialize()
        waymo = companies.upsert_company("", {"name": "Waymo"})
        best_buy = companies.upsert_company("", {"name": "Best Buy"})
        repository.write_applications([
            application_row({
                "id": "A0064",
                "company": "Waymo",
                "company_id": waymo["id"],
                "role": "Senior Technical Program Manager, Simulation",
                "source_url": "https://careers.withwaymo.com/jobs?gh_jid=8026543",
            }),
            application_row({
                "id": "A0009",
                "company": "Best Buy",
                "company_id": best_buy["id"],
                "role": "Senior Product Manager - Customer Data and AI Enablement",
                "source_url": "https://jobs.bestbuy.com/bby?id=job_details&req_id=1024774BR",
            }),
        ])

        self.assertFalse(companies.candidate_is_tracked(
            {
                "title": "Senior Product Manager, Autonomous Vehicle Reliability",
                "url": "https://careers.withwaymo.com/jobs?gh_jid=8109626",
            },
            companies.tracked_posting_context(waymo),
        ))
        self.assertFalse(companies.candidate_is_tracked(
            {
                "title": "Senior Product Manager - Recommendations and Personalization",
                "url": "https://jobs.bestbuy.com/bby?id=job_details&req_id=1039998BR",
            },
            companies.tracked_posting_context(best_buy),
        ))
        self.assertEqual(
            companies.normalized_requisition_ids(
                "https://jobs.bestbuy.com/bby?id=job_details&req_id=1039998BR"
            ),
            {"1039998br"},
        )

    def test_work_mode_uses_explicit_remote_language_in_posting_body(self):
        candidate = companies.normalized_candidate(
            {
                "title": "Design Program Manager, Context Platform",
                "url": "https://job-boards.greenhouse.io/figma/jobs/6135395004?gh_jid=6135395004",
                "location": "San Francisco, CA; New York, NY; United States",
                "description": (
                    "This role can be held from one of our US hubs or remotely in the United States. "
                    "New hires attend in-person onboarding. "
                    "Responsibilities include leading cross-functional design programs."
                ),
            },
            "greenhouse_board",
        )

        self.assertEqual(candidate["work_mode"], "Remote")
        self.assertEqual(candidate["location"], "San Francisco, CA; New York, NY; United States")

    def test_check_company_postings_skips_existing_company_title_when_url_shape_changes(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        repository.write_applications([
            application_row({
                "id": "A0001",
                "company": "Example",
                "company_id": company["id"],
                "role": "Senior Technical Program Manager",
                "source_url": "https://example.com/jobs/legacy-tpm",
            }),
        ])
        html = '<a href="https://example.com/jobs/new-system-id-12345">Senior Technical Program Manager</a>'

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {"status": 200, "final_url": "https://example.com/careers", "html": html, "error": ""},
        )

        self.assertEqual(result["new"], [])
        self.assertEqual(result["candidates"], [])

    def test_check_company_postings_ranks_candidates_against_uploaded_resume(self):
        sqlite_store.initialize()
        resume = (
            "Senior Technical Product and Program Manager with AI platform, "
            "developer tools, data, API, release, and web experience."
        )
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        html = """
        <a href="/jobs/senior-technical-program-manager-ai-platform">Senior Technical Program Manager, AI Platform</a>
        <a href="/jobs/account-executive">Account Executive</a>
        """

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {"status": 200, "final_url": "https://example.com/careers", "html": html, "error": ""},
        )

        candidates = result["candidates"]
        best_fit = next(
            candidate
            for candidate in candidates
            if candidate["title"] == "Senior Technical Program Manager, AI Platform"
        )
        low_fit = next(candidate for candidate in candidates if candidate["title"] == "Account Executive")
        self.assertEqual(result["recommended"], [])
        self.assertEqual(companies.candidate_review_state(best_fit), "needs-detail")
        self.assertGreater(int(best_fit["fit_score"]), int(low_fit["fit_score"] or "0"))
        self.assertIn("technical program manager", best_fit["fit_summary"])
        self.assertEqual(low_fit["fit_score"], "0")

    def test_extract_candidate_links_skips_non_job_careers_and_blog_links(self):
        html = """
        <a href="/careers?context=localeChange">English</a>
        <a href="/blog/breaking-in-a-guide-to-landing-your-first-product-design-role">Read article</a>
        <a href="https://boards.greenhouse.io/figma/jobs/5837760004?gh_jid=5837760004">Technical Program Manager, AI Performance</a>
        """

        candidates = companies.extract_candidate_links(html, "https://www.figma.com/careers")

        self.assertEqual(
            candidates,
            [
                {
                    "title": "Technical Program Manager, AI Performance",
                    "url": "https://boards.greenhouse.io/figma/jobs/5837760004?gh_jid=5837760004",
                }
            ],
        )

    def test_extract_candidate_links_skips_login_and_listing_navigation_links(self):
        html = """
        <a href="https://careers-githubinc.icims.com/jobs/login?loginOnly=1">US Job Listings</a>
        <a href="https://globalcareers-githubinc.icims.com/jobs/login?loginOnly=1">Global Job Listings</a>
        <a href="https://employees-githubinc.icims.com/jobs/login">Employee Login for US Jobs</a>
        <a href="https://globalemployees-githubinc.icims.com/jobs/login">Employee Login for Global Jobs</a>
        """

        candidates = companies.extract_candidate_links(html, "https://www.github.careers/careers-home/jobs")

        self.assertEqual(candidates, [])

    def test_extract_candidate_links_reads_avature_job_detail_location_and_skips_structured_login(self):
        html = """
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Create a job alert","url":"https://jobs.ea.com/en_US/careers/Login"}
        </script>
        <article class="article article--result">
          <h3><a href="https://jobs.ea.com/en_US/careers/JobDetail/Development-Director/215676">Development Director</a></h3>
          <span class="list-item-location">Shanghai, China</span>
        </article>
        """

        candidates = companies.extract_candidate_links(html, "https://jobs.ea.com/en_US/careers")

        self.assertEqual(
            candidates,
            [{
                "title": "Development Director",
                "url": "https://jobs.ea.com/en_US/careers/JobDetail/Development-Director/215676",
                "location": "Shanghai, China",
            }],
        )

    def test_check_company_postings_marks_previously_saved_non_job_candidate_unavailable(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        candidate.update({
            "id": "CP0001",
            "company_id": company["id"],
            "title": "Create a job alert",
            "url": "https://example.com/careers/Login",
            "status": "new",
        })
        repository.write_company_posting_candidates([candidate])

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {
                "status": 200,
                "final_url": "https://example.com/careers",
                "html": '<a href="/jobs/product-manager">Product Manager</a>',
                "error": "",
            },
        )

        saved = next(row for row in result["candidates"] if row["id"] == "CP0001")
        self.assertEqual(saved["status"], "unavailable")
        self.assertEqual(result["unavailable_count"], 1)

    def test_extract_candidate_links_respects_html_base_and_cleans_google_titles(self):
        html = """
        <base href="https://www.google.com/about/careers/applications/">
        <a href="jobs/results/125046890545717958-technical-program-manager?q=technical+program+manager&page=2">
          125046890545717958 Technical Program Manager
        </a>
        """

        candidates = companies.extract_candidate_links(
            html,
            "https://www.google.com/about/careers/applications/jobs/results?q=technical+program+manager&page=2",
        )

        self.assertEqual(
            candidates,
            [
                {
                    "title": "Technical Program Manager",
                    "url": "https://www.google.com/about/careers/applications/jobs/results/125046890545717958-technical-program-manager",
                }
            ],
        )

    def test_google_careers_check_searches_resume_terms_and_pages(self):
        sqlite_store.initialize()
        resume = "Senior Technical Product Manager and Technical Program Manager with AI platform experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Google",
                "careers_url": "https://www.google.com/about/careers/applications/jobs/results",
            },
        )
        calls = []

        def fetcher(url):
            calls.append(url)
            if "q=technical+program+manager" in url and "page=2" in url:
                html = """
                <base href="https://www.google.com/about/careers/applications/">
                <li class="lLd3Je">
                  <h3 class="QJPWVe">Senior Technical Program Manager, Customer Engagement, Applied AI</h3>
                  <span class="r0wTof">Mountain View, CA, USA</span>
                  <span class="r0wTof p3oCrc">; New York, NY, USA</span>
                  <span class="BVHzed">; +2 more</span>
                  <a href="jobs/results/91051814228501190-senior-technical-program-manager-customer-engagement-applied-ai"></a>
                </li>
                """
            else:
                html = "<html></html>"
            return {"status": 200, "final_url": url, "html": html, "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        self.assertTrue(any("q=technical+program+manager" in url for url in calls))
        self.assertTrue(any("q=product+manager" in url for url in calls))
        self.assertTrue(all("location=United+States" in url for url in calls))
        self.assertTrue(any("page=2" in url for url in calls))
        self.assertEqual(len(result["new"]), 1)
        self.assertEqual(result["new"][0]["title"], "Senior Technical Program Manager, Customer Engagement, Applied AI")
        self.assertEqual(result["new"][0]["location"], "Mountain View, CA, USA; New York, NY, USA")

    def test_extract_google_careers_candidates_reads_job_card_locations_only(self):
        html = """
        <base href="https://www.google.com/about/careers/applications/">
        <a href="jobs/results">Jobs</a>
        <li class="lLd3Je" ssk="18:126510179461014214">
          <h3 class="QJPWVe">Technical Program Manager, Infrastructure</h3>
          <span class="r0wTof">Papillion, NE, USA</span>
          <span class="r0wTof p3oCrc">; New Albany, OH, USA</span>
          <span class="BVHzed">; +4 more</span>
          <a href="jobs/results/126510179461014214-technical-program-manager-infrastructure?location=United+States"></a>
          <p>
            <span class="r0wTof">Papillion, NE, USA</span>
            <span class="r0wTof p3oCrc">; New Albany, OH, USA</span>
          </p>
        </li>
        """

        self.assertEqual(
            companies.extract_google_careers_candidates(
                html,
                "https://www.google.com/about/careers/applications/jobs/results?location=United+States",
            ),
            [
                {
                    "title": "Technical Program Manager, Infrastructure",
                    "url": "https://www.google.com/about/careers/applications/jobs/results/126510179461014214-technical-program-manager-infrastructure",
                    "location": "Papillion, NE, USA; New Albany, OH, USA",
                }
            ],
        )

    def test_amazon_jobs_check_uses_search_json_and_scores_descriptions(self):
        sqlite_store.initialize()
        resume = "Senior Technical Product and Program Manager with AI platform and developer tools experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Amazon",
                "careers_url": "https://www.amazon.jobs/en",
            },
        )
        repository.write_applications([
            application_row({
                "id": "A0001",
                "company": "Amazon",
                "company_id": company["id"],
                "role": "Senior Technical Program Manager",
                "source_url": "https://www.amazon.jobs/en/jobs/10435887/senior-technical-program-manager",
            }),
        ])
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if "/search.json" in url and "base_query=technical+product+manager" in url:
                payload = {
                    "jobs": [
                        {
                            "title": "Product Manager - Technical",
                            "job_path": "/en/jobs/10499999/product-manager-technical",
                            "description": "Own AI platform roadmap for developer tools.",
                            "basic_qualifications": "5+ years of product or program management experience",
                            "city": "Seattle",
                            "state": "WA",
                            "country_code": "USA",
                            "locations": [
                                json.dumps({
                                    "normalizedLocation": "Seattle, Washington, USA",
                                    "location": "US, WA, Seattle",
                                    "buildingCodeList": ["SEA71"],
                                }),
                                {"normalizedLocation": "Austin, Texas, USA", "type": "ONSITE"},
                            ],
                            "business_category": "aws",
                            "job_category": "Project/Program/Product Management--Technical",
                            "company_name": "Amazon.com Services LLC",
                        },
                        {
                            "title": "Senior Technical Program Manager",
                            "job_path": "/en/jobs/10435887/senior-technical-program-manager",
                            "description": "Lead delivery for developer platform teams.",
                            "city": "San Francisco",
                            "state": "CA",
                            "country_code": "USA",
                        },
                        {
                            "title": "Account Executive",
                            "job_path": "/en/jobs/10500000/account-executive",
                            "description": "Sales role.",
                        },
                    ]
                }
            else:
                payload = {"jobs": []}
            return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "amazon_jobs")
        self.assertIn("search.json", source["config_json"])
        self.assertTrue(any("/en/search.json" in call["url"] for call in calls))
        self.assertTrue(any("base_query=technical+product+manager" in call["url"] for call in calls))
        self.assertTrue(any("base_query=technical+program+manager+iii" in call["url"] for call in calls))
        self.assertTrue(all("loc_query=United+States" in call["url"] for call in calls))
        self.assertTrue(any(call["headers"].get("Accept") == "application/json" for call in calls))
        self.assertEqual([row["title"] for row in result["new"]], ["Product Manager - Technical"])
        self.assertEqual(result["new"][0]["url"], "https://www.amazon.jobs/en/jobs/10499999/product-manager-technical")
        self.assertEqual(result["new"][0]["location"], "Seattle, Washington, USA; Austin, Texas, USA")
        self.assertGreaterEqual(int(result["new"][0]["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)
        self.assertNotIn("Account Executive", [row["title"] for row in result["candidates"]])

    def test_eightfold_pcs_check_uses_search_api_and_skips_existing_microsoft_display_id(self):
        sqlite_store.initialize()
        resume = "Senior Technical Program Manager with AI platform and developer tools experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Microsoft",
                "careers_url": "https://apply.careers.microsoft.com/careers",
            },
        )
        repository.write_applications([
            application_row({
                "id": "A0001",
                "company": "Microsoft",
                "company_id": company["id"],
                "role": "Previously tracked Microsoft role",
                "source_url": "https://jobs.careers.microsoft.com/global/en/job/200026339",
            }),
        ])
        pcsx_payload = {
            "domain": "microsoft.com",
            "configs": {
                "pcsxConfig": {
                    "searchConfig": {
                        "basePositionFq": "position.type:ATS",
                        "includeRemoteDefault": True,
                    }
                }
            },
        }
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if url == "https://apply.careers.microsoft.com/careers":
                html = f'<code id="pcsx-data">{json.dumps(pcsx_payload)}</code>'
                return {"status": 200, "final_url": url, "html": html, "error": ""}
            if "/api/pcsx/search" in url and "query=technical+program+manager" in url:
                payload = {
                    "status": 200,
                    "data": {
                        "positions": [
                            {
                                "id": 1970393556753134,
                                "displayJobId": "200026339",
                                "atsJobId": "200026339",
                                "name": "Sr. Technical Program Manager - Opportunity Analytics",
                                "locations": ["United States, New York, New York"],
                                "standardizedLocations": ["New York, NY, US"],
                                "department": "Technical Program Management",
                                "positionUrl": "/careers/job/1970393556753134",
                            },
                            {
                                "id": 1970393556870311,
                                "displayJobId": "200038666",
                                "atsJobId": "200038666",
                                "name": "Principal Technical Program Manager, Sovereign & Regulated Cloud",
                                "locations": ["United States, Washington, Redmond"],
                                "department": "Technical Program Management",
                                "positionUrl": "/careers/job/1970393556870311",
                            },
                        ]
                    },
                }
            else:
                payload = {"status": 200, "data": {"positions": []}}
            return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "eightfold_pcs")
        self.assertIn("microsoft.com", source["config_json"])
        self.assertTrue(any("/api/pcsx/search" in call["url"] for call in calls))
        self.assertTrue(any("domain=microsoft.com" in call["url"] for call in calls))
        self.assertTrue(any("location=United+States" in call["url"] for call in calls))
        self.assertTrue(any(call["headers"].get("Accept") == "application/json, text/plain, */*" for call in calls))
        self.assertEqual([row["title"] for row in result["new"]], ["Principal Technical Program Manager, Sovereign & Regulated Cloud"])
        self.assertEqual(
            result["new"][0]["url"],
            "https://apply.careers.microsoft.com/careers/job/1970393556870311?jobId=200038666",
        )
        self.assertEqual(result["new"][0]["location"], "United States, Washington, Redmond")
        self.assertGreaterEqual(int(result["new"][0]["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)

    def test_eightfold_pcs_separates_standardized_location_and_work_mode(self):
        settings.save_resume_upload(
            "resume.txt",
            base64.b64encode(b"Senior Technical Program Manager").decode(),
        )
        payload = {
            "data": {
                "positions": [
                    {
                        "id": 123456789,
                        "name": "Senior Technical Program Manager",
                        "positionUrl": "/careers/job/123456789",
                        "locations": ["US, CA, Santa Clara"],
                        "standardizedLocations": ["Santa Clara, CA, US"],
                        "workLocationOption": "onsite",
                    }
                ]
            }
        }

        candidates = companies.extract_eightfold_pcs_candidates(
            json.dumps(payload),
            "https://jobs.nvidia.com/careers",
        )

        self.assertEqual(candidates[0]["location"], "Santa Clara, CA, US")
        normalized = companies.normalize_extracted_candidates(candidates, "eightfold_pcs")[0]
        self.assertEqual(normalized["work_mode"], "On-site")

    def test_repeated_company_checks_keep_distinct_scan_history(self):
        sqlite_store.initialize()
        careers_url = "https://example.com/careers"
        company = companies.upsert_company("", {"name": "Example", "careers_url": careers_url})
        companies.save_company_career_source(company["id"], careers_url, "generic_html", status="verified")
        html = '<a href="/jobs/12345678-program-manager">Program Manager</a>'

        with patch.object(
            companies,
            "now_scan_iso",
            side_effect=["2026-07-17T10:00:00.000001", "2026-07-17T10:00:00.000002"],
        ):
            companies.check_company_postings(
                company["id"],
                fetcher=lambda url: {"status": 200, "final_url": url, "html": html, "error": ""},
            )
            companies.check_company_postings(
                company["id"],
                fetcher=lambda url: {"status": 200, "final_url": url, "html": html, "error": ""},
            )

        scans = repository.read_company_career_scans(company["id"])
        self.assertEqual([row["checked_at"] for row in scans], [
            "2026-07-17T10:00:00.000002",
            "2026-07-17T10:00:00.000001",
        ])

    def test_eightfold_smartapply_check_uses_jobs_api_and_skips_existing_netflix_posting(self):
        sqlite_store.initialize()
        resume = "Senior Technical Program Manager and Product Manager with games, platform, and data experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Netflix",
                "careers_url": "https://explore.jobs.netflix.net/careers",
            },
        )
        repository.write_applications([
            application_row({
                "id": "A0001",
                "company": "Netflix",
                "company_id": company["id"],
                "role": "Technical Program Manager 6 - Games Data Science & Engineering",
                "source_url": "https://explore.jobs.netflix.net/careers/job/790316246657",
            }),
        ])
        smartapply_payload = {
            "domain": "netflix.com",
            "count": 2,
            "positions": [
                {
                    "id": 790316246657,
                    "name": "Technical Program Manager 6 - Games Data Science & Engineering",
                    "locations": ["USA - Remote"],
                    "department": "Engineering Operations",
                    "business_unit": "Streaming",
                    "display_job_id": "JR41048",
                    "ats_job_id": "JR41048",
                    "canonicalPositionUrl": "https://explore.jobs.netflix.net/careers/job/790316246657",
                }
            ],
        }
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if url == "https://explore.jobs.netflix.net/careers":
                html = f'<code id="smartApplyData">{json.dumps(smartapply_payload)}</code>'
                return {"status": 200, "final_url": url, "html": html, "error": ""}
            if "/api/apply/v2/jobs" in url and "query=technical+program+manager" in url:
                payload = {
                    "domain": "netflix.com",
                    "count": 2,
                    "positions": [
                        smartapply_payload["positions"][0],
                        {
                            "id": 790316473015,
                            "name": "Technical Program Manager - Games Social, Trust and Safety",
                            "locations": ["Los Gatos,California,United States of America"],
                            "department": "Engineering Operations",
                            "business_unit": "Streaming",
                            "display_job_id": "JR41225",
                            "ats_job_id": "JR41225",
                            "canonicalPositionUrl": "https://explore.jobs.netflix.net/careers/job/790316473015",
                        },
                    ],
                }
            elif "/api/apply/v2/jobs" in url and "query=product+manager" in url:
                payload = {
                    "domain": "netflix.com",
                    "count": 1,
                    "positions": [
                        {
                            "id": 790316287334,
                            "name": "Product Manager, Ads Platform",
                            "locations": ["New York,New York,United States of America"],
                            "department": "Product Management",
                            "business_unit": "Streaming",
                            "display_job_id": "JR41085",
                            "ats_job_id": "JR41085",
                            "canonicalPositionUrl": "https://explore.jobs.netflix.net/careers/job/790316287334",
                        },
                    ],
                }
            else:
                payload = {"domain": "netflix.com", "positions": []}
            return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "eightfold_smartapply")
        self.assertIn("netflix.com", source["config_json"])
        self.assertTrue(any("/api/apply/v2/jobs" in call["url"] for call in calls))
        self.assertTrue(any("domain=netflix.com" in call["url"] for call in calls))
        self.assertTrue(any("query=technical+program+manager" in call["url"] for call in calls))
        self.assertTrue(any("query=product+manager" in call["url"] for call in calls))
        self.assertTrue(any(call["headers"].get("Accept") == "application/json, text/plain, */*" for call in calls))
        self.assertEqual(
            [row["title"] for row in result["new"]],
            ["Technical Program Manager - Games Social, Trust and Safety", "Product Manager, Ads Platform"],
        )
        self.assertEqual(
            result["new"][0]["url"],
            "https://explore.jobs.netflix.net/careers/job/790316473015",
        )
        self.assertGreaterEqual(int(result["new"][0]["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)

    def test_avature_waf_challenge_records_blocked_source_with_clear_error(self):
        sqlite_store.initialize()
        company = companies.upsert_company(
            "",
            {
                "name": "Delta Air Lines",
                "careers_url": "https://delta.avature.net/en_US/careers",
            },
        )
        challenge_html = """
        <html>
          <script>window.awsWafCookieDomainList = [];</script>
          <script src="https://example.token.awswaf.com/challenge.js"></script>
          <body><div id="challenge-container"></div></body>
        </html>
        """

        def fetcher(url):
            return {
                "status": 202,
                "final_url": url,
                "html": challenge_html,
                "error": "",
                "waf_action": "challenge",
            }

        with (
            patch("hunter.agent._settings", side_effect=ValueError("No OpenAI API token is configured.")),
            self.assertRaisesRegex(ValueError, "AWS WAF JavaScript challenge"),
        ):
            companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        checked = companies.get_company(company["id"])
        self.assertEqual(source["platform_type"], "avature_web_search")
        self.assertEqual(source["status"], "discovered")
        self.assertIn("aws_waf_javascript_challenge", source["config_json"])
        self.assertIn("openai_web_search", source["config_json"])
        self.assertIn("AWS WAF JavaScript challenge", checked["last_check_status"])

    def test_avature_web_search_accepts_only_direct_official_job_details(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.6-luna",
            "test-token",
            "",
            fit_signals={
                "role_terms": "product manager | 42",
                "search_terms": "product manager",
            },
        )
        company = companies.upsert_company(
            "",
            {
                "name": "Delta Air Lines",
                "careers_url": "https://delta.avature.net/en_US/careers",
            },
        )
        challenge_html = """
        <html>
          <script>window.awsWafCookieDomainList = [];</script>
          <script src="https://example.token.awswaf.com/challenge.js"></script>
        </html>
        """

        def fetcher(url):
            return {
                "status": 202,
                "final_url": url,
                "html": challenge_html,
                "error": "",
                "waf_action": "challenge",
            }

        response = {
            "model": "gpt-5.6-luna",
            "output_text": json.dumps(
                {
                    "jobs": [
                        {
                            "title": "Product Manager - AI and Automation",
                            "url": "https://delta.avature.net/en_US/careers/JobDetail?jobId=33234",
                            "location": "Atlanta, GA",
                            "work_mode": "Hybrid",
                            "source_job_id": "33234",
                            "freshness_evidence": "Official job result crawled this week.",
                        },
                        {
                            "title": "Product Manager from an aggregator",
                            "url": "https://example.com/jobs/33235",
                            "location": "Atlanta, GA",
                            "work_mode": "",
                            "source_job_id": "33235",
                            "freshness_evidence": "Aggregator result crawled this week.",
                        },
                    ]
                }
            ),
            "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
            "output": [{"type": "web_search_call"}],
        }
        with (
            patch(
                "hunter.agent._settings",
                return_value={"token": "test-token", "api_base": "https://api.openai.com/v1"},
            ),
            patch("hunter.agent._request_json", return_value=response) as request_json,
        ):
            result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "avature_web_search")
        self.assertEqual(source["status"], "verified")
        self.assertEqual(len(result["new"]), 1)
        self.assertEqual(result["new"][0]["source_job_id"], "33234")
        self.assertEqual(result["new"][0]["source_platform"], "avature_web_search")
        self.assertEqual(result["new"][0]["location"], "Atlanta, GA")
        self.assertEqual(request_json.call_count, 1)
        usage_rows = (paths.DATA_DIR / "agent_usage.jsonl").read_text(encoding="utf-8")
        self.assertIn('"feature": "career-search"', usage_rows)
        usage = json.loads(usage_rows.splitlines()[-1])
        self.assertEqual(usage["context"]["company_id"], company["id"])
        self.assertEqual(usage["context"]["run_id"], result["scan"]["run_id"])
        self.assertNotIn("example.com/jobs/33235", json.dumps(result))

    def test_cloudflare_blocked_official_site_uses_guarded_web_search(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.6-luna",
            "test-token",
            "",
            fit_signals={"role_terms": "program manager | 42", "search_terms": "program manager"},
        )
        company = companies.upsert_company(
            "",
            {
                "name": "General Motors",
                "careers_url": "https://search-careers.gm.com/en/jobs/",
            },
        )

        def fetcher(url, headers=None):
            del headers
            return {
                "status": 403,
                "final_url": url,
                "html": "<html><title>Just a moment...</title>Cloudflare cf-ray</html>",
                "error": "HTTP Error 403: Forbidden",
            }

        response = {
            "model": "gpt-5.6-luna",
            "output_text": json.dumps(
                {
                    "jobs": [
                        {
                            "title": "Group Program Manager, Hardware Design Program Management",
                            "url": (
                                "https://search-careers.gm.com/en/jobs/jr-202614853/"
                                "group-program-manager-hardware-design-program-management/"
                            ),
                            "location": "Sunnyvale, California; Warren, Michigan",
                            "work_mode": "Hybrid",
                            "source_job_id": "JR-202614853",
                            "freshness_evidence": "Listed on the current official GM jobs page.",
                        }
                    ]
                }
            ),
        }
        with (
            patch(
                "hunter.agent._settings",
                return_value={"token": "test-token", "api_base": "https://api.openai.com/v1"},
            ),
            patch("hunter.agent._request_json", return_value=response),
        ):
            result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "official_web_search")
        self.assertEqual(source["status"], "verified")
        self.assertEqual([row["source_job_id"] for row in result["new"]], ["JR-202614853"])

    def test_shopify_react_router_jobs_are_extracted_from_official_page(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.6-luna",
            "",
            "",
            fit_signals={
                "role_terms": "technical program manager | 50",
                "search_terms": "technical program manager",
            },
        )
        company = companies.upsert_company(
            "",
            {"name": "Shopify", "careers_url": "https://www.shopify.com/careers"},
        )
        payload = {
            "loaderData": {
                "($locale)/careers": {
                    "jobPostingsWithJobs": [
                        {
                            "jobPosting": {
                                "id": "3f5d85c0-816a-4173-96e7-4ee200b3b20e",
                                "title": "Staff Technical Program Manager",
                                "status": "Published",
                                "isListed": True,
                                "teamName": "Engineering",
                                "locationName": "Americas",
                                "workplaceType": "Remote",
                                "publishedDate": "2026-06-04",
                            },
                            "job": {
                                "title": "Engineering - Technical Program Management - Generalist",
                                "status": "Open",
                                "customFields": [
                                    {
                                        "title": "Subdiscipline",
                                        "valueLabel": "Technical Program Management",
                                    }
                                ],
                            },
                        }
                    ]
                }
            }
        }
        flattened = []

        def flatten(value):
            index = len(flattened)
            flattened.append(None)
            if isinstance(value, dict):
                flattened[index] = {f"_{flatten(str(key))}": flatten(item) for key, item in value.items()}
            elif isinstance(value, list):
                flattened[index] = [flatten(item) for item in value]
            else:
                flattened[index] = value
            return index

        flatten(payload)
        encoded = json.dumps(json.dumps(flattened))
        page_html = (
            "<html><script>window.__reactRouterContext.streamController.enqueue("
            f"{encoded})</script></html>"
        )

        def fetcher(url):
            return {"status": 200, "final_url": url, "html": page_html, "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "shopify_embedded_jobs")
        self.assertEqual([row["title"] for row in result["new"]], ["Staff Technical Program Manager"])
        self.assertEqual(
            result["new"][0]["url"],
            "https://www.shopify.com/careers/staff-technical-program-manager_3f5d85c0-816a-4173-96e7-4ee200b3b20e",
        )
        self.assertEqual(result["new"][0]["work_mode"], "Remote")

    def test_greenhouse_board_check_filters_to_matching_company_department(self):
        sqlite_store.initialize()
        resume = "Senior Technical Program Manager with AI platform and developer tools experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Insomniac Games",
                "aliases": "Insomniac",
                "careers_url": "https://job-boards.greenhouse.io/sonyinteractiveentertainmentglobal",
            },
        )
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if url.endswith("/departments"):
                payload = {
                    "departments": [
                        {
                            "id": 4037279004,
                            "name": "Insomniac Games",
                            "jobs": [
                                {
                                    "id": 1001,
                                    "title": "Senior Cinematic Animator",
                                    "absolute_url": "https://job-boards.greenhouse.io/sonyinteractiveentertainmentglobal/jobs/1001",
                                    "location": {"name": "United States, Remote"},
                                },
                                {
                                    "id": 1002,
                                    "title": "Senior Technical Program Manager",
                                    "absolute_url": "https://job-boards.greenhouse.io/sonyinteractiveentertainmentglobal/jobs/1002",
                                    "location": {"name": "Burbank, CA"},
                                },
                            ],
                        },
                        {
                            "id": 1,
                            "name": "Finance",
                            "jobs": [
                                {
                                    "id": 2001,
                                    "title": "Program Manager, Finance",
                                    "absolute_url": "https://job-boards.greenhouse.io/sonyinteractiveentertainmentglobal/jobs/2001",
                                }
                            ],
                        },
                    ]
                }
            elif url.endswith("/jobs/1002?content=true"):
                payload = {
                    "id": 1002,
                    "title": "Senior Technical Program Manager",
                    "absolute_url": "https://job-boards.greenhouse.io/sonyinteractiveentertainmentglobal/jobs/1002",
                    "location": {"name": "Burbank, CA"},
                    "department": {"name": "Insomniac Games", "path": ["PD Group"]},
                    "content": "Lead AI platform programs for game development tools.",
                    "metadata": [
                        {"name": "Career Page - Department", "value": "Production"},
                    ],
                }
            elif url.endswith("/jobs/1001?content=true"):
                payload = {
                    "id": 1001,
                    "title": "Senior Cinematic Animator",
                    "absolute_url": "https://job-boards.greenhouse.io/sonyinteractiveentertainmentglobal/jobs/1001",
                    "location": {"name": "United States, Remote"},
                    "department": {"name": "Insomniac Games", "path": ["PD Group"]},
                    "content": "Create animation for cinematics.",
                }
            else:
                payload = {"jobs": []}
            return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "greenhouse_board")
        self.assertIn("4037279004", source["config_json"])
        self.assertTrue(any(call["headers"].get("Accept") == "application/json" for call in calls))
        self.assertEqual([row["title"] for row in result["new"]], ["Senior Technical Program Manager"])
        self.assertEqual(
            result["new"][0]["url"],
            "https://job-boards.greenhouse.io/sonyinteractiveentertainmentglobal/jobs/1002",
        )
        self.assertGreaterEqual(int(result["new"][0]["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)

    def test_cloudflare_blocked_branded_greenhouse_page_uses_derived_board_token(self):
        sqlite_store.initialize()
        resume = "Product manager and program leader for game developer tools, creator workflows, and discovery systems."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Epic Games",
                "careers_url": "https://www.epicgames.com/site/careers/jobs",
            },
        )
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if url == "https://www.epicgames.com/site/careers/jobs":
                return {
                    "status": 403,
                    "final_url": url,
                    "html": "<html><body>Cloudflare cf_challenge_text_small cf-ray</body></html>",
                    "error": "HTTP Error 403: Forbidden",
                }
            if url.endswith("/departments"):
                payload = {
                    "departments": [
                        {
                            "id": 4014455004,
                            "name": "Epic Games",
                            "jobs": [
                                {
                                    "id": 6103058004,
                                    "title": "Director, Product Management (Discovery)",
                                    "absolute_url": "https://epicgames.com/careers/jobs/6103058004?gh_jid=6103058004",
                                    "location": {"name": "Cary, North Carolina, United States"},
                                }
                            ],
                        }
                    ]
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            if url.endswith("/jobs/6103058004?content=true"):
                payload = {
                    "id": 6103058004,
                    "title": "Director, Product Management (Discovery)",
                    "absolute_url": "https://epicgames.com/careers/jobs/6103058004?gh_jid=6103058004",
                    "location": {"name": "Cary, North Carolina, United States"},
                    "department": {"name": "Epic Games", "path": ["Product Management"]},
                    "content": "Lead product strategy for game discovery systems, creator workflows, and platform tools.",
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            return {"status": 404, "final_url": url, "html": "{}", "error": "HTTP Error 404: Not Found"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        config = json.loads(source["config_json"])
        self.assertEqual(source["platform_type"], "greenhouse_board")
        self.assertEqual(config["board_token"], "epicgames")
        self.assertTrue(any("boards-api.greenhouse.io/v1/boards/epicgames/departments" in call["url"] for call in calls))
        self.assertEqual([row["title"] for row in result["new"]], ["Director, Product Management (Discovery)"])
        self.assertEqual(
            result["new"][0]["url"],
            "https://epicgames.com/careers/jobs/6103058004?gh_jid=6103058004",
        )

    def test_search_terms_allow_role_variants_through_greenhouse_filter(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            search_goals="Primary:\nGame developer experiences and creative workflow systems",
            fit_signals={
                "role_terms": "technical program manager | 42",
                "domain_terms": "game developer | 18\nworkflow systems | 18",
                "seniority_terms": "senior | 12",
                "search_terms": "product manager",
                "exclusion_terms": "",
            },
        )
        settings.save_resume_upload(
            "resume.txt",
            base64.b64encode(b"Product and program leader for game developer workflow systems.").decode(),
        )
        company = companies.upsert_company(
            "",
            {
                "name": "Epic Games",
                "careers_url": "https://job-boards.greenhouse.io/epicgames",
            },
        )

        def fetcher(url, headers=None):
            del headers
            if url.endswith("/departments"):
                return {"status": 200, "final_url": url, "html": json.dumps({"departments": []}), "error": ""}
            if url.endswith("/jobs?content=true"):
                payload = {
                    "jobs": [
                        {
                            "id": 6013333004,
                            "title": "Director, Product Management (Discovery)",
                            "absolute_url": "https://epicgames.com/careers/jobs/6013333004?gh_jid=6013333004",
                            "location": {"name": "Cary, North Carolina, United States"},
                            "content": "Lead discovery for game developer workflow systems.",
                        }
                    ]
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            return {"status": 404, "final_url": url, "html": "{}", "error": "HTTP Error 404: Not Found"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        self.assertEqual([row["title"] for row in result["new"]], ["Director, Product Management (Discovery)"])
        self.assertGreaterEqual(int(result["new"][0]["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)
        self.assertIn("product manager", result["new"][0]["fit_summary"])

    def test_branded_greenhouse_links_resolve_to_board_source(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            search_goals="Primary:\nGame developer tools and internal web workflows",
            fit_signals={
                "role_terms": "web tools programmer | 50",
                "domain_terms": "web | 8\ndeveloper tools | 18",
                "seniority_terms": "senior | 8",
                "search_terms": "web tools programmer",
            },
        )
        settings.save_resume_upload(
            "resume.txt",
            base64.b64encode(b"Senior web tools programmer for game developer tools.").decode(),
        )
        company = companies.upsert_company(
            "",
            {
                "name": "Naughty Dog",
                "careers_url": "https://www.naughtydog.com/openings",
            },
        )
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if url == "https://www.naughtydog.com/openings":
                html = """
                <a href="/greenhouse/job/5822257004?gh_jid=5822257004">
                  Web Tools Programmer Placeholder Lorem ipsum APPLY NOW
                </a>
                """
                return {"status": 200, "final_url": url, "html": html, "error": ""}
            if url.endswith("/departments"):
                payload = {
                    "departments": [
                        {
                            "id": 4037282004,
                            "name": "Naughty Dog",
                            "jobs": [
                                {
                                    "id": 5822257004,
                                    "title": "Web Tools Programmer",
                                    "absolute_url": "https://job-boards.greenhouse.io/naughtydog/jobs/5822257004",
                                    "location": {"name": "Santa Monica, CA"},
                                }
                            ],
                        }
                    ]
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            if url.endswith("/jobs/5822257004?content=true"):
                payload = {
                    "id": 5822257004,
                    "title": "Web Tools Programmer",
                    "absolute_url": "https://job-boards.greenhouse.io/naughtydog/jobs/5822257004",
                    "location": {"name": "Santa Monica, CA"},
                    "department": {"name": "Naughty Dog", "path": ["Naughty Dog"]},
                    "content": "Build internal web workflows and developer tools for game teams.",
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            return {"status": 404, "final_url": url, "html": "{}", "error": "HTTP Error 404: Not Found"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        config = json.loads(source["config_json"])
        self.assertEqual(source["platform_type"], "greenhouse_board")
        self.assertEqual(config["board_token"], "naughtydog")
        self.assertTrue(any("boards-api.greenhouse.io/v1/boards/naughtydog/departments" in call["url"] for call in calls))
        self.assertEqual([row["title"] for row in result["new"]], ["Web Tools Programmer"])
        self.assertNotIn("Placeholder", result["new"][0]["title"])
        self.assertGreaterEqual(int(result["new"][0]["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)

    def test_plain_dynamic_page_falls_back_to_greenhouse_token_probe(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            search_goals="Secondary:\nBuilder productivity and technical workflow systems",
            fit_signals={
                "role_terms": "technical program manager | 42",
                "domain_terms": "developer tools | 18\nworkflow systems | 18",
                "seniority_terms": "senior | 12",
                "search_terms": "technical program manager",
            },
        )
        settings.save_resume_upload(
            "resume.txt",
            base64.b64encode(b"Senior Technical Program Manager for developer tools and workflow systems.").decode(),
        )
        company = companies.upsert_company(
            "",
            {
                "name": "Cloudflare",
                "careers_url": "https://www.cloudflare.com/careers/#open-roles",
            },
        )
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if url == "https://www.cloudflare.com/careers/#open-roles":
                return {"status": 200, "final_url": "https://www.cloudflare.com/careers/", "html": "<html>Open roles</html>", "error": ""}
            if url.endswith("/departments"):
                payload = {
                    "departments": [
                        {
                            "id": 10,
                            "name": "Cloudflare",
                            "jobs": [
                                {
                                    "id": 3001,
                                    "title": "Senior Technical Program Manager",
                                    "absolute_url": "https://job-boards.greenhouse.io/cloudflare/jobs/3001",
                                    "location": {"name": "Remote"},
                                }
                            ],
                        }
                    ]
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            if url.endswith("/jobs/3001?content=true"):
                payload = {
                    "id": 3001,
                    "title": "Senior Technical Program Manager",
                    "absolute_url": "https://job-boards.greenhouse.io/cloudflare/jobs/3001",
                    "location": {"name": "Remote"},
                    "department": {"name": "Cloudflare"},
                    "content": "Lead developer tools and workflow systems programs.",
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            return {"status": 404, "final_url": url, "html": "{}", "error": "HTTP Error 404: Not Found"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        config = json.loads(source["config_json"])
        self.assertEqual(source["platform_type"], "greenhouse_board")
        self.assertEqual(config["board_token"], "cloudflare")
        self.assertTrue(any("boards-api.greenhouse.io/v1/boards/cloudflare/departments" in call["url"] for call in calls))
        self.assertEqual([row["title"] for row in result["new"]], ["Senior Technical Program Manager"])

    def test_greenhouse_boards_declared_in_careers_script_are_checked(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            search_goals="Primary:\nDeveloper productivity and technical workflow systems",
            fit_signals={
                "role_terms": "technical program manager | 50",
                "domain_terms": "developer productivity | 20\nworkflow systems | 20",
                "seniority_terms": "senior | 8",
                "search_terms": "technical program manager",
            },
        )
        settings.save_resume_upload(
            "resume.txt",
            base64.b64encode(b"Senior Technical Program Manager for developer productivity and workflow systems.").decode(),
        )
        company = companies.upsert_company(
            "",
            {
                "name": "Discord",
                "careers_url": "https://discord.com/careers#all-jobs",
            },
        )
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if url == "https://discord.com/careers#all-jobs":
                html = '<script src="/webflow-scripts/careersNew2025.js" defer></script>'
                return {"status": 200, "final_url": url, "html": html, "error": ""}
            if url == "https://discord.com/webflow-scripts/careersNew2025.js":
                script = """
                t.DISCORD_JOB_BOARDS=["discord","discordinternational","internationaleor"];
                fetch(`https://api.greenhouse.io/v1/boards/${t}/jobs?content=true`);
                """
                return {"status": 200, "final_url": url, "html": script, "error": ""}
            if "boards/discord/jobs?content=true" in url:
                payload = {
                    "jobs": [
                        {
                            "id": 101,
                            "title": "Senior Technical Program Manager",
                            "absolute_url": "https://job-boards.greenhouse.io/discord/jobs/101",
                            "location": {"name": "San Francisco, CA"},
                            "content": "Lead developer productivity programs and workflow systems for engineering teams.",
                        }
                    ]
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            if "boards/discordinternational/jobs?content=true" in url:
                payload = {
                    "jobs": [
                        {
                            "id": 201,
                            "title": "Marketing Manager",
                            "absolute_url": "https://job-boards.greenhouse.io/discordinternational/jobs/201",
                            "location": {"name": "London"},
                            "content": "Run regional marketing programs.",
                        }
                    ]
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            if "boards/internationaleor/jobs?content=true" in url:
                return {"status": 200, "final_url": url, "html": json.dumps({"jobs": []}), "error": ""}
            return {"status": 404, "final_url": url, "html": "{}", "error": "HTTP Error 404: Not Found"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        config = json.loads(source["config_json"])
        self.assertEqual(source["platform_type"], "greenhouse_board")
        self.assertEqual(config["board_tokens"], ["discord", "discordinternational"])
        self.assertTrue(any("/boards/discord/jobs?content=true" in call["url"] for call in calls))
        self.assertTrue(any("/boards/discordinternational/jobs?content=true" in call["url"] for call in calls))
        self.assertEqual([row["title"] for row in result["new"]], ["Senior Technical Program Manager"])
        self.assertIn("2 searched", result["company"]["last_check_status"])

    def test_endpoint_json_careers_component_extracts_structured_jobs(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            search_goals="Primary:\nDeveloper productivity and technical workflow systems",
            fit_signals={
                "role_terms": "technical program manager | 42",
                "domain_terms": "developer tools | 18\nworkflow systems | 18",
                "seniority_terms": "senior | 12",
                "search_terms": "technical program manager",
            },
        )
        settings.save_resume_upload(
            "resume.txt",
            base64.b64encode(b"Senior Technical Program Manager for developer tools and workflow systems.").decode(),
        )
        company = companies.upsert_company(
            "",
            {
                "name": "Atlassian",
                "careers_url": "https://www.atlassian.com/company/careers/all-jobs",
            },
        )
        page_html = """
        <div id="imkt-jsx--0406b5ec" class="imkt-jsx--careers"></div>
        <script type="text/jsx-component">
        { "type": "Careers", "domRootId": "imkt-jsx--0406b5ec", "props": {} }
        </script>
        """
        jobs_payload = [
            {
                "portalJobPost": {
                    "portalUrl": "https://careers-atlassian.icims.com/jobs/25001/senior-technical-program-manager/job",
                    "id": 25001,
                    "updatedDate": "2026-07-01 06:26 PM",
                },
                "id": 25001,
                "title": "Senior Technical Program Manager",
                "type": "Full-Time",
                "locations": ["Remote - Remote"],
                "category": "Engineering",
                "overview": "<p>Build developer tools and workflow systems.</p>",
                "responsibilities": "<p>Lead cross-functional execution for technical programs.</p>",
                "qualifications": "<p>Experience with product and program delivery.</p>",
                "applyUrl": "https://careers-atlassian.icims.com/jobs/25001/senior-technical-program-manager/job?mode=apply",
            },
            {
                "portalJobPost": {
                    "portalUrl": "https://careers-atlassian.icims.com/jobs/25002/account-executive/job",
                    "id": 25002,
                },
                "id": 25002,
                "title": "Account Executive",
                "locations": ["Remote - Remote"],
                "category": "Sales",
                "overview": "<p>Sell software.</p>",
            },
        ]
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if url == "https://www.atlassian.com/company/careers/all-jobs":
                return {"status": 200, "final_url": url, "html": page_html, "error": ""}
            if url == "https://www.atlassian.com/endpoint/careers/listings":
                return {"status": 200, "final_url": url, "html": json.dumps(jobs_payload), "error": ""}
            return {"status": 404, "final_url": url, "html": "{}", "error": "HTTP Error 404: Not Found"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        config = json.loads(source["config_json"])
        self.assertEqual(source["platform_type"], "endpoint_json_jobs")
        self.assertEqual(config["endpoint_url"], "https://www.atlassian.com/endpoint/careers/listings")
        self.assertTrue(any(call["headers"].get("Accept") == "application/json, text/plain, */*" for call in calls))
        self.assertEqual([row["title"] for row in result["new"]], ["Senior Technical Program Manager"])
        self.assertEqual(
            result["new"][0]["url"],
            "https://careers-atlassian.icims.com/jobs/25001/senior-technical-program-manager/job",
        )
        self.assertGreaterEqual(int(result["new"][0]["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)

    def test_next_static_jobs_check_uses_embedded_jobs_data(self):
        sqlite_store.initialize()
        resume = "Senior Technical Product Manager and Program Manager with AI platform and developer tools experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Nintendo",
                "careers_url": "https://careers.nintendo.com/jobs/",
            },
        )
        next_payload = {
            "props": {
                "pageProps": {
                    "jobs": [
                        {
                            "id": 111,
                            "title": "Careers",
                            "location": {"name": ""},
                            "metadata": {},
                        },
                        {
                            "id": 222,
                            "title": "Product Manager, Developer Tools",
                            "location": {"name": "Redmond, WA"},
                            "metadata": {
                                "Company": {"value": "Nintendo of America Inc."},
                                "Worksite Classification": {"value": "Hybrid"},
                                "Job Field": {"value": "Product Development"},
                            },
                            "content": "<p>Own AI platform roadmaps for developer tools and partner APIs.</p>",
                            "internal_job_id": 333,
                        },
                        {
                            "id": 444,
                            "title": "Retail Associate",
                            "location": {"name": "New York, NY"},
                            "metadata": {"Job Field": {"value": "Retail Sales"}},
                        },
                    ]
                }
            }
        }
        html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(next_payload)}</script></html>'

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda url: {"status": 200, "final_url": url, "html": html, "error": ""},
        )

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "next_static_jobs")
        self.assertEqual([row["title"] for row in result["new"]], ["Product Manager, Developer Tools"])
        self.assertEqual(result["new"][0]["url"], "https://careers.nintendo.com/jobs/222")
        self.assertGreaterEqual(int(result["new"][0]["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)

    def test_jibe_careers_check_searches_api_and_scores_descriptions(self):
        sqlite_store.initialize()
        resume = "Senior Technical Product Manager and Program Manager with AI platform and developer tools experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "GitHub",
                "careers_url": "https://www.github.careers/careers-home/jobs",
            },
        )
        calls = []

        def fetcher(url):
            calls.append(url)
            if url == "https://www.github.careers/careers-home/jobs":
                return {
                    "status": 200,
                    "final_url": url,
                    "html": '<div data-jibe-search-version="4.11.198"></div><script>window.searchConfig = {}</script>',
                    "error": "",
                }
            if "/api/jobs" in url and "keywords=product+manager" in url and "page=1" in url:
                payload = {
                    "jobs": [
                        {
                            "data": {
                                "slug": "5315",
                                "req_id": "5315",
                                "title": "Staff Product Manager",
                                "description": (
                                    "Lead GitHub Copilot AI platform strategy for developer tools and partner APIs. "
                                    "Define the product vision, roadmap, success measures, and launch plans with "
                                    "engineering, design, research, security, and go-to-market partners. Candidates "
                                    "should have extensive product management experience delivering developer-facing "
                                    "platforms, strong technical judgment about APIs and AI systems, and demonstrated "
                                    "ability to turn customer research into prioritized requirements. The role owns "
                                    "cross-functional execution, executive communication, risk management, adoption "
                                    "measurement, and continuous improvement across a globally distributed team."
                                ),
                                "country": "United States",
                                "full_location": "Remote, United States",
                                "categories": [{"name": "Product"}],
                            }
                        }
                    ]
                }
            else:
                payload = {"jobs": []}
            return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        self.assertTrue(any("/api/jobs" in url for url in calls))
        self.assertTrue(any("keywords=product+manager" in url for url in calls))
        self.assertEqual(len(result["new"]), 1)
        candidate = result["new"][0]
        self.assertEqual(candidate["title"], "Staff Product Manager")
        self.assertEqual(candidate["url"], "https://www.github.careers/careers-home/jobs/5315")
        self.assertGreaterEqual(int(candidate["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)
        self.assertIn(candidate, result["recommended"])

    def test_openai_careers_check_uses_ashby_board_and_filters_to_resume_roles(self):
        sqlite_store.initialize()
        resume = "Senior Technical Product Manager and Technical Program Manager with AI platform, API, and operations experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "OpenAI",
                "careers_url": "https://openai.com/careers/search/",
            },
        )
        repository.write_applications([
            application_row({
                "id": "A0001",
                "company": "OpenAI",
                "company_id": company["id"],
                "role": "Technical Program Manager, Compute Infrastructure",
                "source_url": "https://openai.com/careers/technical-program-manager-compute-infrastructure-san-francisco/",
            }),
        ])
        ashby_payload = {
            "jobBoard": {
                "jobPostings": [
                    {
                        "id": "pm-1",
                        "title": "Product Manager, API Agents",
                        "isListed": True,
                        "locationName": "San Francisco",
                        "departmentName": "Product Management",
                        "teamName": "Product Management",
                    },
                    {
                        "id": "tpm-existing",
                        "title": "Technical Program Manager, Compute Infrastructure",
                        "isListed": True,
                        "locationName": "San Francisco",
                        "departmentName": "Technical Program Management",
                        "teamName": "Technical Program Management",
                    },
                    {
                        "id": "sales-1",
                        "title": "Account Director, Digital Native",
                        "isListed": True,
                        "locationName": "Seoul, South Korea",
                        "departmentName": "Go To Market",
                        "teamName": "Sales",
                    },
                ]
            }
        }
        ashby_html = f"""
        <html><script>
          window.__appData = {json.dumps(ashby_payload)};
          fetch("https://cdn.ashbyprd.com/manifest.json")
        </script></html>
        """
        calls = []

        def fetcher(url):
            calls.append(url)
            if url == "https://jobs.ashbyhq.com/openai":
                return {"status": 200, "final_url": url, "html": ashby_html, "error": ""}
            return {"status": 403, "final_url": url, "html": "", "error": "HTTP Error 403: Forbidden"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        self.assertEqual(calls, ["https://jobs.ashbyhq.com/openai"])
        self.assertEqual([row["title"] for row in result["new"]], ["Product Manager, API Agents"])
        self.assertEqual(result["new"][0]["url"], "https://jobs.ashbyhq.com/openai/pm-1")
        self.assertEqual(companies.candidate_review_state(result["new"][0]), "failed-extraction")
        self.assertEqual(result["recommended"], [])

    def test_branded_careers_page_resolves_embedded_ashby_board(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            fit_signals={"role_terms": "technical program manager | 50", "search_terms": "technical program manager"},
        )
        settings.save_resume_upload(
            "resume.txt",
            base64.b64encode(b"Senior Technical Program Manager for platforms and operations.").decode(),
        )
        company = companies.upsert_company(
            "",
            {"name": "Applied Intuition", "careers_url": "https://www.appliedintuition.com/careers"},
        )
        branded_html = '<a href="https://jobs.ashbyhq.com/applied/job-id">Technical Program Manager</a>'
        ashby_payload = {
            "jobBoard": {
                "jobPostings": [
                    {
                        "id": "tpm-1",
                        "title": "Technical Program Manager, Vehicle OS",
                        "isListed": True,
                        "locationName": "Sunnyvale",
                        "departmentName": "Vehicle OS",
                    },
                    {"id": "sales-1", "title": "Account Executive", "isListed": True},
                ]
            }
        }
        ashby_html = (
            f"<script>window.__appData = {json.dumps(ashby_payload)};\n"
            'fetch("https://cdn.ashbyprd.com/manifest.json")</script>'
        )

        def fetcher(url):
            if url == "https://www.appliedintuition.com/careers":
                return {"status": 200, "final_url": url, "html": branded_html, "error": ""}
            if url == "https://jobs.ashbyhq.com/applied":
                return {"status": 200, "final_url": url, "html": ashby_html, "error": ""}
            return {"status": 404, "final_url": url, "html": "", "error": "HTTP Error 404"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        self.assertEqual(result["career_source"]["platform_type"], "ashby")
        self.assertEqual([row["title"] for row in result["new"]], ["Technical Program Manager, Vehicle OS"])

    def test_lever_careers_check_uses_regional_postings_api(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            fit_signals={"role_terms": "technical product manager | 50", "search_terms": "technical product manager"},
        )
        settings.save_resume_upload(
            "resume.txt",
            base64.b64encode(b"Senior Technical Product Manager for platforms and APIs.").decode(),
        )
        careers_url = "https://jobs.eu.lever.co/quantinuum"
        company = companies.upsert_company("", {"name": "Quantinuum", "careers_url": careers_url})
        payload = [
            {
                "id": "job-1",
                "text": "Senior Technical Product Manager - Quantum Developer Platform",
                "hostedUrl": "https://jobs.eu.lever.co/quantinuum/job-1",
                "workplaceType": "hybrid",
                "categories": {
                    "location": "US Broomfield, CO / US Brooklyn Park, MN",
                    "team": "Quantum Computing Software",
                    "commitment": "Full-time",
                },
                "descriptionPlain": "Lead product management for a developer platform and APIs.",
            },
            {
                "id": "job-2",
                "text": "Senior Counsel",
                "hostedUrl": "https://jobs.eu.lever.co/quantinuum/job-2",
                "categories": {"location": "London", "team": "Legal"},
            },
        ]
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        self.assertEqual(result["career_source"]["platform_type"], "lever")
        self.assertEqual(calls[0]["url"], "https://api.eu.lever.co/v0/postings/quantinuum?mode=json")
        self.assertEqual([row["title"] for row in result["new"]], [payload[0]["text"]])
        self.assertEqual(result["new"][0]["source_job_id"], "job-1")
        self.assertEqual(result["new"][0]["work_mode"], "Hybrid")

    def test_cdpr_careers_check_uses_embedded_jobs_data(self):
        sqlite_store.initialize()
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            fit_signals={"role_terms": "producer | 50", "search_terms": "producer"},
        )
        settings.save_resume_upload(
            "resume.txt",
            base64.b64encode(b"Senior Producer and Technical Program Manager for game development.").decode(),
        )
        careers_url = "https://www.cdprojektred.com/en/jobs"
        company = companies.upsert_company("", {"name": "CD Projekt Red", "careers_url": careers_url})
        payload = [
            {
                "id": 22122,
                "name": "Senior Producer",
                "applyUrl": "https://jobs.smartrecruiters.com/CDPROJEKTRED/22122-senior-producer",
                "category": {"name": "Production"},
                "project": {"name": "Cyberpunk 2"},
                "location": {"name": "United States"},
                "remote": False,
            },
            {
                "id": 22198,
                "name": "Finance Manager",
                "applyUrl": "https://jobs.smartrecruiters.com/CDPROJEKTRED/22198-finance-manager",
                "location": {"name": "Poland"},
                "remote": False,
            },
        ]
        page_html = f"<script>window.cdpData.jobsData = {json.dumps(payload)};</script>"

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda url: {"status": 200, "final_url": url, "html": page_html, "error": ""},
        )

        self.assertEqual(result["career_source"]["platform_type"], "cdpr_embedded_jobs")
        self.assertEqual([row["title"] for row in result["new"]], ["Senior Producer"])
        self.assertEqual(result["new"][0]["location"], "United States")
        self.assertEqual(result["new"][0]["work_mode"], "On-site")

    def test_custom_workday_careers_check_uses_platform_api(self):
        sqlite_store.initialize()
        resume = (
            "Senior Technical Product and Program Manager with project manager, "
            "commerce, operations, release, and web experience."
        )
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "the LEGO Group",
                "careers_url": "https://www.lego.com/en-us/careers/search",
            },
        )
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if url == "https://www.lego.com/en-us/careers/search":
                html = '<script src="/careers/_next/static/chunks/jobs.js"></script>'
                return {"status": 200, "final_url": url, "html": html, "error": ""}
            if url == "https://www.lego.com/careers/_next/static/chunks/jobs.js":
                script = (
                    'const base="https://jobs.careers.services.lego.com/api/v1/GetJobs";'
                    'fetch(base,{headers:{"x-api-key":"test-lego-key"}});'
                )
                return {"status": 200, "final_url": url, "html": script, "error": ""}
            if "/GetJobs" in url and "keyword=project+manager" in url:
                payload = {
                    "Report_Entry": [
                        {
                            "title": "Technical Project Manager - Logistical",
                            "urlPart": "technical-project-manager-logistical-577829f9b3661000cde983a960bf0000",
                            "locationHierarchy": "United States of America",
                            "jobFamilyGroup": "Project / Program Management",
                            "jobPostingLocations": [
                                {
                                    "locationName": "Boston, MA",
                                    "country": "United States of America",
                                }
                            ],
                        }
                    ]
                }
            elif "/GetJobs" in url and "keyword=product+manager" in url:
                payload = {
                    "Report_Entry": [
                        {
                            "title": "Senior Software Engineer, Packing Technology",
                            "urlPart": "senior-software-engineer-packing-technology-123",
                            "locationHierarchy": "Denmark",
                            "jobFamilyGroup": "Engineering",
                        }
                    ]
                }
            else:
                payload = {"Report_Entry": []}
            return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        self.assertTrue(calls)
        self.assertEqual(calls[0]["url"], "https://www.lego.com/en-us/careers/search")
        self.assertIn("https://www.lego.com/careers/_next/static/chunks/jobs.js", [call["url"] for call in calls])
        self.assertTrue(any(call["url"].startswith("https://jobs.careers.services.lego.com/api/v1/GetJobs?") for call in calls))
        self.assertTrue(any(call["headers"].get("x-api-key") == "test-lego-key" for call in calls))
        career_source = repository.read_company_career_sources()[0]
        self.assertEqual(career_source["platform_type"], "custom_workday")
        self.assertIn("GetJobs", career_source["config_json"])
        self.assertEqual([row["title"] for row in result["new"]], ["Technical Project Manager - Logistical"])
        self.assertEqual(
            result["new"][0]["url"],
            "https://www.lego.com/en-us/careers/job/technical-project-manager-logistical-577829f9b3661000cde983a960bf0000",
        )
        self.assertNotIn("Careers", [row["title"] for row in result["candidates"]])

        calls.clear()
        companies.check_company_postings(company["id"], fetcher=fetcher)

        self.assertTrue(calls)
        self.assertTrue(all(call["url"].startswith("https://jobs.careers.services.lego.com/api/v1/GetJobs?") for call in calls))

    def test_linked_workday_cxs_board_uses_platform_api(self):
        sqlite_store.initialize()
        resume = "Technical Product and Program Manager with robotics platform delivery and project management experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Boston Dynamics",
                "careers_url": "https://bostondynamics.com/careers/#jobs",
            },
        )
        calls = []

        def fetcher(url, headers=None, method="GET", data=None):
            calls.append({"url": url, "headers": headers or {}, "method": method, "data": data})
            if url == "https://bostondynamics.com/careers/#jobs":
                html = """
                    <a href="https://bostondynamics.wd1.myworkdayjobs.com/Boston_Dynamics/job/Waltham-Office-POST/Atlas-Technical-Project-Manager--Structures_R2008">
                      Atlas Technical Project Manager- Structures
                    </a>
                    <a href="/industry/construction">Construction Read More</a>
                """
                return {"status": 200, "final_url": url, "html": html, "error": ""}
            if "/wday/cxs/bostondynamics/Boston_Dynamics/jobs" in url and method == "POST":
                payload = {
                    "total": 1,
                    "jobPostings": [
                        {
                            "title": "Atlas Technical Project Manager- Structures",
                            "externalPath": "/job/Waltham-Office-POST/Atlas-Technical-Project-Manager--Structures_R2008",
                            "locationsText": "Waltham Office (POST)",
                            "bulletFields": ["R2008"],
                        }
                    ],
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            if url.endswith("/Boston_Dynamics/job/Waltham-Office-POST/Atlas-Technical-Project-Manager--Structures_R2008"):
                payload = {
                    "jobPostingInfo": {
                        "title": "Atlas Technical Project Manager- Structures",
                        "jobDescription": "Lead cross-functional robotics platform delivery and project execution.",
                        "location": "Waltham, MA",
                        "jobReqId": "R2008",
                    },
                    "hiringOrganization": {"name": "Boston Dynamics"},
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            return {"status": 404, "final_url": url, "html": "", "error": "not found"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "workday_cxs")
        self.assertTrue(any(call["method"] == "POST" and "/wday/cxs/bostondynamics/Boston_Dynamics/jobs" in call["url"] for call in calls))
        self.assertEqual([row["title"] for row in result["new"]], ["Atlas Technical Project Manager- Structures"])
        self.assertEqual(
            result["new"][0]["url"],
            "https://bostondynamics.wd1.myworkdayjobs.com/Boston_Dynamics/job/Waltham-Office-POST/Atlas-Technical-Project-Manager--Structures_R2008",
        )
        self.assertNotIn("Construction Read More", [row["title"] for row in result["candidates"]])

    def test_static_json_careers_feed_extracts_spa_positions(self):
        sqlite_store.initialize()
        resume = "Technical Product and Program Manager with game platform, release, and production pipeline experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Santa Monica Studio",
                "careers_url": "https://sms.playstation.com/careers",
            },
        )
        calls = []

        def fetcher(url, headers=None):
            calls.append({"url": url, "headers": headers or {}})
            if url == "https://sms.playstation.com/careers":
                return {"status": 200, "final_url": url, "html": "<html><body>Careers app</body></html>", "error": ""}
            if url == "https://sms.playstation.com/data/careers.json":
                payload = {
                    "positions": {
                        "production": {
                            "title": "Production",
                            "jobs": [
                                {
                                    "id": 481,
                                    "slug": "producer",
                                    "position": "Producer",
                                    "type": "Contract - Remote OK",
                                    "date": "04.22.2026",
                                }
                            ],
                        }
                    }
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            if url == "https://sms.playstation.com/data/careers/producer.json":
                payload = {
                    "position": "Producer",
                    "type": "Contract - Remote OK",
                    "category": "production",
                    "subtitle": "Keep the big picture and details moving forward.",
                    "content": [
                        {
                            "type": "copy",
                            "content": "Partner with creative leads to support the production pipeline, project management, release planning, and game development execution.",
                        }
                    ],
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            return {"status": 404, "final_url": url, "html": "", "error": "not found"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "static_json_careers")
        self.assertIn("https://sms.playstation.com/data/careers.json", [call["url"] for call in calls])
        self.assertIn("https://sms.playstation.com/data/careers/producer.json", [call["url"] for call in calls])
        self.assertEqual([row["title"] for row in result["new"]], ["Producer"])
        self.assertEqual(result["new"][0]["url"], "https://sms.playstation.com/careers/production/producer")

    def test_servicenow_careers_check_discovers_widget_api(self):
        sqlite_store.initialize()
        resume = "Senior Product Manager with commerce, platform, operations, and web experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Best Buy",
                "careers_url": "https://jobs.bestbuy.com/bby",
            },
        )
        calls = []

        def fetcher(url, headers=None, method="GET", data=None):
            calls.append({"url": url, "headers": headers or {}, "method": method, "data": data})
            if url == "https://jobs.bestbuy.com/bby":
                html = '<html ng-app="sn.$sp"><script>window.NOW = {}; window.NOW.page_id = "all_jobs"; window.g_ck = "guest-token";</script></html>'
                return {"status": 200, "final_url": url, "html": html, "error": "", "cookies": "JSESSIONID=test-session"}
            if url == "https://jobs.bestbuy.com/api/now/sp/page?id=all_jobs":
                payload = {
                    "result": {
                        "containers": [
                            {
                                "rows": [
                                    {
                                        "columns": [
                                            {
                                                "widgets": [
                                                    {"widget": {"id": "bby-jobs-filters", "sys_id": "filters-widget"}},
                                                    {"widget": {"id": "bby-career-map", "sys_id": "map-widget"}},
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                }
                return {"status": 200, "final_url": url, "html": json.dumps(payload), "error": ""}
            if url == "https://jobs.bestbuy.com/api/now/sp/widget/map-widget" and method == "POST":
                term = ((data or {}).get("options") or {}).get("filters", {}).get("q", "")
                if "product manager" in term:
                    features = [
                        {
                            "properties": {
                                "title": "Senior Product Manager, Retail Media Products",
                                "auto_req_id": "1021639BR",
                                "city": "New York",
                                "state": "New York",
                                "country": "United States",
                                "sites": "Corporate, Marketing",
                                "category": "DAT Group",
                                "type": "Full time",
                                "experience": "Individual Contributor",
                            }
                        },
                        {
                            "properties": {
                                "title": "Retail Sales Associate",
                                "auto_req_id": "1032793BR",
                                "city": "Gastonia",
                                "state": "North Carolina",
                                "country": "United States",
                                "category": "Retail Group",
                                "type": "Part time",
                            }
                        },
                    ]
                else:
                    features = []
                payload = {"result": {"data": {"items": {"features": features}, "total_count": len(features)}}}
                return {"status": 201, "final_url": url, "html": json.dumps(payload), "error": ""}
            return {"status": 404, "final_url": url, "html": "", "error": "not found"}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "servicenow_portal")
        self.assertIn("map-widget", source["config_json"])
        self.assertTrue(any(call["headers"].get("X-UserToken") == "guest-token" for call in calls))
        self.assertTrue(any(call["headers"].get("Cookie") == "JSESSIONID=test-session" for call in calls))
        self.assertEqual([row["title"] for row in result["new"]], ["Senior Product Manager, Retail Media Products"])
        self.assertEqual(
            result["new"][0]["url"],
            "https://jobs.bestbuy.com/bby?id=job_details&req_id=1021639BR",
        )
        self.assertEqual(result["new"][0]["location"], "New York, United States")

    def test_phenom_careers_check_searches_resume_terms_from_preloaded_results(self):
        sqlite_store.initialize()
        resume = "Senior Technical Program Manager with platform, operations, release, API, and web experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Blizzard",
                "careers_url": "https://careers.blizzard.com/global/en/search-results",
            },
        )

        def phenom_html(jobs):
            payload = {
                "status": 200,
                "hits": len(jobs),
                "totalHits": len(jobs),
                "data": {"jobs": jobs},
            }
            return (
                '<html><head><script src="https://cdn.phenompeople.com/app.js"></script></head>'
                '<script>var phApp = {"widgetApiEndpoint":"https://careers.blizzard.com/widgets"};'
                f'phApp.ddo = {{"siteConfig":{{"data":{{}}}},"eagerLoadRefineSearch":{json.dumps(payload)}}};'
                "</script></html>"
            )

        calls = []

        def fetcher(url):
            calls.append(url)
            if url == "https://careers.blizzard.com/global/en/search-results":
                return {"status": 200, "final_url": url, "html": phenom_html([]), "error": ""}
            if "keywords=technical+program+manager" in url and "from=10" not in url:
                jobs = [
                    {
                        "title": "Lead Technical Program Manager, Platform Security | Irvine, CA or remote",
                        "jobSeqNo": "BLENGLOBALR027825EXTERNALENGLOBAL",
                        "descriptionTeaser": "Lead platform security programs, release planning, APIs, and cross-functional execution.",
                        "location": "Irvine, CA or remote",
                        "category": "Program Management",
                        "externalTeamName": "Battle.net & Online Products",
                    },
                    {
                        "title": "Senior Animator – Temp (SFD / Cinematics)",
                        "jobSeqNo": "BLENGLOBALR027646EXTERNALENGLOBAL",
                        "descriptionTeaser": "Create cinematic animation.",
                        "location": "Irvine, CA",
                        "category": "Art / Animation",
                    }
                ]
                return {"status": 200, "final_url": url, "html": phenom_html(jobs), "error": ""}
            if "keywords=technical+program+manager" in url and "from=10" in url:
                jobs = [
                    {
                        "title": "Program Manager | São Paulo, BR",
                        "jobSeqNo": "BLENGLOBALR027999EXTERNALENGLOBAL",
                        "descriptionTeaser": "Coordinate operations and delivery plans.",
                        "location": "São Paulo, Brazil",
                        "category": "Project Management",
                    }
                ]
                return {"status": 200, "final_url": url, "html": phenom_html(jobs), "error": ""}
            return {"status": 200, "final_url": url, "html": phenom_html([]), "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "phenom")
        self.assertTrue(any("keywords=technical+program+manager" in url for url in calls))
        self.assertTrue(any("from=10" in url for url in calls))
        self.assertEqual(
            [row["title"] for row in result["new"]],
            [
                "Lead Technical Program Manager, Platform Security | Irvine, CA or remote",
                "Program Manager | São Paulo, BR",
            ],
        )
        self.assertEqual(
            result["new"][0]["url"],
            "https://careers.blizzard.com/global/en/job/BLENGLOBALR027825EXTERNALENGLOBAL/lead-technical-program-manager-platform-security-irvine-ca-or-remote",
        )
        self.assertNotIn("Career Site Cookie Settings", [row["title"] for row in result["candidates"]])
        self.assertNotIn("Senior Animator – Temp (SFD / Cinematics)", [row["title"] for row in result["candidates"]])

    def test_embedded_json_jobs_careers_check_extracts_riot_data_props_jobs(self):
        sqlite_store.initialize()
        resume = "Senior Technical Product and Program Manager with AI platform, operations, release, API, and web experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Riot Games",
                "careers_url": "https://www.riotgames.com/en/work-with-us/jobs",
            },
        )
        payload = {
            "jobs": [
                {
                    "title": "Principal Technical Product Manager, AI - Central Product",
                    "products": "Riot Operations & Support",
                    "office": "Los Angeles, USA",
                    "additionalOfficeNames": ["Mercer Island, USA"],
                    "craft": "Product Management Group",
                    "url": "/j/7551366",
                    "internalId": "REQ-0009411",
                },
                {
                    "title": "Senior Technical Program - Publishing Platform",
                    "products": "Riot Operations & Support",
                    "office": "Los Angeles, USA",
                    "craft": "Program Management Group",
                    "url": "/j/7723704",
                    "internalId": "REQ-0009818",
                },
                {
                    "title": "Associate Art Director - Unpublished R&D Product",
                    "products": "Riot Discovery",
                    "office": "Los Angeles, USA",
                    "craft": "Art",
                    "url": "/j/7984196",
                    "internalId": "REQ-0010063",
                },
            ],
            "filterData": {},
        }
        html = f'<main><div data-props="{html_lib.escape(json.dumps(payload), quote=True)}"></div></main>'

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda url: {"status": 200, "final_url": url, "html": html, "error": ""},
        )

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "embedded_json_jobs")
        self.assertEqual(
            [row["title"] for row in result["new"]],
            [
                "Principal Technical Product Manager, AI - Central Product",
                "Senior Technical Program - Publishing Platform",
            ],
        )
        self.assertEqual(
            result["new"][0]["url"],
            "https://www.riotgames.com/en/work-with-us/job/7551366",
        )
        self.assertNotIn("Associate Art Director - Unpublished R&D Product", [row["title"] for row in result["candidates"]])

    def test_algolia_jobs_careers_check_upgrades_generic_source_and_searches_resume_terms(self):
        sqlite_store.initialize()
        resume = "Senior Technical Product and Program Manager with AI platform, operations, release, API, and web experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company(
            "",
            {
                "name": "Ubisoft",
                "careers_url": "https://www.ubisoft.com/en-us/company/careers/search",
            },
        )
        companies.save_company_career_source(
            company["id"],
            company["careers_url"],
            "generic_html",
            {},
            ["Previous generic fallback."],
            status="verified",
        )
        page_html = """
            <script>
            window.__PRELOADED_STATE__ = {
                "language": {"locale": "en-us"},
                "configuration": {
                    "modules": {
                        "dm-AlgoliaSearch": {
                            "AlgoliaAppId": "APPID",
                            "AlgoliaApiKey": "apikey"
                        }
                    }
                }
            }
            </script>
            <script>var index = "jobs_en-us_default";</script>
        """
        calls = []

        def fetcher(url, headers=None, method="GET", data=None):
            calls.append({"url": url, "headers": headers or {}, "method": method, "data": data})
            if method == "POST":
                return {
                    "status": 200,
                    "final_url": url,
                    "html": json.dumps(
                        {
                            "hits": [
                                {
                                    "title": "Technical Program Manager - AI initiatives",
                                    "link": "https://jobs.smartrecruiters.com/Ubisoft2/744000133930119-technical-program-manager-ai-initiatives",
                                    "city": "Paris",
                                    "countryCode": "fr",
                                    "jobFamily": "Project & Product Management",
                                    "team": "Technical Project Management",
                                    "description": "Bridge AI engineering and roadmap execution for platform operations.",
                                },
                                {
                                    "title": "Event Scripting Designer",
                                    "link": "https://jobs.smartrecruiters.com/Ubisoft2/744000128681179-event-scripting-designer",
                                    "city": "Sofia",
                                    "countryCode": "bg",
                                    "jobFamily": "Design",
                                    "team": "Level Design",
                                    "description": "Integrate scripted events.",
                                },
                            ]
                        }
                    ),
                    "error": "",
                }
            return {"status": 200, "final_url": url, "html": page_html, "error": ""}

        result = companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        self.assertEqual(source["platform_type"], "algolia_jobs")
        self.assertTrue(any(call["method"] == "POST" for call in calls))
        self.assertTrue(any(call["url"] == "https://APPID-dsn.algolia.net/1/indexes/jobs_en-us_default/query" for call in calls))
        self.assertEqual(
            result["new"][0]["title"],
            "Technical Program Manager - AI initiatives",
        )
        self.assertEqual(
            result["new"][0]["url"],
            "https://jobs.smartrecruiters.com/Ubisoft2/744000133930119-technical-program-manager-ai-initiatives",
        )
        self.assertNotIn("Event Scripting Designer", [row["title"] for row in result["candidates"]])

    def test_candidate_fit_uses_enriched_description_text(self):
        checked_at = "2026-06-30T10:00:00"
        resume = "Technical Product Manager with AI platform and developer tools experience."

        scored_with_description = companies.score_candidate_fit(
            {
                "title": "Product Manager",
                "url": "https://example.com/jobs/123",
                "description": "Own AI platform strategy for developer tools.",
            },
            resume,
            checked_at,
        )
        scored_without_description = companies.score_candidate_fit(
            {"title": "Product Manager", "url": "https://example.com/jobs/123"},
            resume,
            checked_at,
        )

        self.assertGreater(int(scored_with_description["fit_score"]), int(scored_without_description["fit_score"]))

    def test_candidate_fit_weights_title_signals_above_description_keyword_density(self):
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            search_goals="Technical program manager with AI platform infrastructure and SaaS experience.",
            fit_signals={
                "role_terms": "technical program manager | 42",
                "domain_terms": "ai | 16\nplatform | 14\ninfrastructure | 12\nsaas | 10",
                "seniority_terms": "principal | 12",
                "search_terms": "technical program manager",
                "low_match_terms": "sales",
                "exclusion_terms": "",
            },
        )
        resume = settings.fit_context()

        title_specific = companies.score_candidate_fit(
            {
                "title": "Principal Technical Program Manager, AI Platform",
                "url": "https://example.com/jobs/role",
                "description": "Lead a cross-functional program.",
            },
            resume,
            "2026-07-29T10:00:00",
        )
        description_dense = companies.score_candidate_fit(
            {
                "title": "Technical Program Manager",
                "url": "https://example.com/jobs/role-2",
                "description": "AI platform infrastructure SaaS AI platform infrastructure SaaS.",
            },
            resume,
            "2026-07-29T10:00:00",
        )

        self.assertGreater(int(title_specific["fit_score"]), int(description_dense["fit_score"]))
        self.assertLess(int(description_dense["fit_score"]), 100)

    def test_candidate_fit_can_use_search_goals_context(self):
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            search_goals="Primary:\nGame developer tools and builder productivity workflows",
        )

        scored = companies.score_candidate_fit(
            {
                "title": "Product Manager, Game Developer Tools",
                "url": "https://example.com/jobs/product-manager-game-developer-tools",
                "description": "Build workflows for creative and technical builders.",
            },
            settings.fit_context(),
            "2026-06-30T10:00:00",
        )

        self.assertGreaterEqual(int(scored["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)
        self.assertIn("product manager", scored["fit_summary"])

    def test_candidate_fit_uses_configured_fit_signals(self):
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            search_goals="Primary:\nUEFN creator economy workflows",
            fit_signals={
                "role_terms": "creator ecosystem producer | 50",
                "domain_terms": "uefn | 20\ncreator economy | 15",
                "seniority_terms": "principal | 7",
                "search_terms": "creator ecosystem producer",
                "low_match_terms": "sales",
                "exclusion_terms": "warehouse",
            },
        )

        scored = companies.score_candidate_fit(
            {
                "title": "Principal Creator Ecosystem Producer",
                "url": "https://example.com/jobs/creator-ecosystem-producer",
                "description": "Lead UEFN creator economy workflows.",
            },
            settings.fit_context(),
            "2026-07-01T10:00:00",
        )

        self.assertGreaterEqual(int(scored["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)
        self.assertIn("creator ecosystem producer", scored["fit_summary"])
        self.assertIn("uefn", scored["fit_summary"])

    def test_resume_search_terms_use_configured_search_terms(self):
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            search_goals="Primary:\nSimulation delivery systems",
            fit_signals={"search_terms": "simulation delivery lead\nrobotics program manager"},
        )

        terms = companies.resume_search_terms(settings.fit_context())

        self.assertEqual(terms[:2], ["simulation delivery lead", "robotics program manager"])

    def test_resume_search_terms_expand_roles_with_configured_level_terms(self):
        settings.save_settings(
            "openai",
            "gpt-5.5",
            "",
            "",
            fit_signals={
                "search_terms": "technical program manager\nproduct manager",
                "role_terms": "technical program manager | 42\nproduct manager | 34",
                "seniority_terms": "iii | 6\nsenior | 8",
            },
        )

        terms = companies.resume_search_terms(settings.fit_context(), max_terms=6)

        self.assertEqual(terms[:2], ["technical program manager", "product manager"])
        self.assertIn("technical program manager iii", terms)
        self.assertIn("senior technical program manager", terms)

    def test_candidate_fit_keeps_excluded_roles_below_recommendation_threshold(self):
        scored = companies.score_candidate_fit(
            {
                "title": "Legal Program Manager",
                "url": "https://example.com/jobs/legal-program-manager",
                "description": "Lead AI platform operations for senior stakeholders.",
            },
            "Senior Technical Product and Program Manager with AI platform operations experience.",
            "2026-06-30T10:00:00",
        )

        self.assertLess(int(scored["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)

    def test_candidate_fit_uses_category_for_excluded_roles(self):
        scored = companies.score_candidate_fit(
            {
                "title": "Program Manager",
                "url": "https://example.com/jobs/program-manager",
                "category": "Sales",
            },
            "Senior Technical Product and Program Manager with platform operations experience.",
            "2026-06-30T10:00:00",
        )

        self.assertLess(int(scored["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)

    def test_candidate_fit_ignores_sales_compensation_boilerplate_for_exclusions(self):
        scored = companies.score_candidate_fit(
            {
                "title": "Technical Program Manager, Cloud Inference",
                "url": "https://example.com/jobs/technical-program-manager-cloud-inference",
                "description": (
                    "Own AI platform infrastructure delivery. "
                    "For sales roles, compensation may include commissions."
                ),
            },
            "Senior Technical Program Manager with AI platform infrastructure experience.",
            "2026-07-09T10:00:00",
        )

        self.assertGreaterEqual(int(scored["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)

    def test_check_company_postings_does_not_recommend_latest_candidate_without_detail(self):
        sqlite_store.initialize()
        resume = "Senior Technical Program Manager with AI platform experience."
        settings.save_resume_upload("resume.txt", base64.b64encode(resume.encode()).decode())
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        old_candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        old_candidate.update({
            "id": "CP0001",
            "company_id": company["id"],
            "title": "Senior Technical Program Manager, Old Search",
            "url": "https://example.com/jobs/old-search",
            "status": "new",
            "last_seen_at": "2026-06-01T00:00:00",
            "fit_score": "100",
        })
        repository.write_company_posting_candidates([old_candidate])
        html = '<a href="/jobs/current-search">Senior Technical Program Manager, Current Search</a>'

        result = companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {"status": 200, "final_url": "https://example.com/careers", "html": html, "error": ""},
        )

        self.assertEqual(result["recommended"], [])
        current = next(row for row in result["candidates"] if row["title"].endswith("Current Search"))
        self.assertEqual(companies.candidate_review_state(current), "needs-detail")

    def test_recommended_candidates_are_limited_for_review(self):
        rows = []
        for index in range(companies.RECOMMENDED_CANDIDATE_LIMIT + 5):
            rows.append({
                "id": f"CP{index:04d}",
                "title": f"Role {index:04d}",
                "url": f"https://example.com/jobs/{index}",
                "status": "new",
                "fit_score": "90",
                "description_excerpt": "Detailed role requirements and responsibilities. " * 20,
                "scan_state": "current",
            })

        recommended = companies.recommended_candidates(rows)

        self.assertEqual(len(recommended), companies.RECOMMENDED_CANDIDATE_LIMIT)

    def test_candidate_status_transitions(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        companies.check_company_postings(
            company["id"],
            fetcher=lambda _url: {"status": 200, "final_url": "https://example.com/careers", "html": '<a href="/jobs/role">Role</a>', "error": ""},
        )
        candidate = repository.read_company_posting_candidates()[0]

        ignored = companies.update_candidate_status(candidate["id"], "ignored")
        ingested = companies.update_candidate_status(candidate["id"], "ingested")

        self.assertEqual(ignored["status"], "ignored")
        self.assertEqual(ingested["status"], "pursued")

    def test_bulk_candidate_status_transition_writes_selected_candidates_once(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        candidates = []
        for index in range(1, 4):
            candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
            candidate.update({
                "id": f"CP{index:04d}",
                "company_id": company["id"],
                "title": f"Role {index}",
                "url": f"https://example.com/jobs/{index}",
                "status": "new",
            })
            candidates.append(candidate)
        repository.write_company_posting_candidates(candidates)

        result = companies.update_candidate_statuses(["CP0001", "CP0003"], "ignored")
        stored = {
            row["id"]: row["status"]
            for row in repository.read_company_posting_candidates()
        }

        self.assertEqual(result["count"], 2)
        self.assertEqual(stored, {
            "CP0001": "ignored",
            "CP0002": "new",
            "CP0003": "ignored",
        })

    def test_fetch_careers_page_uses_certifi_ssl_context(self):
        response = Mock()
        response.status = 200
        response.headers.get_content_charset.return_value = "utf-8"
        response.geturl.return_value = "https://example.com/careers"
        response.read.return_value = b"<html></html>"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        context = object()

        with patch("hunter.companies._certifi_ca_file", return_value="/tmp/certifi.pem"), \
             patch("hunter.companies.ssl.create_default_context", return_value=context) as create_context, \
             patch("hunter.companies.urlopen", return_value=response) as open_url:
            fetched = companies.fetch_careers_page("https://example.com/careers")

        create_context.assert_called_once_with(cafile="/tmp/certifi.pem")
        self.assertIs(open_url.call_args.kwargs["context"], context)
        request = open_url.call_args.args[0]
        self.assertIn("text/html", request.headers["Accept"])
        self.assertEqual(fetched["status"], 200)
        self.assertEqual(fetched["html"], "<html></html>")

    def test_check_all_company_postings_aggregates_results_and_keeps_going(self):
        sqlite_store.initialize()
        apple = companies.upsert_company("", {"name": "Apple", "careers_url": "https://jobs.apple.com"})
        companies.upsert_company("", {"name": "No Careers"})
        companies.upsert_company("", {"name": "Archived", "interest_status": "archived", "careers_url": "https://archived.example/jobs"})
        companies.upsert_company("", {"name": "Not Interested", "interest_status": "not-interested", "careers_url": "https://not-interested.example/jobs"})
        netflix = companies.upsert_company("", {"name": "Netflix", "careers_url": "https://jobs.netflix.com"})

        def fake_check(company_id, fetcher=None):
            del fetcher
            if company_id == netflix["id"]:
                raise ValueError("error: blocked")
            return {
                "company": companies.get_company(company_id),
                "career_source": None,
                "candidates": [],
                "new": [{"id": "CP0001"}],
                "recommended": [{"id": "CP0001"}],
            }

        with patch("hunter.companies.check_company_postings", side_effect=fake_check):
            result = companies.check_all_company_postings()

        self.assertEqual(result["checked_count"], 1)
        self.assertEqual(result["skipped_count"], 3)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["new_count"], 1)
        self.assertEqual(result["recommended_count"], 1)
        self.assertEqual(result["checked"][0]["company"]["id"], apple["id"])
        self.assertEqual(
            {(row["company"]["name"], row["reason"]) for row in result["skipped"]},
            {
                ("No Careers", "missing careers URL"),
                ("Archived", "archived"),
                ("Not Interested", "not interested"),
            },
        )
        self.assertEqual(result["errors"][0]["company"]["id"], netflix["id"])

    def test_mcp_company_detail_caps_embedded_candidates(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example"})
        rows = []
        for index in range(30):
            candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
            candidate.update({
                "id": f"CP{index + 1:04d}",
                "company_id": company["id"],
                "title": f"Role {index}",
                "url": f"https://example.com/jobs/{index}",
                "status": "new",
            })
            rows.append(candidate)
        repository.write_company_posting_candidates(rows)

        result = mcp_server.tool_get_company({"id": company["id"]})
        payload = json.loads(result["content"][0]["text"])

        self.assertEqual(payload["candidate_count"], 30)
        self.assertEqual(len(payload["candidates"]), 25)

    def test_mcp_get_company_candidate_returns_full_detail(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example"})
        candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        candidate.update({
            "id": "CP0001",
            "company_id": company["id"],
            "title": "Technical Program Manager",
            "url": "https://example.com/jobs/tpm",
            "notes": "Full candidate notes",
        })
        repository.write_company_posting_candidates([candidate])

        result = mcp_server.tool_get_company_candidate({"id": "CP0001"})
        payload = json.loads(result["content"][0]["text"])

        self.assertEqual(payload["candidate"]["notes"], "Full candidate notes")
        self.assertEqual(payload["company"]["name"], "Example")

    def test_mcp_list_company_candidates_filters_status_and_fit(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example"})
        other = companies.upsert_company("", {"name": "Other"})
        candidates = []
        for candidate_id, company_id, title, status, fit_score in [
            ("CP0001", company["id"], "Strong role", "new", "82"),
            ("CP0002", company["id"], "Ignored role", "ignored", "91"),
            ("CP0003", other["id"], "Other role", "new", "95"),
            ("CP0004", company["id"], "Lower role", "new", "44"),
        ]:
            candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
            candidate.update({
                "id": candidate_id,
                "company_id": company_id,
                "title": title,
                "url": f"https://example.com/jobs/{candidate_id.lower()}",
                "status": status,
                "fit_score": fit_score,
                "description_excerpt": "Responsibilities and requirements. " * 30,
                "scan_state": "current",
            })
            candidates.append(candidate)
        repository.write_company_posting_candidates(candidates)

        result = mcp_server.call_named_tool(
            "hunter_list_company_candidates",
            {"company_id": company["id"], "status": "new", "minimum_fit_score": 60},
        )
        payload = json.loads(result["content"][0]["text"])

        self.assertEqual(payload["company"]["name"], "Example")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["candidates"][0]["id"], "CP0001")
        self.assertTrue(payload["candidates"][0]["recommended"])

    def test_mcp_company_candidates_default_to_tracked_companies(self):
        sqlite_store.initialize()
        tracked = companies.upsert_company(
            "", {"name": "Tracked", "tracking_status": "tracked"}
        )
        discovered = companies.upsert_company(
            "", {"name": "Discovered", "tracking_status": "discovered"}
        )
        candidates = []
        for index, company in enumerate([tracked, discovered], start=1):
            candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
            candidate.update({
                "id": f"CP{index:04d}",
                "company_id": company["id"],
                "title": f"Role {index}",
                "url": f"https://example.com/jobs/{index}",
                "status": "new",
                "description_excerpt": "Responsibilities and requirements. " * 30,
                "scan_state": "current",
            })
            candidates.append(candidate)
        repository.write_company_posting_candidates(candidates)

        default_payload = json.loads(
            mcp_server.tool_list_company_candidates({})["content"][0]["text"]
        )
        all_payload = json.loads(
            mcp_server.tool_list_company_candidates({"tracking_status": "all"})["content"][0]["text"]
        )

        self.assertEqual(default_payload["tracking_status"], "tracked")
        self.assertEqual([row["company_id"] for row in default_payload["candidates"]], [tracked["id"]])
        self.assertEqual(default_payload["other_tracking_status_candidate_count"], 1)
        self.assertEqual({row["company_id"] for row in all_payload["candidates"]}, {tracked["id"], discovered["id"]})

    def test_company_recommendations_require_usable_detail_and_current_freshness(self):
        base = {
            "status": "new",
            "fit_score": "90",
            "description_excerpt": "Responsibilities and requirements. " * 30,
            "scan_state": "current",
        }
        ready = {**base, "id": "CP0001", "title": "Ready"}
        needs_detail = {**base, "id": "CP0002", "title": "Needs detail", "description_excerpt": "Too short"}
        needs_freshness = {**base, "id": "CP0003", "title": "Needs freshness", "scan_state": "not-seen"}
        failed = {
            **base,
            "id": "CP0004",
            "title": "Failed extraction",
            "description_excerpt": "",
            "source_platform": "ashby",
        }

        self.assertEqual(companies.candidate_review_state(ready), "ready")
        self.assertEqual(companies.candidate_review_state(needs_detail), "needs-detail")
        self.assertEqual(companies.candidate_review_state(needs_freshness), "needs-freshness")
        self.assertEqual(companies.candidate_review_state(failed), "failed-extraction")
        self.assertEqual(
            [row["id"] for row in companies.recommended_candidates([ready, needs_detail, needs_freshness, failed])],
            ["CP0001"],
        )

    def test_candidate_review_contract_gates_company_interest_statuses(self):
        sqlite_store.initialize()
        company_rows = {}
        for name, interest_status in [
            ("Neutral Co", "neutral"),
            ("Interested Co", "interested"),
            ("Not Interested Co", "not-interested"),
            ("Archived Co", "archived"),
        ]:
            company_rows[interest_status] = companies.upsert_company(
                "", {"name": name, "interest_status": interest_status}
            )

        company_candidates = []
        discovery_candidates = []
        for index, interest_status in enumerate(company_rows, start=1):
            company_id = company_rows[interest_status]["id"]
            company_candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
            company_candidate.update(
                {
                    "id": f"CP{index:04d}",
                    "company_id": company_id,
                    "title": f"Company role {index}",
                    "status": "new",
                    "fit_score": "90",
                    "fit_summary": "must not be assessed when excluded",
                }
            )
            company_candidates.append(company_candidate)
            discovery_candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
            discovery_candidate.update(
                {
                    "id": f"DC{index:04d}",
                    "company_id": company_id,
                    "company": company_rows[interest_status]["name"],
                    "title": f"Discovery role {index}",
                    "status": "new",
                    "fit_score": "90",
                    "processing_status": "ready",
                }
            )
            discovery_candidates.append(discovery_candidate)
        repository.write_company_posting_candidates(company_candidates)
        repository.write_discovery_candidates(discovery_candidates)

        company_payload = json.loads(
            mcp_server.tool_list_company_candidates({})["content"][0]["text"]
        )
        discovery_payload = json.loads(
            mcp_server.tool_list_discovery_candidates({})["content"][0]["text"]
        )
        state = app_state.build_payload()

        self.assertEqual(
            {row["company_id"] for row in company_payload["candidates"]},
            {company_rows["neutral"]["id"], company_rows["interested"]["id"]},
        )
        self.assertEqual(company_payload["excluded_company_candidate_count"], 2)
        self.assertEqual(
            {row["company_id"] for row in discovery_payload["candidates"]},
            {company_rows["neutral"]["id"], company_rows["interested"]["id"]},
        )
        self.assertEqual(discovery_payload["excluded_company_candidate_count"], 2)
        self.assertEqual(state["candidate_review_audit"]["excluded_company_candidate_count"], 4)
        self.assertEqual(len(state["company_posting_candidates"]), 2)
        self.assertEqual(len(state["discovery_candidates"]), 2)

        opted_in_state = app_state.build_payload(include_excluded_companies=True)
        self.assertEqual(len(opted_in_state["company_posting_candidates"]), 4)
        self.assertEqual(len(opted_in_state["discovery_candidates"]), 4)

        opted_in = json.loads(
            mcp_server.tool_list_company_candidates(
                {"include_excluded_companies": True}
            )["content"][0]["text"]
        )
        self.assertEqual(opted_in["count"], 4)
        excluded_ids = {
            company_rows["not-interested"]["id"],
            company_rows["archived"]["id"],
        }
        self.assertTrue(
            all(not row["recommended"] for row in opted_in["candidates"] if row["company_id"] in excluded_ids)
        )

    def test_excluded_company_candidate_actions_and_scan_are_rejected_without_mutation(self):
        sqlite_store.initialize()
        company = companies.upsert_company(
            "",
            {
                "name": "No Thanks",
                "interest_status": "not-interested",
                "tracking_status": "tracked",
                "careers_url": "https://example.com/jobs",
            },
        )
        candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        candidate.update(
            {"id": "CP0001", "company_id": company["id"], "title": "Role", "status": "ignored"}
        )
        repository.write_company_posting_candidates([candidate])
        fetched = []

        with self.assertRaisesRegex(ValueError, "not-interested"):
            companies.check_company_postings(company["id"], fetcher=lambda url: fetched.append(url))
        with self.assertRaisesRegex(ValueError, "not-interested"):
            companies.update_candidate_status("CP0001", "new")
        with patch("hunter.companies.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "not-interested"):
                companies.pursue_candidate("CP0001")
            run.assert_not_called()

        self.assertEqual(fetched, [])
        self.assertEqual(repository.read_company_posting_candidates()[0]["status"], "ignored")

    def test_mcp_update_company_candidate_changes_review_status(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example"})
        candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        candidate.update({"id": "CP0001", "company_id": company["id"], "title": "Role", "status": "new"})
        repository.write_company_posting_candidates([candidate])

        result = mcp_server.call_named_tool(
            "hunter_update_company_candidate",
            {"id": "CP0001", "status": "ignored"},
        )
        payload = json.loads(result["content"][0]["text"])

        self.assertEqual(payload["candidate"]["status"], "ignored")
        self.assertEqual(payload["company"]["name"], "Example")
        self.assertEqual(repository.read_company_posting_candidates()[0]["status"], "ignored")

    def test_app_state_and_mcp_expose_companies(self):
        sqlite_store.initialize()
        companies.upsert_company("", {"name": "Apple"})

        payload = app_state.build_payload()
        tool_names = set(mcp_server.TOOLS)

        self.assertEqual(payload["companies"][0]["name"], "Apple")
        self.assertIn("company_contacts", payload)
        self.assertIn("company_career_sources", payload)
        self.assertIn("company_posting_candidates", payload)
        self.assertIn("company_career_scans", payload)
        self.assertIn("hunter_list_companies", tool_names)
        self.assertIn("hunter_upsert_company", tool_names)
        self.assertIn("hunter_archive_company", tool_names)
        self.assertIn("hunter_restore_company", tool_names)
        self.assertIn("hunter_research_company", tool_names)
        self.assertIn("hunter_track_company", tool_names)
        self.assertIn("hunter_untrack_company", tool_names)
        self.assertIn("hunter_resolve_company_metadata_suggestion", tool_names)
        self.assertIn("hunter_check_company_postings", tool_names)
        self.assertIn("hunter_get_company_candidate", tool_names)
        self.assertIn("hunter_list_company_candidates", tool_names)
        self.assertIn("hunter_update_company_candidate", tool_names)
        self.assertIn("hunter_get_resume_text", tool_names)
        self.assertIn("hunter_get_settings", tool_names)
        self.assertIn("hunter_update_settings", tool_names)

    def test_app_state_enriches_company_candidates_with_scope_and_discovery_overlap(self):
        sqlite_store.initialize()
        company = companies.upsert_company(
            "",
            {"name": "Example", "tracking_status": "tracked", "interest_status": "interested"},
        )
        discovery.upsert_search(
            "",
            {
                "name": "US remote",
                "keywords": "technical program manager",
                "lanes": [
                    {
                        "id": "remote-us",
                        "label": "United States remote",
                        "location": "United States",
                        "work_modes": ["remote"],
                    }
                ],
            },
        )
        matching_url = "https://jobs.example.com/jobs/technical-program-manager"
        repository.write_company_posting_candidates([
            {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS} | {
                "id": "CP0001",
                "company_id": company["id"],
                "title": "Technical Program Manager",
                "url": matching_url,
                "location": "Remote; US",
                "work_mode": "Remote",
                "status": "new",
            },
            {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS} | {
                "id": "CP0002",
                "company_id": company["id"],
                "title": "Technical Program Manager",
                "url": "https://jobs.example.com/jobs/india-program-manager",
                "location": "Remote; Bengaluru, India",
                "work_mode": "Remote",
                "status": "new",
            },
        ])
        repository.write_discovery_candidates([
            {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS} | {
                "id": "DC0001",
                "company_id": company["id"],
                "title": "Technical Program Manager",
                "url": matching_url,
                "canonical_url": matching_url,
                "status": "ignored",
            }
        ])

        candidates = {
            candidate["id"]: candidate
            for candidate in app_state.build_payload()["company_posting_candidates"]
        }

        self.assertEqual(candidates["CP0001"]["lane_match"], "United States remote · Remote")
        self.assertEqual(candidates["CP0001"]["discovery_candidate_id"], "DC0001")
        self.assertEqual(candidates["CP0002"]["lane_match"], "")
        self.assertEqual(candidates["CP0002"]["discovery_candidate_id"], "")

    def test_app_state_canonicalizes_cross_pool_visibility_and_application_precedence(self):
        sqlite_store.initialize()
        company_rows = [
            companies.upsert_company(
                "",
                {
                    "name": name,
                    "tracking_status": "tracked",
                    "interest_status": "interested",
                },
            )
            for name in ["Needs Decision", "Ignored", "Ingested"]
        ]
        urls = [f"https://jobs.example.com/{index}" for index in range(1, 4)]
        company_candidates = []
        discovery_candidates = []
        for index, (company, url) in enumerate(zip(company_rows, urls), start=1):
            company_candidate = {
                field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS
            }
            company_candidate.update(
                {
                    "id": f"CP{index:04d}",
                    "company_id": company["id"],
                    "title": f"Role {index}",
                    "url": url,
                    "status": "ignored" if index == 2 else "new",
                    "scan_state": "current",
                    "fit_score": "70",
                }
            )
            discovery_candidate = {
                field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS
            }
            discovery_candidate.update(
                {
                    "id": f"DC{index:04d}",
                    "company_id": company["id"],
                    "title": f"Role {index}",
                    "url": url,
                    "canonical_url": url,
                    "status": "duplicate",
                }
            )
            company_candidates.append(company_candidate)
            discovery_candidates.append(discovery_candidate)
        repository.write_company_posting_candidates(company_candidates)
        repository.write_discovery_candidates(discovery_candidates)
        repository.write_applications(
            [
                application_row(
                    {
                        "id": "A0001",
                        "company_id": company_rows[2]["id"],
                        "company": company_rows[2]["name"],
                        "role": "Role 3",
                        "source_url": urls[2],
                        "stage": "applied",
                    }
                )
            ]
        )

        payload = app_state.build_payload()
        company_by_id = {
            candidate["id"]: candidate
            for candidate in payload["company_posting_candidates"]
        }
        discovery_by_id = {
            candidate["id"]: candidate
            for candidate in payload["discovery_candidates"]
        }

        self.assertEqual(company_by_id["CP0001"]["canonical_status"], "new")
        self.assertEqual(company_by_id["CP0002"]["canonical_status"], "ignored")
        self.assertEqual(company_by_id["CP0003"]["canonical_status"], "pursued")
        self.assertTrue(all(candidate["is_canonical"] for candidate in company_by_id.values()))
        self.assertTrue(all(not candidate["is_canonical"] for candidate in discovery_by_id.values()))
        self.assertEqual(
            [
                candidate["id"]
                for candidate in company_by_id.values()
                if candidate["is_canonical"] and candidate["canonical_status"] == "new"
            ],
            ["CP0001"],
        )

    def test_company_candidate_decisions_sync_to_linked_discovery_record(self):
        sqlite_store.initialize()
        company = companies.upsert_company(
            "",
            {
                "name": "Example",
                "tracking_status": "tracked",
                "interest_status": "interested",
            },
        )
        url = "https://jobs.example.com/linked-role"
        company_candidate = {
            field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS
        }
        company_candidate.update(
            {
                "id": "CP0001",
                "company_id": company["id"],
                "title": "Linked role",
                "url": url,
                "status": "new",
            }
        )
        discovery_candidate = {
            field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS
        }
        discovery_candidate.update(
            {
                "id": "DC0001",
                "company_id": company["id"],
                "title": "Linked role",
                "url": url,
                "canonical_url": url,
                "status": "duplicate",
                "ingested_application_id": "A0099",
            }
        )
        repository.write_company_posting_candidates([company_candidate])
        repository.write_discovery_candidates([discovery_candidate])

        companies.update_candidate_status("CP0001", "ignored")
        stored_discovery = repository.read_discovery_candidates()[0]
        self.assertEqual(stored_discovery["status"], "ignored")
        self.assertEqual(stored_discovery["ingested_application_id"], "A0099")

        companies.update_candidate_status("CP0001", "new")
        stored_discovery = repository.read_discovery_candidates()[0]
        self.assertEqual(stored_discovery["status"], "new")
        self.assertEqual(stored_discovery["ingested_application_id"], "A0099")

    def test_company_merge_suggestion_and_reviewed_merge_relink_records(self):
        sqlite_store.initialize()
        keep = companies.upsert_company("", {"name": "Reddit", "industry": "Social Networking Platforms"})
        duplicate = companies.upsert_company("", {"name": "Reddit, Inc.", "company_size": "1,001-5,000 employees"})
        repository.write_applications([
            application_row({"id": "A0001", "company": duplicate["name"], "company_id": duplicate["id"]}),
        ])
        discovery_candidate = {field: "" for field in schema.DISCOVERY_CANDIDATE_FIELDS}
        discovery_candidate.update(
            {
                "id": "DC0001",
                "company_id": duplicate["id"],
                "title": "Technical Program Manager",
                "url": "https://www.linkedin.com/jobs/view/1234567890",
                "status": "new",
            }
        )
        repository.write_discovery_candidates([discovery_candidate])
        repository.write_contacts([contact_row({"id": "C0001", "name": "Ada"})])
        companies.link_contact(duplicate["id"], "C0001")

        suggestions = companies.company_merge_suggestions()
        merged = companies.merge_companies(keep["id"], duplicate["id"])

        self.assertEqual(len(suggestions), 1)
        self.assertIn("Reddit, Inc.", merged["aliases"])
        self.assertEqual(merged["industry"], "Social Networking Platforms")
        self.assertEqual(merged["company_size"], "1,001–5,000 employees")
        self.assertEqual(repository.read_applications()[0]["company_id"], keep["id"])
        self.assertEqual(repository.read_discovery_candidates()[0]["company_id"], keep["id"])
        self.assertEqual(repository.read_company_contacts()[0]["company_id"], keep["id"])
        self.assertEqual(len(repository.read_companies()), 1)

    def test_export_company_data_writes_related_company_snapshot(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        companies.upsert_company("", {"name": "Other"})
        repository.write_contacts([contact_row({"id": "C0001", "name": "Ada"})])
        companies.link_contact(company["id"], "C0001")
        repository.write_applications([
            application_row({"id": "A0001", "company": "Example", "company_id": company["id"], "role": "Engineer"}),
            application_row({"id": "A0002", "company": "Other", "company_id": "CO0002", "role": "Designer"}),
        ])
        repository.write_actions([
            action_row({"id": "T0001", "application_id": "A0001", "title": "Research company"}),
            action_row({"id": "T0002", "application_id": "A0002", "title": "Unrelated action"}),
        ])
        repository.write_posting_snapshot("A0001", {
            "source_url": "https://example.com/jobs/engineer",
            "captured_at": "2026-07-21T12:00:00",
            "http_status": "200",
            "content_text": "Engineer\nBuild durable systems.",
            "source_html": "<main><h1>Engineer</h1><p>Build durable systems.</p></main>",
        })
        repository.write_company_career_sources([
            {field: "" for field in schema.COMPANY_CAREER_SOURCE_FIELDS} | {
                "company_id": company["id"],
                "source_url": "https://example.com/careers",
                "platform_type": "html",
            }
        ])
        repository.write_company_posting_candidates([
            {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS} | {
                "id": "CP0001",
                "company_id": company["id"],
                "title": "Platform Engineer",
                "url": "https://example.com/jobs/platform-engineer",
                "status": "new",
            }
        ])
        repository.write_company_career_scan(
            {
                "company_id": company["id"],
                "checked_at": "2026-07-17T10:00:00",
                "platform_type": "html",
                "status": "ok",
            }
        )

        result = companies.write_company_export(company["id"])
        payload = json.loads(result["path"].read_text(encoding="utf-8"))

        self.assertTrue(result["path"].name.startswith(f"company-data-{company['id']}-"))
        self.assertEqual(payload["scope"]["company_count"], 1)
        self.assertEqual(payload["companies"][0]["company"]["name"], "Example")
        self.assertEqual(payload["companies"][0]["posting_snapshots"][0]["application_id"], "A0001")
        self.assertEqual(payload["tables"]["posting_snapshots"][0]["content_text"], "Engineer\nBuild durable systems.")
        self.assertEqual(payload["companies"][0]["contacts"][0]["name"], "Ada")
        self.assertEqual(payload["companies"][0]["postings"][0]["id"], "A0001")
        self.assertEqual(payload["companies"][0]["actions"][0]["id"], "T0001")
        self.assertEqual(payload["companies"][0]["career_sources"][0]["source_url"], "https://example.com/careers")
        self.assertEqual(payload["companies"][0]["posting_candidates"][0]["id"], "CP0001")
        self.assertEqual(payload["companies"][0]["career_scans"][0]["status"], "ok")
        self.assertEqual([row["id"] for row in payload["tables"]["applications"]], ["A0001"])
        self.assertEqual([row["id"] for row in payload["tables"]["actions"]], ["T0001"])
        self.assertEqual(payload["tables"]["company_career_scans"][0]["company_id"], company["id"])


    def test_company_candidate_qualification_uses_all_saved_location_lanes(self):
        searches = [{
            "lanes": [{
                "id": "remote-us",
                "label": "United States remote",
                "location": "United States",
                "work_modes": ["remote"],
            }]
        }]
        eligible = {
            "location": "United States",
            "work_mode": "Remote",
            "description_excerpt": "This role may be held remotely in the United States.",
        }
        uncertain = {"location": "", "work_mode": "", "description_excerpt": ""}
        ineligible = {
            "location": "Bengaluru, India",
            "work_mode": "Remote",
            "description_excerpt": "Remote role based in India.",
        }

        self.assertEqual(companies.candidate_qualification(eligible, searches)[0], "eligible")
        self.assertEqual(companies.candidate_review_state(uncertain, searches), "needs-qualification")
        self.assertEqual(companies.candidate_review_state(ineligible, searches), "ineligible")

    def test_company_candidate_requisition_ids_prefer_structured_source_identity(self):
        first = {
            "url": "https://jobs.example.com/careers",
            "source_job_id": "job-id:8109626",
        }
        second = {
            "url": "https://jobs.example.com/careers",
            "source_job_id": "job-id:8026543",
        }
        workday = {
            "url": "https://jobs.example.com/careers",
            "source_job_id": "senior-product-manager-r76154-1",
        }

        self.assertEqual(companies.candidate_requisition_ids(first), {"8109626"})
        self.assertEqual(companies.candidate_requisition_ids(second), {"8026543"})
        self.assertEqual(companies.candidate_requisition_ids(workday), {"r76154-1"})
        self.assertFalse(companies.candidate_requisition_ids(first) & companies.candidate_requisition_ids(second))
        tracked = {
            "postings": [{
                "requisition_ids": {"8026543"},
                "identity_keys": companies.posting_identity_keys(second["url"]),
                "title_key": companies.normalized_key("Product Manager"),
            }]
        }
        self.assertFalse(companies.candidate_is_tracked({**first, "title": "Product Manager"}, tracked))

    def test_mcp_company_candidates_excludes_out_of_scope_by_default(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {
            "name": "Example",
            "tracking_status": "tracked",
            "interest_status": "interested",
        })
        discovery.upsert_search("", {
            "name": "US remote",
            "keywords": "program manager",
            "lanes": [{
                "id": "remote-us",
                "label": "United States remote",
                "location": "United States",
                "work_modes": ["remote"],
            }],
        })
        rows = []
        for candidate_id, location, work_mode in [
            ("CP0001", "Remote, United States", "Remote"),
            ("CP0002", "Bengaluru, India", "Remote"),
            ("CP0003", "", ""),
        ]:
            row = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
            row.update({
                "id": candidate_id,
                "company_id": company["id"],
                "title": "Technical Program Manager",
                "url": f"https://jobs.example.com/jobs/{candidate_id.lower()}",
                "location": location,
                "work_mode": work_mode,
                "status": "new",
                "fit_score": "80",
                "description_excerpt": "Responsibilities and requirements. " * 30 if location else "",
                "scan_state": "current",
            })
            rows.append(row)
        repository.write_company_posting_candidates(rows)

        default_payload = json.loads(mcp_server.tool_list_company_candidates({})["content"][0]["text"])
        audit_payload = json.loads(mcp_server.tool_list_company_candidates({"include_out_of_scope": True})["content"][0]["text"])

        self.assertEqual({row["id"] for row in default_payload["candidates"]}, {"CP0001", "CP0003"})
        self.assertEqual(default_payload["out_of_scope_candidate_count"], 1)
        self.assertEqual({row["id"] for row in audit_payload["candidates"]}, {"CP0001", "CP0002", "CP0003"})
        uncertain = next(row for row in default_payload["candidates"] if row["id"] == "CP0003")
        self.assertEqual(uncertain["review_state"], "needs-qualification")
        self.assertFalse(uncertain["recommended"])


def table_names(connection):
    return {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def application_row(overrides):
    row = {field: "" for field in schema.APPLICATION_FIELDS}
    row.update({
        "company": "Example",
        "role": "Engineer",
        "stage": schema.DEFAULT_STAGE,
        "priority": schema.DEFAULT_PRIORITY,
    })
    row.update(overrides)
    return row


def action_row(overrides):
    row = {field: "" for field in schema.ACTION_FIELDS}
    row.update({
        "id": "T0001",
        "application_id": "A0001",
        "company": "Example",
        "role": "Engineer",
        "type": "review-fit",
        "title": "Review fit",
        "status": "open",
        "priority": "medium",
        "due_date": "2026-07-01",
        "created_date": "2026-06-28",
    })
    row.update(overrides)
    return row


def contact_row(overrides):
    row = {field: "" for field in schema.CONTACT_FIELDS}
    row.update({
        "id": "C0001",
        "name": "Ada",
    })
    row.update(overrides)
    return row


if __name__ == "__main__":
    unittest.main()
