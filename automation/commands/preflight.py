"""``mosdat preflight`` — pre-run health checks for a functional scenario.

Checks (each prints PASS / FAIL / SKIP):
  1. YAML parse + ScenarioModel schema validation
  2. Per-VM:
     a. SSH reachable (BatchMode, timeout 10 s)
     b. X11 cookie present
     c. Tool deps: wmctrl xclip xdotool xdg-mime xdg-open
     d. Binary exists at install path
     e. Symbol grep in <asar> for each --expect-symbol
     f. userData dir detection (kill RC, wipe, launch 18 s, list dirs, kill RC)
     g. Disk free > 1 GB in /tmp
     h. No stale RC processes
  3. Dry-run first 3 ``shell:`` blocks from scenario (no UI steps)

Exit codes:
  0 — all PASS
  1 — one or more FAIL
  2 — internal error (bad args, file not found, etc.)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from automation.config import load_config
from automation.transport.ssh import SSHClient


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

_ICON = {PASS: "✓", FAIL: "✗", SKIP: "-"}


def _print(status: str, label: str, detail: str = "") -> None:
    icon = _ICON.get(status, "?")
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{icon}] {status:<4}  {label}{suffix}")


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

def _check_yaml(scenario_path: Path) -> tuple[str, str, Optional[dict]]:
    """Parse YAML and validate with ScenarioModel. Returns (status, detail, raw_data)."""
    try:
        import yaml
    except ImportError:
        return FAIL, "PyYAML not installed", None

    try:
        with open(scenario_path) as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        return FAIL, f"YAML parse error: {exc}", None

    try:
        from automation.scenario import ScenarioModel
        ScenarioModel.model_validate(data)
    except Exception as exc:
        first_line = str(exc).splitlines()[0][:120]
        return FAIL, f"Schema error: {first_line}", None

    step_count = len(data.get("steps", []))
    return PASS, f"{step_count} steps", data


def _check_ssh(ssh) -> tuple[str, str]:
    """BatchMode SSH reachability check (timeout 10 s)."""
    import subprocess
    opts = [
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
    ]
    cmd = ["ssh"] + opts + [f"{ssh.user}@{ssh.host}", "echo OK"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if result.returncode == 0 and "OK" in result.stdout:
            return PASS, f"{ssh.user}@{ssh.host}"
        return FAIL, f"exit {result.returncode}: {result.stderr.strip()[:80]}"
    except subprocess.TimeoutExpired:
        return FAIL, "timed out (10 s)"
    except Exception as exc:
        return FAIL, str(exc)[:80]


def _check_x11_cookie(ssh) -> tuple[str, str]:
    r = ssh.run(
        r"ls /run/user/1000/.mutter-Xwaylandauth.* /run/user/1000/gdm/Xauthority 2>/dev/null | head -1",
        timeout=10,
    )
    path = r.stdout.strip()
    if path:
        return PASS, path
    return FAIL, "no cookie at /run/user/1000/.mutter-Xwaylandauth.* or /run/user/1000/gdm/Xauthority"


def _check_tool_deps(ssh) -> tuple[str, str]:
    tools = ["wmctrl", "xclip", "xdotool", "xdg-mime", "xdg-open"]
    missing = []
    for tool in tools:
        r = ssh.run(f"command -v {tool} >/dev/null 2>&1 && echo YES || echo NO", timeout=10)
        if r.stdout.strip() != "YES":
            missing.append(tool)
    if missing:
        return FAIL, f"missing: {', '.join(missing)}"
    return PASS, ", ".join(tools)


def _check_binary(ssh, binary_path: str) -> tuple[str, str]:
    r = ssh.run(f"test -f {binary_path} && echo EXISTS || echo MISSING", timeout=10)
    if r.stdout.strip() == "EXISTS":
        return PASS, binary_path
    return FAIL, f"not found: {binary_path}"


def _find_asar(ssh, binary_path: str) -> Optional[str]:
    """Best-effort: look for app.asar next to the binary's install dir."""
    install_dir = str(Path(binary_path).parent)
    r = ssh.run(
        f"find {install_dir} -name 'app.asar' -maxdepth 4 2>/dev/null | head -1",
        timeout=15,
    )
    return r.stdout.strip() or None


def _check_symbol(ssh, asar_path: str, symbol: str) -> tuple[str, str]:
    r = ssh.run(
        f"strings {asar_path} | grep -c {symbol} 2>/dev/null || echo 0",
        timeout=30,
    )
    count_str = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "0"
    try:
        count = int(count_str)
    except ValueError:
        count = 0
    if count > 0:
        return PASS, f"{count} occurrences in {asar_path}"
    return FAIL, f"symbol '{symbol}' not found in {asar_path}"


