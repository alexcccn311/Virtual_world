"""Regression tests for safe Streamlit port recovery in the project launcher."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as project_launcher


class MainLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.main_file = PROJECT_ROOT / "main.py"
        self.app_file = PROJECT_ROOT / "app.py"
        self.expected_python = Path(sys.executable).resolve()
        self.missing_state_file = Path(__file__).with_name("_missing_streamlit_runtime.json")

    def test_project_process_detection_uses_exact_project_paths(self) -> None:
        own_main = f'"{sys.executable}" "{self.main_file}"'
        own_app = f'"{sys.executable}" -m streamlit run "{self.app_file}"'
        other_main = r'"D:\other_project\python.exe" "D:\other_project\main.py"'

        self.assertTrue(
            project_launcher._belongs_to_this_project(
                own_main, main_file=self.main_file, app_file=self.app_file
            )
        )
        self.assertTrue(
            project_launcher._belongs_to_this_project(
                own_app, main_file=self.main_file, app_file=self.app_file
            )
        )
        self.assertFalse(
            project_launcher._belongs_to_this_project(
                other_main, main_file=self.main_file, app_file=self.app_file
            )
        )

    def test_verified_stale_process_is_terminated(self) -> None:
        stale_pid = 43210
        listener = {
            "ProcessId": stale_pid,
            "Name": "python.exe",
            "CommandLine": f'"{sys.executable}" "{self.main_file}"',
        }
        with (
            patch.object(
                project_launcher,
                "_windows_port_listeners",
                side_effect=[[listener], []],
            ),
            patch.object(project_launcher, "_terminate_process_tree", return_value=True) as terminate,
        ):
            result = project_launcher._prepare_streamlit_port(
                project_launcher.APP_PORT,
                main_file=self.main_file,
                app_file=self.app_file,
                state_file=self.missing_state_file,
                expected_python=self.expected_python,
            )

        self.assertTrue(result)
        terminate.assert_called_once_with(stale_pid)

    def test_foreign_listener_is_never_terminated(self) -> None:
        listener = {
            "ProcessId": 54321,
            "Name": "another-server.exe",
            "CommandLine": r'"D:\other_project\server.exe" --port 8765',
        }
        with (
            patch.object(project_launcher, "_windows_port_listeners", return_value=[listener]),
            patch.object(project_launcher, "_terminate_process_tree") as terminate,
        ):
            result = project_launcher._prepare_streamlit_port(
                project_launcher.APP_PORT,
                main_file=self.main_file,
                app_file=self.app_file,
                state_file=self.missing_state_file,
                expected_python=self.expected_python,
            )

        self.assertFalse(result)
        terminate.assert_not_called()

    def test_runtime_state_recognizes_managed_process_without_command_line(self) -> None:
        managed_pid = 65432
        listener = {
            "ProcessId": managed_pid,
            "Name": "python.exe",
            "ExecutablePath": str(self.expected_python),
            "CommandLine": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "runtime.json"
            with (
                patch.object(project_launcher.os, "getpid", return_value=managed_pid),
            ):
                self.assertTrue(
                    project_launcher._write_runtime_state(
                        state_file,
                        port=project_launcher.APP_PORT,
                        project_dir=PROJECT_ROOT,
                    )
                )

            with (
                patch.object(
                    project_launcher,
                    "_windows_port_listeners",
                    side_effect=[[listener], []],
                ),
                patch.object(
                    project_launcher,
                    "_terminate_process_tree",
                    return_value=True,
                ) as terminate,
            ):
                result = project_launcher._prepare_streamlit_port(
                    project_launcher.APP_PORT,
                    main_file=self.main_file,
                    app_file=self.app_file,
                    state_file=state_file,
                    expected_python=self.expected_python,
                )

        self.assertTrue(result)
        terminate.assert_called_once_with(managed_pid)


if __name__ == "__main__":
    unittest.main()
