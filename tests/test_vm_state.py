"""Tests for VM state inventory collector."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from dataclasses import dataclass

from automation.runners.vm_state import VmState, collect


# Local stub for SSH result — avoids importing from automation.transport.ssh
# which gets module-stubbed by other test files (test_runner_dispatch,
# test_concurrent_safety, test_proxmox_vm) when the full suite runs.
@dataclass
class SSHResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


class MockSSHClient:
    """Stub SSH client for testing."""

    def __init__(self, response: str = "", returncode: int = 0, should_timeout: bool = False):
        self.response = response
        self.returncode = returncode
        self.should_timeout = should_timeout
        self.last_command = None

    def run(self, command: str, timeout: int = None):
        """Simulate SSH command execution."""
        self.last_command = command
        if self.should_timeout:
            return SSHResult(returncode=124, stdout=self.response, stderr="SSH command timed out")
        return SSHResult(returncode=self.returncode, stdout=self.response, stderr="")


@pytest.fixture
def fedora42_flatpak_response():
    """Real-world response from fedora42 flatpak install."""
    return """===osrelease===
PRETTY_NAME="Fedora Linux 42 (Workstation Edition)"
ID=fedora
VERSION_ID=42

===kernel===
6.8.0-110-generic

===session===
Type=wayland

===env===
DISPLAY=
WAYLAND_DISPLAY=wayland-1

===proc===
 1234 /usr/bin/rocketchat-desktop.bin --disable-gpu --ozone-platform=x11
 1256 /usr/bin/rocketchat-desktop.bin --disable-gpu --ozone-platform=x11 --background

===app_version===
4.14.0

===rc_config===
{"currentView":"add-new-server","lastSelectedServerUrl":"https://example.com/","__internal__":{"migrations":{"version":"4.14.0"}}}
"""


@pytest.fixture
def ubuntu_deb_response():
    """Response from ubuntu deb install."""
    return """===osrelease===
PRETTY_NAME="Ubuntu 24.04.1 LTS"
ID=ubuntu
VERSION_ID=24.04

===kernel===
6.8.0-45-generic

===session===
Type=x11

===env===
DISPLAY=:0
WAYLAND_DISPLAY=

===proc===
 2345 /usr/bin/rocketchat-desktop.bin --ozone-platform=auto

===app_version===
4.13.0

===rc_config===
{"currentView":"room","lastSelectedServerUrl":"https://rocket.chat/","__internal__":{"migrations":{"version":"4.13.0"}}}
"""


@pytest.fixture
def minimal_response_no_config():
    """Response with missing rc_config section (app not launched yet)."""
    return """===osrelease===
PRETTY_NAME="Fedora Linux 42"

===kernel===
6.8.0-110-generic

===session===
Type=x11

===env===
DISPLAY=:0
WAYLAND_DISPLAY=

===proc===

===app_version===
4.14.0
"""


@pytest.fixture
def malformed_json_response():
    """Response with malformed JSON in rc_config."""
    return """===osrelease===
PRETTY_NAME="Test OS"

===kernel===
6.8.0-test

===session===
Type=wayland

===env===
DISPLAY=
WAYLAND_DISPLAY=wayland-0

===proc===
 9999 /usr/bin/rocketchat-desktop.bin

===app_version===
4.14.0