def _check_userdata_dirs(ssh, binary_path: str) -> tuple[str, str]:
    """Kill RC, wipe config dirs, launch 18 s, list new dirs, kill RC."""
    kill_cmd = (
        "for attempt in 1 2 3; do "
        "  pkill -TERM -f '/opt/Rocket.Chat' 2>/dev/null || true; "
        "  pkill -TERM -f 'rocketchat-desktop' 2>/dev/null || true; "
        "  sleep 1; "
        "  pgrep -af '(/opt/Rocket.Chat|rocketchat-desktop)' >/dev/null 2>&1 || break; "
        "done; "
        "pkill -KILL -f '/opt/Rocket.Chat' 2>/dev/null || true; "
        "pkill -KILL -f 'rocketchat-desktop' 2>/dev/null || true; "
        "sleep 1"
    )
    wipe_cmd = (
        "for d in \"$HOME/.config/Rocket.Chat\" \"$HOME/.config/Rocket.Chat (development)\"; do "
        "  rm -rf \"$d\"; "
        "done"
    )
    # Capture config dirs before launch
    list_before_cmd = "ls -1d \"$HOME/.config/\"Rocket* 2>/dev/null || true"

    launch_cmd = (
        "XAUTH=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1); "
        "[ -z \"$XAUTH\" ] && XAUTH=/run/user/1000/gdm/Xauthority; "
        f"export DISPLAY=:0 XAUTHORITY=\"$XAUTH\"; "
        f"nohup {binary_path} --no-sandbox --disable-gpu --ozone-platform=x11 "
        ">/dev/null 2>&1 &"
    )
    list_after_cmd = (
        "sleep 18; "
        "ls -1d \"$HOME/.config/\"Rocket* 2>/dev/null || true"
    )
    kill_after_cmd = (
        "pkill -KILL -f '/opt/Rocket.Chat' 2>/dev/null || true; "
        "pkill -KILL -f 'rocketchat-desktop' 2>/dev/null || true"
    )

    try:
        ssh.run(kill_cmd, timeout=15)
        ssh.run(wipe_cmd, timeout=10)
        ssh.run(launch_cmd, timeout=10)
        r = ssh.run(list_after_cmd, timeout=30)
        ssh.run(kill_after_cmd, timeout=10)
        dirs = [ln.strip() for ln in r.stdout.strip().splitlines() if ln.strip()]
        if dirs:
            short = [Path(d).name for d in dirs]
            return PASS, "dirs: " + ", ".join(f'"{n}"' for n in short)
        return FAIL, "no ~/.config/Rocket* dirs appeared after 18 s launch"
    except Exception as exc:
        return FAIL, f"detection failed: {exc}"


def _check_disk_free(ssh) -> tuple[str, str]:
    r = ssh.run("df --output=avail -B1 /tmp 2>/dev/null | tail -1", timeout=10)
    avail_str = r.stdout.strip()
    try:
        avail = int(avail_str)
        gb = avail / (1024 ** 3)
        if avail >= 1024 ** 3:
            return PASS, f"{gb:.1f} GB free in /tmp"
        return FAIL, f"only {gb:.2f} GB free in /tmp (need ≥ 1 GB)"
    except ValueError:
        return FAIL, f"could not parse df output: {avail_str!r}"


def _check_no_stale_rc(ssh) -> tuple[str, str]:
    r = ssh.run(
        "pgrep -af '(/opt/Rocket.Chat|rocketchat-desktop)' 2>/dev/null || true",
        timeout=10,
    )
    procs = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    if not procs:
        return PASS, "no stale RC processes"
    return FAIL, f"{len(procs)} stale process(es): {procs[0][:80]}"


