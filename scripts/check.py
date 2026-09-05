#!/usr/bin/env python3
"""Run Hunter's complete local validation gate without using personal data."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def run(command, *, cwd=ROOT, env=None):
    print("Running " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main():
    with TemporaryDirectory(prefix="hunter-check-") as workspace:
        env = {**os.environ, "HUNTER_ROOT": workspace}
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], env=env)
    for script in ["test", "typecheck", "build", "test:e2e"]:
        run(["npm", "run", script], cwd=ROOT / "app")
    run(["npm", "exec", "--", "playwright", "test", "--config", "playwright.integration.config.ts"], cwd=ROOT / "app")
    run([sys.executable, "hunter.py", "repo-check"])
    run([sys.executable, "hunter.py", "clean-caches"])
    run(["git", "diff", "--check"])


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
