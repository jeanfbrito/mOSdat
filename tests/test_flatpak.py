"""Tests for J1/J3 — Flatpak package format support in config.py and functional.py.

Covers:
  J1a — Package dataclass accepts flatpak-specific fields (appid, remote, data_dir)
  J1b — load_config parses a Flatpak package entry from TOML without error
  J1c — Flatpak entry in rocketchat.toml is present for fedora42/ubuntu2404/manjaro
  J1d — FunctionalRunner launch step: flatpak run <appid> → app_basename from appid
  J1e — FunctionalRunner launch step: flatpak run + launch_window → launch_window wins
  J3a — Package.effective_version returns per-package version when set
  J3b — Package.effective_version falls back to app version when not set
"""

import sys
import types
import importlib.util
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Load config.py directly (avoid __init__ chain)
# ---------------------------------------------------------------------------

_cspec = importlib.util.spec_from_file_location(
    "automation.config",
    _PROJ / "automation" / "config.py",
)
_cmod = importlib.util.module_from_spec(_cspec)
_cmod.__package__ = "automation"
sys.modules["automation.config"] = _cmod
_cspec.loader.exec_module(_cmod)

Package = _cmod.Package
load_config = _cmod.load_config


# ---------------------------------------------------------------------------
# Load functional.py (for launch-step Flatpak basename tests)
# ---------------------------------------------------------------------------

for _sn in ("automation.vlm.client", "automation.vlm.input", "automation.vlm.screenshot"):
    if _sn not in sys.modules:
        _m = types.ModuleType(_sn)
        _m.VLMClient = object
        _m.InputInjector = object
        _m.Screenshotter = object
        _m.VLMError = Exception
        sys.modules[_sn] = _m

if "automation.vlm.agent" not in sys.modules:
    _agent_mod = types.ModuleType("automation.vlm.agent")
    _agent_mod.AgentLoop = MagicMock
    sys.modules["automation.vlm.agent"] = _agent_mod

_fspec = importlib.util.spec_from_file_location(
    "automation.runners.functional",
    _PROJ / "automation" / "runners" / "functional.py",
)
_fmod = importlib.util.module_from_spec(_fspec)
_fmod.__package__ = "automation.runners"
sys.modules["automation.runners.functional"] = _fmod
_fspec.loader.exec_module(_fmod)

FunctionalRunner = _fmod.FunctionalRunner
FunctionalStep = _fmod.FunctionalStep
StepFailed = _fmod.StepFailed


# ---------------------------------------------------------------------------
# J1a — Package dataclass: flatpak fields parse without error
# ---------------------------------------------------------------------------

class TestPackageDataclass:
    def test_flatpak_fields_accepted(self):
        pkg = Package(
            format="flatpak",
            install_cmd="flatpak install --user --noninteractive flathub chat.rocket.RocketChat//4.14.0",
            uninstall_cmd="flatpak uninstall --user --noninteractive chat.rocket.RocketChat -y || true",
            app_path="flatpak run chat.rocket.RocketChat",
            process_name="rocketchat-desktop",
            appid="chat.rocket.RocketChat",
            remote="flathub",
            data_dir="~/.var/app/chat.rocket.RocketChat",
        )
        assert pkg.format == "flatpak"
        assert pkg.appid == "chat.rocket.RocketChat"
        assert pkg.remote == "flathub"
        assert pkg.data_dir == "~/.var/app/chat.rocket.RocketChat"

    def test_non_flatpak_package_appid_defaults_none(self):
        pkg = Package(
            format="deb",
            install_cmd="sudo apt install -y /tmp/app.deb",
        )
        assert pkg.appid is None
        assert pkg.remote is None
        assert pkg.data_dir is None


# ---------------------------------------------------------------------------
# J1b — load_config parses Flatpak entry from inline TOML
# ---------------------------------------------------------------------------

class TestLoadConfigFlatpak:
    def test_flatpak_package_parsed(self, tmp_path, monkeypatch):
        toml_content = textwrap.dedent("""\
            [app]
            name = "testapp"
            version = "1.0.0"
            binary = "/usr/bin/testapp"

            [proxmox]
            host = "192.168.1.1"
            password = "secret"

            [[vm]]
            name = "testvm"
            vmid = 100
            ip = "192.168.1.10"

            [[vm.package]]
            format = "flatpak"
            appid = "chat.rocket.RocketChat"
            remote = "flathub"
            install = "flatpak install --user --noninteractive flathub chat.rocket.RocketChat//{version}"
            uninstall = "flatpak uninstall --user --noninteractive chat.rocket.RocketChat -y || true"
            app_path = "flatpak run chat.rocket.RocketChat"
            process_name = "rocketchat-desktop"
            data_dir = "~/.var/app/chat.rocket.RocketChat"
        """)
        cfg_file = tmp_path / "test.toml"
        cfg_file.write_text(toml_content)

        cfg = load_config(cfg_file)
        assert len(cfg.vms) == 1
        pkgs = cfg.vms[0].packages
        assert len(pkgs) == 1
        pkg = pkgs[0]
        assert pkg.format == "flatpak"
        assert pkg.appid == "chat.rocket.RocketChat"
        assert pkg.remote == "flathub"
        assert pkg.data_dir == "~/.var/app/chat.rocket.RocketChat"
        assert pkg.process_name == "rocketchat-desktop"
        assert pkg.app_path == "flatpak run chat.rocket.RocketChat"
        assert "{version}" in pkg.install_cmd


