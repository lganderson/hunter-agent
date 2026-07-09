import base64
import html as html_lib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hunter import app_state, companies, mcp_server, paths, repository, schema, settings, sqlite_store


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
        self.assertIn("fit_score", candidates[0])

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
        self.assertEqual(result["candidates"][0]["status"], "ingested")

    def test_ingest_candidate_passes_candidate_title_as_role(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Example", "careers_url": "https://example.com/careers"})
        candidate = {field: "" for field in schema.COMPANY_POSTING_CANDIDATE_FIELDS}
        candidate.update({
            "id": "CP0001",
            "company_id": company["id"],
            "title": "Product Manager, AI Platform",
            "url": "https://example.com/jobs/product-manager-ai-platform",
            "status": "new",
        })
        repository.write_company_posting_candidates([candidate])

        with patch.object(companies.subprocess, "run", return_value=Mock(returncode=0, stdout="ingested", stderr="")) as run:
            result = companies.ingest_candidate("CP0001")

        command = run.call_args.args[0]
        self.assertIn("--role", command)
        self.assertEqual(command[command.index("--role") + 1], "Product Manager, AI Platform")
        self.assertEqual(command[-1], "https://example.com/jobs/product-manager-ai-platform")
        self.assertEqual(result["candidate"]["status"], "ingested")

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
        self.assertEqual(statuses["Old Role"], "new")
        self.assertEqual(statuses["New Role"], "new")
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

        recommended = result["recommended"]
        candidates = result["candidates"]
        low_fit = next(candidate for candidate in candidates if candidate["title"] == "Account Executive")
        self.assertEqual(recommended[0]["title"], "Senior Technical Program Manager, AI Platform")
        self.assertGreater(int(recommended[0]["fit_score"]), int(low_fit["fit_score"] or "0"))
        self.assertIn("technical program manager", recommended[0]["fit_summary"])
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
                <a href="jobs/results/91051814228501190-senior-technical-program-manager-customer-engagement-applied-ai">
                  91051814228501190 Senior Technical Program Manager, Customer Engagement, Applied AI
                </a>
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
        self.assertGreaterEqual(int(result["new"][0]["fit_score"]), companies.FIT_RECOMMENDATION_THRESHOLD)

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

        with self.assertRaisesRegex(ValueError, "AWS WAF JavaScript challenge"):
            companies.check_company_postings(company["id"], fetcher=fetcher)

        source = repository.read_company_career_sources()[0]
        checked = companies.get_company(company["id"])
        self.assertEqual(source["platform_type"], "avature_waf_blocked")
        self.assertEqual(source["status"], "blocked")
        self.assertIn("aws_waf_javascript_challenge", source["config_json"])
        self.assertIn("AWS WAF JavaScript challenge", checked["last_check_status"])

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
                                "description": "Lead GitHub Copilot AI platform strategy for developer tools.",
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
        self.assertEqual(result["recommended"][0]["title"], "Product Manager, API Agents")

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

    def test_check_company_postings_recommends_only_latest_seen_candidates(self):
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

        self.assertEqual([row["title"] for row in result["recommended"]], ["Senior Technical Program Manager, Current Search"])

    def test_recommended_candidates_are_limited_for_review(self):
        rows = []
        for index in range(companies.RECOMMENDED_CANDIDATE_LIMIT + 5):
            rows.append({
                "id": f"CP{index:04d}",
                "title": f"Role {index:04d}",
                "url": f"https://example.com/jobs/{index}",
                "status": "new",
                "fit_score": "90",
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
        self.assertEqual(ingested["status"], "ingested")

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
        self.assertEqual(result["skipped_count"], 2)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["new_count"], 1)
        self.assertEqual(result["recommended_count"], 1)
        self.assertEqual(result["checked"][0]["company"]["id"], apple["id"])
        self.assertEqual(
            {(row["company"]["name"], row["reason"]) for row in result["skipped"]},
            {("No Careers", "missing careers URL"), ("Archived", "archived")},
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

    def test_app_state_and_mcp_expose_companies(self):
        sqlite_store.initialize()
        companies.upsert_company("", {"name": "Apple"})

        payload = app_state.build_payload()
        tool_names = set(mcp_server.TOOLS)

        self.assertEqual(payload["companies"][0]["name"], "Apple")
        self.assertIn("company_contacts", payload)
        self.assertIn("company_career_sources", payload)
        self.assertIn("company_posting_candidates", payload)
        self.assertIn("hunter_list_companies", tool_names)
        self.assertIn("hunter_upsert_company", tool_names)
        self.assertIn("hunter_archive_company", tool_names)
        self.assertIn("hunter_restore_company", tool_names)
        self.assertIn("hunter_check_company_postings", tool_names)
        self.assertIn("hunter_get_company_candidate", tool_names)
        self.assertIn("hunter_get_resume_text", tool_names)
        self.assertIn("hunter_get_settings", tool_names)
        self.assertIn("hunter_update_settings", tool_names)

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

        result = companies.write_company_export(company["id"])
        payload = json.loads(result["path"].read_text(encoding="utf-8"))

        self.assertTrue(result["path"].name.startswith(f"company-data-{company['id']}-"))
        self.assertEqual(payload["scope"]["company_count"], 1)
        self.assertEqual(payload["companies"][0]["company"]["name"], "Example")
        self.assertEqual(payload["companies"][0]["contacts"][0]["name"], "Ada")
        self.assertEqual(payload["companies"][0]["postings"][0]["id"], "A0001")
        self.assertEqual(payload["companies"][0]["actions"][0]["id"], "T0001")
        self.assertEqual(payload["companies"][0]["career_sources"][0]["source_url"], "https://example.com/careers")
        self.assertEqual(payload["companies"][0]["posting_candidates"][0]["id"], "CP0001")
        self.assertEqual([row["id"] for row in payload["tables"]["applications"]], ["A0001"])
        self.assertEqual([row["id"] for row in payload["tables"]["actions"]], ["T0001"])


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
