# Waybar Timer

Keep the core CLI compatible with Python 3.11 or later and the standard library.
Load GTK dependencies only for the optional popup.
Keep tests independent of the desktop, audio server, and personal settings.

Run these checks after a code change:

```sh
python3 -m unittest discover -s tests -v
ruff check .
ruff format --check .
```

Read `CONTRIBUTING.md` for the project structure and popup checks.
Update `skills/desktop-timer/SKILL.md` when CLI behavior changes.
Keep real Waybar configs, credentials, logs, timer state, and machine paths outside this repository.
The installer must not overwrite a user's Waybar config.