===rc_config===
{"currentView":"add-new-server","invalid": json here }
"""


class TestCollectFedora42Flatpak:
    """Test collecting state from fedora42 flatpak."""

    def test_basic_fields(self, fedora42_flatpak_response):
        ssh = MockSSHClient(fedora42_flatpak_response)
        state = collect(ssh, "flatpak")

        assert state.install_method == "flatpak"
        assert state.os_pretty_name == "Fedora Linux 42 (Workstation Edition)"
        assert state.kernel == "6.8.0-110-generic"
        assert state.display_server == "wayland"
        assert state.x11_display is None  # empty DISPLAY is not set
        assert state.wayland_display == "wayland-1"

    def test_process_info(self, fedora42_flatpak_response):
        ssh = MockSSHClient(fedora42_flatpak_response)
        state = collect(ssh, "flatpak")

        assert state.process_count == 2
        assert state.process_cmdline is not None
        assert "rocketchat-desktop.bin" in state.process_cmdline
        assert "--ozone-platform=x11" in state.process_cmdline

    def test_ozone_backend_extraction(self, fedora42_flatpak_response):
        ssh = MockSSHClient(fedora42_flatpak_response)
        state = collect(ssh, "flatpak")

        assert state.ozone_backend == "x11"

    def test_app_version(self, fedora42_flatpak_response):
        ssh = MockSSHClient(fedora42_flatpak_response)
        state = collect(ssh, "flatpak")

        assert state.app_version == "4.14.0"

    def test_rc_config_parsing(self, fedora42_flatpak_response):
        ssh = MockSSHClient(fedora42_flatpak_response)
        state = collect(ssh, "flatpak")

        assert state.config_view == "add-new-server"
        assert state.last_selected_server == "https://example.com/"
        assert state.migrations_version == "4.14.0"

    def test_raw_sections_stashed(self, fedora42_flatpak_response):
        ssh = MockSSHClient(fedora42_flatpak_response)
        state = collect(ssh, "flatpak")

        assert "osrelease" in state.raw
        assert "rc_config" in state.raw
        assert "PRETTY_NAME" in state.raw["osrelease"]


class TestCollectUbuntuDeb:
    """Test collecting state from ubuntu deb install."""

    def test_deb_install_method(self, ubuntu_deb_response):
        ssh = MockSSHClient(ubuntu_deb_response)
        state = collect(ssh, "deb")

        assert state.install_method == "deb"
        assert state.os_pretty_name == "Ubuntu 24.04.1 LTS"
        assert state.app_version == "4.13.0"

    def test_x11_environment(self, ubuntu_deb_response):
        ssh = MockSSHClient(ubuntu_deb_response)
        state = collect(ssh, "deb")

        assert state.display_server == "x11"
        assert state.x11_display == ":0"
        assert state.wayland_display is None  # empty env var → None (no Wayland session)


class TestMissingRcConfig:
    """Test graceful handling of missing rc_config section."""

    def test_missing_section_no_crash(self, minimal_response_no_config):
        ssh = MockSSHClient(minimal_response_no_config)
        state = collect(ssh, "flatpak")

        # Should not raise; fields just remain None
        assert state.config_view is None
        assert state.last_selected_server is None
        assert state.migrations_version is None

    def test_other_fields_still_populated(self, minimal_response_no_config):
        ssh = MockSSHClient(minimal_response_no_config)
        state = collect(ssh, "flatpak")

        # But non-config fields should still be populated
        assert state.os_pretty_name == "Fedora Linux 42"
        assert state.kernel == "6.8.0-110-generic"
        assert state.app_version == "4.14.0"

    def test_empty_process_list(self, minimal_response_no_config):
        ssh = MockSSHClient(minimal_response_no_config)
        state = collect(ssh, "flatpak")

        assert state.process_count == 0
        assert state.process_cmdline is None


class TestMalformedJson:
    """Test best-effort JSON parsing with malformed data."""

    def test_malformed_config_no_crash(self, malformed_json_response):
        ssh = MockSSHClient(malformed_json_response)
        state = collect(ssh, "flatpak")

        # Should not raise; config fields remain None
        assert state.config_view is None
        assert state.last_selected_server is None
        assert state.migrations_version is None

    def test_raw_malformed_stashed(self, malformed_json_response):
        ssh = MockSSHClient(malformed_json_response)
        state = collect(ssh, "flatpak")

        # Raw text should still be available for debugging
        assert "rc_config" in state.raw
        assert "invalid" in state.raw["rc_config"]


class TestTimeoutHandling:
    """Test SSH timeout handling."""

    def test_timeout_returns_partial_state(self, fedora42_flatpak_response):
        # Partial response (some sections only)
        partial = """===osrelease===
PRETTY_NAME="Fedora Linux 42"

