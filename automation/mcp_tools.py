"""
mOSdat MCP tool definitions and implementations.

All tool metadata (TOOL_DEFINITIONS) and handler functions live here.
mcp_server.py imports these and wires them into the JSON-RPC dispatch table.
"""

import argparse
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

FRAMEWORK = Path(__file__).parent.parent

Request = dict[str, Any]

# run_build() exit codes from automation.commands.build
_RC_BUILD_FAIL = {
    2: "build failed",
    4: "clone/fetch failed",
    5: "invalid args / preconditions",
}
_RC_DEPLOY_FAIL = {
    1: "verify-symbol missing on deployed artifact",
    3: "deploy failed (scp/install)",
}

# ── Config helpers ──────────────────────────────────────────────────────────


def _config_path() -> Optional[Path]:
    """Locate the mosdat TOML config without loading it."""
    env_cfg = os.environ.get("MOSDAT_CONFIG")
    if env_cfg:
        return Path(env_cfg)
    cwd_cfg = Path("rocketchat.toml")
    if cwd_cfg.exists():
        return cwd_cfg
    examples = FRAMEWORK / "examples"
    for f in ["ubuntu2404.toml", "rocketchat.toml"]:
        p = examples / f
        if p.exists():
            return p
    return None


def _config():
    """Find and load the most likely config file."""
    from automation.config import load_config

    path = _config_path()
    if path is None:
        raise FileNotFoundError(
            "No mosdat config found. Set MOSDAT_CONFIG or run from project dir."
        )
    return load_config(path)


def _vm_config(vm_name: str):
    cfg = _config()
    vm = cfg.vm_by_name.get(vm_name)
    if not vm:
        names = list(cfg.vm_by_name.keys())
        raise ValueError(f"Unknown VM '{vm_name}'. Available: {', '.join(names)}")
    return vm, cfg


def _get_proxmox_api():
    from automation.proxmox.api import ProxmoxAPI

    cfg = _config()
    return ProxmoxAPI(cfg.proxmox)


