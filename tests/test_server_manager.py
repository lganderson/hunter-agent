import tempfile
import threading
import unittest
import os
import socket
import subprocess
import sys
import time
import json
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import Mock, patch

from scripts import server_manager


class HunterServerManagerTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tempdir.name)
        self.path_patches = [
            patch.object(server_manager.paths, "DATA_DIR", self.data_dir),
            patch.object(server_manager, "PID_FILE", self.data_dir / "hunter-server.pid"),
            patch.object(server_manager, "LOG_FILE", self.data_dir / "hunter-server.log"),
            patch.object(server_manager, "PORT_FILE", self.data_dir / "hunter-server.port"),
            patch.object(server_manager, "URL_FILE", self.data_dir / "hunter-server.url"),
            patch.object(server_manager, "LOCK_FILE", self.data_dir / "hunter-server.lock"),
        ]
        for item in self.path_patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.path_patches):
            item.stop()
        self.tempdir.cleanup()

    def fake_process(self, pid=4242):
        process = Mock()
        process.pid = pid
        process.returncode = None
        process.poll.return_value = None
        return process

    def test_start_owns_serve_app_process_and_publishes_after_health(self):
        process = self.fake_process()

        def ready_after_state_check(*_args, **_kwargs):
            self.assertFalse(server_manager.PID_FILE.exists())
            self.assertFalse(server_manager.PORT_FILE.exists())
            self.assertFalse(server_manager.URL_FILE.exists())
            return True, ""

        with (
            patch.object(server_manager, "listening_pids", return_value=[]),
            patch.object(server_manager.subprocess, "Popen", return_value=process) as popen,
            patch.object(server_manager, "wait_for_health", side_effect=ready_after_state_check),
            patch("builtins.print"),
        ):
            result = server_manager.start_server(8123, build=False)

        self.assertEqual(result, 0)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], server_manager.sys.executable)
        self.assertEqual(Path(command[1]), server_manager.ROOT_FOR_IMPORTS / "scripts" / "serve_app.py")
        self.assertEqual(command[2], "8123")
        self.assertEqual(server_manager.PID_FILE.read_text(encoding="utf-8"), "4242")
        self.assertEqual(server_manager.PORT_FILE.read_text(encoding="utf-8"), "8123")
        self.assertEqual(server_manager.URL_FILE.read_text(encoding="utf-8"), "http://127.0.0.1:8123/\n")

    def test_lifecycle_lock_serializes_across_processes(self):
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import fcntl,sys; "
                    "handle=open(sys.argv[1], 'a+'); "
                    "fcntl.flock(handle.fileno(), fcntl.LOCK_EX); "
                    "print('locked', flush=True); input()"
                ),
                str(server_manager.LOCK_FILE),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            entered = threading.Event()
            attempting = threading.Event()

            def wait_for_lock():
                attempting.set()
                with server_manager.lifecycle_lock():
                    entered.set()

            waiter = threading.Thread(target=wait_for_lock, daemon=True)
            waiter.start()
            self.assertTrue(attempting.wait(timeout=2))
            self.assertFalse(entered.wait(timeout=0.1))
            holder.stdin.write("\n")
            holder.stdin.flush()
            holder.wait(timeout=2)
            self.assertTrue(entered.wait(timeout=2))
            waiter.join(timeout=2)
        finally:
            if holder.poll() is None:
                holder.terminate()
                holder.wait(timeout=2)
            holder.stdin.close()
            holder.stdout.close()
            holder.stderr.close()

    def test_failed_readiness_terminates_process_and_leaves_no_state(self):
        process = self.fake_process()
        with (
            patch.object(server_manager, "listening_pids", return_value=[]),
            patch.object(server_manager.subprocess, "Popen", return_value=process),
            patch.object(server_manager, "wait_for_health", return_value=(False, "not ready")),
            patch.object(server_manager, "terminate_process") as terminate_process,
            patch("builtins.print"),
        ):
            result = server_manager.start_server(8124, build=False)

        self.assertNotEqual(result, 0)
        terminate_process.assert_called_once_with(process)
        self.assertFalse(server_manager.PID_FILE.exists())
        self.assertFalse(server_manager.PORT_FILE.exists())
        self.assertFalse(server_manager.URL_FILE.exists())

    def test_state_files_are_replaced_without_temporary_files(self):
        server_manager.publish_server_state(91, 8125, "http://127.0.0.1:8125/")

        self.assertEqual(server_manager.tracked_pid(), 91)
        self.assertEqual(server_manager.tracked_port(), 8125)
        self.assertEqual(
            server_manager.URL_FILE.read_text(encoding="utf-8"),
            "http://127.0.0.1:8125/\n",
        )
        self.assertEqual(list(self.data_dir.glob(".*.tmp")), [])

    def test_stop_does_not_kill_tracked_server_for_another_port(self):
        server_manager.PID_FILE.write_text("99", encoding="utf-8")
        server_manager.PORT_FILE.write_text("8126", encoding="utf-8")
        server_manager.URL_FILE.write_text("http://127.0.0.1:8126/\n", encoding="utf-8")
        with (
            patch.object(server_manager, "listening_pids", return_value=[]),
            patch.object(server_manager, "is_running", return_value=True),
            patch.object(server_manager.os, "kill") as kill,
        ):
            stopped, refused = server_manager.stop_server(8127)

        self.assertEqual(stopped, [])
        self.assertEqual(refused, [])
        kill.assert_not_called()
        self.assertTrue(server_manager.PID_FILE.exists())

    def test_health_wait_rejects_wrong_service_and_accepts_hunter(self):
        process = self.fake_process()
        with (
            patch.object(server_manager, "health_is_ready", side_effect=[False, True]),
            patch.object(server_manager.time, "sleep"),
        ):
            ready, error = server_manager.wait_for_health(process, "http://127.0.0.1:8128/", timeout=1)

        self.assertTrue(ready)
        self.assertEqual(error, "")

    def test_print_status_does_not_take_lifecycle_lock(self):
        with (
            patch.object(
                server_manager,
                "lifecycle_lock",
                side_effect=AssertionError("status must remain lock-free"),
            ),
            patch.object(server_manager, "tracked_pid", return_value=None),
            patch.object(server_manager, "listening_pids", return_value=[]),
            patch("builtins.print"),
        ):
            server_manager.print_status(8128)

    def test_start_refuses_to_orphan_an_existing_managed_server(self):
        server_manager.PID_FILE.write_text("99", encoding="utf-8")
        server_manager.PORT_FILE.write_text("8126", encoding="utf-8")
        with (
            patch.object(server_manager, "listening_pids", return_value=[]),
            patch.object(server_manager, "is_running", return_value=True),
            patch.object(server_manager, "is_hunter_server", return_value=True),
            patch.object(server_manager.subprocess, "Popen") as popen,
            patch("builtins.print"),
        ):
            result = server_manager.start_server(8127, build=False)

        self.assertEqual(result, 2)
        popen.assert_not_called()

    def test_real_managed_server_is_ready_when_url_is_published(self):
        root = self.data_dir / "isolated-root"
        root.mkdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        environment = os.environ.copy()
        environment["HUNTER_ROOT"] = str(root)
        manager_script = server_manager.ROOT_FOR_IMPORTS / "scripts" / "server_manager.py"
        command = [sys.executable, str(manager_script)]
        try:
            started = subprocess.run(
                [*command, "start", str(port), "--no-build"],
                cwd=server_manager.ROOT_FOR_IMPORTS,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            url_file = root / "data" / "hunter-server.url"
            pid_file = root / "data" / "hunter-server.pid"
            self.assertTrue(url_file.exists())
            self.assertTrue(pid_file.exists())
            with urlopen(url_file.read_text(encoding="utf-8").strip() + "api/health", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload, {"service": "hunter", "status": "ok"})
            os.kill(int(pid_file.read_text(encoding="utf-8")), 0)
        finally:
            stopped = subprocess.run(
                [*command, "stop", str(port)],
                cwd=server_manager.ROOT_FOR_IMPORTS,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        time.sleep(0.05)
                except OSError:
                    break
            else:
                self.fail("Managed Hunter server was still listening after stop.")


if __name__ == "__main__":
    unittest.main()