===kernel===
6.8.0-110-generic
"""
        ssh = MockSSHClient(partial, returncode=124)
        state = collect(ssh, "flatpak")

        # Should return best-effort state with whatever was available
        assert state.os_pretty_name == "Fedora Linux 42"
        assert state.kernel == "6.8.0-110-generic"
        # Missing sections remain None
        assert state.app_version is None


class TestSerialization:
    """Test VmState serialization methods."""

    def test_to_dict(self, fedora42_flatpak_response):
        ssh = MockSSHClient(fedora42_flatpak_response)
        state = collect(ssh, "flatpak")

        d = state.to_dict()
        assert isinstance(d, dict)
        assert d["install_method"] == "flatpak"
        assert d["os_pretty_name"] == "Fedora Linux 42 (Workstation Edition)"
        assert d["process_count"] == 2

    def test_to_json(self, fedora42_flatpak_response):
        ssh = MockSSHClient(fedora42_flatpak_response)
        state = collect(ssh, "flatpak")

        json_str = state.to_json()
        assert isinstance(json_str, str)

        # Should be valid JSON
        parsed = json.loads(json_str)
        assert parsed["install_method"] == "flatpak"
        assert parsed["os_pretty_name"] == "Fedora Linux 42 (Workstation Edition)"

    def test_to_json_indentation(self, fedora42_flatpak_response):
        ssh = MockSSHClient(fedora42_flatpak_response)
        state = collect(ssh, "flatpak")

        json_str = state.to_json(indent=4)
        # Should contain newlines (indented)
        assert "\n" in json_str


class TestInstallMethods:
    """Test inventory script generation for different install methods."""

    def test_flatpak_script_contains_flatpak_commands(self):
        from automation.runners.vm_state import _build_inventory_script

        script = _build_inventory_script("flatpak")
        assert "flatpak info chat.rocket.RocketChat" in script
        assert "~/.var/app/chat.rocket.RocketChat/config/Rocket.Chat/config.json" in script

    def test_rpm_script_contains_rpm_commands(self):
        from automation.runners.vm_state import _build_inventory_script

        script = _build_inventory_script("rpm")
        assert "rpm -q rocketchat" in script
        assert "~/.config/Rocket.Chat/config.json" in script

    def test_deb_script_contains_dpkg_commands(self):
        from automation.runners.vm_state import _build_inventory_script

        script = _build_inventory_script("deb")
        assert "dpkg-query" in script
        assert "~/.config/Rocket.Chat/config.json" in script

    def test_snap_script_contains_snap_commands(self):
        from automation.runners.vm_state import _build_inventory_script

        script = _build_inventory_script("snap")
        assert "snap info rocketchat-desktop" in script
        assert '"$HOME"/snap/rocketchat-desktop/current/.config' in script

    def test_appimage_script_contains_appimage_commands(self):
        from automation.runners.vm_state import _build_inventory_script

        script = _build_inventory_script("appimage")
        assert "~/.cache/appimage-" in script
        assert "~/.config/Rocket.Chat/config.json" in script

    def test_all_scripts_contain_common_section(self):
        from automation.runners.vm_state import _build_inventory_script

        for method in ["flatpak", "rpm", "deb", "snap", "appimage"]:
            script = _build_inventory_script(method)
            assert "===osrelease===" in script
            assert "===kernel===" in script
            assert "===session===" in script
            assert "===env===" in script
            assert "===proc===" in script


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_response(self):
        ssh = MockSSHClient("")
        state = collect(ssh, "flatpak")

        assert state.install_method == "flatpak"
        # All other fields should be None/0
        assert state.os_pretty_name is None
        assert state.app_version is None
        assert state.process_count == 0

    def test_ozone_backend_not_in_cmdline(self):
        response = """===osrelease===
PRETTY_NAME="Test OS"

===kernel===
6.8.0-test

===session===
Type=x11

===env===
DISPLAY=:0
WAYLAND_DISPLAY=

===proc===
 5000 /usr/bin/rocketchat-desktop.bin --some-other-flag

===app_version===
4.14.0

===rc_config===
{"currentView":"room"}
"""
        ssh = MockSSHClient(response)
        state = collect(ssh, "flatpak")

        assert state.ozone_backend is None
        assert state.process_cmdline is not None

    def test_ozone_platform_wayland(self):
        response = """===osrelease===
PRETTY_NAME="Test OS"

===session===
Type=wayland

===env===
DISPLAY=
WAYLAND_DISPLAY=wayland-0

===proc===
 5000 /usr/bin/rocketchat-desktop.bin --ozone-platform=wayland
"""
        ssh = MockSSHClient(response)
        state = collect(ssh, "flatpak")

        assert state.ozone_backend == "wayland"

    def test_multiple_processes_takes_first(self):
        response = """===proc===
 1000 /usr/bin/rocketchat-desktop.bin --flag1
 2000 /usr/bin/rocketchat-desktop.bin --flag2 --flag3
 3000 /usr/bin/rocketchat-desktop.bin
"""
        ssh = MockSSHClient(response)
        state = collect(ssh, "flatpak")

        assert state.process_count == 3
        # Should take the first process's cmdline
        assert state.process_cmdline is not None
        assert "1000" not in state.process_cmdline  # PID should be stripped
        assert "rocketchat-desktop.bin" in state.process_cmdline
        assert "--flag1" in state.process_cmdline

    def test_config_json_with_nested_structure(self):
        config_json = {
            "currentView": "room",
            "lastSelectedServerUrl": "https://example.com",
            "__internal__": {
                "migrations": {"version": "4.14.0"},
                "other": "data"
            }
        }
        response = f"""===rc_config===
{json.dumps(config_json)}
"""
        ssh = MockSSHClient(response)
        state = collect(ssh, "flatpak")

        assert state.config_view == "room"
        assert state.migrations_version == "4.14.0"

    def test_quoted_pretty_name(self):
        response = """===osrelease===
PRETTY_NAME="Fedora Linux 42 (with \\"quotes\\")"

===kernel===
6.8.0-110-generic
"""
        ssh = MockSSHClient(response)
        state = collect(ssh, "flatpak")

        # Should strip quotes
        assert "PRETTY_NAME=" not in state.os_pretty_name
        assert state.os_pretty_name.startswith("Fedora")
