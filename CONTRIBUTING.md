# Contribute to Waybar Timer

Keep the CLI usable without GTK, a desktop session, or an audio server.
Keep user settings and state outside the source tree.
Use separate regular files for the repository skill and any installed personal copy.

## Project structure

| Path | Purpose |
| --- | --- |
| `waybar_timer.py` | CLI, state, alarm process, and Waybar output. |
| `timer_popup.py` | Optional GTK 4 confirmation and compositor geometry. |
| `settings.py` | Optional user settings. |
| `install.py`, `uninstall.py` | User installation and removal. |
| `timer.css`, `examples/` | Portable Waybar integration. |
| `skills/desktop-timer/` | Agent instructions for the CLI. |
| `assets/` | Original chime and popup styles. |
| `tests/` | Tests with isolated state and substitute audio players. |

## Check a change

```sh
python3 -m unittest discover -s tests -v
```

Use a virtual environment for the optional lint tools:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Add tests for changes to state transitions, parsing, installation, or cancellation behavior.
Keep tests independent of personal settings, a running compositor, and installed audio tools.
For popup changes, also check the window on a Wayland desktop.
Check the **End timer** action, outside-click dismissal, Escape, and a timer that ends while the popup is open.

Update the README and skill when a CLI command or its behavior changes.
Use generic examples. Do not add personal desktop snapshots, credentials, state files, or logs.
Describe the change and the checks in a pull request.
