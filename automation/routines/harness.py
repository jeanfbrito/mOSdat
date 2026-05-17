"""R3: test harness for running a routine against a VM with an optional fixture.

Entry point: run_routine_test()

Exit codes:
  0 — routine + postconditions passed
  1 — routine failed
  2 — fixture setup failed
  3 — VM unreachable
"""

from __future__ import annotations

import json
import sys
import time
from datetime import timezone, datetime
from pathlib import Path
from typing import Optional

from automation.routines.loader import load_routine
from automation.routines.runner import expand_call
from automation.routines.fixtures import load_fixture, Fixture


# ---------------------------------------------------------------------------
# Test history logging (R6)
# ---------------------------------------------------------------------------

def _history_path() -> Path:
    """Return results/routines-test-history.jsonl (project-root relative)."""
    repo_root = Path(__file__).parent.parent.parent
    return repo_root / "results" / "routines-test-history.jsonl"


def _append_test_history(
    routine_name: str,
    vm_name: str,
    fixture_name: Optional[str],
    exit_code: int,
    duration_ms: int,
) -> None:
    """Append one result line to the test history JSONL file."""
    try:
        history_file = _history_path()
        history_file.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "routine": routine_name,
            "vm": vm_name,
            "fixture": fixture_name or "",
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with history_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        # Non-fatal — never let history logging crash the harness
        print(f"[routines:test] WARNING: could not write test history: {exc}", flush=True)


# ---------------------------------------------------------------------------
# SSH probe
# ---------------------------------------------------------------------------

