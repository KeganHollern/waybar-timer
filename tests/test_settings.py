import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from settings import popup_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="timer-settings-")
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root)})
        self.environment.start()
        self.config = self.root / "waybar-timer/config.toml"
        self.config.parent.mkdir()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_default_works_without_any_config(self):
        self.assertEqual(popup_settings(), {"anchor": "pointer"})

    def test_centered_clock_uses_user_supplied_metrics(self):
        self.config.write_text(
            '[popup]\nanchor = "centered-clock"\nfont = "monospace 13px"\n'
            'clock_format = "%H:%M"\nclock_extra_width = 20\ntimer_extra_width = 16\n'
        )
        values = popup_settings()
        self.assertEqual(values["anchor"], "centered-clock")
        self.assertEqual(values["clock_extra_width"], 20)
        self.assertEqual(values["timer_extra_width"], 16)

    def test_invalid_values_are_rejected(self):
        for value in (
            'anchor = "elsewhere"',
            "font = 42",
            "timer_extra_width = -1",
            "clock_extra_width = inf",
            'clock_format = ""',
            "clock_extra_width = true",
        ):
            with self.subTest(value=value):
                self.config.write_text("[popup]\n" + value + "\n")
                with self.assertRaises(ValueError):
                    popup_settings()


if __name__ == "__main__":
    unittest.main()
