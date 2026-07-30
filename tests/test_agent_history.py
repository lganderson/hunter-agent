import tempfile
import unittest
from pathlib import Path

from hunter import app_state, chat_history, paths, sqlite_store, suggestions


class HunterAgentHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.original_paths = {
            name: getattr(paths, name)
            for name in ["ROOT", "DATA_DIR", "SQLITE_DB"]
        }
        paths.ROOT = self.root
        paths.DATA_DIR = self.root / "data"
        paths.SQLITE_DB = paths.DATA_DIR / "hunter.sqlite"

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        self.tempdir.cleanup()

    def test_record_exchange_roundtrips_content_context_and_receipts(self):
        result = chat_history.record_exchange(
            "Review this role",
            "## Fit\nStrong fit.",
            tool_calls=[{"name": "hunter_get_application", "ok": True, "receipt": "Reviewed A0001."}],
            context={"route": "posting-detail", "entity_id": "A0001"},
        )

        messages = chat_history.list_messages()

        self.assertLess(result["user_id"], result["assistant_id"])
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["content"], "## Fit\nStrong fit.")
        self.assertEqual(messages[1]["tool_calls"][0]["receipt"], "Reviewed A0001.")
        self.assertEqual(messages[0]["context"]["entity_id"], "A0001")

    def test_history_limit_returns_latest_messages_in_conversation_order(self):
        chat_history.record_exchange("First", "First answer")
        chat_history.record_exchange("Second", "Second answer")

        messages = chat_history.list_messages(limit=2)

        self.assertEqual([message["content"] for message in messages], ["Second", "Second answer"])

    def test_clear_messages_removes_the_saved_conversation(self):
        chat_history.record_exchange("Question", "Answer")

        result = chat_history.clear_messages()

        self.assertEqual(result["cleared"], 2)
        self.assertEqual(chat_history.list_messages(), [])

    def test_initialize_creates_runtime_tables_and_schema_version_sixteen(self):
        sqlite_store.initialize()

        with sqlite_store.connect() as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_messages'"
            ).fetchone()
            snapshot_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='posting_snapshots'"
            ).fetchone()
            resume_versions_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='resume_versions'"
            ).fetchone()
            suggestion_dismissals_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='suggestion_dismissals'"
            ).fetchone()
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            snapshot_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(posting_snapshots)").fetchall()
            }

        self.assertEqual(table["name"], "agent_messages")
        self.assertEqual(snapshot_table["name"], "posting_snapshots")
        self.assertEqual(resume_versions_table["name"], "resume_versions")
        self.assertEqual(suggestion_dismissals_table["name"], "suggestion_dismissals")
        self.assertEqual(version["value"], "16")
        self.assertIn("capture_method", snapshot_columns)
        self.assertIn("capture_model", snapshot_columns)
        self.assertIn("sources_json", snapshot_columns)
        self.assertEqual(chat_history.API_VERSION, 2)

    def test_suggestion_dismissal_persists_in_app_state_and_can_be_restored(self):
        dismissal = suggestions.dismiss("company-decision:CO0001")

        self.assertTrue(dismissal["dismissed_at"])
        self.assertEqual(
            app_state.build_payload()["dismissed_suggestion_ids"],
            ["company-decision:CO0001"],
        )

        restoration = suggestions.restore("company-decision:CO0001")

        self.assertTrue(restoration["restored"])
        self.assertEqual(app_state.build_payload()["dismissed_suggestion_ids"], [])

    def test_initialize_marks_existing_posting_snapshots_as_fetched(self):
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite_store.connect() as connection:
            connection.execute(
                "CREATE TABLE posting_snapshots ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "application_id TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '', "
                "final_url TEXT NOT NULL DEFAULT '', captured_at TEXT NOT NULL, "
                "http_status TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL, "
                "content_text TEXT NOT NULL DEFAULT '', source_html TEXT NOT NULL DEFAULT '', "
                "warnings TEXT NOT NULL DEFAULT '', UNIQUE(application_id, content_hash))"
            )
            connection.execute(
                "INSERT INTO posting_snapshots(application_id, captured_at, http_status, content_hash, content_text, source_html) "
                "VALUES ('A0001', '2026-07-21T12:00:00', '200', 'legacy', 'Role', '<h1>Role</h1>')"
            )

        sqlite_store.initialize()

        snapshot = sqlite_store.read_posting_snapshots("A0001")[0]
        self.assertEqual(snapshot["capture_method"], "fetch")


if __name__ == "__main__":
    unittest.main()
