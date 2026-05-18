"""Unit + integration tests for I1 — ``automation.setup.inject_config``.

The unit tests use a ``MockSSH`` recorder that captures every command and
returns scripted stdout. The integration smoke test is gated by
``MOSDAT_TEST_VM`` and skipped by default.
"""

from __future__ import annotations


# Clear sibling-test stub pollution BEFORE importing automation.*.
# Sibling tests (test_negative, test_concurrent_safety, test_proxmox_vm) stub
# heavy modules in sys.modules at their module import time, which runs during
# pytest collection — before our imports. Pop those stubs so we get the real
# modules below.
import sys as _sys
for _name in list(_sys.modules):
    if (
        _name.startswith("automation.transport")
        or _name.startswith("automation.vlm")
        or _name in (
            "openai",
            "httpx",
            "automation.config",
            "automation.proxmox.api",
            "automation.proxmox.vm",
            "automation.reporting.report",
        )
    ):
        _sys.modules.pop(_name, None)

import json
import os
import shlex
from dataclasses import dataclass, field
from typing import Optional

import pytest

from automation.setup.inject_config import (
    _asar_path_from_install_path,
    _merge_config,
    detect_app_version,
    detect_userdata_dirs,
    inject,
    parse_inject_config,
    parse_inject_servers,
    wipe_userdata,
    write_config_json,
    write_servers_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _CannedResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class MockSSH:
    """Records every command; returns canned stdout for ``returns`` keys
    that appear as a substring of the command. First-match wins.
    """

    returns: list[tuple[str, _CannedResult]] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def run(self, command: str, timeout: Optional[int] = None) -> _CannedResult:
        self.commands.append(command)
        for needle, result in self.returns:
            if needle in command:
                return result
        return _CannedResult()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_asar_path_from_binary_path():
    # binary path → strip basename, append resources/app.asar
    assert (
        _asar_path_from_install_path("/opt/Rocket.Chat/rocketchat-desktop")
        == "/opt/Rocket.Chat/resources/app.asar"
    )


def test_asar_path_from_dir_path():
    assert (
        _asar_path_from_install_path("/opt/Rocket.Chat")
        == "/opt/Rocket.Chat/resources/app.asar"
    )


def test_asar_path_passthrough_for_explicit_asar():
    assert (
        _asar_path_from_install_path("/weird/path/app.asar")
        == "/weird/path/app.asar"
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detect_userdata_dirs_with_explicit_name():
    ssh = MockSSH()
    dirs = detect_userdata_dirs(ssh, "/opt/Rocket.Chat", app_name="Rocket.Chat")
    assert dirs == [
        "$HOME/.config/Rocket.Chat",
        "$HOME/.config/Rocket.Chat (development)",
    ]
    # No SSH calls when name explicit — pure path math.
    assert ssh.commands == []


def test_detect_userdata_dirs_extracts_productName_from_asar():
    ssh = MockSSH(returns=[
        ("strings", _CannedResult(stdout='"productName":"My App"\n')),
    ])
    dirs = detect_userdata_dirs(ssh, "/opt/MyApp")
    assert dirs == [
        "$HOME/.config/My App",
        "$HOME/.config/My App (development)",
    ]
    assert any("strings" in c for c in ssh.commands)


def test_detect_userdata_dirs_raises_when_no_name():
    ssh = MockSSH()  # empty stdout
    with pytest.raises(RuntimeError, match="productName"):
        detect_userdata_dirs(ssh, "/opt/MyApp")


def test_detect_app_version_prefers_binary_version():
    ssh = MockSSH(returns=[
        ("--version", _CannedResult(stdout="4.14.1\n")),
    ])
    v = detect_app_version(
        ssh, "/opt/Rocket.Chat", binary="/opt/Rocket.Chat/rocketchat-desktop"
    )
    assert v == "4.14.1"


def test_detect_app_version_falls_back_to_app_update_yml():
    ssh = MockSSH(returns=[
        ("--version", _CannedResult(stdout="")),  # binary --version empty
        ("app-update.yml", _CannedResult(stdout="version: 3.9.2\n")),
    ])
    v = detect_app_version(
        ssh, "/opt/Rocket.Chat", binary="/opt/Rocket.Chat/rocketchat-desktop"
    )
    assert v == "3.9.2"


def test_detect_app_version_raises_when_unknown():
    ssh = MockSSH()  # all returns empty
    with pytest.raises(RuntimeError, match="version"):
        detect_app_version(
            ssh, "/opt/Rocket.Chat", binary="/opt/Rocket.Chat/rocketchat-desktop"
        )


# ---------------------------------------------------------------------------
# Wipe + write
# ---------------------------------------------------------------------------


def test_wipe_userdata_uses_double_quotes_for_dev_suffix_path():
    ssh = MockSSH()
    wipe_userdata(ssh, [
        "$HOME/.config/Rocket.Chat",
        "$HOME/.config/Rocket.Chat (development)",
    ])
    assert len(ssh.commands) == 1
    cmd = ssh.commands[0]
    # Double-quoted so $HOME expands AND the literal space inside (development)
    # stays one argument.
    assert '"$HOME/.config/Rocket.Chat (development)"' in cmd
    assert "rm -rf" in cmd and "mkdir -p" in cmd


def test_wipe_userdata_raises_on_failure():
    ssh = MockSSH(returns=[("rm -rf", _CannedResult(returncode=1, stderr="denied"))])
    with pytest.raises(RuntimeError, match="wipe_userdata"):
        wipe_userdata(ssh, ["$HOME/.config/X"])


def test_write_servers_json_writes_to_each_dir_and_returns_payload():
    ssh = MockSSH()
    dirs = ["$HOME/.config/X", "$HOME/.config/X (development)"]
    payload = write_servers_json(
        ssh,
        dirs,
        {"Workspace": "https://rocketchat.example/", "Mobile RC": "https://mobile.rocket.chat"},
    )
    decoded = json.loads(payload)
    assert decoded == [
        {"url": "https://rocketchat.example/", "title": "Workspace",
         "order": 0, "lastPath": "/"},
        {"url": "https://mobile.rocket.chat", "title": "Mobile RC",
         "order": 1, "lastPath": "/"},
    ]
    # One printf per dir
    printf_cmds = [c for c in ssh.commands if c.startswith("printf '%s\\n'")]
    assert len(printf_cmds) == 2
    assert any("$HOME/.config/X/servers.json" in c for c in printf_cmds)
    assert any("$HOME/.config/X (development)/servers.json" in c for c in printf_cmds)


# ---------------------------------------------------------------------------
# Merge — the critical piece
# ---------------------------------------------------------------------------


def test_merge_config_pins_migrations_version():
    merged = _merge_config(
        {"isTelephonyEnabled": True},
        currentView_url="https://workspace.example/",
        migrations_version="4.14.1",
        first_server_url="https://workspace.example/",
    )
    assert merged["__internal__"]["migrations"]["version"] == "4.14.1"
    assert merged["isTelephonyEnabled"] is True


def test_merge_config_user_cannot_override_migrations_version():
    """The whole point of I1: migrations_version is pinned LAST. A user
    config that tries to set it must lose."""
    merged = _merge_config(
        {"__internal__": {"migrations": {"version": "0.0.0"}}},
        currentView_url="https://x/",
        migrations_version="4.14.1",
        first_server_url="https://x/",
    )
    assert merged["__internal__"]["migrations"]["version"] == "4.14.1"


def test_merge_config_shape_has_currentView_and_lastSelectedServerUrl():
    merged = _merge_config(
        {},
        currentView_url="https://workspace.example/",
        migrations_version="4.14.1",
        first_server_url="https://workspace.example/",
    )
    assert merged["currentView"] == {"url": "https://workspace.example/"}
    assert merged["lastSelectedServerUrl"] == "https://workspace.example/"
    assert merged["isMenuBarEnabled"] is True


def test_merge_config_user_keys_override_defaults():
    merged = _merge_config(
        {"isMenuBarEnabled": False, "telephonyPreferredServer": None},
        currentView_url="https://x/",
        migrations_version="4.14.1",
        first_server_url="https://x/",
    )
    assert merged["isMenuBarEnabled"] is False
    assert "telephonyPreferredServer" in merged
    assert merged["telephonyPreferredServer"] is None


def test_write_config_json_validates_json_and_writes_per_dir():
    ssh = MockSSH()
    dirs = ["$HOME/.config/X", "$HOME/.config/X (development)"]
    payload = write_config_json(
        ssh,
        dirs,
        {"isTelephonyEnabled": True},
        currentView_url="https://x/",
        migrations_version="4.14.1",
        first_server_url="https://x/",
    )
    # Valid JSON
    json.loads(payload)
    printf_cmds = [c for c in ssh.commands if c.startswith("printf '%s\\n'")]
    assert len(printf_cmds) == 2
    assert any("$HOME/.config/X/config.json" in c for c in printf_cmds)
    assert any("$HOME/.config/X (development)/config.json" in c for c in printf_cmds)


def test_write_config_json_escapes_single_quotes_for_printf():
    ssh = MockSSH()
    # A value containing a single quote — without escaping, printf '...' breaks.
    write_config_json(
        ssh,
        ["$HOME/.config/X"],
        {"weirdKey": "value with ' quote"},
        currentView_url="https://x/",
        migrations_version="4.14.1",
    )
    cmd = next(c for c in ssh.commands if "config.json" in c)
    # The escaped form ' \' ' is what survives the outer single-quoted shell literal.
    assert "'\\''" in cmd


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


def test_parse_inject_config_inline_json():
    assert parse_inject_config('{"isTelephonyEnabled": true}') == {
        "isTelephonyEnabled": True
    }


def test_parse_inject_config_file_path(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": 1}))
    assert parse_inject_config(f"@{p}") == {"a": 1}


def test_parse_inject_config_rejects_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        parse_inject_config("[1,2,3]")


def test_parse_inject_servers_kv_pairs():
    assert parse_inject_servers(
        "Workspace=https://rocketchat.example/,Mobile RC=https://mobile.rocket.chat"
    ) == {
        "Workspace": "https://rocketchat.example/",
        "Mobile RC": "https://mobile.rocket.chat",
    }


def test_parse_inject_servers_file_dict(tmp_path):
    p = tmp_path / "servers.json"
    p.write_text(json.dumps({"X": "https://x/"}))
    assert parse_inject_servers(f"@{p}") == {"X": "https://x/"}


def test_parse_inject_servers_rejects_missing_equals():
    with pytest.raises(ValueError, match="TITLE=URL"):
        parse_inject_servers("badentry")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def test_inject_end_to_end_records_full_command_sequence():
    ssh = MockSSH(returns=[
        ("strings", _CannedResult(stdout='"productName":"Rocket.Chat"\n')),
        ("--version", _CannedResult(stdout="4.14.1\n")),
    ])
    report = inject(
        ssh,
        install_path="/opt/Rocket.Chat/rocketchat-desktop",
        config={"isTelephonyEnabled": True},
        servers={"Workspace": "https://workspace.example/"},
    )
    assert report["userdata_dirs"] == [
        "$HOME/.config/Rocket.Chat",
        "$HOME/.config/Rocket.Chat (development)",
    ]
    assert report["migrations_version"] == "4.14.1"
    # We expect (in order): strings probe, --version probe, wipe, 2x servers.json
    # printf, 2x config.json printf.
    kinds = [
        "strings" if "strings" in c else
        "version" if "--version" in c else
        "wipe" if "rm -rf" in c else
        "servers" if "servers.json" in c else
        "config" if "config.json" in c else
        "other"
        for c in ssh.commands
    ]
    assert kinds.count("strings") == 1
    assert kinds.count("version") == 1
    assert kinds.count("wipe") == 1
    assert kinds.count("servers") == 2
    assert kinds.count("config") == 2
    # Sanity: config.json contains the migration pin
    cfg_cmd = next(c for c in ssh.commands if "config.json" in c)
    assert '"version": "4.14.1"' in cfg_cmd


def test_inject_skips_server_write_when_servers_empty():
    ssh = MockSSH(returns=[
        ("strings", _CannedResult(stdout='"productName":"X"\n')),
        ("--version", _CannedResult(stdout="1.0.0\n")),
    ])
    inject(
        ssh,
        install_path="/opt/X/x-desktop",
        config={"foo": "bar"},
        servers={},
    )
    assert not any("servers.json" in c for c in ssh.commands)
    # config.json still written
    assert any("config.json" in c for c in ssh.commands)


def test_inject_explicit_migrations_version_skips_detection():
    ssh = MockSSH(returns=[
        ("strings", _CannedResult(stdout='"productName":"X"\n')),
    ])
    inject(
        ssh,
        install_path="/opt/X",
        config={},
        servers={"S": "https://s/"},
        migrations_version="9.9.9",
    )
    # No --version probe should have happened
    assert not any("--version" in c for c in ssh.commands)
    cfg_cmd = next(c for c in ssh.commands if "config.json" in c)
    assert '"version": "9.9.9"' in cfg_cmd


# ---------------------------------------------------------------------------
# Integration smoke (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("MOSDAT_TEST_VM") != "ubuntu2204",
    reason="set MOSDAT_TEST_VM=ubuntu2204 to run live VM smoke",
)
def test_inject_live_ubuntu2204_vm():
    """Touches a real VM. Wipes RC userData. Only runs with explicit opt-in."""
    from automation.config import load_config
    from automation.transport.ssh import SSHClient

    cfg_path = os.environ.get("MOSDAT_TEST_CONFIG", "examples/rocketchat.toml")
    config = load_config(cfg_path)
    vm = config.vm_by_name["ubuntu2204"]
    ssh = SSHClient(vm.ip, vm.user)
    assert ssh.is_reachable(), "ubuntu2204 SSH unreachable"
    install_path = next(
        (pkg.app_path for pkg in vm.packages if pkg.app_path), None
    )
    assert install_path, "ubuntu2204 has no package with app_path"
    report = inject(
        ssh,
        install_path=install_path,
        config={"isTelephonyEnabled": False},
        servers={"Workspace": "https://example.invalid/"},
        log_fn=print,
    )
    assert report["migrations_version"]
    # Confirm files exist
    r = ssh.run("cat \"$HOME/.config/Rocket.Chat/config.json\" 2>/dev/null | head -1")
    assert "{" in r.stdout
