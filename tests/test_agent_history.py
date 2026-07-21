import tempfile
import unittest
from pathlib import Path

from hunter import chat_history, paths, sqlite_store


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

    def test_initialize_creates_agent_messages_and_posting_snapshots_schema_version_six(self):
        sqlite_store.initialize()

        with sqlite_store.connect() as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_messages'"
            ).fetchone()
            snapshot_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='posting_snapshots'"
            ).fetchone()
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()

        self.assertEqual(table["name"], "agent_messages")
        self.assertEqual(snapshot_table["name"], "posting_snapshots")
        self.assertEqual(version["value"], "6")
        self.assertEqual(chat_history.API_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
