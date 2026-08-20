"""``mosdat ssh-bootstrap`` — install this host's SSH public key onto a Windows VM.

Windows OpenSSH Server has no POSIX shell, so ``ssh-copy-id`` fails, and
admin accounts ignore ``~/.ssh/authorized_keys`` in favour of
``%ProgramData%\\ssh\\administrators_authorized_keys`` (strict ACLs). When
the VM console is reachable and unlocked but SSH is not yet authorized,
this command drives the Proxmox VNC console with VLM localize/verify
(no hardcoded coordinates) to type the key-install commands into an
elevated PowerShell.

Safety gates (do not weaken):
- If SSH already works, return immediately and never touch the console.
- If the console is a lock/sign-in screen (or the VLM check errors), abort
  without clicking or typing — a false "unlocked" would type into a
  password field.

Windows VMs only. Linux SSH setup is a different problem; this command
rejects non-Windows VMs before any I/O.

Exit codes:
    0  — SSH authenticating (already-working or bootstrap succeeded)
    1  — console steps completed but SSH still is not authenticating
    2  — console automation failed before typing key-install commands
    5  — invalid args / unknown VM / non-Windows / missing pubkey
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from automation.commands.doctor import CheckResult, check_ssh
from automation.transport.ssh import SSHClient
from automation.transport.vnc import VncClient


NON_WINDOWS_ERROR = "ssh-bootstrap is not supported for non-Windows VMs"

ERR_LOCK = (
    "VM console is at a lock/sign-in screen — provide the login password "
    "or unlock it manually first; cannot proceed blind."
)
ERR_NO_SHELL = "could not confirm an elevated shell opened after the UAC step"
ERR_SSH_STILL_FAILS = (
    "console steps completed but SSH still isn't authenticating — "
    "see steps for what ran"
)

_LOCK_QUESTION = (
    "Is this a Windows lock screen, sign-in screen, or password prompt — "
    "i.e., NOT a normal usable desktop?"
)
_UAC_QUESTION = (
    "Is a User Account Control dialog asking to allow this app to make "
    "changes to the device currently visible?"
)
_ELEVATED_QUESTION = (
    "Is an elevated Administrator PowerShell or command-line window open "
    "and ready to accept typed input?"
)
_SEARCH_TARGET = "the taskbar search box"
_RUN_AS_ADMIN_TARGET = (
    "the 'Run as administrator' option for Windows PowerShell in the "
    "search result details"
)
_UAC_YES_TARGET = "the Yes button on the User Account Control dialog"

_SETTLE_AFTER_LINE = 0.4
_SETTLE_AFTER_ICACLS = 1.2
_SETTLE_AFTER_UAC = 1.5
_SSH_RETRY_DELAYS = (0.5, 1.0, 2.0)

_DEFAULT_PUBKEY_CANDIDATES = (
    Path.home() / ".ssh" / "id_ed25519.pub",
    Path.home() / ".ssh" / "id_rsa.pub",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class ConsoleAutomationError(Exception):
    """VLM/VNC could not find or confirm a UI element; do not type blind."""


@dataclass
class BootstrapResult:
    vm: str
    already_working: bool = False
    ok: bool = False
    steps: list[str] = field(default_factory=list)
    error: str = ""


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(msg: str) -> None:
    print(f"[mosdat ssh-bootstrap {_ts()}] {msg}", flush=True)


def exit_code_for(result: BootstrapResult) -> int:
    """Map a failed ``BootstrapResult`` to a CLI exit code (0 handled by caller)."""
    if result.ok:
        return 0
    if result.error.startswith("console steps completed"):
        return 1
    return 2


def resolve_pubkey_path(explicit: Optional[str] = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    for candidate in _DEFAULT_PUBKEY_CANDIDATES:
        if candidate.exists():
            return candidate
    return _DEFAULT_PUBKEY_CANDIDATES[0]


def read_pubkey(explicit: Optional[str] = None) -> str:
    """Read and validate a public-key file; return the key string (not a path)."""
    path = resolve_pubkey_path(explicit)
    if not path.exists():
        if explicit:
            raise FileNotFoundError(f"SSH public key not found: {path}")
        tried = ", ".join(str(p) for p in _DEFAULT_PUBKEY_CANDIDATES)
        raise FileNotFoundError(f"SSH public key not found (tried {tried})")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"SSH public key file {path} is empty")
    if "'" in text:
        raise ValueError(
            f"SSH public key at {path} contains a single quote; "
            "refusing to type it into a PowerShell single-quoted string"
        )
    return " ".join(text.split())


def _load_config(config_path: Optional[str]):
    from automation.config import load_config

    if config_path:
        return load_config(config_path)
    env_cfg = os.environ.get("MOSDAT_CONFIG")
    if env_cfg:
        return load_config(env_cfg)
    cwd_cfg = Path("rocketchat.toml")
    if cwd_cfg.exists():
        return load_config(cwd_cfg)
    for name in ("ubuntu2404.toml", "rocketchat.toml"):
        p = _REPO_ROOT / "examples" / name
        if p.exists():
            return load_config(p)
    raise FileNotFoundError(
        "No mosdat config found. Pass --config or set MOSDAT_CONFIG."
    )


def _probe_ssh(vm_ip: str, vm_user: str) -> CheckResult:
    ssh = SSHClient(vm_ip, user=vm_user, connect_timeout=8)
    return check_ssh(ssh)


def _probe_ssh_with_retry(vm_ip: str, vm_user: str, delays: tuple[float, ...]) -> CheckResult:
    result = _probe_ssh(vm_ip, vm_user)
    if result.status == "PASS":
        return result
    for delay in delays:
        time.sleep(delay)
        result = _probe_ssh(vm_ip, vm_user)
        if result.status == "PASS":
            return result
    return result


def _key_install_commands(pubkey: str) -> list[tuple[str, float]]:
    """PowerShell lines to type, with per-line settle delay after Enter."""
    return [
        (f"$key = '{pubkey}'", _SETTLE_AFTER_LINE),
        (
            r'New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.ssh" | Out-Null',
            _SETTLE_AFTER_LINE,
        ),
        (
            r'Add-Content -Force -Path "$env:USERPROFILE\.ssh\authorized_keys" -Value $key',
            _SETTLE_AFTER_LINE,
        ),
        (
            r'Add-Content -Force -Path "$env:ProgramData\ssh\administrators_authorized_keys" -Value $key',
            _SETTLE_AFTER_LINE,
        ),
        (
            r'icacls.exe "$env:ProgramData\ssh\administrators_authorized_keys" /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"',
            _SETTLE_AFTER_ICACLS,
        ),
    ]


def _capture(vnc) -> tuple:
    screenshot, screen_size = vnc.capture()
    return screenshot, screen_size


def _localize_click(vlm, vnc, screenshot, screen_size, target: str, steps: list[str]) -> None:
    try:
        x, y = vlm.localize(screenshot, target, screen_size)
    except Exception as e:
        raise ConsoleAutomationError(
            f"could not find UI element {target!r}: {e}"
        ) from e
    vnc.click(x, y)
    steps.append(f"clicked {target!r} at ({x}, {y})")


def _type_line(vnc, text: str, settle: float) -> None:
    vnc.type_text(text)
    vnc.key("enter")
    time.sleep(settle)


def _dry_run_result(vm_name: str) -> BootstrapResult:
    steps = [
        "[dry-run] probe SSH (skip console if already authenticating)",
        "[dry-run] confirm unlocked Windows desktop via VLM (abort on lock/sign-in)",
        "[dry-run] localize+click taskbar search box, type powershell",
        "[dry-run] localize+click 'Run as administrator'",
        "[dry-run] if UAC dialog visible, localize+click Yes",
        "[dry-run] confirm elevated PowerShell is ready",
        "[dry-run] type key-install commands into both authorized_keys locations + icacls",
        "[dry-run] re-probe SSH",
    ]
    for line in steps:
        _log(line)
    return BootstrapResult(vm=vm_name, ok=True, steps=steps)


def bootstrap_windows_ssh(
    vm_name: str, vm_ip: str, vm_user: str, vmid: int,
    proxmox, vlm, pubkey: str, *, dry_run: bool,
) -> BootstrapResult:
    """Install ``pubkey`` onto a Windows VM via VNC+VLM, or no-op if SSH works.

    ``pubkey`` is the key *contents*, already read from disk by the caller.
    This function never opens ``~/.ssh`` itself so tests can pass a string.
    """
    if dry_run:
        return _dry_run_result(vm_name)

    probe = _probe_ssh(vm_ip, vm_user)
    if probe.status == "PASS":
        return BootstrapResult(vm=vm_name, already_working=True, ok=True)

    steps: list[str] = [
        f"SSH probe failed ({probe.detail or probe.status}); driving console"
    ]
    typed = False

    try:
        with VncClient(proxmox, vmid=vmid) as vnc:
            screenshot, screen_size = _capture(vnc)
            try:
                locked = vlm.verify(screenshot, _LOCK_QUESTION)
            except Exception as e:
                return BootstrapResult(
                    vm=vm_name,
                    ok=False,
                    steps=steps,
                    error=(
                        "VM console lock-screen check failed — aborting rather "
                        f"than typing blind ({e})"
                    ),
                )
            if locked:
                return BootstrapResult(
                    vm=vm_name, ok=False, steps=steps, error=ERR_LOCK,
                )
            steps.append("console is an unlocked desktop")

            _localize_click(vlm, vnc, screenshot, screen_size, _SEARCH_TARGET, steps)
            vnc.type_text("powershell")
            steps.append("typed 'powershell' into search")

            screenshot, screen_size = _capture(vnc)
            _localize_click(
                vlm, vnc, screenshot, screen_size, _RUN_AS_ADMIN_TARGET, steps,
            )

            screenshot, screen_size = _capture(vnc)
            try:
                uac_visible = vlm.verify(screenshot, _UAC_QUESTION)
            except Exception as e:
                raise ConsoleAutomationError(
                    f"UAC visibility check failed: {e}"
                ) from e
            if uac_visible:
                _localize_click(
                    vlm, vnc, screenshot, screen_size, _UAC_YES_TARGET, steps,
                )
            else:
                _log("no UAC dialog visible; continuing")
                steps.append("no UAC dialog visible; continuing")

            time.sleep(_SETTLE_AFTER_UAC)
            screenshot, screen_size = _capture(vnc)
            try:
                elevated = vlm.verify(screenshot, _ELEVATED_QUESTION)
            except Exception as e:
                return BootstrapResult(
                    vm=vm_name,
                    ok=False,
                    steps=steps,
                    error=f"{ERR_NO_SHELL} ({e})",
                )
            if not elevated:
                return BootstrapResult(
                    vm=vm_name, ok=False, steps=steps, error=ERR_NO_SHELL,
                )
            steps.append("elevated PowerShell is ready")

            for line, settle in _key_install_commands(pubkey):
                _type_line(vnc, line, settle)
                steps.append(f"typed: {line}")
            typed = True
    except ConsoleAutomationError as e:
        return BootstrapResult(vm=vm_name, ok=False, steps=steps, error=str(e))
    except Exception as e:
        if not typed:
            return BootstrapResult(
                vm=vm_name,
                ok=False,
                steps=steps,
                error=f"console automation failed: {e}",
            )
        steps.append(f"warning after typing: {e}")

    post = _probe_ssh_with_retry(vm_ip, vm_user, _SSH_RETRY_DELAYS)
    if post.status == "PASS":
        steps.append("SSH probe succeeded after console steps")
        return BootstrapResult(vm=vm_name, ok=True, steps=steps)

    detail = post.detail or post.status
    steps.append(f"SSH probe still failing ({detail})")
    return BootstrapResult(
        vm=vm_name, ok=False, steps=steps, error=ERR_SSH_STILL_FAILS,
    )


def run_ssh_bootstrap(args: argparse.Namespace) -> int:
    try:
        cfg = _load_config(getattr(args, "config", None))
    except Exception as e:
        print(f"[mosdat ssh-bootstrap] ERROR: {e}", file=sys.stderr)
        return 5

    vm_name = args.vm
    if vm_name not in cfg.vm_by_name:
        names = ", ".join(sorted(cfg.vm_by_name.keys())) or "(none)"
        print(
            f"[mosdat ssh-bootstrap] ERROR: unknown VM '{vm_name}'. "
            f"Available: {names}",
            file=sys.stderr,
        )
        return 5

    vm = cfg.vm_by_name[vm_name]
    os_type = getattr(vm, "os_type", "linux")
    if os_type != "windows":
        print(
            f"[mosdat ssh-bootstrap] ERROR: {NON_WINDOWS_ERROR} "
            f"(os_type={os_type!r})",
            file=sys.stderr,
        )
        return 5

    try:
        pubkey = read_pubkey(getattr(args, "pubkey", None))
    except (OSError, ValueError) as e:
        print(f"[mosdat ssh-bootstrap] ERROR: {e}", file=sys.stderr)
        return 5

    dry_run = bool(getattr(args, "dry_run", False))
    proxmox = None
    vlm = None
    if not dry_run:
        from automation.proxmox.api import ProxmoxAPI
        from automation.vlm.client import VLMClient

        proxmox = ProxmoxAPI(cfg.proxmox)
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
        dry_run=dry_run,
    )
    for step in result.steps:
        _log(step)
    if result.already_working:
        _log(f"{vm.name}: SSH already authenticating — nothing to do")
        return 0
    if result.ok:
        _log(f"{vm.name}: SSH bootstrap succeeded")
        return 0
    _log(f"{vm.name}: ERROR: {result.error}")
    return exit_code_for(result)


def add_ssh_bootstrap_subparser(sub) -> None:
    """Wire ``mosdat ssh-bootstrap`` onto an existing subparsers action."""
    p = sub.add_parser(
        "ssh-bootstrap",
        help=(
            "Install this host's SSH public key onto a Windows VM via the "
            "unlocked VNC console"
        ),
        description=(
            "When a Windows VM's desktop is reachable and unlocked but SSH "
            "is not yet authorized, drive the Proxmox VNC console (VLM "
            "localize/verify, no hardcoded coordinates) to install this "
            "host's public key into both per-user authorized_keys and "
            "administrators_authorized_keys. No-ops if SSH already works. "
            "Windows VMs only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  mosdat ssh-bootstrap windows10 --config examples/rocketchat.toml\n"
            "  mosdat ssh-bootstrap windows11 --pubkey ~/.ssh/id_ed25519.pub --dry-run\n"
            "\n"
            "Exit codes: 0 ok/already-working, 1 SSH still failing after "
            "console steps, 2 console automation failed, 5 invalid args.\n"
        ),
    )
    p.add_argument("vm", help="VM name (must have os_type=windows in --config)")
    p.add_argument(
        "--pubkey",
        default=None,
        metavar="PATH",
        help=(
            "Path to an SSH public key (default: ~/.ssh/id_ed25519.pub, "
            "falling back to ~/.ssh/id_rsa.pub)"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Log the planned console/SSH steps without touching the VM",
    )
    p.add_argument(
        "--config",
        default=None,
        help="mosdat TOML config (used to resolve the VM name to IP/vmid)",
    )
