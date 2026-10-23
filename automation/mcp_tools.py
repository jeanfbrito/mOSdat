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
    # A path to a YAML file outside shared/ (e.g. a scratch scenario) runs
    # as-is, so agents do not have to write tmp-*.yaml into the repo.
    direct = Path(str(name)).expanduser()
    if str(name).endswith((".yaml", ".yml")) and direct.is_file():
        return _scenario_meta(direct.resolve(), platform), None
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
    return _scenario_meta(path, platform), None


def _scenario_meta(path: Path, platform: Optional[str]) -> dict:
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
    return meta


def _write_inline_scenario(text: str) -> Path:
    """Persist an inline ``scenario_yaml`` under results/ so the run is replayable."""
    out_dir = FRAMEWORK / "results" / "inline-scenarios"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"inline-{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.yaml"
    path.write_text(text)
    return path


def _vlm_step_numbers(path: Path, platform: Optional[str]) -> list:
    """1-indexed steps whose outcome needs a VLM answer (verify/localize/accept_any).

    AT-SPI and ``click_at`` steps are deterministic and are not counted.
    """
    try:
        from automation.runners.scenario_loader import load_test_yaml

        _, steps, _, _ = load_test_yaml(path, platform=platform or _scenario_platform_from_path(path))
    except Exception:
        return []
    numbers = []
    for i, step in enumerate(steps, start=1):
        needs_vlm = bool(
            getattr(step, "verify", None)
            or getattr(step, "verify_not", None)
            or getattr(step, "accept_any", None)
            or getattr(step, "verify_recorded", None)
            or (
                getattr(step, "localize", None)
                and not getattr(step, "atspi", None)
                and not getattr(step, "click_at", None)
            )
        )
        if needs_vlm:
            numbers.append(i)
    return numbers


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
    """Installed app version from the asar's root package.json (Linux)."""
    from automation.commands.build import _find_installed_asar
    from automation.commands.vm_lifecycle import _asar_package_version

    try:
        asar = _find_installed_asar(ssh)
    except Exception:
        return None
    if not asar:
        return None
    return _asar_package_version(ssh, asar)


