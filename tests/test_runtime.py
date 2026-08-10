from dataclasses import replace
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from app.config import settings
from app.runtime import EnvironmentUnavailable, ensure_environment


ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    @patch("app.runtime.subprocess.run")
    @patch("app.runtime._is_healthy", return_value=True)
    def test_healthy_environment_is_left_untouched(self, _healthy, run):
        with patch("app.runtime.settings", replace(settings, auto_start_environment=False)):
            ensure_environment(1)

        run.assert_not_called()

    def test_healthy_single_slot_runs_resource_cleanup(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "ensure.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            test_settings = replace(
                settings,
                root=root,
                metabase_urls=("http://localhost:43123",),
                max_parallel_rollouts=1,
                environment_start_script=script,
            )
            result = MagicMock(returncode=0, stdout="ready")
            with (
                patch("app.runtime.settings", test_settings),
                patch("app.runtime._is_healthy", return_value=True),
                patch("app.runtime.subprocess.run", return_value=result) as run,
            ):
                ensure_environment(1, reconcile_unused_slot=True)

        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["env"]["REQUIRED_ENVIRONMENT_COUNT"], "1")

    @patch("app.runtime.subprocess.run")
    @patch("app.runtime._is_healthy", return_value=False)
    def test_disabled_auto_start_fails_without_mutating_runtime(self, _healthy, run):
        test_settings = replace(settings, auto_start_environment=False)

        with patch("app.runtime.settings", test_settings):
            with self.assertRaisesRegex(EnvironmentUnavailable, "disabled"):
                ensure_environment(1)

        run.assert_not_called()

    def test_unhealthy_environment_runs_configured_repair_once(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "ensure.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            test_settings = replace(
                settings,
                root=root,
                metabase_urls=("http://localhost:43123",),
                max_parallel_rollouts=1,
                environment_start_script=script,
            )
            result = MagicMock(returncode=0, stdout="ready")
            with (
                patch("app.runtime.settings", test_settings),
                patch("app.runtime._is_healthy", side_effect=[False, False, True]),
                patch("app.runtime.subprocess.run", return_value=result) as run,
            ):
                ensure_environment(1)

        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["env"]["REQUIRED_ENVIRONMENT_COUNT"], "1")
        self.assertEqual(run.call_args.kwargs["env"]["METABASE_URLS"], "http://localhost:43123")
        self.assertNotIn("METABASE_TUNNEL_PORT_1", run.call_args.kwargs["env"])
        self.assertNotIn("GEMINI_API_KEY", run.call_args.kwargs["env"])
        self.assertNotIn("METABASE_PASSWORD", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.PIPE)

    def test_explicit_startup_logging_inherits_terminal_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "ensure.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            test_settings = replace(
                settings,
                root=root,
                metabase_urls=("http://localhost:43126",),
                max_parallel_rollouts=1,
                environment_start_script=script,
            )
            result = MagicMock(returncode=0, stdout=None)
            with (
                patch("app.runtime.settings", test_settings),
                patch("app.runtime._is_healthy", side_effect=[False, False, True]),
                patch("app.runtime.subprocess.run", return_value=result) as run,
            ):
                ensure_environment(1, show_startup_output=True)

        self.assertIsNone(run.call_args.kwargs["stdout"])

    def test_auto_start_rejects_nonlocal_environment_urls(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            test_settings = replace(
                settings,
                root=root,
                metabase_urls=("https://metabase.example.com",),
                max_parallel_rollouts=1,
            )
            with (
                patch("app.runtime.settings", test_settings),
                patch("app.runtime._is_healthy", return_value=False),
                patch("app.runtime.subprocess.run") as run,
            ):
                with self.assertRaisesRegex(EnvironmentUnavailable, "plain local"):
                    ensure_environment(1)

        run.assert_not_called()

    def test_requested_parallelism_must_fit_configured_capacity(self):
        test_settings = replace(
            settings,
            metabase_urls=("http://localhost:33000",),
            max_parallel_rollouts=1,
        )
        with (
            patch("app.runtime.settings", test_settings),
            patch("app.runtime._is_healthy", return_value=True),
            self.assertRaisesRegex(EnvironmentUnavailable, "exceeds configured capacity"),
        ):
            ensure_environment(2)

    def test_settings_reject_effective_capacity_over_two(self):
        with self.assertRaisesRegex(ValueError, "limited to two"):
            replace(
                settings,
                metabase_urls=(
                    "http://localhost:33000",
                    "http://localhost:33001",
                    "http://localhost:33002",
                ),
                max_parallel_rollouts=3,
            )

    def test_lifecycle_scripts_keep_startup_non_destructive(self):
        ensure_script = (ROOT / "scripts/ensure_environment.sh").read_text()
        backend_script = (ROOT / "scripts/docker_backend.sh").read_text()
        bootstrap_script = (ROOT / "scripts/bootstrap_metabase.sh").read_text()

        self.assertIn('scripts/docker_backend.sh', ensure_script)
        self.assertIn("METABASE_URLS", ensure_script)
        self.assertLess(
            ensure_script.index('if wait_for_health "$TUNNEL_REPAIR_WAIT_SECONDS"'),
            ensure_script.index("docker_compose up"),
        )
        self.assertIn('DOCKER_BACKEND must be auto, docker, or colima.', backend_script)
        self.assertIn('docker --context colima', backend_script)
        self.assertIn('docker "$@"', backend_script)
        self.assertIn('active_docker_is_local', backend_script)
        self.assertNotIn("--clean", bootstrap_script)
        self.assertIn("rollout_studio_seed_marker", bootstrap_script)
        self.assertIn("refusing to overwrite", bootstrap_script)

    def test_tunnel_only_repair_does_not_invoke_compose(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            bin_dir = temp / "bin"
            bin_dir.mkdir()
            state_file = temp / "tunnel-ready"
            docker_log = temp / "docker.log"

            commands = {
                "curl": f"""#!/bin/sh
if [ -f '{state_file}' ]; then
  printf '%s\\n' '{{\"status\":\"ok\"}}'
  exit 0
fi
exit 1
""",
                "colima": "#!/bin/sh\nexit 0\n",
                "docker": f"""#!/bin/sh
printf '%s\\n' \"$*\" >> '{docker_log}'
exit 0
""",
                "launchctl": f"""#!/bin/sh
if [ \"${{1:-}}\" = submit ]; then
  : > '{state_file}'
fi
exit 0
""",
            }
            for name, content in commands.items():
                command = bin_dir / name
                command.write_text(content)
                command.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_dir}:{environment['PATH']}",
                    "METABASE_URLS": "http://localhost:43124",
                    "REQUIRED_ENVIRONMENT_COUNT": "1",
                    "TUNNEL_REPAIR_WAIT_SECONDS": "1",
                    "DOCKER_BACKEND": "colima",
                }
            )
            result = subprocess.run(
                [str(ROOT / "scripts/ensure_environment.sh")],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("tunnel repaired", result.stdout.lower())
            docker_calls = docker_log.read_text()
            self.assertNotIn("compose up", docker_calls)
            self.assertNotIn("bootstrap", docker_calls)

    def test_active_docker_backend_uses_direct_ports_without_colima(self):
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            bin_dir = temp / "bin"
            bin_dir.mkdir()
            state_file = temp / "metabase-ready"
            docker_log = temp / "docker.log"
            colima_log = temp / "colima.log"
            launchctl_log = temp / "launchctl.log"

            commands = {
                "curl": f"""#!/bin/sh
if [ -f '{state_file}' ]; then
  printf '%s\\n' '{{"status":"ok"}}'
  exit 0
fi
exit 1
""",
                "colima": f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{colima_log}'\nexit 0\n",
                "docker": f"""#!/bin/sh
printf '%s\\n' "$*" >> '{docker_log}'
if [ "${{1:-}}" = context ] && [ "${{2:-}}" = show ]; then
  printf '%s\\n' desktop-linux
fi
if [ "${{1:-}}" = context ] && [ "${{2:-}}" = inspect ]; then
  printf '%s\\n' unix:///Users/test/.docker/run/docker.sock
fi
if [ "${{1:-}}" = compose ]; then
  for argument in "$@"; do
    if [ "$argument" = up ]; then
      : > '{state_file}'
    fi
  done
fi
exit 0
""",
                "launchctl": f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{launchctl_log}'\nexit 0\n",
            }
            for name, content in commands.items():
                command = bin_dir / name
                command.write_text(content)
                command.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_dir}:{environment['PATH']}",
                    "METABASE_URLS": "http://localhost:43125",
                    "REQUIRED_ENVIRONMENT_COUNT": "1",
                    "ENVIRONMENT_HEALTH_WAIT_SECONDS": "1",
                    "DOCKER_BACKEND": "auto",
                }
            )
            result = subprocess.run(
                [str(ROOT / "scripts/ensure_environment.sh")],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("Using Docker backend: docker", result.stdout)
            self.assertIn("Metabase environment is ready", result.stdout)
            self.assertIn("compose up", docker_log.read_text())
            self.assertFalse(colima_log.exists())
            self.assertNotIn("submit", launchctl_log.read_text())

    def test_local_script_defaults_and_shutdown_contract(self):
        config_source = (ROOT / "app/config.py").read_text()
        project_source = (ROOT / "pyproject.toml").read_text()
        runtime_source = (ROOT / "app/runtime.py").read_text()
        setup_script = (ROOT / "scripts/setup.sh").read_text()
        run_script = (ROOT / "scripts/run_local.sh").read_text()
        stop_script = (ROOT / "scripts/stop_local.sh").read_text()

        self.assertIn('"METABASE_URLS", "http://localhost:33000"', config_source)
        self.assertIn('"COMPUTER_USE_PYTHON", ".venv/bin/python"', config_source)
        self.assertIn('requires-python = ">=3.10"', project_source)
        self.assertFalse((ROOT / "requirements.txt").exists())
        self.assertIn("show_startup_output=True", runtime_source)
        self.assertIn("uv sync --locked", setup_script)
        self.assertIn("uv run --locked playwright install chromium", setup_script)
        self.assertIn("health endpoint is unavailable", run_script)
        self.assertIn("uv run --locked uvicorn", run_script)
        self.assertIn("settings.shutdown_grace_seconds", stop_script)
        self.assertIn("uv run --locked python", stop_script)
        self.assertIn("math.ceil", stop_script)
        self.assertLess(
            stop_script.index("job_manager.reconcile_interrupted_jobs()"),
            stop_script.index("docker_compose --profile pool2 down"),
        )


if __name__ == "__main__":
    unittest.main()
