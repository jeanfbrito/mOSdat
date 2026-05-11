#!/usr/bin/env python3
"""
mOSdat MCP Server — Hermes-native tools for VM orchestration + VLM testing.

Protocol: JSON-RPC 2.0 over stdio (MCP stdio transport).
Discovery: Hermes lists tools via tools/list, calls via tools/call.

Usage:
  mosdat mcp              # via CLI entry point
  python -m automation.mcp_server  # direct
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── mOSdat imports (lazy — only the ones we need) ──────────────────────────

FRAMEWORK = Path(__file__).parent.parent


def _get_proxmox_api():
    from automation.proxmox.api import ProxmoxAPI

    cfg = _config()
    return ProxmoxAPI(cfg.proxmox)


def _config():
    """Find and load the most likely config file."""
    from automation.config import load_config

    # Search order: env var, cwd rocketchat.toml, examples/
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


# ── MCP protocol helpers ────────────────────────────────────────────────────

Request = dict[str, Any]


def jsonrpc_error(req: Request, code: int, message: str, data: Any = None) -> str:
    resp = {
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "error": {"code": code, "message": message},
    }
    if data is not None:
        resp["error"]["data"] = str(data)[:2000]
    return json.dumps(resp) + "\n"


def jsonrpc_result(req: Request, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result}) + "\n"


def jsonrpc_notification(method: str, params: dict = None) -> str:
    msg = {"jsonrpc": "2.0", "method": method}
    if params:
        msg["params"] = params
    return json.dumps(msg) + "\n"


# ── Tool handlers ────────────────────────────────────────────────────────────


def handle_initialize(req: Request) -> str:
    """MCP initialization handshake."""
    return jsonrpc_result(
        req,
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
            },
            "serverInfo": {
                "name": "mosdat",
                "version": "0.2.0",
            },
        },
    )


TOOL_DEFINITIONS = [
    {
        "name": "mosdat_list_vms",
        "description": "List available Proxmox VMs with their status (running/stopped)",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "mosdat_vm_start",
        "description": "Start a Proxmox VM",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vm_name": {
                    "type": "string",
                    "description": "VM name (e.g. ubuntu2404, fedora42)",
                }
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
                "package_path": {
                    "type": "string",
                    "description": "Path to local .deb/.rpm file",
                },
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
                "timeout": {
                    "type": "number",
                    "description": "Test timeout in seconds (default: 120)",
                },
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
                "model": {
                    "type": "string",
                    "description": "VLM model override (default: from config)",
                },
                "verify_model": {
                    "type": "string",
                    "description": "Verify model override for yes/no checks",
                },
                "save_screenshots": {
                    "type": "boolean",
                    "description": "Save screenshots on failure",
                },
                "timeout": {
                    "type": "number",
                    "description": "Scenario timeout in seconds (default: 900)",
                },
                "from_step": {
                    "type": "number",
                    "description": "Start from step N (1-indexed)",
                },
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
                "path": {
                    "type": "string",
                    "description": "Scenarios directory (default: shared/scenarios/)",
                },
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
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (default: 30)",
                },
            },
            "required": ["vm_name", "command"],
        },
    },
]


def handle_tools_list(req: Request) -> str:
    # MCP spec: tools/list result must be { tools: [...] }
    return jsonrpc_result(req, {"tools": TOOL_DEFINITIONS})


def handle_tools_call(req: Request) -> str:
    params = req.get("params", {})
    name = params.get("name", "")
    args = params.get("arguments", {})

    try:
        if name == "mosdat_list_vms":
            return _list_vms(req)
        elif name == "mosdat_vm_start":
            return _vm_start(req, args["vm_name"])
        elif name == "mosdat_vm_stop":
            return _vm_stop(req, args["vm_name"])
        elif name == "mosdat_vm_status":
            return _vm_status(req, args["vm_name"])
        elif name == "mosdat_build":
            return _build(req, args)
        elif name == "mosdat_deploy":
            return _deploy(req, args["vm_name"], args["package_path"])
        elif name == "mosdat_run_smoke":
            return _run_smoke(req, args["vm_name"], args.get("timeout", 120))
        elif name == "mosdat_run_functional":
            return _run_functional(req, args)
        elif name == "mosdat_list_scenarios":
            return _list_scenarios(req, args.get("path"))
        elif name == "mosdat_ssh":
            return _ssh_tool(
                req, args["vm_name"], args["command"], args.get("timeout", 30)
            )
        else:
            return jsonrpc_error(req, -32601, f"Unknown tool: {name}")
    except Exception as e:
        import traceback

        return jsonrpc_error(req, -32000, str(e), traceback.format_exc())


# ── Tool implementations ────────────────────────────────────────────────────


def _list_vms(req: Request) -> str:
    api = _get_proxmox_api()
    # Get all VMs from the configured node
    endpoint = f"/nodes/{api.config.node}/qemu"
    data = api.get(endpoint).get("data", [])
    vms = []
    for vm in data:
        vms.append(
            {
                "name": vm.get("name", f"VM-{vm['vmid']}"),
                "vmid": vm["vmid"],
                "status": vm.get("status", "unknown"),
                "node": api.config.node,
            }
        )
    return jsonrpc_result(req, {"vms": vms})


def _vm_start(req: Request, vm_name: str) -> str:
    vm, _ = _vm_config(vm_name)
    api = _get_proxmox_api()
    api.start_vm(vm.vmid)
    return jsonrpc_result(req, {"status": "started", "vm": vm_name})


def _vm_stop(req: Request, vm_name: str) -> str:
    vm, _ = _vm_config(vm_name)
    api = _get_proxmox_api()
    api.shutdown_vm(vm.vmid)
    return jsonrpc_result(req, {"status": "stopped", "vm": vm_name})


def _vm_status(req: Request, vm_name: str) -> str:
    vm, _ = _vm_config(vm_name)
    api = _get_proxmox_api()
    status = api.get_vm_status(vm.vmid)
    return jsonrpc_result(req, {"name": vm_name, "vmid": vm.vmid, "status": status})


def _build(req: Request, args: dict) -> str:
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

    # Build
    cmds = [
        f"cd {repo} && yarn install",
        f"cd {repo} && yarn build",
        f"cd {repo} && npx electron-builder --linux {pkg_flag}",
    ]

    results = []
    for cmd in cmds:
        label = cmd.split("&&")[-1].strip()
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=600
            )
            results.append(
                {
                    "step": label,
                    "exit_code": r.returncode,
                    "stderr": r.stderr[-500:] if r.stderr else "",
                }
            )
            if r.returncode != 0:
                break
        except subprocess.TimeoutExpired:
            results.append(
                {"step": label, "exit_code": -1, "stderr": "Timed out (600s)"}
            )
            break

    return jsonrpc_result(req, {"results": results})


def _deploy(req: Request, vm_name: str, package_path: str) -> str:
    vm, cfg = _vm_config(vm_name)
    from automation.transport.ssh import SSHClient

    pkg = Path(package_path)
    if not pkg.exists():
        return jsonrpc_error(req, -32000, f"Package not found: {package_path}")

    ext = pkg.suffix.lstrip(".")
    dest = f"/tmp/{pkg.name}"

    ssh = SSHClient(vm.ip, user=vm.user or "root")
    try:
        # SCP
        r = ssh.scp_to(Path(str(pkg)), dest)
        if not r.success:
            return jsonrpc_error(req, -32000, f"SCP failed: {r.stderr[:500]}")
        # Install
        if ext == "deb":
            install_cmd = (
                f"sudo dpkg -i {dest} 2>/dev/null || sudo apt-get install -f -y -qq"
            )
        elif ext == "rpm":
            install_cmd = f"rpm -i {dest} 2>/dev/null || yum install -y {dest}"
        else:
            install_cmd = f"chmod +x {dest} && {dest}"
        r2 = ssh.run(install_cmd, timeout=60)
        if not r2.success:
            return jsonrpc_error(
                req,
                -32000,
                f"Install failed (exit={r2.returncode}): {r2.stderr[:500] or r2.stdout[:500]}",
            )
        out = r2.stdout
        return jsonrpc_result(
            req, {"vm": vm_name, "package": package_path, "install_output": out}
        )
    except Exception as e:
        return jsonrpc_error(req, -32000, f"Deploy failed: {e}")


def _run_smoke(req: Request, vm_name: str, timeout: int = 120) -> str:
    vm, _ = _vm_config(vm_name)
    from automation.transport.ssh import SSHClient

    ssh = SSHClient(vm.ip, user=vm.user or "root")
    try:
        # Launch app in background, probe window
        app_path = (
            vm.packages[0].app_path
            if vm.packages and vm.packages[0].app_path
            else "/opt/Rocket.Chat/rocketchat-desktop"
        )
        cmds = f"""
