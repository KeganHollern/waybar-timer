"""Optional per-user popup settings. No machine settings belong in the source tree."""

import os
import tomllib
from pathlib import Path


def popup_settings():
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = config_home / "waybar-timer/config.toml"
    try:
        with path.open("rb") as source:
            document = tomllib.load(source)
    except FileNotFoundError:
        return {"anchor": "pointer"}
    settings = document.get("popup", {})
    if not isinstance(settings, dict):
        raise ValueError("The [popup] settings must be a TOML table.")
    anchor = settings.get("anchor", "pointer")
    if anchor not in ("pointer", "centered-clock"):
        raise ValueError("The popup anchor must be pointer or centered-clock.")
    result = {"anchor": anchor}
    for name, default in (("font", "monospace 14px"), ("clock_format", "%H:%M")):
        value = settings.get(name, default)
        if not isinstance(value, str) or not value:
            raise ValueError(f"The popup {name} must be a nonempty string.")
        result[name] = value
    for name in ("clock_extra_width", "timer_extra_width"):
        value = settings.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 512:
            raise ValueError(f"The popup {name} must be between 0 and 512 pixels.")
        result[name] = value
    return result
