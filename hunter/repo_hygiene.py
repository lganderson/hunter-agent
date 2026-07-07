"""Repo hygiene helpers for keeping private local Hunter data out of commits."""

import shutil
import subprocess
from pathlib import Path

from . import paths


PRIVATE_TRACKED_PATTERNS = [
    "data/*",
    "exports/*",
    "app/dist/*",
    "app/node_modules/*",
    ".env",
    ".env.*",
]

CACHE_PATTERNS = [
    "__pycache__",
    ".DS_Store",
]


def remove_caches():
    removed = []
    for cache_dir in paths.ROOT.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir)
            removed.append(cache_dir)
    for ds_store in paths.ROOT.rglob(".DS_Store"):
        if ds_store.is_file():
            ds_store.unlink()
            removed.append(ds_store)
    return removed


def local_private_or_generated_paths():
    found = []
    local_dirs = [
        paths.DATA_DIR,
        paths.ROOT / "exports",
        paths.FRONTEND_DIST,
        paths.FRONTEND_DIR / "node_modules",
    ]
    for directory in local_dirs:
        if not directory.exists():
            continue
        if directory.name in {"dist", "node_modules"}:
            found.append(directory)
            continue
        found.extend(path for path in directory.iterdir() if path.name != ".gitkeep")

    found.extend(paths.ROOT.glob(".env*"))
    return sorted(set(found))


def tracked_private_or_generated_files():
    try:
        result = subprocess.run(
            ["git", "ls-files", *PRIVATE_TRACKED_PATTERNS],
            cwd=paths.ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode not in {0, 128}:
        return []
    return [
        paths.ROOT / line
        for line in result.stdout.splitlines()
        if line.strip() and Path(line).name != ".gitkeep"
    ]


def git_available():
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=paths.ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def format_path(path):
    try:
        return str(path.relative_to(paths.ROOT))
    except ValueError:
        return str(path)


def repo_check():
    private_files = local_private_or_generated_paths()
    tracked_files = tracked_private_or_generated_files()
    inside_git = git_available()
    return {
        "inside_git": inside_git,
        "private_files": private_files,
        "tracked_private_files": tracked_files,
        "cache_paths": [
            path
            for pattern in CACHE_PATTERNS
            for path in paths.ROOT.rglob(pattern)
            if path.exists()
        ],
    }
