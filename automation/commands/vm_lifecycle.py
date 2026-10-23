"""VM lifecycle and readiness helpers (plan 003, workstream E).

Plain functions for the MCP tools to call:

- :func:`wait_ready` — poll SSH after a VM start, so an agent does not have
  to ``sleep 60`` and guess.
- :func:`installed_app_info` — where the installed Rocket.Chat app is, its
  version, and when its ``app.asar`` was written. This answers "is the PR
  build installed?" on Linux and Windows.
- :func:`windows_disk_free_gb` — free space on ``C:``.
- :func:`readiness_hints` — a next step when Proxmox or SSH is down.

Inputs are ``VMConfig`` / ``ProjectConfig`` objects from
:mod:`automation.config` and an :class:`~automation.transport.ssh.SSHClient`.
Outputs are dicts.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from automation.commands.doctor import check_ssh

# Connect timeout for one wait_ready probe. Short, because the loop retries.
_WAIT_CONNECT_TIMEOUT = 5

_APPIMAGE_EXTRACT_DIR = "/tmp/mosdat-appimage-asar/"
_APPIMAGE_BIN = "/opt/Rocket.Chat/rocketchat-desktop"


def _default_ssh_factory(vm):
    from automation.transport.ssh import SSHClient

    return SSHClient(
        vm.ip, user=vm.user or "root", connect_timeout=_WAIT_CONNECT_TIMEOUT,
    )


def wait_ready(
    vm,
    *,
    timeout: int = 240,
    interval: int = 5,
    ssh_factory: Optional[Callable] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Poll SSH (``echo OK``) on ``vm`` until it answers or ``timeout`` elapses.

    Returns ``{"ready": bool, "waited_s": int, "detail": str}``. ``detail``
    holds the last SSH error when not ready. ``ssh_factory(vm)`` builds the
    client; ``sleep`` and ``clock`` are injectable for tests.
    """
    factory = ssh_factory or _default_ssh_factory
    start = clock()
    attempts = 0
    last_detail = ""
    while True:
        attempts += 1
        try:
            res = check_ssh(factory(vm))
            status, last_detail = res.status, res.detail or ""
        except Exception as e:  # factory or probe blew up — keep polling
            status, last_detail = "FAIL", str(e)[:120]
        elapsed = clock() - start
        if status == "PASS":
            return {
                "ready": True,
                "waited_s": int(round(elapsed)),
                "detail": f"SSH answered after {attempts} attempt(s)",
            }
        if elapsed + interval > timeout:
            break
        sleep(interval)
    return {
        "ready": False,
        "waited_s": int(round(clock() - start)),
        "detail": (
            f"SSH did not answer within {timeout}s ({attempts} attempt(s)); "
            f"last error: {last_detail or 'unknown'}"
        ),
    }


