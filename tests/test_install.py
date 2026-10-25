import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import install
import uninstall


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="timer-install-")
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin with spaces"
        self.data = self.root / "share with spaces/waybar-timer"
        self.skills = self.root / "skills"
        self.environment = patch.dict(
            os.environ,
            {
                "XDG_STATE_HOME": str(self.root / "state"),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_DATA_HOME": str(self.root / "share with spaces"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def install(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return install.install(
                bin_dir=self.bin, data_dir=self.data, skill_dir=self.skills, **kwargs
            )

    def test_install_update_and_uninstall(self):
        waybar = self.root / "config/waybar/config.jsonc"
        waybar.parent.mkdir(parents=True)
        waybar.write_text('{"modules-center": ["clock"]}\n')
        self.install(with_skill=True)
        command = self.bin / "waybar-timer"
        self.assertTrue(command.is_file())
        self.assertFalse(command.is_symlink())
        result = subprocess.run([command, "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(waybar.read_text(), '{"modules-center": ["clock"]}\n')
        skill = self.skills / "desktop-timer/SKILL.md"
        self.assertFalse(skill.is_symlink())
        self.assertEqual(
            skill.read_bytes(), (install.SOURCE / "skills/desktop-timer/SKILL.md").read_bytes()
        )
        self.install()
        manifest = json.loads((self.data / install.MANIFEST).read_text())
        self.assertIn(str(skill), manifest["files"])
        self.assertTrue(
            list((self.root / "state/waybar-timer/install-backups").glob("*/files.json"))
        )
        with contextlib.redirect_stdout(io.StringIO()):
            uninstall.uninstall(self.data)
        self.assertFalse(command.exists())
        self.assertFalse(skill.exists())
        self.assertEqual(waybar.read_text(), '{"modules-center": ["clock"]}\n')

    def test_installed_timer_works_without_checkout(self):
        checkout = self.root / "temporary checkout"
        for name in install.RUNTIME_FILES:
            target = checkout / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(install.SOURCE / name, target)
        with patch.object(install, "SOURCE", checkout):
            self.install()
        shutil.rmtree(checkout)
        command = self.bin / "waybar-timer"
        result = subprocess.run(
            [command, "1s", "Detached install", "--silent"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.root / "state/waybar-timer/state.json"
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and json.loads(state.read_text())["status"] != "done":
            time.sleep(0.05)
        self.assertEqual(json.loads(state.read_text())["status"], "done")
        result = subprocess.run([command, "dismiss"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_force_replaces_link_without_touching_its_target(self):
        self.bin.mkdir()
        original = self.root / "old-command"
        original.write_text("keep this file\n")
        command = self.bin / "waybar-timer"
        command.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.install()
        self.install(force=True)
        self.assertFalse(command.is_symlink())
        self.assertEqual(original.read_text(), "keep this file\n")

    def test_install_and_uninstall_refuse_modified_files(self):
        self.install()
        command = self.bin / "waybar-timer"
        modified = command.read_text() + "# local edit\n"
        command.write_text(modified)
        with self.assertRaisesRegex(ValueError, "edited files"):
            self.install()
        with self.assertRaisesRegex(ValueError, "changed"):
            uninstall.uninstall(self.data)
        self.assertEqual(command.read_text(), modified)
        self.assertTrue((self.data / "assets/ding.wav").exists())

    def test_uninstall_can_keep_personal_skill(self):
        self.install(with_skill=True)
        skill = self.skills / "desktop-timer/SKILL.md"
        skill.write_text(skill.read_text() + "\nPersonal note.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            uninstall.uninstall(self.data, keep_skill=True)
        self.assertTrue(skill.is_file())
        self.assertFalse((self.bin / "waybar-timer").exists())

    def test_write_failure_restores_previous_installation(self):
        self.install()
        command = self.bin / "waybar-timer"
        original = command.read_bytes()
        writer = install.atomic_write
        failed = False

        def fail_once(target, data, mode=0o644):
            nonlocal failed
            if target.name == "timer_popup.py" and not failed:
                failed = True
                raise OSError("simulated disk error")
            return writer(target, data, mode)

        with patch.object(install, "atomic_write", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "simulated disk error"):
                self.install()
        self.assertEqual(command.read_bytes(), original)
        result = subprocess.run([command, "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