def _ssh(cmd: str, vm_name: str, timeout: int = 30) -> tuple[str, int]:
    """Run a command on a VM via SSH, return (stdout, exit_code)."""
    from automation.transport.ssh import SSHClient

    vm, _ = _vm_config(vm_name)
    ssh = SSHClient(vm.ip, user=vm.user or "root")
    try:
        r = ssh.run(cmd, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except Exception as e:
        return str(e), 1


# ── Uniform envelope + lookup helpers (US1 foundation) ──────────────────────


def _envelope(ok, error=None, degraded=None, **fields) -> dict:
    """Uniform MCP tool result (research.md Decision 2).

    ``ok: false`` always carries a non-null ``error``. Degraded/partial
    conditions (e.g. VLM backend down) go in ``degraded`` so they are
    distinct from a hard failure.
    """
    result = {
        "ok": bool(ok),
        "error": None if ok else (error or "unknown error"),
        "degraded": list(degraded) if degraded else [],
    }
    for key, value in fields.items():
        if key in ("ok", "error", "degraded"):
            continue
        result[key] = value
    return result


def _resolve_vm(name: str):
    """Look up a configured VM by alias.

    Returns ``(VMConfig, None)`` on success or ``(None, error_envelope)``
    naming the invalid value plus valid alternatives (FR-005).
    """
    if not name:
        return None, _envelope(False, error="missing required argument 'vm'")
    try:
        cfg = _config()
    except FileNotFoundError as e:
        return None, _envelope(False, error=str(e))
    vm = cfg.vm_by_name.get(name)
    if vm is None:
        names = sorted(cfg.vm_by_name.keys())
        alt = ", ".join(names) if names else "(none)"
        return None, _envelope(
            False, error=f"unknown VM '{name}' — available: {alt}"
        )
    return vm, None


def _scenario_platform_from_path(path: Path) -> str:
    joined = "/".join(p.lower() for p in path.parts)
    if "windows" in joined:
        return "windows"
    return "linux"


def _iter_scenario_yaml() -> list[Path]:
    base = FRAMEWORK / "shared" / "scenarios"
    if not base.exists():
        return []
    return sorted(p for p in base.rglob("*.yaml") if p.is_file())


def _scenario_names(platform: Optional[str] = None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for path in _iter_scenario_yaml():
        if platform and _scenario_platform_from_path(path) != platform:
            continue
        if path.stem in seen:
            continue
        seen.add(path.stem)
        names.append(path.stem)
    return names


def _resolve_scenario(name: str, platform: Optional[str] = None):
    """Look up a scenario YAML by name, optionally filtered by platform.

    Returns ``(metadata, None)`` on success or ``(None, error_envelope)``
    naming the invalid value plus valid alternatives (FR-005).
    """
    if not name:
        return None, _envelope(False, error="missing required argument 'scenario'")
    stem = name[:-5] if str(name).endswith(".yaml") else name
    files = _iter_scenario_yaml()
    matches = [p for p in files if p.stem == stem]
    if platform:
        plat_matches = [p for p in matches if _scenario_platform_from_path(p) == platform]
        if plat_matches:
            matches = plat_matches
        elif matches:
            alts = _scenario_names(platform)
            listed = ", ".join(alts[:30]) if alts else "(none)"
            return None, _envelope(
                False,
                error=(
                    f"unknown scenario '{name}' for platform {platform} "
                    f"— available: {listed}"
                ),
            )
    if not matches:
        alts = _scenario_names(platform)
        listed = ", ".join(alts[:30]) if alts else "(none)"
        plat = f" for platform {platform}" if platform else ""
        return None, _envelope(
            False,
            error=f"unknown scenario '{name}'{plat} — available: {listed}",
        )
    path = matches[0]
    for candidate in matches:
        if "functional" in candidate.parts:
            path = candidate
            break
    step_count = 0
    try:
        from automation.runners.scenario_loader import load_test_yaml

        _, steps, _, _ = load_test_yaml(
            path, platform=platform or _scenario_platform_from_path(path)
        )
        step_count = len(steps)
    except Exception:
        step_count = 0
    try:
        rel = str(path.relative_to(FRAMEWORK))
    except ValueError:
        rel = str(path)
    meta = {
        "name": path.stem,
        "path": rel,
        "resolved_path": path,
        "platform": platform or _scenario_platform_from_path(path),
        "step_count": step_count,
    }
    return meta, None


def _vm_busy(vmid: int) -> bool:
    """Non-blocking probe of ``_vm_lock(vmid)``.

    Returns True if another process currently holds the per-VMID lock.
    Acquires and immediately releases the lock when it is free.
    """
    from automation.proxmox.gpu import ProxmoxLockTimeout
    from automation.proxmox.vm import _vm_lock

    try:
        with _vm_lock(int(vmid), timeout=0):
            return False
    except (ProxmoxLockTimeout, BlockingIOError):
        return True


@contextmanager
def _nonblocking_vm_lock(vmid: int):
    """Acquire ``_vm_lock(vmid)`` without waiting.

    Raises ``ProxmoxLockTimeout`` / ``BlockingIOError`` on contention so
    callers can return the FR-010 busy envelope immediately.
    """
    from automation.proxmox.vm import _vm_lock

    with _vm_lock(int(vmid), timeout=0):
        yield


def _busy_envelope(vmid: int) -> dict:
    return _envelope(False, error=f"vm busy: {vmid} held by another operation")


def _check_item(label: str, status: str, detail: str = "") -> dict:
    return {"label": label, "status": status, "detail": detail or ""}


def _ssh_client(vm):
    """Build an SSHClient for a VMConfig (connect_timeout matches doctor)."""
    from automation.transport.ssh import SSHClient

    return SSHClient(vm.ip, user=vm.user or "root", connect_timeout=8)


def _env_not_ready_envelope(vm_name: str, reason: str, **fields) -> dict:
    """FR-003 discriminator: environment problem, not a scenario assertion failure.

    ``verdict`` is always ``"error"`` — never ``"fail"``. ``env_not_ready`` is
    the field an agent should inspect to tell this apart from an in-run
    exception (which also uses ``verdict: "error"`` but omits the flag).
    """
    prefix = "environment not ready: "
    error = reason if str(reason).startswith(prefix) else prefix + reason
    payload = {
        "env_not_ready": True,
        "verdict": "error",
        "steps": [],
        "artifacts": [],
        "vm": vm_name,
    }
    payload.update(fields)
    return _envelope(False, error=error, **payload)


def _run_env_precheck(vm) -> Optional[dict]:
    """Cheap pre-run probe: SSH reachability + required tool deps.

    Returns an env-not-ready envelope if the VM cannot currently host a
    scenario run, else ``None``. Reuses ``doctor.check_ssh`` / ``check_deps``
    rather than reimplementing SSH. Linux-only dep checks are skipped on
    Windows (doctor itself skips Windows VMs).
    """
    from automation.commands.doctor import check_deps, check_ssh

    ssh = _ssh_client(vm)
    ssh_res = check_ssh(ssh)
    if ssh_res.status != "PASS":
        detail = ssh_res.detail or "unreachable"
        return _env_not_ready_envelope(
            vm.name, f"VM {vm.name} is unreachable ({detail})"
        )
    if getattr(vm, "is_windows", False):
        return None
    dep_results = check_deps(ssh)
    missing = [
        r.label.split(":", 1)[-1] for r in dep_results if r.status == "FAIL"
    ]
    if missing:
        return _env_not_ready_envelope(
            vm.name,
            f"VM {vm.name} is missing dependencies ({', '.join(missing)})",
        )
    return None


def _read_deployed_version(ssh) -> Optional[str]:
    """Installed app version via the same asar grep ``deploy_to_vm`` uses."""
    import shlex

    from automation.commands.build import _find_installed_asar

    try:
        asar = _find_installed_asar(ssh)
    except Exception:
        return None
    if not asar:
        return None
    ver_res = ssh.run(
        f"grep -a -m1 -oE '\"version\":\"[^\"]+\"' {shlex.quote(asar)} | head -1",
        timeout=60,
    )
    raw = (getattr(ver_res, "stdout", None) or "").strip()
    if not raw:
        return None
    if ":" in raw:
        rhs = raw.split(":", 1)[1].strip().strip('"')
        return rhs or None
    return raw


def _expected_build_check(ssh, deployed_version, expect_pr, expect_symbol) -> dict:
    """``deployed_build_matches_expected`` check for ``mosdat_readiness``."""
    from automation.commands.build import _find_installed_asar, _verify_symbols_on_vm

    reasons: list[str] = []
    asar = None
    try:
        asar = _find_installed_asar(ssh)
    except Exception as e:
        return _check_item(
            "deployed_build_matches_expected",
            "FAIL",
            f"could not inspect installed build: {e}",
        )

    if expect_pr is not None and expect_pr != "":
        try:
            pr = int(expect_pr)
        except (TypeError, ValueError):
            return _check_item(
                "deployed_build_matches_expected",
                "FAIL",
                f"invalid expect_pr {expect_pr!r} — expected an integer",
            )
        version = deployed_version or ""
        ver_l = version.lower()
        matched = f"pr{pr}" in ver_l or f"#{pr}" in version
        if not matched and asar:
            missing = _verify_symbols_on_vm(ssh, asar, [f"pr{pr}"])
            matched = not missing
        if not matched:
            found = version or "(unknown)"
            reasons.append(f"expected PR #{pr}, found {found}")

    if expect_symbol:
        symbols = _parse_verify_symbols(expect_symbol)
        if not asar:
            reasons.append("no installed app.asar — cannot verify symbols")
        elif symbols:
            missing = _verify_symbols_on_vm(ssh, asar, symbols)
            if missing:
                reasons.append(f"missing symbols: {', '.join(missing)}")

    if reasons:
        return _check_item(
            "deployed_build_matches_expected", "FAIL", "; ".join(reasons)
        )
    return _check_item(
        "deployed_build_matches_expected", "PASS", deployed_version or ""
    )


def _raw_step_count(path: Path) -> int:
    """Top-level YAML ``steps:`` length (discovery; not post-expansion)."""
    try:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f) or {}
        steps = data.get("steps") or []
        return len(steps) if isinstance(steps, list) else 0
    except Exception:
        return 0


def _deploy_dict(res) -> dict:
    return {
        "vm": getattr(res, "vm", ""),
        "scp_ok": bool(getattr(res, "scp_ok", False)),
        "install_ok": bool(getattr(res, "install_ok", False)),
        "installed_version": getattr(res, "installed_version", "") or "",
        "missing_symbols": list(getattr(res, "missing_symbols", []) or []),
        "error": getattr(res, "error", "") or "",
    }


def _synthetic_deploy(vm_name: str, *, ok: bool, error: str = "") -> dict:
    return {
        "vm": vm_name,
        "scp_ok": ok,
        "install_ok": ok,
        "installed_version": "",
        "missing_symbols": [],
        "error": "" if ok else error,
    }


# ── Tool definitions ────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "mosdat_list_vms",
        "description": "List available Proxmox VMs with their status (running/stopped)",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "mosdat_vm_start",
        "description": "Start a Proxmox VM",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm_name": {"type": "string", "description": "VM name (e.g. ubuntu2404, fedora42)"}
            },
            "required": ["vm_name"],
        },
    },
    {
        "name": "mosdat_vm_stop",
        "description": "Stop a Proxmox VM",
        "inputSchema": {
            "type": "object",
            "properties": {"vm_name": {"type": "string", "description": "VM name"}},
            "required": ["vm_name"],
        },
    },
    {
        "name": "mosdat_vm_status",
        "description": "Check if a VM is running",
        "inputSchema": {
            "type": "object",
            "properties": {"vm_name": {"type": "string", "description": "VM name"}},
            "required": ["vm_name"],
        },
    },
    {
        "name": "mosdat_build",
        "description": (
            "Clone/build a PR (full mosdat build flow) and optionally deploy "
            "to a VM with symbol verification"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pr": {
                    "type": "number",
                    "description": "Pull request number to clone and build",
                },
                "target": {
                    "type": "string",
                    "description": "Build target: deb | rpm | appimage | exe (default: deb)",
                },
                "deploy_to": {
                    "type": "string",
                    "description": "VM name to deploy the artifact to (omit to build only)",
                },
                "verify_symbol": {
                    "description": "Symbol(s) expected in the deployed app.asar (string or list)",
                },
                "repo": {
                    "type": "string",
                    "description": "owner/repo (default: RocketChat/Rocket.Chat.Electron)",
                },
            },
            "required": ["pr"],
        },
    },
    {
        "name": "mosdat_deploy",
        "description": "SCP a built package to a VM and install it",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm": {"type": "string", "description": "Target VM name"},
                "vm_name": {"type": "string", "description": "Target VM name (alias of vm)"},
                "artifact_path": {"type": "string", "description": "Path to local .deb/.rpm/.exe file"},
                "package_path": {"type": "string", "description": "Path to local package (alias of artifact_path)"},
                "target": {"type": "string", "description": "deb | rpm | appimage | exe (inferred from suffix if omitted)"},
            },
            "required": [],
        },
    },
    {
        "name": "mosdat_run_smoke",
        "description": "Run smoke tests on a VM (launch + basic UI check)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm_name": {"type": "string", "description": "VM name"},
                "timeout": {"type": "number", "description": "Test timeout in seconds (default: 120)"},
            },
            "required": ["vm_name"],
        },
    },
    {
        "name": "mosdat_run_functional",
        "description": "Run a VLM functional test scenario on a VM (GUI interaction via screenshots + VLM)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm": {"type": "string", "description": "VM name to run on"},
                "vm_name": {"type": "string", "description": "VM name to run on (alias of vm)"},
                "scenario": {
                    "type": "string",
                    "description": "Scenario name (e.g. 'rocketchat-smoke', 'issues/3325')",
                },
                "model": {"type": "string", "description": "VLM model override (default: from config)"},
                "verify_model": {"type": "string", "description": "Verify model override for yes/no checks"},
                "save_screenshots": {"type": "boolean", "description": "Save screenshots on failure"},
                "timeout": {"type": "number", "description": "Scenario timeout in seconds (default: 900)"},
                "from_step": {"type": "number", "description": "Start from step N (1-indexed)"},
                "until_step": {"type": "number", "description": "Stop after step N"},
            },
            "required": ["vm_name", "scenario"],
        },
    },
    {
        "name": "mosdat_list_scenarios",
        "description": "List available functional test scenario YAML files",
        "inputSchema": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "Filter by platform: linux | windows (omit for all)",
                },
                "path": {"type": "string", "description": "Scenarios directory (default: shared/scenarios/)"},
            },
        },
    },
    {
        "name": "mosdat_readiness",
        "description": (
            "Check whether a target VM is ready (SSH reachable, tool deps, "
            "disk, optional deployed-build match) before a build/deploy/run"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm": {"type": "string", "description": "VM name (e.g. ubuntu2404)"},
                "vm_name": {"type": "string", "description": "VM name (alias of vm)"},
                "expect_pr": {
                    "type": "number",
                    "description": "PR number expected to be deployed (omit to skip)",
                },
                "expect_symbol": {
                    "description": "Symbol(s) expected in the deployed app.asar (string or list)",
                },
            },
            "required": ["vm"],
        },
    },
    {
        "name": "mosdat_ssh",
        "description": "Run an arbitrary shell command on a VM",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm_name": {"type": "string", "description": "VM name"},
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout": {"type": "number", "description": "Timeout in seconds (default: 30)"},
            },
            "required": ["vm_name", "command"],
        },
    },
]


