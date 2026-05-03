"""Launch-step name helpers for functional UI runs."""

import ntpath
import os
import shlex
from typing import Optional


def launch_probe_name(launch: str, launch_window: Optional[str], is_windows: bool) -> str:
    """Return the process/window name used to verify a launch step."""
    if launch_window:
        return launch_window

    try:
        parts = shlex.split(launch, posix=not is_windows)
    except ValueError:
        parts = launch.split()
    if not parts:
        return ""

    if len(parts) >= 3 and parts[0] == "flatpak" and parts[1] == "run":
        return parts[2].rsplit(".", 1)[-1]

    launch_first = parts[0]
    if is_windows:
        return ntpath.basename(launch_first)
    return os.path.basename(launch_first)


def natural_window_name(probe_name: str) -> str:
    """Strip common binary suffixes for natural-language VLM verification."""
    natural = probe_name
    for suffix in ("-desktop", "-bin", ".exe", "-electron", ".AppImage"):
        if natural.lower().endswith(suffix.lower()):
            return natural[: -len(suffix)]
    return natural
