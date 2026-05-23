"""
mOSdat MCP tool definitions and implementations.

All tool metadata (TOOL_DEFINITIONS) and handler functions live here.
mcp_server.py imports these and wires them into the JSON-RPC dispatch table.
"""

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

FRAMEWORK = Path(__file__).parent.parent

Request = dict[str, Any]

# ── Config helpers ──────────────────────────────────────────────────────────


def _config():
    """Find and load the most likely config file."""
    from automation.config import load_config

    env_cfg = os.environ.get("MOSDAT_CONFIG")
    if env_cfg:
        return load_config(Path(env_cfg))
    cwd_cfg = Path("rocketchat.toml")
    if cwd_cfg.exists():
        return load_config(cwd_cfg)
    examples = FRAMEWORK / "examples"
    for f in ["ubuntu2404.toml", "rocketchat.toml"]:
        p = examples / f
        if p.exists():
            return load_config(p)
    raise FileNotFoundError(
        "No mosdat config found. Set MOSDAT_CONFIG or run from project dir."
    )


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
        "description": "Build the Electron app (yarn install + build) on the host",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to Rocket.Chat.Electron repo (default: from config)",
                },
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Package formats to build (default: deb, rpm)",
                },
            },
        },
    },
    {
        "name": "mosdat_deploy",
        "description": "SCP a built package to a VM and install it",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm_name": {"type": "string", "description": "Target VM name"},
                "package_path": {"type": "string", "description": "Path to local .deb/.rpm file"},
            },
            "required": ["vm_name", "package_path"],
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
                "vm_name": {"type": "string", "description": "VM name to run on"},
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
                "path": {"type": "string", "description": "Scenarios directory (default: shared/scenarios/)"},
            },
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


def _list_vms(req: Request, jsonrpc_result) -> str:
    api = _get_proxmox_api()
    endpoint = f"/nodes/{api.config.node}/qemu"
    data = api.get(endpoint).get("data", [])
    vms = [
        {
            "name": vm.get("name", f"VM-{vm['vmid']}"),
            "vmid": vm["vmid"],
            "status": vm.get("status", "unknown"),
            "node": api.config.node,
        }
        for vm in data
    ]
    return jsonrpc_result(req, {"vms": vms})


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


def _build(req: Request, args: dict, jsonrpc_result, jsonrpc_error) -> str:
    repo = args.get("repo_path", "")
    if not repo:
        cfg = _config()
        if cfg.build and cfg.build.repo_path:
            repo = str(cfg.build.repo_path)
        else:
            repo = str(FRAMEWORK.parent / "Rocket.Chat.Electron")

    packages = args.get("packages", ["deb", "rpm"])
    pkg_flag = " ".join(f"--{p}" for p in packages)

    if not os.path.isdir(repo):
        return jsonrpc_error(req, -32000, f"Repo path does not exist: {repo}")

    cmds = [
        f"cd {repo} && yarn install",
        f"cd {repo} && yarn build",
        f"cd {repo} && npx electron-builder --linux {pkg_flag}",
    ]
    results = []
    for cmd in cmds:
        label = cmd.split("&&")[-1].strip()
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
            results.append({
                "step": label,
                "exit_code": r.returncode,
                "stderr": r.stderr[-500:] if r.stderr else "",
            })
            if r.returncode != 0:
                break
        except subprocess.TimeoutExpired:
            results.append({"step": label, "exit_code": -1, "stderr": "Timed out (600s)"})
            break

    return jsonrpc_result(req, {"results": results})


def _deploy(req: Request, vm_name: str, package_path: str, jsonrpc_result, jsonrpc_error) -> str:
    vm, _ = _vm_config(vm_name)
    from automation.transport.ssh import SSHClient

    pkg = Path(package_path)
    if not pkg.exists():
        return jsonrpc_error(req, -32000, f"Package not found: {package_path}")

    ext = pkg.suffix.lstrip(".")
    dest = f"/tmp/{pkg.name}"
    ssh = SSHClient(vm.ip, user=vm.user or "root")
    try:
        r = ssh.scp_to(Path(str(pkg)), dest)
        if not r.success:
            return jsonrpc_error(req, -32000, f"SCP failed: {r.stderr[:500]}")
        if ext == "deb":
            install_cmd = f"sudo dpkg -i {dest} 2>/dev/null || sudo apt-get install -f -y -qq"
        elif ext == "rpm":
            install_cmd = f"rpm -i {dest} 2>/dev/null || yum install -y {dest}"
        else:
            install_cmd = f"chmod +x {dest} && {dest}"
        r2 = ssh.run(install_cmd, timeout=60)
        if not r2.success:
            return jsonrpc_error(
                req, -32000,
                f"Install failed (exit={r2.returncode}): {r2.stderr[:500] or r2.stdout[:500]}",
            )
        return jsonrpc_result(req, {"vm": vm_name, "package": package_path, "install_output": r2.stdout})
    except Exception as e:
        return jsonrpc_error(req, -32000, f"Deploy failed: {e}")


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