# ── Tool implementations ────────────────────────────────────────────────────


def _list_vms(args: Optional[dict] = None) -> dict:
    """mosdat_list_vms: configured VMs in the uniform envelope (FR-004)."""
    try:
        cfg = _config()
    except FileNotFoundError as e:
        return _envelope(False, error=str(e), vms=[])

    status_by_vmid: dict[int, str] = {}
    status_by_name: dict[str, str] = {}
    degraded: list[str] = []
    try:
        api = _get_proxmox_api()
        node = getattr(getattr(api, "config", None), "node", None) or "pve"
        data = api.get(f"/nodes/{node}/qemu").get("data", []) or []
        for row in data:
            try:
                vmid = int(row.get("vmid"))
            except (TypeError, ValueError):
                continue
            status = row.get("status") or "unknown"
            status_by_vmid[vmid] = status
            name = row.get("name")
            if name:
                status_by_name[str(name)] = status
    except Exception:
        degraded.append("proxmox_unreachable")

    configured = getattr(cfg, "vms", None) or list(cfg.vm_by_name.values())
    vms = []
    for vm in configured:
        vmid = getattr(vm, "vmid", None)
        status = "unknown"
        if vmid is not None:
            status = status_by_vmid.get(int(vmid), "unknown")
        if status == "unknown":
            status = status_by_name.get(getattr(vm, "name", ""), "unknown")
        vms.append(
            {
                "name": vm.name,
                "vmid": vm.vmid,
                "os_type": getattr(vm, "os_type", None) or "linux",
                "status": status,
            }
        )
    return _envelope(True, degraded=degraded, vms=vms)