set -e
# Kill existing
pkill -f "rocketchat" 2>/dev/null || true
sleep 1
# Launch with timeout
timeout {timeout} {app_path} --no-sandbox &
APP_PID=$!
sleep 3
# Check process
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
        out = r2.stdout
        return jsonrpc_result(req, {"vm": vm_name, "output": out})
    except Exception as e:
        return jsonrpc_error(req, -32000, f"Smoke test failed: {e}")


def _run_functional(req: Request, args: dict) -> str:
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
    from datetime import datetime
    import os as _os

    # Build scenario path
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
    )

    proxmox = ProxmoxAPI(cfg.proxmox)
    ssh = SSHClient(vm.ip, vm.user)

    # Screenshot dir
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    screenshot_dir = FRAMEWORK / "results" / "functional" / f"{ts}_functional" / vm.name
    _os.makedirs(str(screenshot_dir), exist_ok=True)

    with VncClient(proxmox, vmid=vm.vmid) as vnc:
        screenshotter = Screenshotter(vnc)
        injector = InputInjector(vnc, ssh, vm.is_windows)
        runner = FunctionalRunner(
            vlm=vlm,
            screenshotter=screenshotter,
            injector=injector,
            screenshot_dir=screenshot_dir,
            log_fn=lambda msg: print(f"[mOSdat] {msg}"),
            popup_sweep=True,
        )
        # Load steps from scenario file
        from automation.runners.scenario_loader import load_test_yaml

        name, steps, _, _ = load_test_yaml(scenario_path, cfg)
        # Run the scenario
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


