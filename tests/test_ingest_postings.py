import json
import sys
import tempfile
import unittest
from pathlib import Path

from hunter import companies, paths, repository, sqlite_store


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
        self.assertIn("Own platform product strategy and roadmap.", snapshots[0]["content_text"])
        self.assertIn("Lead cross-functional teams.", snapshots[0]["content_text"])
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


if __name__ == "__main__":
    unittest.main()