def _vm_start(req: Request, vm_name: str, jsonrpc_result) -> str:
    vm, _ = _vm_config(vm_name)
    api = _get_proxmox_api()
    api.start_vm(vm.vmid)
    return jsonrpc_result(req, {"status": "started", "vm": vm_name})


def _vm_stop(req: Request, vm_name: str, jsonrpc_result) -> str:
    vm, _ = _vm_config(vm_name)
    api = _get_proxmox_api()
    api.shutdown_vm(vm.vmid)
    return jsonrpc_result(req, {"status": "stopped", "vm": vm_name})


def _vm_status(req: Request, vm_name: str, jsonrpc_result) -> str:
    vm, _ = _vm_config(vm_name)
    api = _get_proxmox_api()
    status = api.get_vm_status(vm.vmid)
    return jsonrpc_result(req, {"name": vm_name, "vmid": vm.vmid, "status": status})


def _parse_verify_symbols(raw) -> list:
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, tuple)):
        values = [str(v) for v in raw]
    else:
        values = [str(raw)]
    from automation.commands.build import parse_verify_symbols

    return parse_verify_symbols(values)


def _call_run_build(ns):
    """Invoke ``run_build`` and capture any ``DeployResult`` it produces.

    ``run_build`` itself returns only an exit code. Wrapping the deploy
    helpers lets the MCP response surface the structured DeployOutcome
    without changing ``build.py``.
    """
    from automation.commands import build as build_mod

    captured: list = []
    orig_linux = build_mod.deploy_to_vm
    orig_win = build_mod.deploy_to_windows_vm
    orig_win_remote = build_mod.build_on_windows_vm

    def _wrap(orig):
        def _inner(*a, **k):
            res = orig(*a, **k)
            captured.append(res)
            return res

        return _inner

    build_mod.deploy_to_vm = _wrap(orig_linux)
    build_mod.deploy_to_windows_vm = _wrap(orig_win)
    build_mod.build_on_windows_vm = _wrap(orig_win_remote)
    try:
        rc = build_mod.run_build(ns)
    finally:
        build_mod.deploy_to_vm = orig_linux
        build_mod.deploy_to_windows_vm = orig_win
        build_mod.build_on_windows_vm = orig_win_remote
    return rc, captured