def _run_functional(req: Request, args: dict, *, jsonrpc_result, jsonrpc_error) -> str:
    vm_name = args["vm_name"]
    scenario = args["scenario"]
    vm, cfg = _vm_config(vm_name)
    from automation.runners.functional import FunctionalRunner
    from automation.vlm.client import VLMClient
    from automation.vlm.screenshot import Screenshotter
    from automation.vlm.input import InputInjector
    from automation.transport.vnc import VncClient
    from automation.transport.ssh import SSHClient
    from automation.proxmox.api import ProxmoxAPI

    scenarios_dir = FRAMEWORK / "shared" / "scenarios"
    scenario_path = scenarios_dir / f"{scenario}.yaml"
    if not scenario_path.exists():
        scenario_path = scenarios_dir / "functional" / f"{scenario}.yaml"
    if not scenario_path.exists():
        return jsonrpc_error(req, -32000, f"Scenario not found: {scenario}")

    vlm = VLMClient(
        base_url=cfg.vlm.base_url,
        model=args.get("model") or cfg.vlm.model,
        verify_model=args.get("verify_model") or cfg.vlm.verify_model or None,
        api_key=cfg.vlm.api_key,
        max_tokens_floor=cfg.vlm.max_tokens_floor,
    )
    proxmox = ProxmoxAPI(cfg.proxmox)
    ssh = SSHClient(vm.ip, vm.user)

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    screenshot_dir = FRAMEWORK / "results" / "functional" / f"{ts}_functional" / vm.name
    os.makedirs(str(screenshot_dir), exist_ok=True)

    with VncClient(proxmox, vmid=vm.vmid) as vnc:
        screenshotter = Screenshotter(vnc)
        injector = InputInjector(vnc, ssh, vm.is_windows)
        # Stage 3c / Stage 4: per-OS coordinate-free driver shares one
        # persistent SSHClient. AtspiClient on Linux, UiaClient on Windows.
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
            from automation.runners.scenario_loader import load_test_yaml

            name, steps, _, _ = load_test_yaml(
                scenario_path, cfg, platform=vm.os_type,
            )
            passed, log = runner.run_test(
                steps=steps,
                name=name,
                vars={
                    "app_path": vm.packages[0].app_path
                    if vm.packages
                    else "/opt/Rocket.Chat/rocketchat-desktop"
                },
                results_dir=str(screenshot_dir.parent),
                vm_name=vm.name,
            )
        finally:
            if _ssh_atspi is not None:
                try:
                    _ssh_atspi.close_persistent()
                except Exception:
                    pass

    return jsonrpc_result(
        req,
        {
            "success": passed,
            "vm": vm_name,
            "scenario": scenario,
            "screenshot_dir": str(screenshot_dir),
            "log": log,
        },
    )


def _list_scenarios(req: Request, path: str = None, *, jsonrpc_result) -> str:
    base = Path(path) if path else FRAMEWORK / "shared" / "scenarios"
    if not base.exists():
        return jsonrpc_result(req, {"scenarios": [], "path": str(base)})

    files = sorted(base.rglob("*.yaml"))
    scenarios_base = FRAMEWORK / "shared" / "scenarios"
    scenarios = [
        {
            "path": str(f.relative_to(scenarios_base) if scenarios_base in f.parents else f),
            "name": f.stem,
            "size": f.stat().st_size,
        }
        for f in files
    ]
    return jsonrpc_result(req, {"scenarios": scenarios, "path": str(base)})


def _ssh_tool(req: Request, vm_name: str, command: str, timeout: int = 30, *, jsonrpc_result) -> str:
    out, ec = _ssh(command, vm_name, timeout)
    return jsonrpc_result(req, {"exit_code": ec, "stdout": out})