# ---------------------------------------------------------------------------
# J1c — rocketchat.toml contains Flatpak entries for fedora42/ubuntu2404/manjaro
# ---------------------------------------------------------------------------

class TestRocketchatTomlFlatpak:
    _toml_path = _PROJ / "examples" / "rocketchat.toml"

    def _load_raw(self):
        import tomllib
        with open(self._toml_path, "rb") as f:
            return tomllib.load(f)

    @pytest.mark.parametrize("vm_name", ["fedora42", "ubuntu2404", "manjaro"])
    def test_vm_has_flatpak_package(self, vm_name):
        raw = self._load_raw()
        vm = next((v for v in raw["vm"] if v["name"] == vm_name), None)
        assert vm is not None, f"VM '{vm_name}' not found in rocketchat.toml"
        formats = [p["format"] for p in vm.get("package", [])]
        assert "flatpak" in formats, f"VM '{vm_name}' has no flatpak package entry"

    @pytest.mark.parametrize("vm_name", ["fedora42", "ubuntu2404", "manjaro"])
    def test_flatpak_entry_has_required_fields(self, vm_name):
        raw = self._load_raw()
        vm = next((v for v in raw["vm"] if v["name"] == vm_name), None)
        pkg = next((p for p in vm.get("package", []) if p["format"] == "flatpak"), None)
        assert pkg is not None
        assert pkg.get("appid") == "chat.rocket.RocketChat"
        assert pkg.get("remote") == "flathub"
        assert "{version}" in pkg.get("install", "")
        assert "flatpak run" in pkg.get("app_path", "")


# ---------------------------------------------------------------------------
# J1d — FunctionalRunner: flatpak run <appid> → app_basename from appid tail
# ---------------------------------------------------------------------------

class TestFlatpakLaunchBasename:
    def _make_runner(self, is_windows=False):
        vlm = MagicMock()
        screenshotter = MagicMock()
        injector = MagicMock()
        injector.is_windows = is_windows
        return FunctionalRunner(
            vlm=vlm,
            screenshotter=screenshotter,
            injector=injector,
            screenshot_dir=None,
        )

    def test_flatpak_run_uses_appid_tail_as_basename(self, tmp_path):
        """When launch='flatpak run chat.rocket.RocketChat' and no launch_window,
        app_basename should be 'RocketChat' (last appid segment)."""
        runner = self._make_runner()
        # wait>0 triggers the process+window polling loop where process_running is called
        step = FunctionalStep(
            launch="flatpak run chat.rocket.RocketChat",
            wait=5,
            launch_window=None,
        )
        # We expect injector.launch to be called; process_running will be mocked
        runner.injector.launch = MagicMock()
        runner.injector.process_running = MagicMock(return_value=True)
        runner.screenshotter.capture = MagicMock(return_value=(MagicMock(), (1920, 1080)))
        runner.vlm.verify = MagicMock(return_value=True)

        runner.run_step(step, 1)

        # process_running should have been called with 'RocketChat' (appid tail),
        # not 'flatpak'
        calls = [call.args[0] for call in runner.injector.process_running.call_args_list]
        assert "RocketChat" in calls, f"Expected 'RocketChat' in process_running calls, got: {calls}"
        assert "flatpak" not in calls, "'flatpak' should not appear in process_running calls"

    def test_flatpak_run_launch_window_overrides_basename(self, tmp_path):
        """When launch_window is set, it overrides the appid-derived basename."""
        runner = self._make_runner()
        # wait>0 triggers the process+window polling loop where process_running is called
        step = FunctionalStep(
            launch="flatpak run chat.rocket.RocketChat",
            wait=5,
            launch_window="rocketchat-desktop",
        )
        runner.injector.launch = MagicMock()
        runner.injector.process_running = MagicMock(return_value=True)
        runner.screenshotter.capture = MagicMock(return_value=(MagicMock(), (1920, 1080)))
        runner.vlm.verify = MagicMock(return_value=True)

        runner.run_step(step, 1)

        calls = [call.args[0] for call in runner.injector.process_running.call_args_list]
        assert "rocketchat-desktop" in calls, (
            f"Expected 'rocketchat-desktop' in process_running calls, got: {calls}"
        )


# ---------------------------------------------------------------------------
# J3a/J3b — Package.effective_version
# ---------------------------------------------------------------------------

class TestEffectiveVersion:
    def test_per_package_version_overrides_app(self):
        pkg = Package(
            format="flatpak",
            install_cmd="flatpak install flathub chat.rocket.RocketChat//{version}",
            version="4.14.0",
        )
        assert pkg.effective_version("4.12.0") == "4.14.0"

    def test_falls_back_to_app_version_when_not_set(self):
        pkg = Package(
            format="flatpak",
            install_cmd="flatpak install flathub chat.rocket.RocketChat//{version}",
        )
        assert pkg.effective_version("4.12.0") == "4.12.0"

    def test_version_substitution_in_install_cmd(self):
        pkg = Package(
            format="flatpak",
            install_cmd="flatpak install --user --noninteractive flathub chat.rocket.RocketChat//{version}",
            version="4.14.0",
        )
        v = pkg.effective_version("4.12.0")
        result = pkg.install_cmd.replace("{version}", v)
        assert "4.14.0" in result
        assert "4.12.0" not in result
