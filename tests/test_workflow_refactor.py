import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import action_engine

from hunter import actions, app_state, applications, mcp_server, paths, repository, schema, sqlite_store, workflow


class HunterWorkflowTest(unittest.TestCase):
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

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        self.tempdir.cleanup()

    def test_migrates_legacy_status_to_stage_and_outcome(self):
        legacy_fields = [field if field != "outcome" else "status" for field in schema.APPLICATION_FIELDS]
        with sqlite_store.connect() as connection:
            columns = ", ".join(f'"{field}" TEXT NOT NULL DEFAULT ""' for field in legacy_fields)
            connection.execute(f"CREATE TABLE applications ({columns}, PRIMARY KEY(id))")
            row = {field: "" for field in legacy_fields}
            row.update({"id": "A0001", "company": "Example", "role": "Engineer", "status": "rejected", "stage": "waiting-response"})
            connection.execute(
                f"INSERT INTO applications ({', '.join(legacy_fields)}) VALUES ({', '.join('?' for _ in legacy_fields)})",
                [row[field] for field in legacy_fields],
            )

        sqlite_store.initialize()
        rows = repository.read_applications()

        self.assertEqual(rows[0]["stage"], "closed")
        self.assertEqual(rows[0]["outcome"], "rejected")
        with sqlite_store.connect() as connection:
            columns = sqlite_store.table_columns(connection, "applications")
        self.assertIn("outcome", columns)
        self.assertNotIn("status", columns)

    def test_workflow_seed_and_archive_action_type(self):
        sqlite_store.initialize()
        current = workflow.read_workflow()
        self.assertIn("posting-review", {stage["id"] for stage in current["stages"]})
        self.assertIn("review-fit", {item["id"] for item in current["action_types"]})

        workflow.archive_action_type("review-fit")

        with self.assertRaises(ValueError):
            workflow.validate_action_type("review-fit")
        self.assertEqual(workflow.validate_action_type("review-fit", allow_inactive=True), "review-fit")

    def test_closed_posting_requires_structured_outcome(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "stage": "posting-review"}),
        ])

        with self.assertRaises(ValueError):
            applications.update_application("A0001", {"stage": "closed"})

        updated = applications.update_application("A0001", {"stage": "closed", "outcome": "withdrawn"})
        self.assertEqual(updated["stage"], "closed")
        self.assertEqual(updated["outcome"], "withdrawn")

    def test_manual_application_creation_assigns_id_and_validates_required_fields(self):
        sqlite_store.initialize()
        repository.write_applications([application_row({"id": "A0007"})])

        created = applications.create_application({
            "company": "Acme",
            "role": "Product Manager",
            "location": "Chicago",
            "tags": "Warm Lead, remote",
        })

        self.assertEqual(created["id"], "A0008")
        self.assertEqual(created["stage"], schema.DEFAULT_STAGE)
        self.assertEqual(created["tags"], "warm-lead,remote")
        with self.assertRaisesRegex(ValueError, "Role is required"):
            applications.create_application({"company": "Acme"})

    def test_manual_action_creation_syncs_posting_next_action(self):
        sqlite_store.initialize()
        repository.write_applications([application_row({"id": "A0001"})])

        created = actions.create_action("A0001", {
            "title": "Email recruiter",
            "type": "follow-up",
            "priority": "high",
            "due_date": "2026-07-20",
        })

        self.assertEqual(created["id"], "T0001")
        self.assertEqual(created["source"], "manual")
        posting = repository.read_applications()[0]
        self.assertEqual(posting["next_action_id"], "T0001")
        self.assertEqual(posting["next_action"], "Email recruiter")

    def test_mcp_create_action_returns_action_and_synced_posting(self):
        sqlite_store.initialize()
        repository.write_applications([application_row({"id": "A0001"})])

        result = mcp_server.call_named_tool(
            "hunter_create_action",
            {
                "application_id": "A0001",
                "values": {
                    "title": "Prepare portfolio examples",
                    "type": "review-fit",
                    "priority": "high",
                    "due_date": "2026-07-22",
                },
            },
        )
        payload = json.loads(result["content"][0]["text"])

        self.assertEqual(payload["action"]["title"], "Prepare portfolio examples")
        self.assertEqual(payload["posting"]["next_action"], "Prepare portfolio examples")
        self.assertIn("hunter_create_action", mcp_server.TOOLS)

    def test_mcp_get_posting_returns_readable_snapshot_without_raw_html(self):
        sqlite_store.initialize()
        repository.write_applications([application_row({"id": "A0001"})])
        repository.write_posting_snapshot("A0001", {
            "source_url": "https://example.com/jobs/engineer",
            "final_url": "https://example.com/jobs/engineer",
            "captured_at": "2026-07-21T12:00:00",
            "http_status": "200",
            "content_text": "Engineer\nBuild durable systems.",
            "source_html": "<main><h1>Engineer</h1><p>Build durable systems.</p></main>",
        })

        result = mcp_server.call_named_tool("hunter_get_posting", {"id": "A0001"})
        payload = json.loads(result["content"][0]["text"])

        self.assertEqual(payload["posting_snapshots"][0]["content_text"], "Engineer\nBuild durable systems.")
        self.assertGreater(payload["posting_snapshots"][0]["source_html_char_count"], 0)
        self.assertNotIn("source_html", payload["posting_snapshots"][0])

    def test_application_company_update_syncs_related_actions(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "company": "", "role": "Engineer"}),
        ])
        repository.write_actions([
            action_row({"id": "T0001", "application_id": "A0001", "company": "", "role": "Engineer"}),
        ])

        updated = applications.update_application("A0001", {"company": "Apple"})

        self.assertEqual(updated["company"], "Apple")
        action = repository.read_actions()[0]
        self.assertEqual(action["company"], "Apple")

    def test_mcp_update_application_exposes_company_field(self):
        input_schema = mcp_server.TOOLS["hunter_update_application"]["inputSchema"]
        update_properties = input_schema["properties"]["updates"]["properties"]

        self.assertTrue({
            "company", "role", "location", "work_mode", "source", "source_url",
            "compensation", "date_found",
        }.issubset(update_properties))

    def test_mcp_update_application_can_set_company(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "company": ""}),
        ])
        repository.write_actions([
            action_row({"id": "T0001", "application_id": "A0001", "company": ""}),
        ])

        result = mcp_server.tool_update_application({"id": "A0001", "updates": {"company": "Apple"}})
        payload = result["content"][0]["text"]

        self.assertIn('"company": "Apple"', payload)
        self.assertEqual(repository.read_applications()[0]["company"], "Apple")
        self.assertEqual(repository.read_actions()[0]["company"], "Apple")

    def test_action_type_validation_uses_catalog(self):
        sqlite_store.initialize()
        rows = []
        with self.assertRaises(ValueError):
            actions.upsert_action(rows, action_row({"type": "not-real"}))

        created, row = actions.upsert_action(rows, action_row({"type": "review-posting"}))
        self.assertTrue(created)
        self.assertEqual(row["type"], "review-fit")

    def test_generated_actions_skip_archived_action_types(self):
        sqlite_store.initialize()
        workflow.archive_action_type("verify-source")
        app = application_row({"id": "A0001", "notes": "Requires browser verification."})
        repository.write_applications([app])

        created, warning = action_engine.create_actions_for_application(
            app,
            warnings=["Browser verification is recommended."],
        )

        self.assertEqual(warning, "")
        self.assertEqual([row["type"] for row in created], ["review-fit"])
        self.assertEqual([row["type"] for row in repository.read_actions()], ["review-fit"])

    def test_action_completion_syncs_posting_next_action(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "stage": "posting-review", "next_action": "Old", "next_action_date": "2026-07-01"}),
        ])
        repository.write_actions([
            action_row({"id": "T0001", "application_id": "A0001", "title": "First", "due_date": "2026-07-01", "priority": "medium"}),
            action_row({"id": "T0002", "application_id": "A0001", "title": "Second", "due_date": "2026-07-02", "priority": "high"}),
        ])

        actions.update_action_status("T0001", "done")
        app = repository.read_applications()[0]
        self.assertEqual(app["next_action_id"], "T0002")
        self.assertEqual(app["next_action"], "Second")
        self.assertEqual(app["next_action_date"], "2026-07-02")

        actions.update_action_status("T0002", "completed")
        app = repository.read_applications()[0]
        self.assertEqual(app["next_action_id"], "")
        self.assertEqual(app["next_action"], "")
        self.assertEqual(app["next_action_date"], "")

        actions.update_action_status("T0001", "open")
        app = repository.read_applications()[0]
        self.assertEqual(app["next_action_id"], "T0001")
        self.assertEqual(app["next_action"], "First")
        self.assertEqual(app["next_action_date"], "2026-07-01")

    def test_app_state_derives_next_action_from_actions(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({
                "id": "A0001",
                "stage": "posting-review",
                "next_action": "Stale",
                "next_action_date": "2026-07-01",
            }),
        ])
        repository.write_actions([
            action_row({"id": "T0001", "application_id": "A0001", "title": "Done", "status": "done", "due_date": "2026-07-01"}),
        ])

        app = app_state.read_applications()[0]

        self.assertEqual(app["next_action_id"], "")
        self.assertEqual(app["next_action"], "")
        self.assertEqual(app["next_action_date"], "")

    def test_can_make_specific_open_action_next(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "stage": "posting-review"}),
        ])
        repository.write_actions([
            action_row({"id": "T0001", "application_id": "A0001", "title": "First", "due_date": "2026-07-01"}),
            action_row({"id": "T0002", "application_id": "A0001", "title": "Chosen", "due_date": "2026-07-10"}),
        ])

        app = actions.make_next_action("T0002")

        self.assertEqual(app["next_action_id"], "T0002")
        self.assertEqual(app["next_action"], "Chosen")
        self.assertEqual(app["next_action_date"], "2026-07-10")

        actions.update_action_status("T0002", "done")
        app = repository.read_applications()[0]
        self.assertEqual(app["next_action_id"], "T0001")
        self.assertEqual(app["next_action"], "First")

    def test_action_due_date_update_syncs_posting_summary(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "stage": "posting-review"}),
        ])
        repository.write_actions([
            action_row({"id": "T0001", "application_id": "A0001", "title": "Review", "due_date": "2026-07-01"}),
        ])

        actions.update_action_fields("T0001", {"due_date": "2026-07-05"})

        app = repository.read_applications()[0]
        self.assertEqual(app["next_action_id"], "T0001")
        self.assertEqual(app["next_action"], "Review")
        self.assertEqual(app["next_action_date"], "2026-07-05")

    def test_mcp_list_postings_uses_derived_next_action(self):
        sqlite_store.initialize()
        repository.write_applications([
            application_row({"id": "A0001", "stage": "posting-review", "next_action": "Stale", "next_action_date": "2026-07-01"}),
        ])
        repository.write_actions([
            action_row({"id": "T0001", "application_id": "A0001", "title": "Done", "status": "done", "due_date": "2026-07-01"}),
        ])

        result = mcp_server.tool_list_postings({})
        payload = result["content"][0]["text"]

        self.assertIn('"next_action": ""', payload)
        self.assertIn('"next_action_date": ""', payload)


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


if __name__ == "__main__":
    unittest.main()
