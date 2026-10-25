#!/usr/bin/env python3
"""A quiet, single countdown timer for Waybar and the command line."""

import argparse
import contextlib
import ctypes
import datetime as dt
import fcntl
import json
import math
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOOT_ID = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
MAX_DURATION = 366 * 86400


def duration(value):
    """Accept minutes, unit sequences, MM:SS, and HH:MM:SS."""
    value = value.strip().lower()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        seconds = float(value) * 60
    elif re.fullmatch(r"\d+:\d{2}(?::\d{2})?", value):
        parts = [int(part) for part in value.split(":")]
        if any(part >= 60 for part in parts[1:]):
            raise ValueError("Use values below 60 for the seconds and inner minutes.")
        seconds = sum(part * 60**index for index, part in enumerate(reversed(parts)))
    else:
        compact = re.sub(r"\s+", "", value)
        pieces = re.findall(r"(\d+(?:\.\d+)?)([dhms])", compact)
        if not pieces or "".join(n + unit for n, unit in pieces) != compact:
            raise ValueError("Use a duration such as 10m, 1h30m, 45s, or 02:30.")
        seconds = sum(
            float(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit] for n, unit in pieces
        )
    if not math.isfinite(seconds) or not 1 <= seconds <= MAX_DURATION:
        raise ValueError("Set a timer between 1 second and 366 days.")
    return seconds


def boottime():
    # CLOCK_BOOTTIME includes suspend and is unaffected by wall-clock corrections.
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def remaining(state):
    if state.get("status") != "running":
        return 0
    if state["boot_id"] == BOOT_ID:
        return max(0, math.ceil(state["end_boot"] - boottime()))
    return max(0, math.ceil(state["end_wall"] - time.time()))


def clock_text(seconds):
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


