"""mosdat doctor — per-VM and host-side health checks.

Usage:
    mosdat doctor <toml> [--vms VM1,VM2]

Per-VM checks (over SSH):
    - SSH reachable
    - GNOME/KDE session detected
    - X11 cookie present
    - Installed deps: wmctrl xclip xdotool xdg-mime xdg-open ssh scp
    - Binary at TOML app_path or /opt/Rocket.Chat/rocketchat-desktop
    - asar present
    - ~/.config/<App>* dirs
    - Disk free in /tmp (warn < 1 GB)
    - Disk free in $HOME (warn < 1 GB)
    - RC process count (warn > 0)
    - Uptime + load avg

Host-side checks:
    - mosdat version (python -m mosdat --help or package version)
    - python version
    - gh CLI present
    - Proxmox API reachable
    - VLM endpoint reachable
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

PASS = "✓"   # ✓
FAIL = "✗"   # ✗
WARN = "⚠"   # ⚠


@dataclass
class CheckResult:
    label: str
    status: str          # PASS | FAIL | WARN
    detail: str = ""


@dataclass
class VMReport:
    vm_name: str
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "FAIL")

    @property
    def warned(self) -> int:
        return sum(1 for r in self.results if r.status == "WARN")

    @property
    def healthy(self) -> bool:
        return self.failed == 0


# ---------------------------------------------------------------------------
# Per-VM check functions (each takes an SSHClient and returns CheckResult)
# ---------------------------------------------------------------------------

def check_ssh(ssh) -> CheckResult:
    """SSH reachable with 8s timeout."""
    try:
        result = ssh.run("echo OK", timeout=8)
        if result.success and "OK" in result.stdout:
            return CheckResult("SSH reachable", "PASS")
        return CheckResult("SSH reachable", "FAIL", result.stderr.strip() or f"exit {result.returncode}")
    except Exception as e:
        return CheckResult("SSH reachable", "FAIL", str(e)[:80])


def check_session(ssh) -> CheckResult:
    """GNOME/KDE session detected via XDG_SESSION_TYPE and XDG_CURRENT_DESKTOP."""
    result = ssh.run(
        "echo SESSION_TYPE=$XDG_SESSION_TYPE DESKTOP=$XDG_CURRENT_DESKTOP",
        timeout=8,
    )
    out = result.stdout.strip()
    if not result.success or not out:
        return CheckResult("Desktop session", "WARN", "could not query XDG vars")
    session_type = ""
    desktop = ""
    for token in out.split():
        if token.startswith("SESSION_TYPE="):
            session_type = token.split("=", 1)[1]
        elif token.startswith("DESKTOP="):
            desktop = token.split("=", 1)[1]
    if session_type or desktop:
        return CheckResult("Desktop session", "PASS", f"{session_type}/{desktop}" if session_type else desktop)
    return CheckResult("Desktop session", "WARN", "XDG vars empty (headless SSH?)")


def check_x11_cookie(ssh) -> CheckResult:
    """X11 cookie present: mutter-Xwaylandauth.*, gdm/Xauthority, ~/.Xauthority."""
    cmd = (
        "ls /run/user/$(id -u)/.mutter-Xwaylandauth.* "
        "   /run/user/$(id -u)/gdm/Xauthority "
        "   ~/.Xauthority 2>/dev/null | head -5"
    )
    result = ssh.run(cmd, timeout=8)
    found = result.stdout.strip()
    if found:
        first = found.splitlines()[0]
        return CheckResult("X11 cookie", "PASS", first)
    return CheckResult("X11 cookie", "WARN", "no XAUTHORITY file found; X11 GUI steps will fail")


def check_deps(ssh) -> list[CheckResult]:
    """Check each required dep with `command -v`."""
    deps = ["wmctrl", "xclip", "xdotool", "xdg-mime", "xdg-open", "ssh", "scp"]
    results = []
    for dep in deps:
        r = ssh.run(f"command -v {dep} >/dev/null 2>&1 && echo FOUND || echo MISSING", timeout=8)
        out = r.stdout.strip()
        if "FOUND" in out:
            results.append(CheckResult(f"dep:{dep}", "PASS"))
        else:
            results.append(CheckResult(f"dep:{dep}", "FAIL", "not installed"))
    return results


def check_binary(ssh, install_path: str, default_path: str = "/opt/Rocket.Chat/rocketchat-desktop") -> CheckResult:
    """Binary present at TOML install_path or default."""
    candidates = [p for p in [install_path, default_path] if p and "{file}" not in p]
    if not candidates:
        return CheckResult("App binary", "WARN", "install_path contains {file} — runtime resolved")
    for path in candidates:
        r = ssh.run(f'test -f "{path}" && echo FOUND || echo MISSING', timeout=8)
        if "FOUND" in r.stdout:
            return CheckResult("App binary", "PASS", path)
    return CheckResult("App binary", "FAIL", f"not found: {candidates[0]}")


def check_asar(ssh) -> CheckResult:
    """asar present at standard Electron locations."""
    cmd = (
        "find /opt/Rocket.Chat /opt/rocketchat /usr/lib/rocketchat "
        "    ~/.local/lib/rocketchat 2>/dev/null "
        "    -name 'app.asar' -o -name '*.asar' 2>/dev/null | head -3"
    )
    r = ssh.run(cmd, timeout=10)
    found = r.stdout.strip()
    if found:
        return CheckResult("asar", "PASS", found.splitlines()[0])
    return CheckResult("asar", "WARN", "no .asar found — binary may not be installed yet")


def check_userdata_dirs(ssh, app_name: str) -> CheckResult:
    """List ~/.config/<App>* dirs."""
    safe_name = app_name.replace(".", "\\.").replace("(", "\\(").replace(")", "\\)")
    r = ssh.run(f'ls -d ~/.config/{safe_name}* 2>/dev/null || true', timeout=8)
    dirs = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    if dirs:
        return CheckResult("userData dirs", "PASS", ", ".join(dirs))
    return CheckResult("userData dirs", "WARN", f"no ~/.config/{app_name}* found")


def check_disk_tmp(ssh) -> CheckResult:
    """Disk free in /tmp — warn if < 1 GB."""
    r = ssh.run("df -BG /tmp 2>/dev/null | awk 'NR==2{print $4}'", timeout=8)
    val = r.stdout.strip().rstrip("G")
    try:
        gb = int(val)
    except ValueError:
        return CheckResult("Disk /tmp free", "WARN", f"could not parse: {r.stdout.strip()!r}")
    if gb < 1:
        return CheckResult("Disk /tmp free", "WARN", f"{gb} GB (< 1 GB threshold)")
    return CheckResult("Disk /tmp free", "PASS", f"{gb} GB")


def check_disk_home(ssh) -> CheckResult:
    """Disk free in $HOME — warn if < 1 GB."""
    r = ssh.run("df -BG ~ 2>/dev/null | awk 'NR==2{print $4}'", timeout=8)
    val = r.stdout.strip().rstrip("G")
    try:
        gb = int(val)
    except ValueError:
        return CheckResult("Disk $HOME free", "WARN", f"could not parse: {r.stdout.strip()!r}")
    if gb < 1:
        return CheckResult("Disk $HOME free", "WARN", f"{gb} GB (< 1 GB threshold)")
    return CheckResult("Disk $HOME free", "PASS", f"{gb} GB")


def check_rc_processes(ssh) -> CheckResult:
    """Current RC process count — warn if > 0."""
    r = ssh.run(
        "pgrep -af '(/opt/Rocket.Chat|rocketchat-desktop|Rocket\\.Chat)' 2>/dev/null | wc -l",
        timeout=8,
    )
    val = r.stdout.strip()
    try:
        count = int(val)
    except ValueError:
        return CheckResult("RC processes", "WARN", f"could not parse: {val!r}")
    if count > 0:
        return CheckResult("RC processes", "WARN", f"{count} running (may interfere with tests)")
    return CheckResult("RC processes", "PASS", "0")


def check_uptime(ssh) -> CheckResult:
    """Uptime and load avg."""
    r = ssh.run("uptime", timeout=8)
    out = r.stdout.strip()
    if r.success and out:
        return CheckResult("Uptime/load", "PASS", out)
    return CheckResult("Uptime/load", "WARN", "uptime unavailable")


# ---------------------------------------------------------------------------
# Host-side checks
# ---------------------------------------------------------------------------

def host_check_mosdat_version() -> CheckResult:
    """mosdat version (from package metadata or __version__)."""
    try:
        from importlib.metadata import version as pkg_version
        v = pkg_version("mosdat")
        return CheckResult("mosdat version", "PASS", v)
    except Exception:
        pass
    # Fallback: check that the module is importable
    try:
        import automation  # noqa: F401
        return CheckResult("mosdat version", "PASS", "importable (no package version)")
    except Exception as e:
        return CheckResult("mosdat version", "FAIL", str(e)[:80])


def host_check_python() -> CheckResult:
    v = sys.version.replace("\n", " ")
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 10):
        return CheckResult("Python version", "WARN", f"{v} (recommend 3.10+)")
    return CheckResult("Python version", "PASS", v)


def host_check_gh() -> CheckResult:
    path = shutil.which("gh")
    if path:
        try:
            r = subprocess.run(["gh", "--version"], capture_output=True, text=True, timeout=5)
            ver = r.stdout.splitlines()[0] if r.stdout else "unknown"
            return CheckResult("gh CLI", "PASS", ver)
        except Exception:
            return CheckResult("gh CLI", "PASS", path)
    return CheckResult("gh CLI", "WARN", "not found in PATH")


def host_check_proxmox(host: str, port: int = 8006) -> CheckResult:
    """Proxmox API reachable: GET /api2/json/version."""
    if not host:
        return CheckResult("Proxmox API", "WARN", "no host configured")
    url = f"https://{host}:{port}/api2/json/version"
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="GET")
        # Proxmox uses self-signed certs; import ssl to bypass verification
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return CheckResult("Proxmox API", "PASS", f"HTTP {resp.status} in {elapsed}ms")
    except urllib.error.HTTPError as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        if 200 <= e.code < 500:
            return CheckResult("Proxmox API", "PASS", f"HTTP {e.code} in {elapsed}ms")
        return CheckResult("Proxmox API", "FAIL", f"HTTP {e.code}")
    except Exception as e:
        return CheckResult("Proxmox API", "FAIL", str(e)[:80])


def host_check_vlm(base_url: str) -> CheckResult:
    """VLM endpoint reachable: GET /models or /v1/models."""
    if not base_url:
        return CheckResult("VLM endpoint", "WARN", "no VLM base_url configured")
    # Normalise: strip trailing /v1 or similar — try both /models and /v1/models
    base = base_url.rstrip("/")
    # Strip /v1 suffix if present so we can append /v1/models ourselves
    if base.endswith("/v1"):
        root = base[:-3]
    else:
        root = base
    candidates = [f"{root}/v1/models", f"{root}/models", f"{base}/models"]
    t0 = time.perf_counter()
    last_err = "no candidates"
    for url in candidates:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                elapsed = int((time.perf_counter() - t0) * 1000)
                return CheckResult("VLM endpoint", "PASS", f"HTTP {resp.status} {url} in {elapsed}ms")
        except urllib.error.HTTPError as e:
            if 200 <= e.code < 500:
                elapsed = int((time.perf_counter() - t0) * 1000)
                return CheckResult("VLM endpoint", "PASS", f"HTTP {e.code} {url} in {elapsed}ms")
            last_err = f"HTTP {e.code} @ {url}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:60]} @ {url}"
    return CheckResult("VLM endpoint", "FAIL", last_err)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_STATUS_ICON = {"PASS": PASS, "FAIL": FAIL, "WARN": WARN}


def _icon(status: str) -> str:
    return _STATUS_ICON.get(status, "?")


def print_vm_report(report: VMReport) -> None:
    print(f"\n  VM: {report.vm_name}")
    print(f"  {'─' * 50}")
    for r in report.results:
        icon = _icon(r.status)
        detail = f"  — {r.detail}" if r.detail else ""
        print(f"  {icon} {r.label}{detail}")


def print_host_report(results: list[CheckResult]) -> None:
    print("\n  Host")
    print(f"  {'─' * 50}")
    for r in results:
        icon = _icon(r.status)
        detail = f"  — {r.detail}" if r.detail else ""
        print(f"  {icon} {r.label}{detail}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

try:
    from automation.config import load_config as _load_config
    from automation.transport.ssh import SSHClient as _SSHClient
except ImportError:
    _load_config = None  # type: ignore[assignment]
    _SSHClient = None    # type: ignore[assignment]

# Expose at module level so tests can patch them via
# patch("automation.commands.doctor.load_config") etc.
load_config = _load_config
SSHClient = _SSHClient


def run_doctor(args) -> int:
    config = load_config(args.config)

    # Resolve VM list
    if args.vms:
        vm_names = [v.strip() for v in args.vms.split(",")]
        for name in vm_names:
            if name not in config.vm_by_name:
                print(f"[doctor] ERROR: Unknown VM '{name}'. Available: {', '.join(config.vm_by_name.keys())}")
                return 1
        vms = [config.vm_by_name[name] for name in vm_names]
    else:
        vms = config.vms

    print(f"\nmosdat doctor — {len(vms)} VM(s)")

    # --- Host checks ---
    host_results: list[CheckResult] = [
        host_check_mosdat_version(),
        host_check_python(),
        host_check_gh(),
        host_check_proxmox(config.proxmox.host, config.proxmox.port),
        host_check_vlm(config.vlm.base_url),
    ]
    print_host_report(host_results)

    # --- Per-VM checks ---
    vm_reports: list[VMReport] = []
    for vm in vms:
        if vm.is_windows:
            report = VMReport(vm.name)
            report.results.append(CheckResult("Windows VM", "WARN", "doctor skips Windows VMs"))
            vm_reports.append(report)
            print_vm_report(report)
            continue

        ssh = SSHClient(vm.ip, vm.user, connect_timeout=8)
        report = VMReport(vm.name)

        # 1. SSH
        ssh_result = check_ssh(ssh)
        report.results.append(ssh_result)
        if ssh_result.status == "FAIL":
            # Cannot proceed without SSH
            report.results.append(CheckResult("(remaining checks skipped)", "WARN", "SSH unreachable"))
            vm_reports.append(report)
            print_vm_report(report)
            continue

        # 2. Session
        report.results.append(check_session(ssh))

        # 3. X11 cookie
        report.results.append(check_x11_cookie(ssh))

        # 4. Deps
        report.results.extend(check_deps(ssh))

        # 5. Binary
        install_path = ""
        for pkg in vm.packages:
            if pkg.app_path and "{file}" not in pkg.app_path and pkg.app_path.startswith("/"):
                install_path = pkg.app_path
                break
        report.results.append(check_binary(ssh, install_path))

        # 6. asar
        report.results.append(check_asar(ssh))

        # 7. userData dirs
        app_display_name = config.app.name.title()  # e.g. "rocketchat" -> "Rocketchat"
        # Try canonical names for RC
        report.results.append(check_userdata_dirs(ssh, "Rocket.Chat"))

        # 8. Disk /tmp
        report.results.append(check_disk_tmp(ssh))

        # 9. Disk $HOME
        report.results.append(check_disk_home(ssh))

        # 10. RC processes
        report.results.append(check_rc_processes(ssh))

        # 11. Uptime
        report.results.append(check_uptime(ssh))

        vm_reports.append(report)
        print_vm_report(report)

    # --- Summary ---
    host_fails = sum(1 for r in host_results if r.status == "FAIL")
    host_warns = sum(1 for r in host_results if r.status == "WARN")

    healthy_vms = sum(1 for r in vm_reports if r.healthy)
    warn_vms = sum(1 for r in vm_reports if not r.healthy and r.failed == 0)
    fail_vms = sum(1 for r in vm_reports if r.failed > 0)

    print(f"\n{'─' * 54}")
    print(f"  Summary: {healthy_vms} VM(s) healthy, {warn_vms} warning, {fail_vms} fail")
    if host_fails or host_warns:
        print(f"  Host:    {host_fails} fail, {host_warns} warn")
    print()

    any_fail = fail_vms > 0 or host_fails > 0
    return 1 if any_fail else 0
