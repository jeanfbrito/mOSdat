"""VM state inventory collector for issue confirmation harness."""

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..transport.ssh import SSHClient


@dataclass
class VmState:
    """Structured snapshot of VM environment and Rocket.Chat state."""

    # core identity
    install_method: str  # "flatpak" | "rpm" | "appimage" | "snap" | "deb"
    app_version: Optional[str] = None

    # process / runtime
    process_cmdline: Optional[str] = None  # most recent rocketchat-desktop.bin cmdline
    process_count: int = 0  # number of rocketchat-desktop processes

    # display / session
    ozone_backend: Optional[str] = None  # "x11" | "wayland" | None (parsed from cmdline)
    display_server: Optional[str] = None  # "wayland" | "x11" — from loginctl Type
    wayland_display: Optional[str] = None  # value of $WAYLAND_DISPLAY in user session
    x11_display: Optional[str] = None  # value of $DISPLAY

    # RC userdata
    config_view: Optional[str] = None  # config.json:currentView
    last_selected_server: Optional[str] = None
    migrations_version: Optional[str] = None  # config.json:__internal__.migrations.version

    # OS
    os_pretty_name: Optional[str] = None  # /etc/os-release PRETTY_NAME
    kernel: Optional[str] = None  # uname -r

    # raw outputs (debug)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)


def collect(ssh: SSHClient, install_method: str, *, timeout: int = 20) -> VmState:
    """Run the inventory bundle on the VM and return a populated VmState.

    Best-effort: each inventory command's failure populates None on its
    field but does not raise. Total wall-time capped by `timeout`.

    Args:
        ssh: Connected SSH client to the VM
        install_method: One of "flatpak", "rpm", "appimage", "snap", "deb"
        timeout: Command timeout in seconds (default 20)

    Returns:
        VmState with best-effort populated fields. Any command failure
        leaves the corresponding field(s) as None.
    """
    state = VmState(install_method=install_method)

    # Build the multi-command inventory script
    inventory_script = _build_inventory_script(install_method)

    # Run all commands in one SSH call
    result = ssh.run(inventory_script, timeout=timeout)

    if not result.success and result.returncode == 124:
        # Timeout: still try to parse what we got
        pass

    # Parse sections from the output
    sections = _parse_sections(result.stdout)

    # Extract fields from each section
    _parse_osrelease(sections.get("osrelease"), state)
    _parse_kernel(sections.get("kernel"), state)
    _parse_session(sections.get("session"), state)
    _parse_env(sections.get("env"), state)
    _parse_proc(sections.get("proc"), state)
    _parse_app_version(sections.get("app_version"), state)
    _parse_rc_config(sections.get("rc_config"), state)

    # Stash raw sections for debugging
    state.raw = sections

    return state


def _build_inventory_script(install_method: str) -> str:
    """Build the multi-section bash script for inventory."""
    script = """set -e

echo "===osrelease==="
cat /etc/os-release 2>/dev/null | grep -E '^(PRETTY_NAME|ID|VERSION_ID)=' || true

echo "===kernel==="
uname -r 2>/dev/null || true

echo "===session==="
session_id=$(loginctl list-sessions --no-legend 2>/dev/null | head -1 | awk '{print $1}')
if [ -n "$session_id" ]; then
  loginctl show-session "$session_id" -p Type 2>/dev/null || true
fi

echo "===env==="
echo "DISPLAY=$DISPLAY"
echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"

echo "===proc==="
ps -eo pid,cmd 2>/dev/null | grep -E '[r]ocketchat-desktop(\.bin)?' | head -5 || true
"""

    # Add install-method-specific commands
    if install_method == "flatpak":
        script += """
echo "===app_version==="
flatpak info chat.rocket.RocketChat 2>/dev/null | grep -E '^[[:space:]]*Version:' | awk '{print $2}' || true

echo "===rc_config==="
cat ~/.var/app/chat.rocket.RocketChat/config/Rocket.Chat/config.json 2>/dev/null || true
"""
    elif install_method == "rpm":
        script += """
echo "===app_version==="
rpm -q rocketchat --queryformat '%{VERSION}' 2>/dev/null || true

echo "===rc_config==="
cat ~/.config/Rocket.Chat/config.json 2>/dev/null || true
"""
    elif install_method == "deb":
        script += """
echo "===app_version==="
dpkg-query -f '${Version}' -W rocketchat 2>/dev/null || true

echo "===rc_config==="
cat ~/.config/Rocket.Chat/config.json 2>/dev/null || true
"""
    elif install_method == "snap":
        script += """
echo "===app_version==="
snap info rocketchat-desktop 2>/dev/null | grep -E '^installed:' | awk '{print $2}' || true

echo "===rc_config==="
cat "$HOME"/snap/rocketchat-desktop/current/.config/Rocket.Chat/config.json 2>/dev/null || true
"""
    elif install_method == "appimage":
        script += """
echo "===app_version==="
ls -1 ~/.cache/appimage-* 2>/dev/null | head -1 || true

echo "===rc_config==="
cat ~/.config/Rocket.Chat/config.json 2>/dev/null || true
"""

    return script


