import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import timer_popup
import waybar_timer as timer

ROOT = Path(__file__).resolve().parents[1]


class DurationTests(unittest.TestCase):
    def test_formats(self):
        for text, expected in [
            ("10", 600),
            ("0.5", 30),
            ("45s", 45),
            ("1h30m", 5400),
            ("1h 5m 30s", 3930),
            ("02:30", 150),
            ("1:02:03", 3723),
            ("1d", 86400),
            ("0.1m", 6),
        ]:
            with self.subTest(text=text):
                self.assertEqual(timer.duration(text), expected)

    def test_invalid(self):
        for text in ["", "0", "-1m", "1:60", "1:99:00", "inf", "NaN", "1m garbage", "367d", "0.1s"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                timer.duration(text)

    def test_suspend_clock_and_reboot_fallback(self):
        state = {"status": "running", "boot_id": timer.BOOT_ID, "end_boot": 110, "end_wall": 200}
        with (
            patch.object(timer, "boottime", return_value=105.1),
            patch.object(timer.time, "time", return_value=9999),
        ):
            self.assertEqual(timer.remaining(state), 5)
        state["boot_id"] = "previous-boot"
        with patch.object(timer.time, "time", return_value=197):
            self.assertEqual(timer.remaining(state), 3)

    def test_hidden_idle_and_persistent_done(self):
        self.assertEqual(timer.module_data({"status": "idle"})["text"], "")
        state = {"status": "done", "label": "Tea <&>", "sound_error": "No audio"}
        rendered = timer.module_data(state)
        self.assertIn("Tea <&>", rendered["text"])
        self.assertEqual(rendered["class"], "done")
        self.assertIn("sound failed", rendered["tooltip"])


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="waybar-timer-test-")
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.sound_log = self.root / "sounds"
        fake = f'#!{sys.executable}\nimport os\nfrom pathlib import Path\nPath(os.environ["SOUND_LOG"]).open("a").write("ding\\n")\n'
        (self.bin / "paplay").write_text(fake)
        (self.bin / "paplay").chmod(0o755)
        self.env = {
            **os.environ,
            "XDG_STATE_HOME": str(self.root / "state"),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "SOUND_LOG": str(self.sound_log),
            "XDG_CONFIG_HOME": str(self.root / "config"),
        }
        self.watchers = []

    def tearDown(self):
        self.run_cli("cancel")
        for watcher in self.watchers:
            watcher.terminate()
            watcher.communicate(timeout=3)
        # Let detached workers observe cancellation before deleting their state directory.
        time.sleep(0.3)
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "waybar_timer.py"), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=4,
        )

    def status(self):
        result = self.run_cli("status", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def wait_until(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.05)
        self.fail("The condition did not become true before the timeout.")

    def watcher(self):
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "waybar_timer.py"), "watch"],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.watchers.append(process)
        return process

    def next_record(self, process):
        self.assertTrue(
            select.select([process.stdout], [], [], 3)[0], "The stream did not emit a record."
        )
        return json.loads(process.stdout.readline())

    def test_cli_exit_alarm_and_no_replay(self):
        first, second = self.watcher(), self.watcher()
        self.assertEqual(self.next_record(first)["text"], "")
        self.assertEqual(self.next_record(second)["text"], "")
        result = self.run_cli("1s", "Tea <&>")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.next_record(first)["class"], "running")
        self.wait_until(lambda: self.status()["status"] == "done")
        self.wait_until(self.sound_log.exists)
        self.assertEqual(self.sound_log.read_text(), "ding\n")
        for watcher in (first, second):
            watcher.terminate()
            watcher.communicate(timeout=3)
        third = self.watcher()
        self.assertEqual(self.next_record(third)["class"], "done")
        time.sleep(0.3)
        self.assertEqual(self.sound_log.read_text(), "ding\n")
        self.assertEqual(self.run_cli("dismiss").returncode, 0)
        self.assertEqual(self.next_record(third)["text"], "")

    def test_active_click_refusal_replace_and_cancel(self):
        self.assertEqual(self.run_cli("5s", "First").returncode, 0)
        self.assertEqual(self.run_cli("dismiss").returncode, 0)
        self.assertEqual(self.status()["label"], "First")
        self.assertNotEqual(self.run_cli("5s", "Second").returncode, 0)
        self.assertEqual(self.run_cli("5s", "Second", "--replace").returncode, 0)
        self.assertEqual(self.status()["label"], "Second")
        self.assertEqual(self.run_cli("cancel").returncode, 0)
        self.assertEqual(self.status()["status"], "idle")
        self.assertFalse(self.sound_log.exists())

    def test_silent_timer(self):
        self.assertEqual(self.run_cli("1s", "--silent").returncode, 0)
        self.wait_until(lambda: self.status()["status"] == "done")
        self.assertFalse(self.sound_log.exists())

    def test_alarm_continues_after_watch_closes(self):
        watcher = self.watcher()
        self.assertEqual(self.next_record(watcher)["text"], "")
        self.assertEqual(self.run_cli("1s", "Detached timer").returncode, 0)
        watcher.terminate()
        watcher.communicate(timeout=3)
        state_file = self.root / "state/waybar-timer/state.json"
        self.wait_until(lambda: json.loads(state_file.read_text())["status"] == "done")
        self.wait_until(self.sound_log.exists)
        self.assertEqual(self.sound_log.read_text(), "ding\n")

    def test_pipewire_fallback(self):
        # Audio checks use substitute players, so CI needs no audio server.
        (self.bin / "paplay").write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n")
        fallback = self.bin / "pw-play"
        fallback.write_text(
            f"#!{sys.executable}\nimport os, subprocess, sys\nfrom pathlib import Path\n"
            'Path(os.environ["SOUND_LOG"]).write_text("pipewire\\n")\n'
        )
        fallback.chmod(0o755)
        result = self.run_cli("sound-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sound_log.read_text(), "pipewire\n")

    def test_concurrent_starts(self):
        processes = [
            subprocess.Popen(
                [sys.executable, str(ROOT / "waybar_timer.py"), "5s", str(index)],
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for index in range(6)
        ]
        for process in processes:
            process.communicate(timeout=3)
        self.assertEqual(sum(process.returncode == 0 for process in processes), 1)
        self.assertEqual(self.status()["status"], "running")

    def test_audio_failure_is_visible(self):
        for name in ("paplay", "pw-play"):
            path = self.bin / name
            path.write_text(
                f'#!{sys.executable}\nimport sys\nprint("Test audio failure", file=sys.stderr)\nsys.exit(1)\n'
            )
            path.chmod(0o755)
        self.assertEqual(self.run_cli("1s").returncode, 0)
        self.wait_until(lambda: "sound_error" in self.status())
        self.assertIn("Test audio failure", self.status()["sound_error"])

    def test_invalid_duration_does_not_create_timer(self):
        self.assertNotEqual(self.run_cli("garbage").returncode, 0)
        self.assertEqual(self.status()["status"], "idle")


class ClickTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="timer-click-test-")
        self.environment = patch.dict(
            os.environ,
            {
                "XDG_STATE_HOME": self.temp.name,
                "XDG_CONFIG_HOME": str(Path(self.temp.name) / "config"),
            },
        )
        self.environment.start()
        self.store = timer.Store()
        self.state = {
            "status": "running",
            "id": "original-timer",
            "label": "Tea",
            "duration": 600,
            "end_wall": time.time() + 600,
            "end_boot": timer.boottime() + 600,
            "boot_id": timer.BOOT_ID,
        }

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_active_click_opens_popup_without_cancelling(self):
        self.store.write(self.state)
        with (
            patch.object(timer.subprocess, "Popen") as launch,
            patch.object(timer_popup, "hyprland_position", return_value=None),
        ):
            timer.click_timer(self.store)
        self.assertEqual(self.store.read(), self.state)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(launch.call_args.args[0][2], "original-timer")

    def test_done_click_dismisses_without_popup(self):
        self.state["status"] = "done"
        self.store.write(self.state)
        with patch.object(timer.subprocess, "Popen") as launch:
            timer.click_timer(self.store)
        self.assertEqual(self.store.read()["status"], "idle")
        launch.assert_not_called()

    def test_idle_click_does_nothing(self):
        with patch.object(timer.subprocess, "Popen") as launch:
            timer.click_timer(self.store)
        launch.assert_not_called()

    def test_confirmation_cancels_only_its_original_timer(self):
        self.store.write(self.state)
        self.assertFalse(timer.cancel_if_current(self.store, "a-stale-timer"))
        self.assertEqual(self.store.read(), self.state)
        self.assertTrue(timer.cancel_if_current(self.store, "original-timer"))
        self.assertEqual(self.store.read()["status"], "idle")

    def test_popup_position_uses_monitor_scale_and_bar_height(self):
        values = {
            "cursorpos": {"x": 400, "y": 20},
            "monitors": [
                {"name": "TEST", "x": -200, "y": 0, "width": 2560, "height": 1440, "scale": 2}
            ],
            "layers": {
                "TEST": {
                    "levels": {
                        "2": [{"namespace": "waybar", "x": -190, "y": 5, "w": 1260, "h": 32}]
                    }
                }
            },
        }

        def query(arguments, **_kwargs):
            return subprocess.CompletedProcess(arguments, 0, json.dumps(values[arguments[-1]]))

        with patch.object(timer_popup.subprocess, "run", side_effect=query):
            position = timer_popup.hyprland_position()
        self.assertEqual(position, {"monitor": "TEST", "left": 478, "top": 45})


if __name__ == "__main__":
    unittest.main()