def _dry_run_shell_steps(ssh, steps: list) -> list[tuple[str, str, str]]:
    """Find first 3 shell: blocks, run them, return list of (label, status, detail)."""
    results = []
    shell_count = 0
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            continue
        shell_script = raw.get("shell")
        if not shell_script:
            continue
        shell_count += 1
        label = f"shell step #{shell_count} (step {i + 1})"
        try:
            r = ssh.run(shell_script, timeout=60)
            last_5 = "\n".join(
                (r.stdout or "").strip().splitlines()[-5:]
            )
            if r.returncode == 0:
                results.append((label, PASS, f"exit 0\n{last_5}"))
            else:
                results.append((label, FAIL, f"exit {r.returncode}\n{last_5}"))
        except Exception as exc:
            results.append((label, FAIL, str(exc)[:120]))
        if shell_count >= 3:
            break
    return results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_preflight(
    config_path: str,
    scenario_name: str,
    vms: list[str],
    expected_symbols: Optional[list[str]] = None,
) -> int:
    """Run all preflight checks. Returns 0 (all PASS), 1 (FAIL), 2 (error)."""
    expected_symbols = expected_symbols or []
    any_fail = False

    # --- Resolve paths ---
    config_path = Path(config_path)
    if not config_path.exists():
        print(f"[preflight] ERROR: config not found: {config_path}", file=sys.stderr)
        return 2

    try:
        config = load_config(config_path)
    except Exception as exc:
        print(f"[preflight] ERROR loading config: {exc}", file=sys.stderr)
        return 2

    framework_root = config.framework_path
    scenarios_dir = framework_root / "shared" / "scenarios" / "functional"
    scenario_path = scenarios_dir / f"{scenario_name}.yaml"

    print(f"\n[preflight] Scenario: {scenario_name}")
    print(f"[preflight] Config:   {config_path}")
    print(f"[preflight] VMs:      {', '.join(vms)}")
    if expected_symbols:
        print(f"[preflight] Symbols:  {', '.join(expected_symbols)}")
    print()

    # -----------------------------------------------------------------------
    # Check 1: YAML + schema
    # -----------------------------------------------------------------------
    print("=== 1. Scenario YAML + schema ===")
    if not scenario_path.exists():
        _print(FAIL, "File exists", f"not found: {scenario_path}")
        return 2

    yaml_status, yaml_detail, raw_data = _check_yaml(scenario_path)
    _print(yaml_status, "YAML parse + ScenarioModel", yaml_detail)
    if yaml_status == FAIL:
        any_fail = True

    raw_steps = (raw_data or {}).get("steps", []) if raw_data else []

    print()

    # -----------------------------------------------------------------------
    # Check 2: Per-VM
    # -----------------------------------------------------------------------
    for vm_name in vms:
        if vm_name not in config.vm_by_name:
            print(f"=== VM: {vm_name} ===")
            _print(FAIL, "VM known in config", f"'{vm_name}' not in config")
            any_fail = True
            print()
            continue

        vm = config.vm_by_name[vm_name]
        ssh = SSHClient(vm.ip, vm.user, connect_timeout=10)

        print(f"=== 2. VM: {vm_name} ({vm.ip}) ===")

        # a. SSH reachable
        s, d = _check_ssh(ssh)
        _print(s, "SSH reachable", d)
        if s == FAIL:
            any_fail = True
            _print(SKIP, "X11 cookie", "skipped — SSH unreachable")
            _print(SKIP, "Tool deps", "skipped — SSH unreachable")
            _print(SKIP, "Binary exists", "skipped — SSH unreachable")
            for sym in expected_symbols:
                _print(SKIP, f"Symbol: {sym}", "skipped — SSH unreachable")
            _print(SKIP, "userData dir detection", "skipped — SSH unreachable")
            _print(SKIP, "Disk free /tmp", "skipped — SSH unreachable")
            _print(SKIP, "No stale RC processes", "skipped — SSH unreachable")
            print()
            continue

        # b. X11 cookie
        s, d = _check_x11_cookie(ssh)
        _print(s, "X11 cookie", d)
        if s == FAIL:
            any_fail = True

        # c. Tool deps
        s, d = _check_tool_deps(ssh)
        _print(s, "Tool deps (wmctrl xclip xdotool xdg-mime xdg-open)", d)
        if s == FAIL:
            any_fail = True

        # d. Binary exists
        binary_path = config.app.binary
        # prefer per-VM package app_path if set
        for pkg in vm.packages:
            if pkg.app_path and "{file}" not in pkg.app_path:
                binary_path = pkg.app_path
                break

        s, d = _check_binary(ssh, binary_path)
        _print(s, "Binary exists", d)
        if s == FAIL:
            any_fail = True

        # e. Symbol grep
        if expected_symbols:
            asar_path = _find_asar(ssh, binary_path)
            if not asar_path:
                for sym in expected_symbols:
                    _print(FAIL, f"Symbol: {sym}", "app.asar not found — cannot grep")
                    any_fail = True
            else:
                for sym in expected_symbols:
                    s, d = _check_symbol(ssh, asar_path, sym)
                    _print(s, f"Symbol: {sym}", d)
                    if s == FAIL:
                        any_fail = True

        # f. userData dir detection
        s, d = _check_userdata_dirs(ssh, binary_path)
        _print(s, "userData dir detection", d)
        if s == FAIL:
            any_fail = True

        # g. Disk free > 1 GB
        s, d = _check_disk_free(ssh)
        _print(s, "Disk free > 1 GB (/tmp)", d)
        if s == FAIL:
            any_fail = True

        # h. No stale RC processes
        s, d = _check_no_stale_rc(ssh)
        _print(s, "No stale RC processes", d)
        if s == FAIL:
            any_fail = True

        print()

        # -----------------------------------------------------------------------
        # Check 3: Dry-run first 3 shell: blocks
        # -----------------------------------------------------------------------
        print(f"=== 3. Dry-run shell steps on {vm_name} ===")
        if not raw_steps:
            _print(SKIP, "Dry-run", "no steps loaded (YAML failed)")
        else:
            dry_results = _dry_run_shell_steps(ssh, raw_steps)
            if not dry_results:
                _print(SKIP, "Dry-run", "no shell: steps found in first blocks")
            else:
                for label, s, detail in dry_results:
                    # Show only first line of detail inline; rest as indented block
                    lines = detail.strip().splitlines()
                    first = lines[0] if lines else ""
                    _print(s, label, first)
                    for ln in lines[1:]:
                        print(f"             {ln}")
                    if s == FAIL:
                        any_fail = True

        print()

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    overall = FAIL if any_fail else PASS
    icon = _ICON[overall]
    print(f"[{icon}] Overall: {overall}")
    return 1 if any_fail else 0