def _list_scenarios(req: Request, path: str = None) -> str:
    base = Path(path) if path else FRAMEWORK / "shared" / "scenarios"
    if not base.exists():
        return jsonrpc_result(req, {"scenarios": [], "path": str(base)})

    files = sorted(base.rglob("*.yaml"))
    scenarios = []
    for f in files:
        rel = (
            f.relative_to(FRAMEWORK / "shared" / "scenarios")
            if FRAMEWORK / "shared" / "scenarios" in f.parents
            else f
        )
        scenarios.append(
            {
                "path": str(rel),
                "name": f.stem,
                "size": f.stat().st_size,
            }
        )

    return jsonrpc_result(req, {"scenarios": scenarios, "path": str(base)})


def _ssh_tool(req: Request, vm_name: str, command: str, timeout: int = 30) -> str:
    out, ec = _ssh(command, vm_name, timeout)
    return jsonrpc_result(req, {"exit_code": ec, "stdout": out})


# ── Main loop ────────────────────────────────────────────────────────────────

HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


def main():
    """Run MCP server: read JSON-RPC from stdin, write responses to stdout."""
    # Ensure we're in the right directory
    os.chdir(str(FRAMEWORK))

    # Send server info via stderr (MCP spec: stderr for logging)
    print("[mcp] mOSdat MCP server starting", file=sys.stderr)
    print(f"[mcp] Framework: {FRAMEWORK}", file=sys.stderr)
    print(f"[mcp] Python: {sys.version.split()[0]}", file=sys.stderr)

    buffer = ""
    for line in sys.stdin:
        if not line:
            continue
        buffer += line
        # Try to parse — handle batched messages
        while buffer.strip():
            try:
                req = json.loads(buffer.strip())
                buffer = ""
            except json.JSONDecodeError:
                # Incomplete line — wait for more
                buffer += sys.stdin.readline()
                continue

            method = req.get("method", "")
            handler = HANDLERS.get(method)
            if handler:
                response = handler(req)
                sys.stdout.write(response)
                sys.stdout.flush()
            else:
                # Echo back for unknown methods (spec compliant)
                sys.stdout.write(
                    jsonrpc_error(req, -32601, f"Method not found: {method}")
                )
                sys.stdout.flush()

        # Flush buffer periodically
        sys.stdout.flush()


if __name__ == "__main__":
    main()
