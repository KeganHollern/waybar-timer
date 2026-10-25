---
name: desktop-timer
description: Start, inspect, cancel, or dismiss a local Waybar countdown timer. Use for requests such as "set a timer for 10 minutes" or "how much time is left?".
---

# Desktop timer

Use `waybar-timer` for the local countdown. If `PATH` does not include it, use `~/.local/bin/waybar-timer`.

```sh
waybar-timer start 10m "Tea"
waybar-timer status --json
waybar-timer cancel
waybar-timer dismiss
```

Use the requested duration and a short label. Prefer explicit units such as `10m`, `45s`, or `1h30m`.
Bare numbers mean minutes. `02:30` means 2 minutes and 30 seconds.

Only 1 timer can be active. If a start fails because a timer is active, read its status and report the conflict.
Use `--replace` only when the user asks to replace the timer.
Use `--silent` only when the user asks for no sound.

Read `status --json` after a change to check the result.
Report the label and time left. Do not wait for the countdown to finish unless the user asks.
The detached alarm process continues after the agent turn ends.

The timer plays 1 chime at zero. Its Waybar item stays visible until the user clicks it or requests dismissal.
Click an active Waybar timer to open a small popup with 1 **End timer** button.
Click outside the popup or press Escape to close it without a change.
`dismiss` removes a completed timer. `cancel` also removes an active timer.
Use `cancel` directly when the user asks the agent to stop a timer. The popup applies to the Waybar click action.

If the command is absent, report that the utility is unavailable.