def _build_response(pr, target: str, deploy_to: Optional[str], rc: int, captured: list) -> dict:
    """Map ``run_build`` exit code (+ optional captured DeployResult) to envelope."""
    if captured:
        res = captured[0]
        outcome = _deploy_dict(res)
        if getattr(res, "ok", False) and rc == 0:
            return _envelope(True, pr=pr, target=target, build_ok=True, deploy=outcome)
        if (not getattr(res, "ok", False)) or rc in _RC_DEPLOY_FAIL:
            err = res.error or _RC_DEPLOY_FAIL.get(rc, f"deploy failed (rc={rc})")
            return _envelope(
                False, error=err, pr=pr, target=target, build_ok=True, deploy=outcome
            )
        return _envelope(
            False,
            error=_RC_BUILD_FAIL.get(rc, f"build failed (rc={rc})"),
            pr=pr,
            target=target,
            build_ok=False,
            deploy=None,
        )

    # Typical of unit tests that mock ``run_build`` (no deploy helper ran).
    if rc == 0:
        deploy = None if not deploy_to else _synthetic_deploy(deploy_to, ok=True)
        return _envelope(True, pr=pr, target=target, build_ok=True, deploy=deploy)
    if rc in _RC_DEPLOY_FAIL and deploy_to:
        err = _RC_DEPLOY_FAIL[rc]
        return _envelope(
            False,
            error=err,
            pr=pr,
            target=target,
            build_ok=True,
            deploy=_synthetic_deploy(deploy_to, ok=False, error=err),
        )
    return _envelope(
        False,
        error=_RC_BUILD_FAIL.get(rc, f"build failed (rc={rc})"),
        pr=pr,
        target=target,
        build_ok=False,
        deploy=None,
    )


def _build(args: dict) -> dict:
    """mosdat_build: full ``run_build()`` clone→build→deploy→verify-symbol flow."""
    from automation.commands.build import TARGETS
    from automation.proxmox.gpu import ProxmoxLockTimeout

    pr_raw = args.get("pr")
    if pr_raw is None or pr_raw == "":
        return _envelope(False, error="missing required argument 'pr'", build_ok=False, deploy=None)
    try:
        pr = int(pr_raw)
    except (TypeError, ValueError):
        return _envelope(
            False, error=f"invalid pr {pr_raw!r} — expected an integer", build_ok=False, deploy=None
        )

    target = args.get("target") or "deb"
    if target not in TARGETS:
        alts = ", ".join(sorted(TARGETS))
        return _envelope(
            False,
            error=f"unknown target '{target}' — available: {alts}",
            pr=pr,
            target=target,
            build_ok=False,
            deploy=None,
        )

    deploy_to = args.get("deploy_to") or None
    vm = None
    if deploy_to:
        vm, err = _resolve_vm(deploy_to)
        if err:
            err["pr"] = pr
            err["target"] = target
            err["build_ok"] = False
            err["deploy"] = None
            return err

    repo = args.get("repo") or "RocketChat/Rocket.Chat.Electron"
    ns = argparse.Namespace(
        pr=pr,
        repo=repo,
        target=target,
        clone_dir=args.get("clone_dir"),
        deploy=deploy_to or "",
        verify_symbol=_parse_verify_symbols(args.get("verify_symbol")),
        config=str(_config_path()) if _config_path() else None,
        dry_run=bool(args.get("dry_run", False)),
        artifact_first=args.get("artifact_first", True),
    )

    def _do_build() -> dict:
        try:
            rc, captured = _call_run_build(ns)
        except Exception as e:
            return _envelope(
                False, error=f"build failed: {e}", pr=pr, target=target, build_ok=False, deploy=None
            )
        return _build_response(pr, target, deploy_to, rc, captured)

    if vm is None:
        return _do_build()
    try:
        with _nonblocking_vm_lock(vm.vmid):
            return _do_build()
    except (ProxmoxLockTimeout, BlockingIOError):
        return _busy_envelope(vm.vmid)


