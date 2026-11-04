import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools import start_all


class EnvironmentTests(unittest.TestCase):
    def test_first_install_then_skip_and_reinstall_on_requirements_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            python = root / ".venv" / "Scripts" / "python.exe"
            (root / "requirements.txt").write_text("dependency-a", encoding="ascii")
            requirement = root / "requirements-mcp.txt"
            requirement.write_text("mcp==2.3.0", encoding="ascii")

            def create(_root):
                python.parent.mkdir(parents=True)
                python.touch()

            builder = Mock()
            builder.create.side_effect = create
            with (
                patch.object(start_all, "ROOT", root),
                patch.object(start_all, "PYTHON", python),
                patch.object(start_all.venv, "EnvBuilder", return_value=builder),
                patch.object(
                    start_all.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=0),
                ) as run,
            ):
                self.assertEqual(start_all.prepare_environment(), python)
                builder.create.assert_called_once_with(root / ".venv")
                self.assertIn("pip", run.call_args.args[0])
                run.reset_mock()
                start_all.prepare_environment()
                self.assertNotIn("pip", run.call_args.args[0])
                requirement.write_text("mcp==2.3.1", encoding="ascii")
                run.reset_mock()
                start_all.prepare_environment()
                self.assertIn("pip", run.call_args.args[0])

    def test_failed_dependency_install_is_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            python = root / "python.exe"
            python.touch()
            for name in ("requirements.txt", "requirements-mcp.txt"):
                (root / name).write_text("dependency", encoding="ascii")
            with (
                patch.object(start_all, "ROOT", root),
                patch.object(start_all, "PYTHON", python),
                patch.object(
                    start_all.subprocess,
                    "run",
                    side_effect=subprocess.CalledProcessError(1, ["pip"]),
                ),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                start_all.prepare_environment()
            self.assertFalse((root / ".venv" / "wechat-mcp-deps.sha256").exists())


class ServiceTests(unittest.TestCase):
    def test_voice_install_only_runs_for_missing_components(self):
        import exporter_core as core

        for ready in (False, True):
            with (
                patch.object(core, "rust_silk_status", return_value=(ready, "")),
                patch.object(core, "local_asr_status", return_value=(ready, "")),
                patch.object(core, "install_rust_silk") as silk,
                patch.object(core, "install_local_asr_model") as model,
            ):
                start_all.prepare_voice()
                self.assertEqual(silk.call_count, int(not ready))
                self.assertEqual(model.call_count, int(not ready))

    def test_existing_reader_is_reused_and_foreign_port_is_rejected(self):
        import psutil

        connection = SimpleNamespace(
            pid=123, status="LISTEN", laddr=SimpleNamespace(port=18765)
        )
        process = Mock()
        process.cwd.return_value = str(start_all.ROOT)
        process.cmdline.return_value = [
            "python.exe",
            str(start_all.ROOT / "mcp_server.py"),
            "--docker-reader",
        ]
        with (
            patch.object(psutil, "net_connections", return_value=[connection]),
            patch.object(psutil, "Process", return_value=process),
        ):
            self.assertTrue(start_all.reader_running())
            process.cmdline.return_value = ["python.exe", "unrelated.py"]
            with self.assertRaisesRegex(RuntimeError, "其他服务"):
                start_all.reader_running()

    def test_reader_reuse_does_not_launch_another_process(self):
        with (
            patch.object(start_all, "prepare_token", return_value="a" * 43),
            patch.object(start_all, "reader_running", return_value=True),
            patch.object(start_all.subprocess, "Popen") as popen,
        ):
            self.assertIsNone(start_all.start_reader())
            popen.assert_not_called()

    def test_docker_start_is_skipped_when_ready(self):
        with (
            patch.object(start_all.shutil, "which", return_value="docker.exe"),
            patch.object(start_all, "docker_type", return_value="linux"),
            patch.object(start_all.subprocess, "run") as run,
        ):
            self.assertEqual(start_all.prepare_docker(), "docker.exe")
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][1:3], ["compose", "version"])

    def test_docker_launches_when_stopped_and_rejects_windows_containers(self):
        with (
            patch.object(start_all.shutil, "which", return_value="docker.exe"),
            patch.object(start_all, "docker_type", side_effect=["", "linux"]),
            patch.object(start_all.subprocess, "run") as run,
        ):
            start_all.prepare_docker()
            self.assertEqual(run.call_args_list[0].args[0][1:3], ["desktop", "start"])
        with (
            patch.object(start_all.shutil, "which", return_value="docker.exe"),
            patch.object(start_all, "docker_type", return_value="windows"),
            self.assertRaisesRegex(RuntimeError, "Linux containers"),
        ):
            start_all.prepare_docker()

    def test_failure_only_cleans_up_reader_created_by_this_launch(self):
        process = Mock()
        process.poll.return_value = None
        for child in (process, None):
            with (
                patch.object(start_all, "start_reader", return_value=child),
                patch.object(start_all, "wait_reader"),
                patch.object(
                    start_all.subprocess,
                    "run",
                    side_effect=subprocess.CalledProcessError(1, ["docker"]),
                ),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                start_all.launch("docker.exe")
        process.terminate.assert_called_once()

    def test_failed_protocol_check_does_not_report_startup_success(self):
        with (
            patch.object(start_all, "start_reader", return_value=None),
            patch.object(start_all, "wait_reader"),
            patch.object(
                start_all.subprocess, "run", return_value=SimpleNamespace(returncode=1)
            ),
            self.assertRaisesRegex(RuntimeError, "MCP连接检查失败"),
        ):
            start_all.launch("docker.exe")


class TunnelTests(unittest.TestCase):
    def status(self, ready):
        return {
            "tunnel_id": "tunnel_" + "a" * 32,
            "profile_dir": str(start_all.ROOT / ".tunnel-profiles"),
            "process_running": ready,
            "healthy": ready,
            "ready": ready,
        }

    def test_healthy_tunnel_is_reused_without_requesting_key(self):
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(start_all, "tunnel_status", return_value=self.status(True)),
            patch.object(start_all.getpass, "getpass") as password,
            patch.object(start_all.subprocess, "run") as run,
        ):
            start_all.start_tunnel()
        password.assert_not_called()
        run.assert_not_called()

    def test_missing_key_in_agent_mode_reports_user_action_without_echo(self):
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(start_all, "tunnel_status", return_value=self.status(False)),
            patch.dict(start_all.os.environ, {}, clear=True),
            patch.object(start_all.sys.stdin, "isatty", return_value=False),
            patch.object(start_all.getpass, "getpass") as password,
            self.assertRaisesRegex(RuntimeError, "双击start_all.cmd"),
        ):
            start_all.start_tunnel()
        password.assert_not_called()

    def test_reconnection_uses_environment_key_not_command_arguments(self):
        key = "synthetic-runtime-key"
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(
                start_all,
                "tunnel_status",
                side_effect=[self.status(False), self.status(True)],
            ),
            patch.dict(start_all.os.environ, {"CONTROL_PLANE_API_KEY": key}),
            patch.object(
                start_all, "getproxies", return_value={"https": "http://127.0.0.1:7897"}
            ),
            patch.object(
                start_all.subprocess, "run", return_value=SimpleNamespace(returncode=0)
            ) as run,
        ):
            start_all.start_tunnel()
        self.assertNotIn(key, " ".join(run.call_args.args[0]))
        self.assertIn("env:CONTROL_PLANE_API_KEY", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["env"]["CONTROL_PLANE_API_KEY"], key)
        self.assertIn("127.0.0.1", run.call_args.kwargs["env"]["NO_PROXY"])

    def test_failed_reconnection_is_not_reported_ready(self):
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(start_all, "tunnel_status", return_value=self.status(False)),
            patch.dict(
                start_all.os.environ, {"CONTROL_PLANE_API_KEY": "synthetic-key"}
            ),
            patch.object(
                start_all.subprocess, "run", return_value=SimpleNamespace(returncode=1)
            ),
            self.assertRaisesRegex(RuntimeError, "重连失败"),
        ):
            start_all.start_tunnel()


if __name__ == "__main__":
    unittest.main()
