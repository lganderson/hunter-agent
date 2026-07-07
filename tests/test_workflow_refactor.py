import tempfile
import unittest
from pathlib import Path

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

        self.assertIn("company", update_properties)

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
