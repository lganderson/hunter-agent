#!/usr/bin/env python3
"""Manage the local Hunter app server on a fixed port."""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORTS))

from hunter import paths  # noqa: E402


PID_FILE = paths.DATA_DIR / "hunter-server.pid"
LOG_FILE = paths.DATA_DIR / "hunter-server.log"


def command_for_pid(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False)
    return result.stdout.strip()


def is_running(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def is_hunter_server(pid):
    command = command_for_pid(pid)
    return "hunter.py serve" in command or "scripts/serve_app.py" in command


def listening_pids(port):
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def tracked_pid():
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def stop_server(port):
    candidates = []
    pid = tracked_pid()
    if pid:
        candidates.append(pid)
    candidates.extend(listening_pids(port))
    stopped = []
    refused = []
    for candidate in sorted(set(candidates)):
        if not is_running(candidate):
            continue
        if not is_hunter_server(candidate):
            refused.append((candidate, command_for_pid(candidate)))
            continue
        os.kill(candidate, signal.SIGTERM)
        for _ in range(30):
            if not is_running(candidate):
                break
            time.sleep(0.1)
        if is_running(candidate):
            os.kill(candidate, signal.SIGKILL)
        stopped.append(candidate)
    if PID_FILE.exists() and (tracked_pid() in stopped or not tracked_pid() or not is_running(tracked_pid())):
        PID_FILE.unlink()
    return stopped, refused


def build_frontend():
    return subprocess.call(["npm", "run", "build"], cwd=paths.FRONTEND_DIR)


def start_server(port, build=True):
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if build:
        build_status = build_frontend()
        if build_status:
            return build_status
    blockers = [(pid, command_for_pid(pid)) for pid in listening_pids(port) if is_running(pid)]
    if blockers:
        print(f"error: port {port} is already in use. Run: python3 hunter.py serve-stop {port}")
        for pid, command in blockers:
            print(f"  {pid}: {command}")
        return 2
    log_handle = LOG_FILE.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(paths.ROOT / "hunter.py"), "serve", str(port)],
        cwd=paths.ROOT,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    time.sleep(0.5)
    if process.poll() is not None:
        print(f"error: Hunter server exited with code {process.returncode}. Log: {LOG_FILE}")
        return process.returncode or 1
    print(f"Serving Hunter at http://127.0.0.1:{port}/")
    print(f"PID: {process.pid}")
    print(f"Log: {LOG_FILE}")
    return 0


def print_status(port):
    pid = tracked_pid()
    listeners = listening_pids(port)
    if pid and is_running(pid):
        print(f"Tracked Hunter server: pid={pid} command={command_for_pid(pid)}")
    else:
        print("Tracked Hunter server: none")
    if listeners:
        print(f"Listening on port {port}:")
        for listener in listeners:
            marker = "hunter" if is_hunter_server(listener) else "other"
            print(f"  {listener} [{marker}] {command_for_pid(listener)}")
    else:
        print(f"Listening on port {port}: none")


def build_parser():
    parser = argparse.ArgumentParser(description="Manage the local Hunter app server.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ["status", "stop", "start", "restart"]:
        item = subparsers.add_parser(name)
        item.add_argument("port", nargs="?", type=int, default=8010)
        if name in {"start", "restart"}:
            item.add_argument("--no-build", action="store_true")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "status":
        print_status(args.port)
        return 0
    if args.command == "stop":
        stopped, refused = stop_server(args.port)
        for pid, command in refused:
            print(f"Refused to stop non-Hunter process {pid}: {command}")
        print("Stopped Hunter server PIDs: " + (", ".join(str(pid) for pid in stopped) if stopped else "none"))
        return 1 if refused else 0
    if args.command == "start":
        return start_server(args.port, build=not args.no_build)
    if args.command == "restart":
        stopped, refused = stop_server(args.port)
        for pid, command in refused:
            print(f"Refused to stop non-Hunter process {pid}: {command}")
        if refused:
            return 1
        if stopped:
            print("Stopped Hunter server PIDs: " + ", ".join(str(pid) for pid in stopped))
        return start_server(args.port, build=not args.no_build)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