def _infer_target(path: Path, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    suffix = path.suffix.lower()
    if suffix == ".deb":
        return "deb"
    if suffix == ".rpm":
        return "rpm"
    if suffix == ".exe":
        return "exe"
    if suffix == ".appimage" or path.name.endswith(".AppImage"):
        return "appimage"
    return "deb"


def _deploy(args: dict) -> dict:
    """mosdat_deploy: install an existing artifact, under the per-VM lock."""
    from automation.commands.build import (
        deploy_to_vm,
        deploy_to_windows_vm,
        resolve_target,
    )
    from automation.proxmox.gpu import ProxmoxLockTimeout

    vm_name = args.get("vm") or args.get("vm_name")
    artifact_path = args.get("artifact_path") or args.get("package_path")
    if not vm_name:
        return _envelope(False, error="missing required argument 'vm'")
    if not artifact_path:
        return _envelope(False, error="missing required argument 'artifact_path'")

    vm, err = _resolve_vm(vm_name)
    if err:
        return err

    pkg = Path(artifact_path)
    if not pkg.exists():
        return _envelope(False, error=f"Package not found: {artifact_path}")

    try:
        target = resolve_target(_infer_target(pkg, args.get("target")))
    except ValueError as e:
        return _envelope(False, error=str(e))

    def _do_deploy() -> dict:
        kwargs = dict(
            vm_name=vm.name,
            vm_ip=vm.ip,
            vm_user=vm.user or "root",
            artifact=pkg,
            target=target,
            verify_symbols=_parse_verify_symbols(args.get("verify_symbol")),
            dry_run=bool(args.get("dry_run", False)),
        )
        if vm.is_windows:
            res = deploy_to_windows_vm(**kwargs)
        else:
            res = deploy_to_vm(**kwargs)
        outcome = _deploy_dict(res)
        if res.ok:
            return _envelope(True, **outcome)
        return _envelope(False, error=res.error or "deploy failed", **outcome)

    try:
        with _nonblocking_vm_lock(vm.vmid):
            return _do_deploy()
    except (ProxmoxLockTimeout, BlockingIOError):
        return _busy_envelope(vm.vmid)


def _run_smoke(req: Request, vm_name: str, timeout: int = 120, *, jsonrpc_result, jsonrpc_error) -> str:
    vm, _ = _vm_config(vm_name)
    from automation.transport.ssh import SSHClient

    ssh = SSHClient(vm.ip, user=vm.user or "root")
    try:
        app_path = (
            vm.packages[0].app_path
            if vm.packages and vm.packages[0].app_path
            else "/opt/Rocket.Chat/rocketchat-desktop"
        )
        cmds = f"""
set -e
pkill -f "rocketchat" 2>/dev/null || true
sleep 1
timeout {timeout} {app_path} --no-sandbox &
APP_PID=$!
sleep 3
if kill -0 $APP_PID 2>/dev/null; then
    echo "APP_RUNNING"
    wmctrl -l 2>/dev/null | grep -i rocket || echo "WINDOW_NOT_FOUND"
    kill $APP_PID 2>/dev/null || true
    exit 0
fi
echo "APP_DIED"
exit 1
"""
        r2 = ssh.run(cmds, timeout=timeout + 30)
        return jsonrpc_result(req, {"vm": vm_name, "output": r2.stdout})
    except Exception as e:
        return jsonrpc_error(req, -32000, f"Smoke test failed: {e}")


def _probe_vlm_degraded(vlm) -> list:
    """Return ``["vlm_unavailable"]`` if warmup/probe failed, else ``[]``."""
    try:
        from automation.commands.functional_cmd import _warmup_vlm

        if not _warmup_vlm(vlm):
            return ["vlm_unavailable"]
    except Exception:
        return ["vlm_unavailable"]
    try:
        models = vlm.list_models()
        if not models:
            return ["vlm_unavailable"]
    except Exception:
        return ["vlm_unavailable"]
    return []


def _collect_artifacts(screenshot_dir: Optional[Path]) -> list:
    if screenshot_dir is None or not Path(screenshot_dir).exists():
        return []
    suffixes = {".png", ".jpg", ".jpeg", ".jsonl", ".html", ".mp4", ".gif", ".log"}
    paths: list[str] = []
    for p in sorted(Path(screenshot_dir).rglob("*")):
        if p.is_file() and p.suffix.lower() in suffixes:
            paths.append(str(p))
    return paths


def _artifact_for_step(artifacts: list, index: int) -> Optional[str]:
    needle = f"step{index + 1}"
    for path in artifacts:
        name = Path(path).name.lower()
        if needle in name:
            return path
    return None


def _steps_from_run(
    screenshot_dir: Optional[Path],
    step_count: int,
    passed: bool,
    log: str = "",
) -> list:
    """Normalize per-step outcomes from events.jsonl, falling back to run_test."""
    artifacts = _collect_artifacts(screenshot_dir)
    by_index: dict[int, dict] = {}
    events_path = Path(screenshot_dir) / "events.jsonl" if screenshot_dir else None
    if events_path is not None and events_path.exists():
        for line in events_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") != "step_end":
                continue
            raw_num = rec.get("step_num", 1)
            try:
                idx = int(str(raw_num).split(".")[0]) - 1
            except (TypeError, ValueError):
                continue
            if idx < 0:
                continue
            status = rec.get("status", "ok")
            outcome = {"ok": "pass", "failed": "fail", "fail": "fail", "skipped": "skipped"}.get(
                status, "pass"
            )
            by_index[idx] = {
                "index": idx,
                "outcome": outcome,
                "reason": rec.get("reason") or rec.get("error") or None,
                "artifact": _artifact_for_step(artifacts, idx),
            }
    if not by_index and step_count > 0:
        for i in range(step_count):
            failed = (not passed) and i == step_count - 1
            by_index[i] = {
                "index": i,
                "outcome": "fail" if failed else "pass",
                "reason": (log or None) if failed else None,
                "artifact": _artifact_for_step(artifacts, i),
            }
    elif not by_index:
        by_index[0] = {
            "index": 0,
            "outcome": "pass" if passed else "fail",
            "reason": None if passed else (log or None),
            "artifact": artifacts[0] if artifacts else None,
        }
    return [by_index[i] for i in sorted(by_index)]


def _invoke_functional_runner(vm, cfg, scenario_path: Path, args: dict) -> dict:
    """Run ``FunctionalRunner.run_test`` and return the raw per-VM outcome.

    Ordinary (non-confirm) scenarios return ``(passed: bool, log: str)`` from
    ``run_test`` — not ``BugConfirmationResult``. See data-model.md.
    """
    from automation.proxmox.api import ProxmoxAPI
    from automation.runners.functional import FunctionalRunner
    from automation.runners.scenario_loader import load_test_yaml
    from automation.transport.ssh import SSHClient
    from automation.transport.vnc import VncClient
    from automation.vlm.client import VLMClient
    from automation.vlm.input import InputInjector
    from automation.vlm.screenshot import Screenshotter

    vlm = VLMClient(
        base_url=cfg.vlm.base_url,
        model=args.get("model") or cfg.vlm.model,
        verify_model=args.get("verify_model") or cfg.vlm.verify_model or None,
        api_key=cfg.vlm.api_key,
        max_tokens_floor=cfg.vlm.max_tokens_floor,
    )
    degraded = _probe_vlm_degraded(vlm)
    proxmox = ProxmoxAPI(cfg.proxmox)
    ssh = SSHClient(vm.ip, vm.user)

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    screenshot_dir = FRAMEWORK / "results" / "functional" / f"{ts}_functional" / vm.name
    os.makedirs(str(screenshot_dir), exist_ok=True)

    name, steps, _, _ = load_test_yaml(scenario_path, cfg, platform=vm.os_type)
    from_step = int(args.get("from_step") or 1)
    until_step = args.get("until_step")
    until_step = int(until_step) if until_step is not None else len(steps)
    steps = steps[from_step - 1 : until_step]

    passed = False
    log = ""
    with VncClient(proxmox, vmid=vm.vmid) as vnc:
        screenshotter = Screenshotter(vnc)
        injector = InputInjector(vnc, ssh, vm.is_windows)
        from automation.atspi import AtspiClient as _AtspiClient
        from automation.uia import UiaClient as _UiaClient

        _ssh_atspi = SSHClient(vm.ip, vm.user, persistent=True)
        _atspi_client = None
        _uia_client = None
        if vm.is_windows:
            _uia_client = _UiaClient(ssh=_ssh_atspi, use_daemon=True)
        else:
            _atspi_client = _AtspiClient(ssh=_ssh_atspi)
        try:
            runner = FunctionalRunner(
                vlm=vlm,
                screenshotter=screenshotter,
                injector=injector,
                screenshot_dir=screenshot_dir,
                log_fn=lambda msg: print(f"[mOSdat] {msg}"),
                popup_sweep=True,
                atspi=_atspi_client,
                uia=_uia_client,
            )
            vars_ = {
                "app_path": vm.packages[0].app_path
                if vm.packages
                else "/opt/Rocket.Chat/rocketchat-desktop"
            }
            passed, log = runner.run_test(
                steps=steps,
                name=name,
                vars=vars_,
                results_dir=str(screenshot_dir.parent),
                vm_name=vm.name,
            )
        finally:
            if _ssh_atspi is not None:
                try:
                    _ssh_atspi.close_persistent()
                except Exception:
                    pass

    return {
        "passed": bool(passed),
        "log": log or "",
        "screenshot_dir": screenshot_dir,
        "step_count": len(steps),
        "degraded": degraded,
    }


def _run_functional(args: dict) -> dict:
    """mosdat_run_functional: run a scenario and return a three-state verdict."""
    vm_name = args.get("vm") or args.get("vm_name")
    scenario = args.get("scenario")
    vm, err = _resolve_vm(vm_name)
    if err:
        return err
    meta, err = _resolve_scenario(scenario, platform=getattr(vm, "os_type", None))
    if err:
        return err

    t0 = time.perf_counter()
    env_err = _run_env_precheck(vm)
    if env_err:
        env_err["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        env_err["scenario"] = scenario
        return env_err

    try:
        cfg = _config()
        raw = _invoke_functional_runner(vm, cfg, meta["resolved_path"], args)
    except Exception as e:
        elapsed = round((time.perf_counter() - t0) * 1000)
        return _envelope(
            False,
            error=str(e),
            verdict="error",
            steps=[],
            artifacts=[],
            elapsed_ms=elapsed,
        )
    elapsed = round((time.perf_counter() - t0) * 1000)
    passed = raw.get("passed", False)
    log = raw.get("log", "")
    screenshot_dir = raw.get("screenshot_dir")
    step_count = int(raw.get("step_count") or meta.get("step_count") or 0)
    steps = _steps_from_run(screenshot_dir, step_count, passed, log)
    artifacts = _collect_artifacts(screenshot_dir)
    verdict = "pass" if passed else "fail"
    return _envelope(
        True,
        degraded=raw.get("degraded") or [],
        verdict=verdict,
        steps=steps,
        artifacts=artifacts,
        elapsed_ms=elapsed,
        vm=vm_name,
        scenario=scenario,
        log=log,
        screenshot_dir=str(screenshot_dir) if screenshot_dir else None,
    )


def _list_scenarios(args: Optional[dict] = None) -> dict:
    """mosdat_list_scenarios: uniform envelope + per-entry ``platform`` (FR-004)."""
    args = args or {}
    platform = args.get("platform") or None
    if platform == "":
        platform = None
    if platform is not None and platform not in ("linux", "windows"):
        return _envelope(
            False,
            error=f"unknown platform '{platform}' — available: linux, windows",
            scenarios=[],
        )

    path_arg = args.get("path")
    if path_arg:
        base = Path(path_arg)
        files = sorted(p for p in base.rglob("*.yaml") if p.is_file()) if base.exists() else []
    else:
        files = _iter_scenario_yaml()

    by_stem: dict[str, Path] = {}
    for path in files:
        if platform and _scenario_platform_from_path(path) != platform:
            continue
        prev = by_stem.get(path.stem)
        if prev is None or ("functional" in path.parts and "functional" not in prev.parts):
            by_stem[path.stem] = path

    scenarios = []
    for stem in sorted(by_stem):
        path = by_stem[stem]
        try:
            rel = str(path.relative_to(FRAMEWORK))
        except ValueError:
            rel = str(path)
        scenarios.append(
            {
                "name": stem,
                "path": rel,
                "platform": _scenario_platform_from_path(path),
                "step_count": _raw_step_count(path),
            }
        )
    return _envelope(True, scenarios=scenarios)


def _readiness(args: dict) -> dict:
    """mosdat_readiness: wrap doctor/preflight checks into a go/no-go answer."""
    from automation.commands.doctor import check_deps, check_disk_tmp, check_ssh

    vm_name = args.get("vm") or args.get("vm_name")
    vm, err = _resolve_vm(vm_name)
    if err:
        return err

    expect_pr = args.get("expect_pr")
    expect_symbol = args.get("expect_symbol")
    busy = _vm_busy(vm.vmid)
    checks: list[dict] = []
    reachable = False
    deployed_version: Optional[str] = None

    ssh = _ssh_client(vm)
    ssh_res = check_ssh(ssh)
    checks.append(_check_item("ssh_reachable", ssh_res.status, ssh_res.detail))
    reachable = ssh_res.status == "PASS"

    if reachable:
        if not getattr(vm, "is_windows", False):
            dep_results = check_deps(ssh)
            missing = [
                r.label.split(":", 1)[-1]
                for r in dep_results
                if r.status == "FAIL"
            ]
            if missing:
                checks.append(
                    _check_item("tool_deps", "FAIL", f"missing: {', '.join(missing)}")
                )
            else:
                found = [r.label.split(":", 1)[-1] for r in dep_results]
                checks.append(_check_item("tool_deps", "PASS", ", ".join(found)))

            disk = check_disk_tmp(ssh)
            # doctor WARNs below 1 GB; readiness treats that as not-ready (FAIL)
            # so an agent gets a go/no-go rather than a soft warning.
            disk_status = "PASS" if disk.status == "PASS" else "FAIL"
            checks.append(_check_item("disk_tmp", disk_status, disk.detail))

        deployed_version = _read_deployed_version(ssh)
        if (expect_pr is not None and expect_pr != "") or expect_symbol:
            checks.append(
                _expected_build_check(ssh, deployed_version, expect_pr, expect_symbol)
            )

    ready = bool(checks) and all(c["status"] == "PASS" for c in checks)
    return _envelope(
        True,
        ready=ready,
        vm={
            "name": vm.name,
            "vmid": vm.vmid,
            "reachable": reachable,
            "deployed_version": deployed_version,
            "busy": busy,
        },
        checks=checks,
    )


def _ssh_tool(req: Request, vm_name: str, command: str, timeout: int = 30, *, jsonrpc_result) -> str:
    out, ec = _ssh(command, vm_name, timeout)
    return jsonrpc_result(req, {"exit_code": ec, "stdout": out})
