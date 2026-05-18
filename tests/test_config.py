from pathlib import Path

# Clear sibling-test stub pollution (test_concurrent_safety stubs automation.config).
import sys as _sys
_sys.modules.pop("automation.config", None)

from automation.config import load_config


def test_load_config_cursor_defaults_when_table_absent(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('''[app]
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
install = "sudo dpkg -i /tmp/{file}"
uninstall = "sudo apt remove -y rocketchat"
app_path = "/opt/Rocket.Chat/rocketchat-desktop"
file_glob = "rocketchat-*.deb"

[report]
title = "Test"
''')

    config = load_config(config_path)

    assert config.cursor.profile == "bezier"
    assert config.cursor.duration_ms == 1000
    assert config.cursor.hover_dwell_ms == 250
    assert config.cursor.seed == "auto"
