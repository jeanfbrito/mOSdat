from pathlib import Path

# Clear sibling-test stub pollution (test_concurrent_safety stubs automation.config).
import sys as _sys
_sys.modules.pop("automation.config", None)

import pytest
from pydantic import ValidationError

from automation.config import CursorConfig, load_config


def _minimal_config_toml(cursor_block: str = "") -> str:
    return f'''[app]
name = "Rocket.Chat"
version = "1.0.0"
binary = "rocketchat"

[proxmox]
host = "192.168.13.85"
password = "secret"

[[vm]]
name = "ubuntu2204"
vmid = 101
ip = "192.168.13.10"
desktop = "GNOME"

[[vm.package]]
format = "deb"
install = "sudo dpkg -i /tmp/{{file}}"
uninstall = "sudo apt remove -y rocketchat"
app_path = "/opt/Rocket.Chat/rocketchat-desktop"
file_glob = "rocketchat-*.deb"

[report]
title = "Test"

{cursor_block}
'''


def test_cursor_config_defaults() -> None:
    cfg = CursorConfig()
    assert cfg.profile == "bezier"
    assert cfg.duration_ms == 150
    assert cfg.hover_dwell_ms == 0
    assert cfg.seed == "auto"


def test_cursor_profile_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        CursorConfig(profile="windmouse")


def test_cursor_duration_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        CursorConfig(duration_ms=-1)


def test_cursor_duration_rejects_too_large() -> None:
    with pytest.raises(ValidationError):
        CursorConfig(duration_ms=99999)


def test_cursor_toml_round_trip_profile_instant(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(_minimal_config_toml('[cursor]\nprofile = "instant"'))

    config = load_config(config_path)

    assert config.cursor.profile == "instant"


def test_cursor_seed_accepts_numeric_string() -> None:
    cfg = CursorConfig(seed="12345")
    assert cfg.seed == "12345"
