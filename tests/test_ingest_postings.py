import json
import sys
import tempfile
import unittest
from pathlib import Path

from hunter import companies, paths, posting_snapshots, repository, sqlite_store


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ingest_postings  # noqa: E402


class IngestPostingsTest(unittest.TestCase):
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
                "SETTINGS_FILE",
                "SQLITE_DB",
                "APPLICATIONS",
                "CONTACTS",
                "INTERVIEWS",
                "ACTIONS",
            ]
        }
        self.original_tracker_paths = {
            name: getattr(ingest_postings.tracker, name)
            for name in [
                "ROOT",
                "DATA_DIR",
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
        paths.SETTINGS_FILE = paths.DATA_DIR / "settings.local.json"
        paths.SQLITE_DB = paths.DATA_DIR / "hunter.sqlite"
        paths.APPLICATIONS = paths.DATA_DIR / "applications.csv"
        paths.CONTACTS = paths.DATA_DIR / "contacts.csv"
        paths.INTERVIEWS = paths.DATA_DIR / "interviews.csv"
        paths.ACTIONS = paths.DATA_DIR / "actions.csv"
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        ingest_postings.tracker.ROOT = paths.ROOT
        ingest_postings.tracker.DATA_DIR = paths.DATA_DIR
        ingest_postings.tracker.APPLICATIONS = paths.APPLICATIONS
        ingest_postings.tracker.CONTACTS = paths.CONTACTS
        ingest_postings.tracker.INTERVIEWS = paths.INTERVIEWS
        ingest_postings.tracker.ACTIONS = paths.ACTIONS
        (paths.ROOT / "templates").mkdir(parents=True, exist_ok=True)
        (paths.ROOT / "templates" / "job-posting.md").write_text(
            "# {{company}}\n\n{{role}}\n",
            encoding="utf-8",
        )

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        for name, value in self.original_tracker_paths.items():
            setattr(ingest_postings.tracker, name, value)
        self.tempdir.cleanup()

    def test_apple_careers_url_infers_company_and_clean_role(self):
        company, role = ingest_postings.infer_company_role(
            "https://jobs.apple.com/en-us/details/200660532-3956/aiml-technical-program-manager?team=CORSV",
            "AIML Technical Program Manager - Jobs - Careers at Apple",
            {},
            {},
        )

        self.assertEqual(company, "Apple")
        self.assertEqual(role, "AIML Technical Program Manager")

    def test_apply_fields_fills_blank_existing_company_without_overwrite(self):
        row = {"company": "", "role": "AIML Technical Program Manager"}

        ingest_postings.apply_fields(
            row,
            {
                "company": "Apple",
                "role": "AIML Technical Program Manager",
                "location": "",
                "work_mode": "",
                "source": "",
                "source_url": "",
                "compensation": "",
                "priority": "",
                "notes": "",
            },
            overwrite=False,
        )

        self.assertEqual(row["company"], "Apple")

    def test_ingest_associates_existing_company_by_exact_name(self):
        sqlite_store.initialize()
        company = companies.upsert_company("", {"name": "Apple"})
        args = ingest_postings.build_parser().parse_args([
            "--company",
            "Apple",
            "--role",
            "AIML Technical Program Manager",
            "https://jobs.apple.com/en-us/details/200660532-3956/aiml-technical-program-manager?team=CORSV",
        ])
        original_fetch = ingest_postings.fetch
        ingest_postings.fetch = lambda url: {
            "status": 200,
            "final_url": url,
            "html": "<html><title>AIML Technical Program Manager - Jobs - Careers at Apple</title><body>Apply now</body></html>",
            "error": "",
        }
        try:
            created, row, _data = ingest_postings.upsert(args.urls[0], args)
        finally:
            ingest_postings.fetch = original_fetch

        app = repository.read_applications()[0]
        self.assertTrue(created)
        self.assertEqual(row["company_id"], company["id"])
        self.assertEqual(app["company_id"], company["id"])
        self.assertEqual(app["company"], "Apple")

    def test_ingest_creates_company_when_none_exists(self):
        sqlite_store.initialize()
        args = ingest_postings.build_parser().parse_args([
            "--company",
            "NewCo",
            "--role",
            "Technical Program Manager",
            "https://example.com/jobs/technical-program-manager",
        ])
        original_fetch = ingest_postings.fetch
        ingest_postings.fetch = lambda url: {
            "status": 200,
            "final_url": url,
            "html": "<html><title>Technical Program Manager</title><body>Apply now</body></html>",
            "error": "",
        }
        try:
            created, row, _data = ingest_postings.upsert(args.urls[0], args)
        finally:
            ingest_postings.fetch = original_fetch

        company = repository.read_companies()[0]
        app = repository.read_applications()[0]
        self.assertTrue(created)
        self.assertEqual(company["name"], "NewCo")
        self.assertEqual(company["interest_status"], "neutral")
        self.assertEqual(row["company_id"], company["id"])
        self.assertEqual(app["company_id"], company["id"])

    def test_ingest_does_not_add_review_needed_tag_by_default(self):
        sqlite_store.initialize()
        args = ingest_postings.build_parser().parse_args([
            "--company",
            "Example",
            "--role",
            "Technical Program Manager",
            "https://example.com/jobs/technical-program-manager",
        ])
        original_fetch = ingest_postings.fetch
        ingest_postings.fetch = lambda url: {
            "status": 200,
            "final_url": url,
            "html": "<html><title>Technical Program Manager</title><body>Apply now</body></html>",
            "error": "",
        }
        try:
            created, row, _data = ingest_postings.upsert(args.urls[0], args)
        finally:
            ingest_postings.fetch = original_fetch

        app = repository.read_applications()[0]
        self.assertTrue(created)
        self.assertEqual(row["tags"], "")
        self.assertEqual(app["tags"], "")

    def test_ingest_archives_full_posting_source_and_readable_text(self):
        sqlite_store.initialize()
        args = ingest_postings.build_parser().parse_args([
            "--company",
            "Example",
            "--role",
            "Platform Product Manager",
            "https://example.com/jobs/platform-product-manager",
        ])
        page_html = (
            "<html><head><title>Platform Product Manager</title></head><body><main>"
            "<h1>Platform Product Manager</h1>"
            "<p>Own the complete platform roadmap.</p>"
            "<h2>Requirements</h2><ul><li>Lead cross-functional teams.</li></ul>"
            "</main></body></html>"
        )
        original_fetch = ingest_postings.fetch
        ingest_postings.fetch = lambda url: {
            "status": 200,
            "final_url": f"{url}?canonical=1",
            "html": page_html,
            "error": "",
        }
        try:
            _created, row, data = ingest_postings.upsert(args.urls[0], args)
            ingest_postings.upsert(args.urls[0], args)
        finally:
            ingest_postings.fetch = original_fetch

        snapshots = repository.read_posting_snapshots(row["id"])
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(data["posting_snapshot_id"], snapshots[0]["id"])
        self.assertEqual(snapshots[0]["source_html"], page_html)
        self.assertEqual(snapshots[0]["http_status"], "200")
        self.assertEqual(snapshots[0]["capture_method"], "fetch")
        self.assertIn("Own the complete platform roadmap.", snapshots[0]["content_text"])
        self.assertIn("Lead cross-functional teams.", snapshots[0]["content_text"])
        self.assertTrue(snapshots[0]["content_hash"])

    def test_ingest_recovers_blocked_epic_posting_from_greenhouse(self):
        sqlite_store.initialize()
        url = "https://epicgames.com/careers/jobs/5674511004?gh_jid=5674511004"
        args = ingest_postings.build_parser().parse_args([
            "--company",
            "Epic Games",
            "--role",
            "Product Management Director (Platform)",
            url,
        ])
        calls = []
        greenhouse_payload = {
            "id": 5674511004,
            "title": "Product Management Director (Platform)",
            "absolute_url": url,
            "location": {"name": "Multiple Locations"},
            "content": (
                "<h2>What you'll do</h2><p>Own platform product strategy and roadmap.</p>"
                "<h2>What we're looking for</h2><p>Lead cross-functional teams.</p>"
            ),
        }

        def fake_fetch(request_url):
            calls.append(request_url)
            if request_url == url:
                return {
                    "status": 403,
                    "final_url": request_url,
                    "html": "<html><body>Enable JavaScript and cookies to continue</body></html>",
                    "error": "HTTP Error 403: Forbidden",
                }
            return {
                "status": 200,
                "final_url": request_url,
                "html": json.dumps(greenhouse_payload),
                "error": "",
            }

        original_fetch = ingest_postings.fetch
        ingest_postings.fetch = fake_fetch
        try:
            _created, row, data = ingest_postings.upsert(url, args)
        finally:
            ingest_postings.fetch = original_fetch

        snapshots = repository.read_posting_snapshots(row["id"])
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(data["location"], "Multiple Locations")
        self.assertEqual(snapshots[0]["http_status"], "200")
        self.assertEqual(snapshots[0]["final_url"], url)
        self.assertIn("## What you'll do", snapshots[0]["content_text"])
        self.assertIn("Own platform product strategy and roadmap.", snapshots[0]["content_text"])
        self.assertIn("Lead cross-functional teams.", snapshots[0]["content_text"])
        self.assertNotIn("<h2>", snapshots[0]["content_text"])
        self.assertNotIn("<p>", snapshots[0]["content_text"])
        self.assertIn('"id": 5674511004', snapshots[0]["source_html"])
        self.assertIn("captured posting through the Greenhouse Job Board API", snapshots[0]["warnings"])
        self.assertNotIn("browser verification", snapshots[0]["warnings"])
        self.assertEqual(
            calls,
            [
                url,
                "https://boards-api.greenhouse.io/v1/boards/epicgames/jobs/5674511004?content=true",
            ],
        )

    def test_archive_existing_posting_captures_snapshot_without_generating_note(self):
        sqlite_store.initialize()
        repository.write_applications([{
            "id": "A0042",
            "company": "Example",
            "role": "Platform Product Manager",
            "source_url": "https://example.com/jobs/platform-product-manager",
        }])
        original_fetch = ingest_postings.fetch
        ingest_postings.fetch = lambda url: {
            "status": 200,
            "final_url": url,
            "html": "<main><h1>Platform Product Manager</h1><p>Own the roadmap.</p></main>",
            "error": "",
        }
        try:
            first = ingest_postings.archive_application_posting("A0042")
            second = ingest_postings.archive_application_posting("A0042")
        finally:
            ingest_postings.fetch = original_fetch

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["snapshot"]["http_status"], "200")
        self.assertIn("Own the roadmap.", first["snapshot"]["content_text"])
        self.assertNotIn("source_html", first["snapshot"])
        self.assertGreater(first["snapshot"]["source_html_char_count"], 0)
        self.assertIsNone(repository.read_posting_note("A0042"))

    def test_archive_existing_posting_rejects_unreachable_demo_url(self):
        sqlite_store.initialize()
        repository.write_applications([{
            "id": "A0043",
            "company": "Example",
            "role": "Demo Product Manager",
            "source_url": "https://example.invalid/demo-job",
        }])
        original_fetch = ingest_postings.fetch
        ingest_postings.fetch = lambda url: {
            "status": 0,
            "final_url": url,
            "html": "",
            "error": "Name or service not known",
        }
        try:
            with self.assertRaisesRegex(ValueError, "demo placeholder URL cannot be archived"):
                ingest_postings.archive_application_posting("A0043")
        finally:
            ingest_postings.fetch = original_fetch

        self.assertEqual(repository.read_posting_snapshots("A0043"), [])

    def test_archive_existing_posting_recovers_with_openai_web_search(self):
        sqlite_store.initialize()
        repository.write_applications([{
            "id": "A0046",
            "company": "Example",
            "role": "Platform Product Manager",
            "source_url": "https://example.com/jobs/platform-product-manager",
        }])
        original_fetch = ingest_postings.fetch
        original_recovery = ingest_postings.action_engine.recover_posting_with_openai
        ingest_postings.fetch = lambda url: {
            "status": 403,
            "final_url": url,
            "html": "<html><body>Enable JavaScript and cookies to continue</body></html>",
            "error": "HTTP Error 403: Forbidden",
        }
        ingest_postings.action_engine.recover_posting_with_openai = lambda app: ({
            "source_url": app["source_url"],
            "final_url": app["source_url"],
            "capture_method": "ai-web",
            "capture_model": "gpt-5.5",
            "sources_json": json.dumps([{"url": app["source_url"], "title": "Platform PM"}]),
            "content_text": "# Platform Product Manager\n\nOwn the complete platform roadmap and partner across functions.",
            "source_html": json.dumps({"id": "response-test"}),
            "warnings": "Cited AI reconstruction, not raw source HTML.",
        }, "")
        try:
            result = ingest_postings.archive_application_posting("A0046")
        finally:
            ingest_postings.fetch = original_fetch
            ingest_postings.action_engine.recover_posting_with_openai = original_recovery

        snapshot = repository.read_posting_snapshots("A0046")[0]
        self.assertTrue(result["created"])
        self.assertEqual(snapshot["capture_method"], "ai-web")
        self.assertEqual(snapshot["capture_model"], "gpt-5.5")
        self.assertEqual(json.loads(snapshot["sources_json"])[0]["title"], "Platform PM")
        self.assertIn("Own the complete platform roadmap", snapshot["content_text"])
        self.assertIn("HTTP Error 403", snapshot["warnings"])
        self.assertIn("not raw source HTML", snapshot["warnings"])

    def test_manual_posting_archive_preserves_pasted_content_and_deduplicates(self):
        sqlite_store.initialize()
        repository.write_applications([{
            "id": "A0044",
            "company": "Example",
            "role": "Platform Product Manager",
            "source_url": "https://example.com/jobs/platform-product-manager",
        }])
        content = (
            "<h1>Platform Product Manager</h1>"
            "<p>Own the roadmap.</p>"
            "<h2>What you'll do</h2><ul><li>Lead cross-functional teams.</li></ul>"
        )

        first = ingest_postings.save_manual_posting_snapshot("A0044", content)
        second = ingest_postings.save_manual_posting_snapshot("A0044", content)

        snapshots = repository.read_posting_snapshots("A0044")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["capture_method"], "manual")
        self.assertEqual(snapshots[0]["http_status"], "")
        self.assertEqual(snapshots[0]["source_html"], content)
        self.assertIn("# Platform Product Manager", snapshots[0]["content_text"])
        self.assertIn("- Lead cross-functional teams.", snapshots[0]["content_text"])
        self.assertEqual(first["snapshot"]["source_html_char_count"], len(content))
        self.assertNotIn("source_html", first["snapshot"])

    def test_manual_posting_archive_rejects_blank_content(self):
        sqlite_store.initialize()
        repository.write_applications([{"id": "A0045", "company": "Example", "role": "Manager"}])

        with self.assertRaisesRegex(ValueError, "Paste the posting content"):
            ingest_postings.save_manual_posting_snapshot("A0045", "  \n  ")

        self.assertEqual(repository.read_posting_snapshots("A0045"), [])

    def test_google_careers_archive_focuses_the_job_detail(self):
        noisy_content = "\n".join([
            "Careers Careers",
            "## Jobs search results",
            "3,442 jobs matched",
            "### Staff Software Developer, Embedded Systems/Firmware",
            "Waterloo, ON, Canada",
            "## Senior Technical Program Manager, Software Engineering, Core Systems",
            "share",
            "- Copy link",
            "corporate_fare Google place San Jose, CA, USA",
            "bar_chart Advanced",
            "## Advanced",
            "Experience owning outcomes and decision making.",
            "Apply",
            "### Minimum qualifications:",
            "- 8 years of experience in program management.",
            "### Preferred qualifications:",
            "- Experience managing cross-functional projects.",
            "### About the job",
            "Lead complex, multi-disciplinary projects from start to finish.",
            "### Responsibilities",
            "- Establish a reliable cadence for program reviews.",
            "Information collected and processed as part of your Google Careers profile.",
            "## Follow Life at Google on",
        ])

        focused = posting_snapshots.readable_content(
            "https://www.google.com/about/careers/applications/jobs/results/123-senior-tpm",
            noisy_content,
        )

        self.assertTrue(focused.startswith("# Senior Technical Program Manager"))
        self.assertIn("Google · San Jose, CA, USA", focused)
        self.assertIn("**Experience level:** Advanced", focused)
        self.assertIn("## Minimum qualifications:", focused)
        self.assertIn("## Responsibilities", focused)
        self.assertNotIn("Jobs search results", focused)
        self.assertNotIn("Staff Software Developer", focused)
        self.assertNotIn("Information collected and processed", focused)
        self.assertNotIn("Follow Life at Google", focused)

    def test_repository_normalizes_existing_google_archive_content(self):
        sqlite_store.initialize()
        repository.write_posting_snapshot("A0047", {
            "source_url": "https://www.google.com/about/careers/applications/jobs/results/123-role",
            "final_url": "https://www.google.com/about/careers/applications/jobs/results/123-role",
            "http_status": "200",
            "content_text": "\n".join([
                "## Jobs search results",
                "3,442 jobs matched",
                "## Product Manager, Search",
                "### Minimum qualifications:",
                "- Product management experience.",
                "### Responsibilities",
                "- Define product strategy.",
                "Information collected and processed as part of your Google Careers profile.",
            ]),
            "source_html": "<main>stored raw Google source</main>",
        })

        snapshot = repository.read_posting_snapshots("A0047")[0]

        self.assertTrue(snapshot["content_text"].startswith("# Product Manager, Search"))
        self.assertNotIn("Jobs search results", snapshot["content_text"])
        self.assertEqual(
            sqlite_store.read_posting_snapshots("A0047")[0]["source_html"],
            "<main>stored raw Google source</main>",
        )


if __name__ == "__main__":
    unittest.main()
