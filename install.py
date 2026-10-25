#!/usr/bin/env python3
"""Install Waybar Timer for the current user without changing Waybar settings."""

import argparse
import datetime as dt
import hashlib
import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
RUNTIME_FILES = (
    "waybar_timer.py",
    "timer_popup.py",
    "settings.py",
    "assets/ding.wav",
    "assets/popup.css",
    "assets/popup-border.svg",
    "LICENSE",
)
MANIFEST = "install-manifest.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(target, data, mode=0o644):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".waybar-timer-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, mode)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def default_data_dir():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "waybar-timer"


def install(bin_dir=None, data_dir=None, with_skill=False, skill_dir=None, force=False):
    bin_dir = Path(bin_dir or Path.home() / ".local/bin").expanduser().absolute()
    data_dir = Path(data_dir or default_data_dir()).expanduser().absolute()
    skill_dir = Path(skill_dir or Path.home() / ".agents/skills").expanduser().absolute()
    manifest_path = data_dir / MANIFEST
    previous = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {"files": {}}
    if previous.get("schema", 1) != 1:
        raise ValueError("The installation manifest has an unsupported version.")
    operations = {}
    roles = {}
    for name in RUNTIME_FILES:
        source = SOURCE / name
        if not source.is_file():
            raise ValueError(f"The source file is absent: {source}")
        target = data_dir / name
        operations[target] = (source.read_bytes(), 0o644)
        roles[str(target)] = "runtime"
    command = bin_dir / "waybar-timer"
    wrapper = (
        "#!/bin/sh\nexport PYTHONDONTWRITEBYTECODE=1\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(data_dir / "waybar_timer.py"))
        + ' "$@"\n'
    )
    operations[command] = (wrapper.encode(), 0o755)
    roles[str(command)] = "command"
    if with_skill:
        target = skill_dir / "desktop-timer/SKILL.md"
        operations[target] = ((SOURCE / "skills/desktop-timer/SKILL.md").read_bytes(), 0o644)
        roles[str(target)] = "skill"

    # An unrelated command or a locally edited installation needs an explicit --force.
    for target, (data, _) in operations.items():
        if target.is_symlink():
            if not force:
                raise ValueError(
                    f"The destination is a symbolic link: {target}. Use --force to replace it."
                )
        elif target.exists():
            if not target.is_file():
                raise ValueError(f"The destination is not a file: {target}")
            old_hash = digest(target.read_bytes())
            known_hash = previous.get("files", {}).get(str(target), {}).get("sha256")
            if old_hash not in (known_hash, digest(data)) and not force:
                raise ValueError(
                    f"The destination contains other or edited files: {target}. Use --force to replace it."
                )

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    backup = state_home / "waybar-timer/install-backups" / stamp
    saved = {}
    for index, target in enumerate((*operations, manifest_path)):
        if target.is_symlink():
            saved[target] = ("link", os.readlink(target), None)
        elif target.is_file():
            backup.mkdir(parents=True, exist_ok=True)
            filename = backup / str(index)
            shutil.copy2(target, filename)
            saved[target] = ("file", filename, target.stat().st_mode & 0o777)
        else:
            saved[target] = ("absent", None, None)
    if any(kind != "absent" for kind, _, _ in saved.values()):
        backup.mkdir(parents=True, exist_ok=True)
        records = [
            {"path": str(target), "kind": kind, "saved": str(value) if value is not None else None}
            for target, (kind, value, _) in saved.items()
        ]
        (backup / "files.json").write_text(json.dumps(records, indent=2) + "\n")

    files = {
        str(target): {"sha256": digest(data), "role": roles[str(target)]}
        for target, (data, _) in operations.items()
    }
    # Keep ownership of an optional skill when a later update omits --with-skill.
    for name, record in previous.get("files", {}).items():
        if record.get("role") == "skill" and name not in files:
            files[name] = record
    manifest = {
        "schema": 1,
        "files": files,
        "python": sys.executable,
        "application_dir": str(data_dir),
        "command": str(command),
    }
    try:
        for target, (data, mode) in operations.items():
            atomic_write(target, data, mode)
        atomic_write(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
    except BaseException:
        for target, (kind, value, mode) in reversed(tuple(saved.items())):
            target.unlink(missing_ok=True)
            if kind == "file":
                atomic_write(target, value.read_bytes(), mode)
            elif kind == "link":
                target.symlink_to(value)
        raise
    print(f"Installed the command: {command}")
    print(f"Installed the runtime files: {data_dir}")
    if with_skill:
        print(f"Installed a regular skill copy: {skill_dir / 'desktop-timer/SKILL.md'}")
    if backup.exists():
        print(f"Saved the previous files: {backup}")
    print("Add the module and stylesheet from examples to your Waybar settings.")
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, help="Command directory. Default: ~/.local/bin.")
    parser.add_argument(
        "--data-dir", type=Path, help="Runtime directory. Default: $XDG_DATA_HOME/waybar-timer."
    )
    parser.add_argument(
        "--with-skill", action="store_true", help="Copy the agent skill into ~/.agents/skills."
    )
    parser.add_argument(
        "--skill-dir", type=Path, help="Skill parent directory; used with --with-skill."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Back up and replace modified files or an old command link.",
    )
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.exit(1, "Waybar Timer needs Python 3.11 or later.\n")
    try:
        install(args.bin_dir, args.data_dir, args.with_skill, args.skill_dir, args.force)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