def _expected_build_check(
    ssh, deployed_version, expect_pr, expect_symbol, *, is_windows: bool = False,
    windows_asar: Optional[str] = None,
) -> dict:
    """``deployed_build_matches_expected`` check for ``mosdat_readiness``."""
    from automation.commands.build import (
        _find_installed_asar,
        _verify_symbols_on_vm,
        _verify_windows_install,
    )

    if is_windows:
        # Windows has no grep and no /opt: reuse the deploy-time verifier.
        reasons: list[str] = []
        if expect_pr is not None and expect_pr != "":
            reasons.append(
                "expect_pr is not checked on Windows (the NSIS FileVersion has no "
                "PR tag) — pass expect_symbol with a symbol unique to the PR"
            )
        if expect_symbol:
            symbols = _parse_verify_symbols(expect_symbol)
            if not windows_asar:
                reasons.append("no installed app.asar — cannot verify symbols")
            elif symbols:
                _, _, missing = _verify_windows_install(ssh, symbols)
                if missing:
                    reasons.append(f"missing symbols: {', '.join(missing)}")
        if reasons:
            return _check_item("deployed_build_matches_expected", "FAIL", "; ".join(reasons))
        return _check_item("deployed_build_matches_expected", "PASS", deployed_version or "")

    reasons = []
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
        "artifact": getattr(res, "artifact", "") or "",
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
                "vm_name": {"type": "string", "description": "VM name (e.g. ubuntu2404, fedora42)"},
                "wait_ready": {
                    "type": "boolean",
                    "description": (
                        "Wait until SSH answers before returning (adds "
                        "ready: {ready, waited_s, detail}). Use this instead of sleeping."
                    ),
                },
                "timeout": {"type": "number", "description": "Max seconds for wait_ready (default 240)"},
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
                    "description": "Pull request number to clone and build (omit with 'source')",
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Local source tree on this host (uncommitted files included) "
                        "to build natively on the Windows deploy_to VM instead of "
                        "cloning a PR. target must be exe."
                    ),
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
            "required": [],
        },
    },
    {
        "name": "mosdat_deploy",
        "description": (
            "Install a build on a VM without rebuilding. Give exactly one source: "
            "artifact_path (local package, SCP'd), artifact_on_vm (installer "
            "already on a Windows VM), pr_build (newest installer from an "
            "earlier native Windows build of that PR), or release (a published "
            "Rocket.Chat.Electron tag, e.g. 4.18.3). Package files are checked "
            "(size + magic bytes) and Linux package versions are checked after install."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm": {"type": "string", "description": "Target VM name"},
                "vm_name": {"type": "string", "description": "Target VM name (alias of vm)"},
                "artifact_path": {"type": "string", "description": "Path to local .deb/.rpm/.exe file"},
                "package_path": {"type": "string", "description": "Path to local package (alias of artifact_path)"},
                "target": {"type": "string", "description": "deb | rpm | appimage | exe (inferred from suffix if omitted; for release on Linux: deb default)"},
                "artifact_on_vm": {"type": "string", "description": "Windows: path of an installer .exe already on the VM"},
                "pr_build": {"type": "number", "description": "Windows: reinstall the installer from C:\\mosdat-builds\\pr<N>\\dist"},
                "release": {"type": "string", "description": "Published release tag to download and install (e.g. 4.18.3)"},
                "verify_symbol": {"description": "Symbol(s) expected in the installed app.asar (string or list)"},
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
        "description": (
            "Run a VLM functional test scenario on a VM (GUI interaction via screenshots + VLM). "
            "verdict: pass | fail | error | unverified. 'unverified' means the VLM was down "
            "and the scenario has VLM-judged steps: read screenshots_to_judge yourself."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm": {"type": "string", "description": "VM name to run on"},
                "vm_name": {"type": "string", "description": "VM name to run on (alias of vm)"},
                "scenario": {
                    "type": "string",
                    "description": (
                        "Scenario name (e.g. 'rocketchat-smoke'), or a path to a "
                        ".yaml file outside shared/ (scratch scenarios)"
                    ),
                },
                "scenario_yaml": {
                    "type": "string",
                    "description": (
                        "Inline scenario YAML (instead of 'scenario'). It is saved "
                        "under results/inline-scenarios/ and run from there."
                    ),
                },
                "model": {"type": "string", "description": "VLM model override (default: from config)"},
                "verify_model": {"type": "string", "description": "Verify model override for yes/no checks"},
                "save_screenshots": {"type": "boolean", "description": "Save screenshots on failure"},
                "timeout": {"type": "number", "description": "Scenario timeout in seconds (default: 900)"},
                "from_step": {"type": "number", "description": "Start from step N (1-indexed)"},
                "until_step": {"type": "number", "description": "Stop after step N"},
                "server_url": {
                    "type": "string",
                    "description": "Override the scenario's configured workspace_url for this call only; omit to use today's default.",
                },
            },
            "required": ["vm_name"],
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
        "name": "mosdat_ssh_bootstrap",
        "description": (
            "Install this host's SSH public key onto a VM whose SSH refuses "
            "the key. Windows: drives the unlocked Proxmox VNC console with "
            "VLM localize/verify; aborts on a lock/sign-in screen. Linux: "
            "appends the key to authorized_keys through the QEMU guest agent "
            "(needs qemu-guest-agent on the VM). No-ops if SSH already works."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm": {"type": "string", "description": "VM name (Windows or Linux)"},
                "vm_name": {"type": "string", "description": "VM name (alias of vm)"},
                "pubkey_path": {
                    "type": "string",
                    "description": (
                        "Path to an SSH public key (default: ~/.ssh/id_ed25519.pub, "
                        "falling back to ~/.ssh/id_rsa.pub)"
                    ),
                },
            },
            "required": ["vm"],
        },
    },
    {
        "name": "mosdat_vm_hygiene",
        "description": (
            "Check and idempotently fix four confirmed Windows-VM hygiene "
            "issues that silently break VLM toast/notification testing: VM "
            "clock drift (reset from this host's UTC clock, no NTP "
            "required), the OneDrive first-run sign-in nag, the 'Turn on "
            "Windows Backup' toast, and the taskbar news/weather widget. "
            "Already-clean items report already-ok and are left untouched. "
            "Windows VMs only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm": {"type": "string", "description": "VM name (must be os_type=windows)"},
                "vm_name": {"type": "string", "description": "VM name (alias of vm)"},
            },
            "required": ["vm"],
        },
    },
    {
        "name": "mosdat_server_provision",
        "description": (
            "Provision a Rocket.Chat server matching a published image "
            "reference (PR/release/RC/develop) via Docker Compose. Idempotent: "
            "re-invoking for a matching already-running instance returns its "
            "current state (starting/ready) without starting a duplicate. "
            "Waits up to 180 s; a slow cold boot returns state=starting with "
            "its url — call again to poll. Starts Docker Desktop if it is "
            "down (macOS). Tries GHCR, then Docker Hub, so older releases "
            "work. The seeded admin (mosdat-admin / mosdat-admin-pass) has 2FA off."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": (
                        "PR (pr-<N> or bare <N>), release/RC tag, or develop"
                    ),
                },
            },
            "required": ["ref"],
        },
    },
    {
        "name": "mosdat_server_teardown",
        "description": (
            "Stop and remove a provisioned Rocket.Chat server instance "
            "(Compose down -v). Idempotent: no matching instance for the "
            "reference is not an error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": (
                        "PR (pr-<N> or bare <N>), release/RC tag, or develop"
                    ),
                },
            },
            "required": ["ref"],
        },
    },
    {
        "name": "mosdat_server_list",
        "description": (
            "List currently managed Rocket.Chat server instances "
            "(Docker Compose projects mosdat has provisioned), each "
            "probed once for ready/starting state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "mosdat_ssh",
        "description": (
            "Run an arbitrary shell command on a VM. The mosdat-mcp server "
            "handles one request at a time (synchronous stdio loop) — this "
            "call blocks the server for the command's full duration, up to "
            "`timeout`. Keep commands short; for anything that needs to run "
            "longer, background it on the VM (e.g. Windows: `Start-Process`, "
            "then poll separately) rather than sleeping in-command."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm_name": {"type": "string", "description": "VM name"},
                "command": {"type": "string", "description": "Shell command to run (one line). Use 'script' for anything with PowerShell $variables, quotes or several lines."},
                "script": {
                    "type": "string",
                    "description": (
                        "Script body (PowerShell on Windows, bash on Linux). It is "
                        "copied to the VM as a file and run with -File / bash, so "
                        "$variables, quotes and newlines arrive unchanged. Use "
                        "instead of 'command'."
                    ),
                },
                "detach": {
                    "type": "boolean",
                    "description": (
                        "With 'script': start it so it keeps running after this "
                        "call returns (survives the SSH session). Returns pid and "
                        "log_path; read the log with a later call."
                    ),
                },
                "session": {
                    "type": "string",
                    "description": (
                        "With 'script', Windows only: 'ssh' (default, session 0, "
                        "no desktop) or 'desktop' (the logged-in user's visible "
                        "session — use it to start GUI apps or send real input)."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        "Timeout in seconds (default: 30). On expiry the "
                        "underlying process is killed and mosdat_ssh returns "
                        "exit_code 124 — the call itself fails cleanly, it "
                        "does not crash the mosdat-mcp server. Recommended "
                        "max ~60s; for longer work, background the remote "
                        "command and poll instead of blocking one call on it."
                    ),
                },
            },
            "required": ["vm_name"],
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


def _vm_start(req: Request, vm_name: str, jsonrpc_result, *, wait_ready: bool = False,
              timeout: int = 240) -> str:
    vm, _ = _vm_config(vm_name)
    api = _get_proxmox_api()
    api.start_vm(vm.vmid)
    result = {"status": "started", "vm": vm_name}
    if wait_ready:
        # Poll SSH here so agents do not sleep-loop after a start.
        from automation.commands.vm_lifecycle import wait_ready as _wait_ready

        result["ready"] = _wait_ready(vm, timeout=int(timeout))
    return jsonrpc_result(req, result)


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
    source = args.get("source") or None
    if (pr_raw is None or pr_raw == "") and source:
        pr_raw = 0  # local tree, no PR clone
    if pr_raw is None or pr_raw == "":
        return _envelope(False, error="missing required argument 'pr' (or 'source')", build_ok=False, deploy=None)
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
        source=source,
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
    sources = [k for k in ("artifact_on_vm", "pr_build", "release") if args.get(k) not in (None, "")]
    if artifact_path:
        sources.append("artifact_path")
    if not sources:
        return _envelope(
            False,
            error="missing source: pass one of artifact_path, artifact_on_vm, pr_build, release",
        )
    if len(sources) > 1:
        return _envelope(False, error=f"pass only one source, got: {', '.join(sources)}")

    vm, err = _resolve_vm(vm_name)
    if err:
        return err
    if not artifact_path:
        return _deploy_without_upload(vm, args, sources[0])

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
        return _envelope(False, **{**outcome, "error": res.error or "deploy failed"})

    try:
        with _nonblocking_vm_lock(vm.vmid):
            return _do_deploy()
    except (ProxmoxLockTimeout, BlockingIOError):
        return _busy_envelope(vm.vmid)


def _deploy_without_upload(vm, args: dict, source: str) -> dict:
    """mosdat_deploy for an installer already on the VM, a PR build dir, or a release tag."""
    from automation.commands import build as build_mod
    from automation.proxmox.gpu import ProxmoxLockTimeout
    from automation.transport.ssh import SSHClient

    is_windows = bool(getattr(vm, "is_windows", False))
    if source in ("artifact_on_vm", "pr_build") and not is_windows:
        return _envelope(False, error=f"{source} is for Windows VMs (got {vm.name})")
    symbols = _parse_verify_symbols(args.get("verify_symbol"))
    dry_run = bool(args.get("dry_run", False))
    user = vm.user or "root"

    def _do() -> dict:
        if source == "release":
            res = build_mod.install_release_on_vm(
                vm.name, vm.ip, user, "windows" if is_windows else (vm.os_type or "linux"),
                str(args["release"]), symbols, dry_run=dry_run,
                linux_target=args.get("target") or "deb",
            )
        else:
            on_vm = args.get("artifact_on_vm")
            if source == "pr_build":
                on_vm = build_mod.find_windows_build_artifact(
                    SSHClient(vm.ip, user), int(args["pr_build"])
                )
                if not on_vm:
                    return _envelope(
                        False,
                        error=(
                            f"no installer in {build_mod._windows_build_dir(int(args['pr_build']))}"
                            "\\dist — the earlier build did not finish packaging; rebuild"
                        ),
                    )
            res = build_mod.install_existing_on_windows_vm(
                vm.name, vm.ip, user, on_vm, symbols, dry_run=dry_run,
            )
        outcome = _deploy_dict(res)
        if res.ok:
            return _envelope(True, **outcome)
        return _envelope(False, **{**outcome, "error": res.error or "deploy failed"})

    try:
        with _nonblocking_vm_lock(vm.vmid):
            return _do()
    except (ProxmoxLockTimeout, BlockingIOError):
        return _busy_envelope(vm.vmid)


def _kill_existing_app(injector, app_basename: str) -> None:
    """Best-effort cross-platform kill of any previously-running instance.

    Mirrors the ``Get-Process | Stop-Process`` idiom already used in
    ``build.py``/``vm_hygiene.py`` for Windows, and the ``pkill -f`` idiom
    used elsewhere in this codebase for Linux. ``injector.shell`` already
    branches PowerShell vs. POSIX shell internally.
    """
    if injector.is_windows:
        name = app_basename[:-4] if app_basename.lower().endswith(".exe") else app_basename
        injector.shell(
            f"Get-Process -Name '{name}' -ErrorAction SilentlyContinue "
            "| Stop-Process -Force -ErrorAction SilentlyContinue"
        )
    else:
        injector.shell(f'pkill -f "{app_basename}" 2>/dev/null || true')


def _run_smoke_impl(args: dict) -> dict:
    """mosdat_run_smoke: cheap cross-platform launch + basic UI check.

    Kills any existing instance, launches the app, and does a single cheap
    check: process presence (``injector.process_running``) plus one VNC
    screenshot + one ``vlm.verify`` call — no multi-step scenario, no
    checkpoint/rewind, no retry-loop budget like ``mosdat_run_functional``.
    """
    from automation.proxmox.api import ProxmoxAPI
    from automation.runners.launch import launch_probe_name, natural_window_name
    from automation.transport.ssh import SSHClient
    from automation.transport.vnc import VncClient
    from automation.vlm.client import VLMClient
    from automation.vlm.input import InputInjector
    from automation.vlm.screenshot import Screenshotter

    vm_name = args.get("vm_name") or args.get("vm")
    vm, err = _resolve_vm(vm_name)
    if err:
        return err

    timeout = int(args.get("timeout") or 120)
    cfg = _config()

    default_app_path = (
        r"C:\Program Files\Rocket.Chat\Rocket.Chat.exe"
        if getattr(vm, "is_windows", False)
        else "/opt/Rocket.Chat/rocketchat-desktop"
    )
    app_path = (
        vm.packages[0].app_path
        if vm.packages and vm.packages[0].app_path
        else default_app_path
    )
    app_basename = launch_probe_name(app_path, None, vm.is_windows)
    window_name = natural_window_name(app_basename)

    vlm = VLMClient(
        base_url=cfg.vlm.base_url,
        model=cfg.vlm.model,
        verify_model=cfg.vlm.verify_model or None,
        api_key=cfg.vlm.api_key,
        max_tokens_floor=cfg.vlm.max_tokens_floor,
    )
    degraded = _probe_vlm_degraded(vlm)

    proxmox = ProxmoxAPI(cfg.proxmox)
    ssh = SSHClient(vm.ip, vm.user or "root")

    try:
        with VncClient(proxmox, vmid=vm.vmid) as vnc:
            screenshotter = Screenshotter(vnc)
            injector = InputInjector(vnc, ssh, vm.is_windows)

            _kill_existing_app(injector, app_basename)
            time.sleep(1)

            injector.launch(app_path)

            # Poll for process presence instead of a single check after a
            # fixed sleep — a cold Electron launch on a VM can take well
            # past 10s. Poll every 2s up to min(timeout, 60)s (cheap tools
            # shouldn't default to the full 120s budget), exiting the
            # moment the process is detected, mirroring the "stop polling
            # as soon as confirmed" behavior of _run_launch_step.
            poll_interval = 2
            effective_cap = min(max(timeout, 1), 60)
            max_polls = max(1, -(-effective_cap // poll_interval))  # ceil div
            process_running = False
            for attempt in range(max_polls):
                try:
                    process_running = bool(injector.process_running(app_basename))
                except Exception:
                    process_running = False
                if process_running:
                    break
                if attempt < max_polls - 1:
                    time.sleep(poll_interval)

            window_visible = False
            if process_running:
                try:
                    screenshot, _ = screenshotter.capture()
                    window_visible = bool(
                        vlm.verify(
                            screenshot,
                            f"is the {window_name} window visible on screen",
                        )
                    )
                except Exception:
                    window_visible = False
                    if "vlm_unavailable" not in degraded:
                        degraded.append("vlm_unavailable")
    except Exception as e:
        return _envelope(
            False,
            error=f"smoke test failed: {e}",
            vm=vm_name,
            process_running=False,
            window_visible=False,
            verdict="fail",
        )

    verdict = "pass" if (process_running and window_visible) else "fail"
    return _envelope(
        True,
        degraded=degraded,
        vm=vm_name,
        process_running=process_running,
        window_visible=window_visible,
        verdict=verdict,
    )


def _run_smoke(req: Request, vm_name: str, timeout: int = 120, *, jsonrpc_result, jsonrpc_error) -> str:
    result = _run_smoke_impl({"vm_name": vm_name, "timeout": timeout})
    return jsonrpc_result(req, result)


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

    server_url = args.get("server_url") or None
    effective_workspace_url = server_url or getattr(cfg.functional, "workspace_url", None)
    load_kwargs = {"platform": vm.os_type}
    if server_url:
        # Override point for double-brace scenarios (I8: cli_vars wins over the
        # scenario's own `vars:` block). Scoped to explicit overrides only, so
        # scenarios relying on their YAML-baked default keep working when no
        # server_url is supplied and no config default is configured either.
        load_kwargs["cli_vars"] = {"workspace_url": server_url}
    name, steps, _, _ = load_test_yaml(scenario_path, cfg, **load_kwargs)
    from_step = int(args.get("from_step") or 1)
    until_step = args.get("until_step")
    until_step = int(until_step) if until_step is not None else len(steps)
    steps = steps[from_step - 1 : until_step]
    if from_step > 1:
        print(
            f"[mOSdat] WARNING: starting at step {from_step} — {from_step - 1} "
            f"earlier step(s) skipped; scenario state (focus, navigation, login) "
            f"from those steps is NOT guaranteed."
        )
        degraded.append("partial_run_no_precondition_guarantee")

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
                else "/opt/Rocket.Chat/rocketchat-desktop",
                "workspace_url": effective_workspace_url,
            }
            passed, log = runner.run_test(
                steps=steps,
                name=name,
                vars=vars_,
                results_dir=str(screenshot_dir.parent),
                vm_name=vm.name,
                start_index=from_step - 1,
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
    if args.get("scenario_yaml"):
        if scenario:
            return _envelope(False, error="pass either 'scenario' or 'scenario_yaml', not both")
        scenario = str(_write_inline_scenario(str(args["scenario_yaml"])))
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
    degraded = raw.get("degraded") or []
    extra: dict = {}
    if "vlm_unavailable" in degraded:
        # A run with no VLM cannot judge its verify/localize steps: a "pass"
        # only means the steps executed, a "fail" may be the VLM itself.
        # Say so, and hand the caller the screenshots to judge.
        vlm_steps = _vlm_step_numbers(meta["resolved_path"], meta.get("platform"))
        if vlm_steps:
            verdict = "unverified"
            extra["unverified_steps"] = vlm_steps
            extra["screenshots_to_judge"] = [
                p for p in artifacts if p.lower().endswith((".png", ".jpg", ".jpeg"))
            ]
    return _envelope(
        True,
        degraded=degraded,
        verdict=verdict,
        **extra,
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


_WINDOWS_MIN_FREE_GB = 8


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
    installed: Optional[dict] = None

    ssh = _ssh_client(vm)
    ssh_res = check_ssh(ssh)
    ssh_detail = ssh_res.detail
    if ssh_res.status == "FAIL" and getattr(vm, "os_type", "linux") == "windows":
        hint = (
            "next step: mosdat_ssh_bootstrap (install this host's SSH "
            "public key via the unlocked VM console)"
        )
        ssh_detail = f"{ssh_detail} — {hint}" if ssh_detail else hint
    checks.append(_check_item("ssh_reachable", ssh_res.status, ssh_detail))
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
        else:
            try:
                from automation.commands.vm_lifecycle import windows_disk_free_gb

                free = windows_disk_free_gb(ssh)
                # A native Windows build needs ~8 GB on C: (it failed live on a full disk).
                checks.append(_check_item(
                    "disk_c", "PASS" if free >= _WINDOWS_MIN_FREE_GB else "FAIL",
                    f"{free} GB free on C:",
                ))
            except Exception as e:
                checks.append(_check_item("disk_c", "FAIL", f"could not read free space: {e}"))

        is_windows = bool(getattr(vm, "is_windows", False))
        try:
            from automation.commands.vm_lifecycle import installed_app_info

            installed = installed_app_info(ssh, is_windows=is_windows)
        except Exception as e:
            installed = {"found": False, "error": str(e)}
        deployed_version = (
            installed.get("version") if is_windows else _read_deployed_version(ssh)
        )
        if (expect_pr is not None and expect_pr != "") or expect_symbol:
            checks.append(
                _expected_build_check(
                    ssh, deployed_version, expect_pr, expect_symbol,
                    is_windows=is_windows, windows_asar=installed.get("asar_path"),
                )
            )

    ready = bool(checks) and all(c["status"] == "PASS" for c in checks)
    extra: dict = {}
    if installed is not None:
        extra["installed"] = installed
    if not reachable:
        extra["hints"] = _readiness_hints_safe(vm)
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
        **extra,
    )


def _readiness_hints_safe(vm) -> list:
    """Next steps for an unreachable VM; never raises."""
    try:
        from automation.commands.vm_lifecycle import readiness_hints

        proxmox_ok = True
        try:
            _get_proxmox_api().get_vm_status(vm.vmid)
        except Exception:
            proxmox_ok = False
        return readiness_hints(vm, _config(), ssh_ok=False, proxmox_ok=proxmox_ok)
    except Exception:
        return []


def _vm_hygiene(args: dict) -> dict:
    """mosdat_vm_hygiene: fix Windows focus-thief UI (clock/OneDrive/backup/news)."""
    from automation.commands.vm_hygiene import exit_code_for, vm_hygiene

    vm_name = args.get("vm") or args.get("vm_name")
    vm, err = _resolve_vm(vm_name)
    if err:
        return err

    if not vm.is_windows:
        return _envelope(
            False,
            error=(
                "vm-hygiene is not supported for non-Windows VMs "
                f"(os_type={vm.os_type!r})"
            ),
        )

    result = vm_hygiene(vm.name, vm.ip, vm.user or "root", dry_run=False)
    items = [
        {"item": i.item, "status": i.status, "detail": i.detail}
        for i in result.items
    ]
    ok = exit_code_for(result) == 0 and not result.error
    return _envelope(
        ok,
        error=result.error or (None if ok else "one or more vm-hygiene items failed"),
        vm=vm.name,
        items=items,
    )


def _ssh_bootstrap(args: dict) -> dict:
    """mosdat_ssh_bootstrap: install this host's SSH key (Windows: VNC console; Linux: guest agent)."""
    from automation.commands.ssh_bootstrap import (
        bootstrap_linux_ssh,
        bootstrap_windows_ssh,
        read_pubkey,
    )
    from automation.proxmox.gpu import ProxmoxLockTimeout

    vm_name = args.get("vm") or args.get("vm_name")
    vm, err = _resolve_vm(vm_name)
    if err:
        return err

    os_type = getattr(vm, "os_type", "linux")

    try:
        pubkey = read_pubkey(args.get("pubkey_path"))
    except (OSError, ValueError) as e:
        return _envelope(False, error=str(e))

    cfg = _config()

    def _do() -> dict:
        from automation.proxmox.api import ProxmoxAPI
        from automation.vlm.client import VLMClient

        proxmox = ProxmoxAPI(cfg.proxmox)
        if os_type != "windows":
            result = bootstrap_linux_ssh(
                vm.name, vm.ip, vm.user or "root", int(vm.vmid), proxmox, pubkey,
                dry_run=False,
            )
            return _envelope(
                result.ok,
                error=result.error or None,
                already_working=result.already_working,
                steps=result.steps,
                vm=vm.name,
            )
        vlm_cfg = cfg.vlm
        vlm = VLMClient(
            base_url=vlm_cfg.base_url,
            model=vlm_cfg.model,
            verify_model=getattr(vlm_cfg, "verify_model", None) or None,
            api_key=getattr(vlm_cfg, "api_key", "") or "",
            max_tokens_floor=getattr(vlm_cfg, "max_tokens_floor", 0) or 0,
        )
        result = bootstrap_windows_ssh(
            vm.name,
            vm.ip,
            vm.user or "root",
            int(vm.vmid),
            proxmox,
            vlm,
            pubkey,
            dry_run=False,
        )
        return _envelope(
            result.ok,
            error=result.error or None,
            already_working=result.already_working,
            steps=result.steps,
            vm=vm.name,
        )

    try:
        with _nonblocking_vm_lock(vm.vmid):
            return _do()
    except (ProxmoxLockTimeout, BlockingIOError):
        return _busy_envelope(vm.vmid)


def _server_provision(args: dict) -> dict:
    """mosdat_server_provision: provision a Rocket.Chat server for a published image ref."""
    from automation.commands.rc_server import provision_server

    raw = args.get("ref") if isinstance(args, dict) else None
    ref = raw.strip() if isinstance(raw, str) else raw
    if not ref:
        return _envelope(False, error="ref is required", ref="", state="failed")

    result = provision_server(ref, dry_run=False)
    if result.state == "failed":
        extra = {}
        if result.elapsed_ms > 0:
            extra["elapsed_ms"] = result.elapsed_ms
        return _envelope(
            False,
            error=result.error,
            ref=result.ref,
            state="failed",
            **extra,
        )
    return _envelope(
        True,
        ref=result.ref,
        state=result.state,
        url=result.url,
        elapsed_ms=result.elapsed_ms,
    )


def _server_teardown(args: dict) -> dict:
    """mosdat_server_teardown: stop and remove a provisioned server instance."""
    from automation.commands.rc_server import teardown_server

    raw = args.get("ref") if isinstance(args, dict) else None
    ref = raw.strip() if isinstance(raw, str) else raw
    if not ref:
        return _envelope(False, error="ref is required", ref="", torn_down=False)

    result = teardown_server(ref)
    if result.error:
        return _envelope(
            False, error=result.error, ref=result.ref, torn_down=result.torn_down
        )
    return _envelope(True, ref=result.ref, torn_down=result.torn_down)


def _server_list(args: Optional[dict] = None) -> dict:
    """mosdat_server_list: discover currently managed server instances."""
    from automation.commands.rc_server import DAEMON_ERROR, list_instances

    try:
        instances = list_instances()
    except RuntimeError as e:
        return _envelope(False, error=str(e) or DAEMON_ERROR, instances=[])
    return _envelope(
        True,
        instances=[
            {
                "ref": inst.ref,
                "state": inst.state,
                "url": inst.url,
                "elapsed_ms": inst.elapsed_ms,
            }
            for inst in instances
        ],
    )


def _ssh_tool(req: Request, vm_name: str, command: str, timeout: int = 30, *, jsonrpc_result) -> str:
    out, ec = _ssh(command, vm_name, timeout)
    return jsonrpc_result(req, {"exit_code": ec, "stdout": out})


def _ssh_dispatch(req: Request, args: dict, *, jsonrpc_result) -> str:
    """mosdat_ssh: a one-line ``command``, or a multi-line ``script`` sent as a file."""
    vm_name = args.get("vm_name") or args.get("vm")
    timeout = int(args.get("timeout", 30))
    script = args.get("script")
    detach = bool(args.get("detach", False))
    session = args.get("session") or "ssh"
    if not script:
        if detach or session != "ssh":
            return jsonrpc_result(
                req,
                {"exit_code": 2, "stdout": "",
                 "stderr": "'detach' and 'session' need 'script', not 'command'"},
            )
        return _ssh_tool(req, vm_name, args["command"], timeout, jsonrpc_result=jsonrpc_result)

    from automation.transport.remote_script import run_script
    from automation.transport.ssh import SSHClient

    vm, _ = _vm_config(vm_name)
    ssh = SSHClient(vm.ip, user=vm.user or "root")
    try:
        res = run_script(
            ssh, script, is_windows=bool(getattr(vm, "is_windows", False)),
            timeout=timeout, detach=detach, session=session,
        )
    except ValueError as e:
        return jsonrpc_result(req, {"exit_code": 2, "stdout": "", "stderr": str(e)})
    payload = {
        "exit_code": res.returncode,
        "stdout": (res.stdout or "").strip(),
        "stderr": (res.stderr or "").strip(),
    }
    if detach:
        payload["log_path"] = res.log_path
        payload["pid"] = res.pid
    return jsonrpc_result(req, payload)
