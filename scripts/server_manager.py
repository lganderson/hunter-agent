#!/usr/bin/env python3
"""Manage the local Hunter app server on a fixed port."""

import argparse
import fcntl
import http.client
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORTS))

from hunter import paths  # noqa: E402


PID_FILE = paths.DATA_DIR / "hunter-server.pid"
LOG_FILE = paths.DATA_DIR / "hunter-server.log"
PORT_FILE = paths.DATA_DIR / "hunter-server.port"
URL_FILE = paths.DATA_DIR / "hunter-server.url"
LOCK_FILE = paths.DATA_DIR / "hunter-server.lock"
READY_TIMEOUT_SECONDS = 30
HEALTH_REQUEST_TIMEOUT_SECONDS = 1
HEALTH_POLL_INTERVAL_SECONDS = 0.1


@contextmanager
def lifecycle_lock():
    """Serialize server lifecycle state across manager processes."""
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


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


def tracked_port():
    if not PORT_FILE.exists():
        return None
    try:
        return int(PORT_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def first_available_port(start_port, limit=50):
    for port in range(start_port, start_port + limit):
        if not listening_pids(port):
            return port
    return None


def remove_server_state():
    for path in [PID_FILE, PORT_FILE, URL_FILE]:
        if path.exists():
            path.unlink()


def atomic_write_text(path, value):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def publish_server_state(pid, port, url):
    atomic_write_text(PID_FILE, str(pid))
    atomic_write_text(PORT_FILE, str(port))
    # Publish the URL last; consumers treat its presence as the ready signal.
    atomic_write_text(URL_FILE, url + "\n")


def health_is_ready(url):
    parsed = urlparse(url)
    connection = http.client.HTTPConnection(
        parsed.hostname,
        parsed.port,
        timeout=HEALTH_REQUEST_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        if response.status != 200:
            return False
        payload = json.loads(response.read().decode("utf-8"))
    except (OSError, http.client.HTTPException, UnicodeDecodeError, json.JSONDecodeError):
        return False
    finally:
        connection.close()
    return payload == {"service": "hunter", "status": "ok"}


def wait_for_health(process, url, timeout=READY_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout
    while True:
        exit_code = process.poll()
        if exit_code is not None:
            return False, f"Hunter server exited with code {exit_code}."
        if health_is_ready(url):
            return True, ""
        if time.monotonic() >= deadline:
            return False, f"Hunter server did not become ready within {timeout} seconds."
        time.sleep(HEALTH_POLL_INTERVAL_SECONDS)


def terminate_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _stop_server(port):
    candidates = []
    pid = tracked_pid()
    if pid and tracked_port() == port:
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
    current_tracked_pid = tracked_pid()
    if (
        tracked_port() == port
        and (
            current_tracked_pid in stopped
            or not current_tracked_pid
            or not is_running(current_tracked_pid)
        )
    ):
        remove_server_state()
    return stopped, refused


def stop_server(port):
    with lifecycle_lock():
        return _stop_server(port)


def build_frontend():
    return subprocess.call(["npm", "run", "build"], cwd=paths.FRONTEND_DIR)


def _start_server(port, build=True):
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
    existing_pid = tracked_pid()
    if existing_pid and is_running(existing_pid) and is_hunter_server(existing_pid):
        existing_port = tracked_port()
        print(
            "error: a managed Hunter server is already running "
            f"(pid={existing_pid}, port={existing_port or 'unknown'}). "
            "Use serve-restart or serve-ready."
        )
        return 2
    remove_server_state()
    with LOG_FILE.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            [sys.executable, str(ROOT_FOR_IMPORTS / "scripts" / "serve_app.py"), str(port)],
            cwd=ROOT_FOR_IMPORTS,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    url = f"http://127.0.0.1:{port}/"
    ready, error = wait_for_health(process, url)
    if not ready:
        terminate_process(process)
        remove_server_state()
        print(f"error: {error} Log: {LOG_FILE}")
        return 1
    try:
        publish_server_state(process.pid, port, url)
    except OSError as exc:
        terminate_process(process)
        remove_server_state()
        print(f"error: could not publish Hunter server state: {exc}. Log: {LOG_FILE}")
        return 1
    print(f"Serving Hunter at {url}")
    print(f"PID: {process.pid}")
    print(f"Log: {LOG_FILE}")
    print(f"URL file: {URL_FILE}")
    return 0


def start_server(port, build=True):
    with lifecycle_lock():
        return _start_server(port, build=build)


def _ready_server(start_port, build=True):
    port = tracked_port()
    if port is not None:
        stopped, refused = _stop_server(port)
        for pid, command in refused:
            print(f"Refused to stop non-Hunter process {pid}: {command}")
        if refused:
            return 1
        if stopped:
            print("Stopped Hunter server PIDs: " + ", ".join(str(pid) for pid in stopped))
    port = first_available_port(start_port)
    if port is None:
        print(f"error: no free port found from {start_port} to {start_port + 49}")
        return 2
    return _start_server(port, build=build)


def ready_server(start_port, build=True):
    with lifecycle_lock():
        return _ready_server(start_port, build=build)


def restart_server(port, build=True):
    with lifecycle_lock():
        stopped, refused = _stop_server(port)
        for pid, command in refused:
            print(f"Refused to stop non-Hunter process {pid}: {command}")
        if refused:
            return 1
        if stopped:
            print("Stopped Hunter server PIDs: " + ", ".join(str(pid) for pid in stopped))
        return _start_server(port, build=build)


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
    if URL_FILE.exists():
        print(f"Last Hunter URL: {URL_FILE.read_text(encoding='utf-8').strip()}")


def build_parser():
    parser = argparse.ArgumentParser(description="Manage the local Hunter app server.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ["status", "stop", "start", "restart", "ready"]:
        item = subparsers.add_parser(name)
        item.add_argument("port", nargs="?", type=int, default=8010)
        if name in {"start", "restart", "ready"}:
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
        return restart_server(args.port, build=not args.no_build)
    if args.command == "ready":
        return ready_server(args.port, build=not args.no_build)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