def _probe_ssh(vm_ip: str, vm_user: str) -> bool:
    """Return True if SSH is reachable (BatchMode, 10 s timeout)."""
    import subprocess
    opts = [
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
    ]
    cmd = ["ssh"] + opts + [f"{vm_user}@{vm_ip}", "echo OK"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        return result.returncode == 0 and "OK" in result.stdout
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Synthetic scenario builder
# ---------------------------------------------------------------------------

def build_synthetic_scenario(
    routine_name: str,
    fixture: Optional[Fixture] = None,
    with_inputs: Optional[dict] = None,
) -> list[dict]:
    """Build a flat step list: [fixture.setup_steps] + [routine expansion] + [fixture.teardown_steps].

    :param routine_name: slug of the routine to run
    :param fixture: optional Fixture; contributes setup_steps and teardown_steps
    :param with_inputs: optional dict of routine input overrides
    :returns: flat list of step dicts
    :raises FileNotFoundError: if routine not found
    """
    call: dict = {"routine": routine_name}
    if with_inputs:
        call = {"routine": {"name": routine_name, "with": with_inputs}}

    expanded = expand_call(call, parent_vars={})

    setup = list(fixture.setup_steps) if fixture else []
    teardown = list(fixture.teardown_steps) if fixture else []

    return setup + expanded + teardown


# ---------------------------------------------------------------------------
# Config injection from fixture vm_state
# ---------------------------------------------------------------------------

def _inject_from_fixture(ssh, fixture: Fixture, install_path: str, log_fn) -> None:
    """If fixture.vm_state has 'config' and/or 'servers', call inject()."""
    vm_state = fixture.vm_state or {}
    config_block = vm_state.get("config")
    if not config_block:
        return

    from automation.setup.inject_config import inject

    # Extract servers list from config block (nested under 'servers' key)
    servers_list = config_block.get("servers") or []
    servers: dict[str, str] = {}
    for entry in servers_list:
        if isinstance(entry, dict) and "url" in entry and "title" in entry:
            servers[entry["title"]] = entry["url"]

    # Build the config dict to pass — everything except 'servers' (handled separately)
    extra_config: dict = {k: v for k, v in config_block.items() if k != "servers"}

    inject(
        ssh,
        install_path=install_path,
        config=extra_config,
        servers=servers,
        log_fn=log_fn,
    )


# ---------------------------------------------------------------------------
# Main harness entry point
# ---------------------------------------------------------------------------

def run_routine_test(
    routine_name: str,
    vm_name: str,
    *,
    fixture_name: Optional[str] = None,
    model: Optional[str] = None,
    verify_model: Optional[str] = None,
    with_inputs: Optional[dict] = None,
    screenshot_dir: Optional[Path] = None,
    config_path: Optional[str] = None,
) -> int:
    """Run a routine against a VM with an optional fixture.

    Returns:
      0 — routine + postconditions passed
      1 — routine failed
      2 — fixture setup / inject failed
      3 — VM unreachable
    """
    log = lambda msg: print(f"[routines:test] {msg}", flush=True)

    # --- Load routine (fail fast) ---
    try:
        load_routine(routine_name)
    except FileNotFoundError as exc:
        print(f"[routines:test] ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[routines:test] ERROR loading routine {routine_name!r}: {exc}", file=sys.stderr)
        return 1

    # --- Load fixture ---
    fixture: Optional[Fixture] = None
    if fixture_name:
        try:
            fixture = load_fixture(fixture_name)
            log(f"Fixture: {fixture.name} — {fixture.description}")
        except FileNotFoundError as exc:
            print(f"[routines:test] ERROR: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"[routines:test] ERROR loading fixture {fixture_name!r}: {exc}", file=sys.stderr)
            return 2

    # --- Resolve VM config ---
    vm_ip: Optional[str] = None
    vm_user: str = "user"
    install_path: str = "/opt/Rocket.Chat/rocketchat-desktop"

    if config_path:
        try:
            from automation.config import load_config
            cfg = load_config(config_path)
            if vm_name not in cfg.vm_by_name:
                print(f"[routines:test] ERROR: VM {vm_name!r} not in config", file=sys.stderr)
                return 3
            vm = cfg.vm_by_name[vm_name]
            vm_ip = vm.ip
            vm_user = vm.user
            # prefer per-VM package app_path if set
            for pkg in vm.packages:
                if pkg.app_path and "{file}" not in pkg.app_path:
                    install_path = pkg.app_path
                    break
        except Exception as exc:
            print(f"[routines:test] ERROR loading config: {exc}", file=sys.stderr)
            return 3

    # --- SSH probe ---
    if vm_ip:
        log(f"Probing SSH {vm_user}@{vm_ip} ...")
        if not _probe_ssh(vm_ip, vm_user):
            print(f"[routines:test] ERROR: VM {vm_name!r} ({vm_ip}) unreachable via SSH", file=sys.stderr)
            return 3
        log("SSH OK")
    else:
        log(f"No config supplied — skipping SSH probe (VM: {vm_name})")

    # --- Build synthetic scenario ---
    try:
        steps = build_synthetic_scenario(routine_name, fixture=fixture, with_inputs=with_inputs)
    except Exception as exc:
        print(f"[routines:test] ERROR building scenario: {exc}", file=sys.stderr)
        return 2

    log(f"Synthetic scenario: {len(steps)} steps")

    # --- Inject config from fixture vm_state.config ---
    if fixture and vm_ip:
        vm_state = fixture.vm_state or {}
        if vm_state.get("config"):
            log("Injecting config from fixture vm_state.config ...")
            try:
                from automation.transport.ssh import SSHClient
                ssh = SSHClient(vm_ip, vm_user, connect_timeout=10)
                _inject_from_fixture(ssh, fixture, install_path, log)
            except Exception as exc:
                print(f"[routines:test] ERROR during config inject: {exc}", file=sys.stderr)
                return 2

    # --- Set up screenshot dir ---
    if screenshot_dir is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        screenshot_dir = Path("results") / "routines" / vm_name / f"{routine_name}_{ts}"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    log(f"Screenshots: {screenshot_dir}")

    # --- Build runner (requires live VM connections) ---
    t_run_start = time.time()

    def _return(exit_code: int) -> int:
        """Log to history and return exit_code."""
        elapsed_ms = int((time.time() - t_run_start) * 1000)
        _append_test_history(routine_name, vm_name, fixture_name, exit_code, elapsed_ms)
        return exit_code

    if not vm_ip:
        log("WARNING: no VM IP — cannot run FunctionalRunner steps without config")
        log("(dry-run: scenario built successfully)")
        log(f"  {len(steps)} steps ready")
        return _return(0)

    try:
        from automation.transport.ssh import SSHClient
        from automation.transport.vnc import VncClient
        from automation.vlm.client import VLMClient
        from automation.vlm.input import InputInjector
        from automation.vlm.screenshot import Screenshotter
        from automation.proxmox.api import ProxmoxAPI
        from automation.runners.functional import FunctionalRunner
        from automation.runners.scenario_loader import parse_step

        # Re-load config (already loaded above for VM lookup)
        from automation.config import load_config
        cfg = load_config(config_path)
        vm = cfg.vm_by_name[vm_name]

        proxmox = ProxmoxAPI(cfg.proxmox)
        ssh = SSHClient(vm_ip, vm_user, connect_timeout=10)
        vlm = VLMClient(
            model=model or cfg.vlm.model,
            verify_model=verify_model or cfg.vlm.verify_model,
            base_url=cfg.vlm.base_url,
        )

        t_start = time.time()

        with VncClient(proxmox, vmid=vm.vmid) as vnc:
            screenshotter = Screenshotter(vnc)
            injector = InputInjector(vnc, ssh, vm.is_windows)
            runner = FunctionalRunner(
                vlm=vlm,
                screenshotter=screenshotter,
                injector=injector,
                screenshot_dir=screenshot_dir,
                log_fn=log,
            )
            parsed_steps = [parse_step(s) for s in steps]
            passed, summary = runner.run_test(parsed_steps, name=routine_name)

        elapsed = int((time.time() - t_start) * 1000)
        status = "PASS" if passed else "FAIL"
        log(f"Result: {status} ({elapsed} ms) — screenshots: {screenshot_dir}")
        return _return(0 if passed else 1)

    except Exception as exc:
        print(f"[routines:test] ERROR running FunctionalRunner: {exc}", file=sys.stderr)
        return _return(1)
