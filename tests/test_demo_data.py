import tempfile
import unittest
from pathlib import Path

from hunter import app_state, demo_data, paths, repository, sqlite_store


class HunterDemoDataTest(unittest.TestCase):
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

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(paths, name, value)
        self.tempdir.cleanup()

    def test_load_demo_data_seeds_local_sqlite(self):
        counts = demo_data.load_demo_data()

        self.assertEqual(counts["applications"], 8)
        self.assertEqual(counts["actions"], 15)
        self.assertEqual(counts["companies"], 33)
        self.assertEqual(counts["posting_notes"], 8)
        self.assertEqual(repository.read_applications()[0]["id"], "A1001")
        self.assertEqual(repository.read_posting_note("A1001")["path"], "demo/posting-notes/A1001.md")
        self.assertIn("Anthropic", {company["name"] for company in repository.read_companies()})
        self.assertIn("OpenAI", {company["name"] for company in repository.read_companies()})

        state = app_state.build_payload()
        self.assertEqual(len(state["applications"]), 8)
        self.assertTrue(any(action["status"] == "done" for action in state["actions"]))

    def test_load_demo_data_requires_overwrite_when_data_exists(self):
        sqlite_store.initialize()
        demo_data.load_demo_data()

        with self.assertRaises(ValueError):
            demo_data.load_demo_data()

        counts = demo_data.load_demo_data(overwrite=True)
        self.assertEqual(counts["contacts"], 4)


if __name__ == "__main__":
    unittest.main()