def _iso_utc(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _asar_package_version(ssh, asar: str) -> Optional[str]:
    """``version`` from the root ``package.json`` inside an ``app.asar``.

    Do not grep the first ``"version":"..."`` in the asar: that is the
    version of whichever bundled file comes first (measured on ubuntu2204:
    grep gave 23.7.20, the app is 4.17.0-alpha.1). Instead read the asar
    header (Chromium pickle: 4 uint32 LE, then the JSON file table), find
    ``package.json``, and read only those bytes. Needs only coreutils.
    """
    q = shlex.quote(asar)
    res = ssh.run(
        f"set -- $(head -c 16 {q} | od -An -v -tu4) && "
        'echo "HS=$2" && '
        f'head -c $((16 + $4)) {q} | tail -c "$4"',
        timeout=60,
    )
    first, _, header_json = (res.stdout or "").partition("\n")
    if not res.success or not first.startswith("HS="):
        return None
    try:
        header_size = int(first[3:])
        entry = json.loads(header_json)["files"]["package.json"]
        offset, size = int(entry["offset"]), int(entry["size"])
    except (ValueError, KeyError, TypeError):
        return None
    start = 8 + header_size + offset + 1  # tail -c +N is 1-based
    res = ssh.run(f"tail -c +{start} {q} | head -c {size}", timeout=60)
    try:
        version = json.loads(res.stdout or "").get("version")
    except (ValueError, AttributeError):
        return None
    return str(version) if version else None


def _linux_app_info(ssh) -> dict:
    from automation.commands.build import _find_installed_asar

    info = {
        "found": False, "path": None, "asar_path": None,
        "version": None, "asar_mtime_utc": None,
    }
    asar = _find_installed_asar(ssh)
    if not asar:
        return info
    info["found"] = True
    info["asar_path"] = asar
    if asar.startswith(_APPIMAGE_EXTRACT_DIR):
        # AppImage: the asar was extracted to /tmp just now, so its mtime says
        # nothing about the install. Report the AppImage file instead.
        info["path"] = _APPIMAGE_BIN
        stat_target = _APPIMAGE_BIN
    else:
        # /opt/<app>/resources/app.asar -> /opt/<app>
        info["path"] = asar.rsplit("/resources/", 1)[0]
        stat_target = asar
    info["version"] = _asar_package_version(ssh, asar)
    res = ssh.run(f"stat -c %Y {shlex.quote(stat_target)}", timeout=15)
    try:
        info["asar_mtime_utc"] = _iso_utc(int((res.stdout or "").strip()))
    except ValueError:
        pass
    return info


def _windows_app_info(ssh) -> dict:
    from automation.commands.build import (
        _ps_quote, _ps_run, _ps_tag, _verify_windows_install,
    )

    info = {
        "found": False, "path": None, "asar_path": None,
        "version": None, "asar_mtime_utc": None,
    }
    installed_at, version, _ = _verify_windows_install(ssh, [])
    if not installed_at:
        return info
    info["found"] = True
    info["path"] = installed_at
    info["version"] = version if version and version != "(unknown)" else None
    script = (
        f"$dir = Split-Path -Parent {_ps_quote(installed_at)};"
        "$asar = Join-Path $dir 'resources\\app.asar';"
        "if (Test-Path $asar) {"
        " Write-Host \"ASAR=$asar\";"
        " Write-Host ('MTIME=' + (Get-Item $asar).LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ', [Globalization.CultureInfo]::InvariantCulture))"
        "}"
    )
    res = _ps_run(ssh, script, timeout=60)
    info["asar_path"] = _ps_tag(res.stdout, "ASAR") or None
    info["asar_mtime_utc"] = _ps_tag(res.stdout, "MTIME") or None
    return info


def installed_app_info(ssh, *, is_windows: bool) -> dict:
    """Locate the installed Rocket.Chat app on a VM.

    Returns ``{"found", "path", "asar_path", "version", "asar_mtime_utc"}``.
    ``path`` is the install directory on Linux (``/opt/<app>``, or the
    AppImage file) and ``Rocket.Chat.exe`` on Windows. ``version`` is the
    version in the asar's root ``package.json`` on Linux and the exe FileVersion on
    Windows. ``asar_mtime_utc`` is ISO 8601 UTC. Unknown values are None.
    """
    if is_windows:
        return _windows_app_info(ssh)
    return _linux_app_info(ssh)


def windows_disk_free_gb(ssh) -> float:
    """Free space on ``C:`` in GiB, rounded to 2 decimals.

    Raises ``RuntimeError`` when the value cannot be read.
    """
    from automation.commands.build import _ps_run, _ps_tag

    res = _ps_run(ssh, "Write-Host ('FREE=' + (Get-PSDrive C).Free)", timeout=30)
    raw = _ps_tag(res.stdout, "FREE")
    try:
        return round(int(raw) / 1024 ** 3, 2)
    except ValueError:
        detail = (res.stderr or "").strip() or f"exit {res.returncode}"
        raise RuntimeError(f"could not read free space on C: ({detail[:200]})")


def _family(name: str) -> str:
    """``ubuntu2404`` -> ``ubuntu``; ``windows10`` -> ``windows``."""
    return re.sub(r"[\d._-]+$", "", name or "").lower()


def readiness_hints(vm, cfg, *, ssh_ok: bool, proxmox_ok: bool) -> list[str]:
    """Plain next steps for a readiness failure. Empty when nothing failed.

    Proxmox down hides everything else, because no VM can run. SSH down
    names the other configured VMs of the same OS type (same family first)
    and the matching ``mosdat_ssh_bootstrap`` path.
    """
    if not proxmox_ok:
        host = getattr(getattr(cfg, "proxmox", None), "host", None) or "(unknown)"
        return [
            f"Proxmox host {host} is not reachable from this machine: the lab "
            "is off or off-network; no VM can run until it is back"
        ]
    if ssh_ok:
        return []

    hints: list[str] = []
    os_type = getattr(vm, "os_type", None) or "linux"
    family = _family(vm.name)
    configured = getattr(cfg, "vms", None) or list(cfg.vm_by_name.values())
    others = [
        o for o in configured
        if o.name != vm.name and (getattr(o, "os_type", None) or "linux") == os_type
    ]
    same_family = [o.name for o in others if _family(o.name) == family]
    same_os = [o.name for o in others if _family(o.name) != family]
    alts = [f"{n} (same family: {family})" for n in same_family]
    alts += [f"{n} (same OS type: {os_type})" for n in same_os]
    if alts:
        hints.append(f"SSH to {vm.name} fails; try " + ", ".join(alts))
    hints.append(
        f"check that {vm.name} is running (mosdat_vm_status); after "
        "mosdat_vm_start, wait for SSH with wait_ready instead of sleep"
    )
    if os_type == "windows":
        hints.append(
            "mosdat_ssh_bootstrap installs this host's SSH key via the "
            "unlocked VM console"
        )
    else:
        hints.append(
            "mosdat_ssh_bootstrap installs this host's SSH key through the "
            "QEMU guest agent (no login needed when the agent runs in the VM)"
        )
    return hints