def _parse_sections(output: str) -> dict[str, str]:
    """Split multi-section output by ===key=== markers."""
    sections = {}
    current_key = None
    current_lines = []

    for line in output.split("\n"):
        match = re.match(r"^===(\w+)===$", line)
        if match:
            # Save previous section
            if current_key is not None:
                sections[current_key] = "\n".join(current_lines).strip()
            # Start new section
            current_key = match.group(1)
            current_lines = []
        else:
            if current_key is not None:
                current_lines.append(line)

    # Save final section
    if current_key is not None:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def _parse_osrelease(text: Optional[str], state: VmState) -> None:
    """Extract PRETTY_NAME from /etc/os-release output."""
    if not text:
        return
    match = re.search(r'^PRETTY_NAME=(.*)$', text, re.MULTILINE)
    if match:
        value = match.group(1).strip().strip('"\'')
        state.os_pretty_name = value


def _parse_kernel(text: Optional[str], state: VmState) -> None:
    """Extract kernel version from uname -r output."""
    if not text:
        return
    state.kernel = text.strip()


def _parse_session(text: Optional[str], state: VmState) -> None:
    """Extract display server type from loginctl show-session Type=..."""
    if not text:
        return
    match = re.search(r'^Type=(\w+)$', text, re.MULTILINE)
    if match:
        state.display_server = match.group(1).lower()


def _parse_env(text: Optional[str], state: VmState) -> None:
    """Extract DISPLAY and WAYLAND_DISPLAY from env output."""
    if not text:
        return
    for line in text.split("\n"):
        if line.startswith("DISPLAY="):
            value = line.split("=", 1)[1].strip()
            if value:
                state.x11_display = value
        elif line.startswith("WAYLAND_DISPLAY="):
            value = line.split("=", 1)[1].strip()
            if value:
                state.wayland_display = value


def _parse_proc(text: Optional[str], state: VmState) -> None:
    """Extract process count and cmdline from ps output."""
    if not text:
        return

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    state.process_count = len(lines)

    if lines:
        # Take first line's full cmdline
        first_line = lines[0]
        # ps -eo pid,cmd outputs: pid command...
        # Extract everything after the first field (PID)
        parts = first_line.split(None, 1)
        if len(parts) > 1:
            state.process_cmdline = parts[1]

        # Extract ozone backend from cmdline
        if state.process_cmdline:
            match = re.search(r'--ozone-platform=(\w+)', state.process_cmdline)
            if match:
                state.ozone_backend = match.group(1)


def _parse_app_version(text: Optional[str], state: VmState) -> None:
    """Extract app version from install-method-specific command."""
    if not text:
        return
    state.app_version = text.strip()


def _parse_rc_config(text: Optional[str], state: VmState) -> None:
    """Extract currentView, lastSelectedServerUrl, and migrations version from config.json."""
    if not text:
        return

    try:
        config = json.loads(text)
        state.config_view = config.get("currentView")
        state.last_selected_server = config.get("lastSelectedServerUrl")

        # Navigate __internal__.migrations.version
        internal = config.get("__internal__", {})
        migrations = internal.get("migrations", {})
        state.migrations_version = migrations.get("version")
    except (json.JSONDecodeError, TypeError):
        # Best-effort: if JSON is malformed, leave fields as None
        # The raw section is already stashed for debugging
        pass
