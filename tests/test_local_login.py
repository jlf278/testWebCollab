import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cabanas.browser import desktop_available, launch_options, save_session
from cabanas.core import Config

spec = importlib.util.spec_from_file_location("local_login", Path(__file__).resolve().parents[1] / "scripts/local_login.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class LocalLoginTests(unittest.TestCase):
    def test_bootstrap_runs_login_without_starting_monitor_or_booking(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config.example.json").write_bytes(Path("config.example.json").read_bytes())
            with patch.object(helper, "__file__", str(root / "scripts/local_login.py")), \
                 patch.object(helper.sys, "argv", ["local_login.py"]), \
                 patch.object(helper.sys, "platform", "darwin"), \
                 patch.object(helper.getpass, "getpass", return_value="12345678"), \
                 patch.object(helper, "run") as execute, patch("builtins.print"):
                self.assertEqual(helper.main(), 0)
            commands = [call.args[0] for call in execute.call_args_list]
            self.assertEqual(commands[-1][-3:], ["-m", "cabanas.cli", "login"])
            self.assertFalse(any("--book" in command or "run" in command for command in commands))
            self.assertFalse(any("12345678" in " ".join(command) for command in commands))

    def test_initialize_config_preserves_dates_and_keeps_identifier_private(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = Path("config.example.json").read_bytes()
            (root / "config.example.json").write_bytes(source)
            destination = helper.initialize_config(root, "12345678")
            config = Config.load(destination)
            self.assertEqual([t.day for t in config.targets], ["2026-12-22", "2026-12-23", "2026-12-26"])
            self.assertEqual(config.browser, "")
            self.assertEqual((root / "config.example.json").read_bytes(), source)
            if os.name != "nt":
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_existing_config_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".private").mkdir()
            config = root / ".private/config.json"
            config.write_text("preserve existing calibration")
            helper.initialize_config(root, "12345678")
            self.assertEqual(config.read_text(), "preserve existing calibration")

    def test_invalid_identifier_does_not_create_config(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(ValueError):
                helper.initialize_config(root, "not-a-booking-number")
            self.assertFalse((root / ".private/config.json").exists())

    def test_mac_and_windows_do_not_require_linux_display_variables(self):
        with patch.dict(os.environ, {}, clear=True):
            for platform in ["darwin", "win32"]:
                with patch("cabanas.browser.sys.platform", platform):
                    self.assertTrue(desktop_available())
            with patch("cabanas.browser.sys.platform", "linux"):
                self.assertFalse(desktop_available())

    def test_browser_managed_and_custom_executables(self):
        self.assertEqual(launch_options(SimpleNamespace(browser=""), headless=False), {"headless": False})
        self.assertEqual(launch_options(SimpleNamespace(browser="/custom/chromium"), headless=True),
                         {"headless": True, "executable_path": "/custom/chromium"})

    def test_failed_session_write_preserves_previous_session(self):
        class Context:
            def storage_state(self, path):
                Path(path).write_text("partial")
                raise OSError("simulated disk failure")
        with tempfile.TemporaryDirectory() as d:
            session = Path(d) / "session.json"
            session.write_text("previous session")
            with self.assertRaises(OSError):
                save_session(Context(), session)
            self.assertEqual(session.read_text(), "previous session")
            self.assertFalse(session.with_suffix(".tmp").exists())

    def test_session_write_is_private_and_complete(self):
        class Context:
            def storage_state(self, path):
                Path(path).write_text(json.dumps({"cookies": [], "origins": []}))
        with tempfile.TemporaryDirectory() as d:
            session = Path(d) / "session.json"
            save_session(Context(), session)
            self.assertEqual(json.loads(session.read_text()), {"cookies": [], "origins": []})
            if os.name != "nt":
                self.assertEqual(session.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
