"""Filesystem paths for the local Hunter workspace."""

import os
from pathlib import Path


ROOT = Path(os.environ.get("HUNTER_ROOT") or os.environ.get("JOB_HUNT_ROOT") or Path(__file__).resolve().parents[1]).resolve()
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "app"
FRONTEND_DIST = FRONTEND_DIR / "dist"
OUTPUT_FILE = FRONTEND_DIST / "index.html"
SETTINGS_FILE = DATA_DIR / "settings.local.json"
SQLITE_DB = DATA_DIR / "hunter.sqlite"

APPLICATIONS = DATA_DIR / "applications.csv"
CONTACTS = DATA_DIR / "contacts.csv"
INTERVIEWS = DATA_DIR / "interviews.csv"
ACTIONS = DATA_DIR / "actions.csv"

WORKSPACE_DIRS = [
    "data",
    "exports",
    "templates",
]
