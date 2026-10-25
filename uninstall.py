#!/usr/bin/env python3
"""Remove a user installation while keeping timer state and Waybar settings."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from install import MANIFEST, default_data_dir


def uninstall(data_dir=None, keep_skill=False):
    data_dir = Path(data_dir or default_data_dir()).expanduser().absolute()
    manifest_path = data_dir / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != 1 or manifest.get("application_dir") != str(data_dir):
        raise ValueError("The installation manifest does not match this directory.")
    targets = []
    for name, record in manifest["files"].items():
        if keep_skill and record["role"] == "skill":
            continue
        target = Path(name)
        if not target.exists() and not target.is_symlink():
            continue
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"The installed file changed: {target}")
        if hashlib.sha256(target.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(
                f"The installed file changed: {target}. Keep or restore that file before uninstalling."
            )
        targets.append(target)
    entry = data_dir / "waybar_timer.py"
    if entry.is_file():
        result = subprocess.run(
            [manifest["python"], "-B", str(entry), "cancel"], capture_output=True, text=True
        )
        if result.returncode:
            raise ValueError(f"The timer could not stop: {result.stderr.strip()}")
    for target in targets:
        target.unlink()
    manifest_path.unlink()
    for directory in sorted((item for item in data_dir.rglob("*") if item.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        data_dir.rmdir()
    except OSError:
        pass
    print("Removed the installed files. Timer state and Waybar settings remain unchanged.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, help="Use the same runtime directory as the installer."
    )
    parser.add_argument("--keep-skill", action="store_true", help="Keep the personal agent skill.")
    args = parser.parse_args()
    try:
        uninstall(args.data_dir, args.keep_skill)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