class Store:
    def __init__(self):
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        self.root = base / "waybar-timer"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "state.json"

    @contextlib.contextmanager
    def lock(self):
        with (self.root / "state.lock").open("a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            yield

    def read(self):
        try:
            state = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {"status": "idle"}
        if not isinstance(state, dict) or state.get("status") not in ("running", "done"):
            raise ValueError(f"The timer state is invalid: {self.path}")
        for key in ("id", "label", "boot_id"):
            if not isinstance(state.get(key), str):
                raise ValueError(f"The timer state is invalid: {self.path}")
        for key in ("end_boot", "end_wall", "duration"):
            if not isinstance(state.get(key), (int, float)) or not math.isfinite(state[key]):
                raise ValueError(f"The timer state is invalid: {self.path}")
        return state

    def write(self, state):
        descriptor, name = tempfile.mkstemp(prefix=".state-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(state, output, ensure_ascii=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)


class Changes:
    """Sleep without polling while idle; wake on an atomic state replacement."""

    def __init__(self, root):
        self.fd = -1
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.inotify_init1.argtypes = [ctypes.c_int]
            libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
            if self.fd < 0 or libc.inotify_add_watch(self.fd, os.fsencode(root), 0x80 | 0x200) < 0:
                raise OSError("Cannot watch the timer directory.")
        except (OSError, AttributeError):
            self.close()

    def wait(self, timeout):
        if self.fd < 0:
            time.sleep(min(timeout, 1) if timeout is not None else 1)
            return
        until = time.monotonic() + timeout if timeout is not None else None
        while True:
            delay = max(0, until - time.monotonic()) if until is not None else None
            if not select.select([self.fd], [], [], delay)[0]:
                return
            data = os.read(self.fd, 65536)
            offset = 0
            while offset < len(data):
                _, mask, _, size = struct.unpack_from("iIII", data, offset)
                name = data[offset + 16 : offset + 16 + size].split(b"\0", 1)[0]
                offset += 16 + size
                if name == b"state.json" or mask & 0x4000:
                    return

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def ensure_worker(store):
    """Call under the state lock. Transfer the worker lock to one detached child."""
    guard = (store.root / "worker.lock").open("a")
    try:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with (store.root / "worker.log").open("ab") as log:
            subprocess.Popen(
                [sys.executable, str(ROOT / "waybar_timer.py"), "__worker", str(guard.fileno())],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                pass_fds=(guard.fileno(),),
                close_fds=True,
            )
    finally:
        # Do not explicitly unlock: the inherited descriptor keeps the lock held.
        guard.close()


def play_sound(path):
    candidates = [
        ("paplay", ["--client-name=waybar-timer", "--stream-name=Timer complete"]),
        ("pw-play", ["--media-role=Notification"]),
    ]
    errors = []
    for name, options in candidates:
        executable = shutil.which(name)
        if not executable:
            continue
        try:
            result = subprocess.run(
                [executable, *options, str(path)], capture_output=True, text=True, timeout=8
            )
            if result.returncode == 0:
                return None
            errors.append(f"{name}: {result.stderr.strip()}")
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"{name}: {error}")
    return "; ".join(errors) or "Install paplay or pw-play to hear the timer."


def worker(store, descriptor):
    with os.fdopen(descriptor, "a") as guard:
        while True:
            completed = None
            with store.lock():
                state = store.read()
                if state["status"] != "running":
                    # Release before the state lock so a concurrent start cannot miss us.
                    guard.close()
                    return
                seconds = remaining(state)
                if seconds == 0:
                    state["status"] = "done"
                    store.write(state)
                    completed = state
            if completed:
                if not completed.get("silent"):
                    error = play_sound(completed["sound"])
                    if error:
                        print(error, file=sys.stderr, flush=True)
                        with store.lock():
                            state = store.read()
                            if state.get("id") == completed["id"]:
                                state["sound_error"] = error
                                store.write(state)
                # A new timer may start while the sound plays. Keep serving it.
                continue
            time.sleep(min(0.25, seconds))


def module_data(state):
    if state["status"] == "idle":
        return {"text": "", "tooltip": "", "class": "idle"}
    seconds = remaining(state)
    done = state["status"] == "done" or seconds == 0
    label = state["label"]
    short_label = label if len(label) <= 24 else label[:23] + "…"
    prefix = f"{short_label} · " if label else ""
    if done:
        tooltip = f"{label or 'Timer'} is complete.\nClick to dismiss the timer."
        if state.get("sound_error"):
            tooltip += "\nThe sound failed. Run waybar-timer sound-test to check audio."
        return {"text": f"󰄉 {prefix}Done", "tooltip": tooltip, "class": "done"}
    end = dt.datetime.fromtimestamp(state["end_wall"]).astimezone().strftime("%H:%M:%S")
    return {
        "text": f"󰔟 {prefix}{clock_text(seconds)}",
        "tooltip": f"{label or 'Timer'} ends at {end}.\n"
        "Click to cancel the timer with confirmation.",
        "class": "running",
    }


def cancel_if_current(store, timer_id):
    """A stale confirmation must never cancel a replacement timer."""
    with store.lock():
        state = store.read()
        if state.get("id") != timer_id:
            return False
        store.path.unlink(missing_ok=True)
        return True


def click_timer(store):
    with store.lock():
        state = store.read()
        if state["status"] == "idle":
            return
        if state["status"] == "done":
            store.path.unlink(missing_ok=True)
            return
    from timer_popup import hyprland_position

    position = hyprland_position(state)
    with (store.root / "popup.log").open("ab") as log:
        subprocess.Popen(
            [sys.executable, str(ROOT / "timer_popup.py"), state["id"], json.dumps(position)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            start_new_session=True,
        )


def stream(store):
    changes = Changes(store.root)
    previous = None
    try:
        while True:
            with store.lock():
                state = store.read()
                if state["status"] == "running":
                    ensure_worker(store)
            output = json.dumps(module_data(state), ensure_ascii=False)
            if output != previous:
                print(output, flush=True)
                previous = output
            changes.wait(1 if state["status"] == "running" else None)
    finally:
        changes.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = ("start", "status", "cancel", "dismiss", "click", "watch", "sound-test", "__worker")
    if argv and argv[0] not in commands and not argv[0].startswith("-"):
        argv.insert(0, "start")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Start one timer. Bare numbers mean minutes.")
    start.add_argument("duration")
    start.add_argument("label", nargs="*")
    start.add_argument("--replace", action="store_true", help="Replace the current timer.")
    start.add_argument("--silent", action="store_true", help="Do not play the completion sound.")
    start.add_argument("--sound", type=Path, default=ROOT / "assets/ding.wav")
    status = sub.add_parser("status", help="Show the timer state.")
    status.add_argument("--json", action="store_true")
    sub.add_parser("cancel", help="Remove the current timer, including a running timer.")
    sub.add_parser("dismiss", help="Remove a completed timer. Keep a running timer.")
    sub.add_parser(
        "click", help="Ask before cancelling an active timer, or dismiss a completed timer."
    )
    sub.add_parser("watch", help="Write a continuous Waybar JSON stream.")
    sound = sub.add_parser("sound-test", help="Play the completion chime now.")
    sound.add_argument("--sound", type=Path, default=ROOT / "assets/ding.wav")
    internal = sub.add_parser("__worker", help=argparse.SUPPRESS)
    internal.add_argument("descriptor", type=int)
    args = parser.parse_args(argv)
    if args.command == "sound-test":
        error = play_sound(args.sound.expanduser().resolve())
        if error:
            raise ValueError(error)
        print("The timer chime played.")
        return
    store = Store()
    if args.command == "__worker":
        worker(store, args.descriptor)
    elif args.command == "watch":
        stream(store)
    elif args.command == "click":
        click_timer(store)
    elif args.command == "start":
        seconds = duration(args.duration)
        label = " ".join(args.label)
        if len(label) > 200 or any(ord(character) < 32 for character in label):
            raise ValueError("Use a label of up to 200 characters without control characters.")
        sound_path = args.sound.expanduser().resolve()
        if not args.silent and not sound_path.is_file():
            raise ValueError(f"The sound file does not exist: {sound_path}")
        with store.lock():
            current = store.read()
            if current["status"] == "running" and not args.replace:
                raise ValueError("A timer is active. Use --replace or cancel it first.")
            state = {
                "status": "running",
                "id": uuid.uuid4().hex,
                "label": label,
                "duration": seconds,
                "end_wall": time.time() + seconds,
                "end_boot": boottime() + seconds,
                "boot_id": BOOT_ID,
                "sound": str(sound_path),
                "silent": args.silent,
            }
            store.write(state)
            ensure_worker(store)
        print(
            f"The timer started for {clock_text(math.ceil(seconds))}"
            f"{': ' + label if label else ''}."
        )
    elif args.command in ("cancel", "dismiss"):
        with store.lock():
            state = store.read()
            if args.command == "dismiss" and state["status"] == "running":
                return
            store.path.unlink(missing_ok=True)
        print("The timer is clear.")
    else:
        with store.lock():
            state = store.read()
            if state["status"] == "running":
                ensure_worker(store)
        state = {**state, "remaining_seconds": remaining(state)}
        if args.json:
            print(json.dumps(state, ensure_ascii=False))
        elif state["status"] == "idle":
            print("No timer is active.")
        elif state["status"] == "done" or state["remaining_seconds"] == 0:
            print(f"{state['label'] or 'Timer'} is complete.")
        else:
            print(f"{state['label'] or 'Timer'}: {clock_text(state['remaining_seconds'])} remains.")


if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    except (OSError, ValueError) as error:
        print(f"waybar-timer: {error}", file=sys.stderr)
        sys.exit(1)
