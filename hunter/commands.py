"""Single zero-dependency command entrypoint for local Hunter tasks."""

import subprocess
import sys

from . import demo_data, paths, repo_hygiene, sqlite_store


def run_script(name, args):
    return subprocess.call([sys.executable, str(paths.ROOT / "scripts" / name), *args])


def print_help():
    print(
        "Usage: python3 hunter.py <command> [args]\n\n"
        "Commands:\n"
        "  init, list, due, stats, add, update, make-note\n"
        "  actions, companies, ingest, serve, serve-status, serve-stop, serve-restart, serve-ready, mcp\n"
        "  migrate-to-sqlite, migrate-postings, export-csv, load-demo-data\n\n"
        "  repo-check, clean-caches\n\n"
        "Examples:\n"
        "  python3 hunter.py serve 8010\n"
        "  python3 hunter.py serve-restart 8010\n"
        "  python3 hunter.py serve-ready 8011\n"
        "  python3 hunter.py mcp\n"
        "  python3 hunter.py repo-check\n"
        "  python3 hunter.py clean-caches\n"
        "  python3 hunter.py companies list\n"
        "  python3 hunter.py companies export\n"
        "  python3 hunter.py migrate-to-sqlite\n"
        "  python3 hunter.py migrate-postings\n"
        "  python3 hunter.py export-csv\n"
        "  python3 hunter.py load-demo-data --overwrite\n"
        "  python3 hunter.py list --limit 5\n"
        "  python3 hunter.py ingest --dry-run https://example.com/job\n"
    )


def print_counts(label, counts):
    parts = ", ".join(f"{table}={count}" for table, count in counts.items())
    print(f"{label}: {parts}")


def print_path_list(label, paths_to_print):
    print(label)
    if not paths_to_print:
        print("  none")
        return
    for path in paths_to_print:
        print(f"  {repo_hygiene.format_path(path)}")


def initialize_local_app():
    sqlite_store.initialize()
    print(f"Initialized Hunter at {paths.ROOT}")
    print(f"Local database: {paths.SQLITE_DB}")
    print(f"Frontend build output: {paths.OUTPUT_FILE}")


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print_help()
        raise SystemExit(0)

    command, passthrough = argv[0], argv[1:]
    tracker_commands = {"list", "due", "stats", "add", "update", "make-note"}
    script_commands = {
        "actions": ("action_engine.py", []),
        "companies": ("companies.py", []),
        "ingest": ("ingest_postings.py", []),
        "serve": ("serve_app.py", []),
        "serve-status": ("server_manager.py", ["status"]),
        "serve-stop": ("server_manager.py", ["stop"]),
        "serve-restart": ("server_manager.py", ["restart"]),
        "serve-ready": ("server_manager.py", ["ready"]),
    }

    if command == "init":
        initialize_local_app()
        raise SystemExit(0)
    if command == "mcp":
        from . import mcp_server

        mcp_server.main()
        raise SystemExit(0)
    if command in tracker_commands:
        raise SystemExit(run_script("tracker.py", [command, *passthrough]))
    if command in script_commands:
        script, prefix = script_commands[command]
        raise SystemExit(run_script(script, [*prefix, *passthrough]))
    if command == "migrate-to-sqlite":
        overwrite = "--overwrite" in passthrough
        try:
            counts = sqlite_store.import_from_csv(overwrite=overwrite)
        except ValueError as exc:
            print(f"error: {exc}")
            raise SystemExit(2) from exc
        print_counts(f"Migrated CSV data into {paths.SQLITE_DB}", counts)
        raise SystemExit(0)
    if command == "migrate-postings":
        overwrite = "--overwrite" in passthrough
        counts = sqlite_store.import_posting_notes_from_files(overwrite=overwrite)
        print_counts("Migrated posting Markdown into SQLite", counts)
        raise SystemExit(0)
    if command == "load-demo-data":
        overwrite = "--overwrite" in passthrough
        try:
            counts = demo_data.load_demo_data(overwrite=overwrite)
        except ValueError as exc:
            print(f"error: {exc}")
            raise SystemExit(2) from exc
        demo_data.print_counts(counts)
        raise SystemExit(0)
    if command == "export-csv":
        if not sqlite_store.is_initialized():
            print(f"error: SQLite database has not been initialized at {paths.SQLITE_DB}")
            raise SystemExit(2)
        counts = sqlite_store.export_to_csv()
        print_counts("Exported SQLite data back to CSV", counts)
        raise SystemExit(0)
    if command == "repo-check":
        report = repo_hygiene.repo_check()
        print(f"Git repo initialized: {'yes' if report['inside_git'] else 'no'}")
        print_path_list("Local private/generated files ignored by default:", report["private_files"])
        tracked_files = report["tracked_private_files"]
        if tracked_files is None:
            print("Tracked private/generated files: git is not installed")
        else:
            print_path_list("Tracked private/generated files:", tracked_files)
        print_path_list("Cache files/directories that clean-caches can remove:", report["cache_paths"])
        if tracked_files:
            raise SystemExit(1)
        raise SystemExit(0)
    if command == "clean-caches":
        removed = repo_hygiene.remove_caches()
        print_path_list("Removed cache files/directories:", removed)
        raise SystemExit(0)

    print(f"Unknown command: {command}\n")
    print_help()
    raise SystemExit(2)
