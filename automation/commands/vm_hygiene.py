"""``mosdat vm-hygiene`` — fix default-Windows "focus-thief" UI on test VMs.

Windows test VMs accumulate default-Windows UI that silently breaks
VLM-driven toast/notification testing: an unrelated OS nag can steal focus
or cover the toast region a screenshot check is looking at, and Windows'
default Focus Assist "quiet hours" (typically midnight-6am *local* time)
can suppress toast banners entirely with no error — a real pass looks like
a failure for reasons that have nothing to do with the app under test.

Four confirmed culprits (all live-verified on real VMs), fixed idempotently:

1. **VM clock drift** — a VM sitting at the wrong wall-clock time can land
   inside Focus Assist's quiet-hours window purely by accident. VMs may not
   have reliable NTP/internet access, so instead of relying on ``w32tm``,
   this sets the VM's clock directly from *this host's* current UTC instant
   (converted to the VM's own local timezone offset via
   ``DateTimeOffset.FromUnixTimeSeconds(...).ToLocalTime()``) — correct
   regardless of what timezone the VM is configured for, no NTP required.
2. **OneDrive first-run sign-in nag** — steals focus. Fixed via the
   documented Microsoft policy registry key (Group Policy "Prevent the
   usage of OneDrive for file storage" maps to this key/value):
   ``HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\OneDrive``,
   DWORD ``DisableFileSyncNGSC=1``.
3. **"Turn on Windows Backup" toast** (Windows-11-era) — DESIGN NOTE: no
   clean *registry-only* suppression was found; this nag is driven by the
   scheduled task ``\\Microsoft\\Windows\\WindowsBackup\\ConfigNotification``,
   which is disabled via ``Disable-ScheduledTask`` instead. If a given VM
   build doesn't have this task at all, that item reports already-ok
   (nothing to suppress).
4. **Taskbar news/weather widget** — a mis-localized click can open it
   fullscreen in Edge and kill a whole test run. Confirmed fix (live-tested
   by the reporting session): DWORD ``ShellFeedsTaskbarViewMode=2`` under
   ``HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Feeds``, plus DWORD
   ``TaskbarDa=0`` under
   ``HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced``.
   Explorer is restarted (``Stop-Process -Name explorer -Force``) only when
   this item is actually changed, so the taskbar picks it up live.

Every check reads the current value first and only writes when the VM is
not already in the desired state (idempotent — a repeat call on an
already-hygienic VM reports "already-ok" for all four items, no-op).

Windows VMs only. Rejects a Linux VM target before any I/O (mirrors
``ssh_bootstrap``'s Windows-only guard).

Exit codes:
    0  — all items already-ok or fixed
    2  — one or more items failed
    5  — invalid args / unknown VM / non-Windows VM
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone as _timezone
from pathlib import Path
from typing import Optional


NON_WINDOWS_ERROR = "vm-hygiene is not supported for non-Windows VMs"

# Drift tolerance before we bother resetting the VM clock. Generous — we
# only care about avoiding an accidental Focus-Assist-quiet-hours mismatch,
# not sub-second accuracy.
_CLOCK_DRIFT_TOLERANCE_S = 120

ITEM_CLOCK = "clock"
ITEM_ONEDRIVE = "onedrive"
ITEM_BACKUP_NAG = "backup_nag"
ITEM_NEWS_WIDGET = "news_widget"

ITEMS = (ITEM_CLOCK, ITEM_ONEDRIVE, ITEM_BACKUP_NAG, ITEM_NEWS_WIDGET)

STATUS_ALREADY_OK = "already-ok"
STATUS_FIXED = "fixed"
STATUS_FAILED = "failed"
STATUS_DRY_RUN = "dry-run"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_ONEDRIVE_POLICY_KEY = r"HKLM:\SOFTWARE\Policies\Microsoft\Windows\OneDrive"
_BACKUP_TASK_PATH = r"\Microsoft\Windows\WindowsBackup\\"
_BACKUP_TASK_NAME = "ConfigNotification"
_FEEDS_KEY = r"HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Feeds"
_EXPLORER_ADVANCED_KEY = r"HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced"


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(msg: str) -> None:
    print(f"[mosdat vm-hygiene {_ts()}] {msg}", flush=True)


@dataclass
class ItemResult:
    item: str
    status: str  # "already-ok" | "fixed" | "failed" | "dry-run"
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_ALREADY_OK, STATUS_FIXED, STATUS_DRY_RUN)


@dataclass
class HygieneResult:
    vm: str
    items: list[ItemResult] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and all(i.ok for i in self.items)

    def item(self, name: str) -> Optional[ItemResult]:
        for i in self.items:
            if i.item == name:
                return i
        return None


def exit_code_for(result: HygieneResult) -> int:
    if result.error:
        return 5
    return 0 if result.ok else 2


# ---------------------------------------------------------------------------
# PowerShell helpers — mirrors automation/commands/build.py's
# _ps_quote/_ps_b64/_ps_run convention (base64 -EncodedCommand dodges the
# SSH -> shell -> PowerShell quoting gotcha documented in KNOWN_ISSUES.md).
# ---------------------------------------------------------------------------


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _ps_b64(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _ps_run(ssh, script: str, *, timeout: int):
    return ssh.run(
        f"powershell -NoProfile -EncodedCommand {_ps_b64(script)}",
        timeout=timeout,
    )


def _ps_tag(stdout: str, name: str) -> str:
    prefix = name + "="
    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.split("=", 1)[1].strip()
    return ""


# ---------------------------------------------------------------------------
# Item 1: VM clock drift
# ---------------------------------------------------------------------------


def _check_clock(ssh) -> tuple[Optional[int], str]:
    """Return (drift_seconds, error). ``drift_seconds`` is VM-epoch minus
    host-epoch (captured immediately before/after the SSH round trip)."""
    before = time.time()
    res = _ps_run(
        ssh,
        "Write-Host \"EPOCH=$([System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds())\"",
        timeout=30,
    )
    after = time.time()
    if not res.success:
        return None, (res.stderr or res.stdout or "clock check failed").strip()[:300]
    raw = _ps_tag(res.stdout, "EPOCH")
    try:
        vm_epoch = int(raw)
    except ValueError:
        return None, f"could not parse EPOCH from PowerShell output: {res.stdout!r}"
    host_epoch = (before + after) / 2
    return int(vm_epoch - host_epoch), ""


def _fix_clock(ssh) -> tuple[bool, str]:
    target_epoch = int(time.time())
    script = (
        "$ErrorActionPreference = 'Stop';"
        f"$target = [System.DateTimeOffset]::FromUnixTimeSeconds({target_epoch}).ToLocalTime().DateTime;"
        "Set-Date -Date $target;"
        "Write-Host 'CLOCK_SET_OK'"
    )
    res = _ps_run(ssh, script, timeout=30)
    if not res.success:
        return False, (res.stderr or res.stdout or "Set-Date failed").strip()[:300]
    return True, ""


def check_and_fix_clock(ssh) -> ItemResult:
    drift, err = _check_clock(ssh)
    if err:
        return ItemResult(ITEM_CLOCK, STATUS_FAILED, err)
    assert drift is not None
    if abs(drift) <= _CLOCK_DRIFT_TOLERANCE_S:
        return ItemResult(
            ITEM_CLOCK, STATUS_ALREADY_OK, f"drift {drift:+d}s (within tolerance)"
        )
    ok, err = _fix_clock(ssh)
    if not ok:
        return ItemResult(
            ITEM_CLOCK, STATUS_FAILED, f"drift {drift:+d}s; fix failed: {err}"
        )
    return ItemResult(ITEM_CLOCK, STATUS_FIXED, f"was {drift:+d}s off; reset from host clock")


# ---------------------------------------------------------------------------
# Item 2: OneDrive sign-in nag
# ---------------------------------------------------------------------------


def _check_onedrive(ssh) -> tuple[Optional[bool], str]:
    script = (
        "$v = (Get-ItemProperty -Path "
        f"{_ps_quote(_ONEDRIVE_POLICY_KEY)} -Name DisableFileSyncNGSC "
        "-ErrorAction SilentlyContinue).DisableFileSyncNGSC;"
        "Write-Host \"VALUE=$v\""
    )
    res = _ps_run(ssh, script, timeout=30)
    if not res.success:
        return None, (res.stderr or res.stdout or "OneDrive policy check failed").strip()[:300]
    raw = _ps_tag(res.stdout, "VALUE")
    return raw == "1", ""


def _fix_onedrive(ssh) -> tuple[bool, str]:
    script = (
        "$ErrorActionPreference = 'Stop';"
        f"New-Item -Path {_ps_quote(_ONEDRIVE_POLICY_KEY)} -Force | Out-Null;"
        f"Set-ItemProperty -Path {_ps_quote(_ONEDRIVE_POLICY_KEY)} "
        "-Name DisableFileSyncNGSC -Value 1 -Type DWord;"
        # Best-effort: stop the current sign-in nag if it's already showing.
        "Get-Process -Name OneDrive -ErrorAction SilentlyContinue "
        "| Stop-Process -Force -ErrorAction SilentlyContinue;"
        "Write-Host 'ONEDRIVE_POLICY_OK'"
    )
    res = _ps_run(ssh, script, timeout=30)
    if not res.success:
        return False, (res.stderr or res.stdout or "OneDrive policy write failed").strip()[:300]
    return True, ""


def check_and_fix_onedrive(ssh) -> ItemResult:
    already_ok, err = _check_onedrive(ssh)
    if err:
        return ItemResult(ITEM_ONEDRIVE, STATUS_FAILED, err)
    if already_ok:
        return ItemResult(ITEM_ONEDRIVE, STATUS_ALREADY_OK, "DisableFileSyncNGSC=1")
    ok, err = _fix_onedrive(ssh)
    if not ok:
        return ItemResult(ITEM_ONEDRIVE, STATUS_FAILED, err)
    return ItemResult(ITEM_ONEDRIVE, STATUS_FIXED, "set DisableFileSyncNGSC=1, killed OneDrive.exe")


# ---------------------------------------------------------------------------
# Item 3: "Turn on Windows Backup" toast
# ---------------------------------------------------------------------------


def _check_backup_nag(ssh) -> tuple[Optional[str], str]:
    script = (
        f"$t = Get-ScheduledTask -TaskPath {_ps_quote(_BACKUP_TASK_PATH)} "
        f"-TaskName {_ps_quote(_BACKUP_TASK_NAME)} -ErrorAction SilentlyContinue;"
        "if ($t) { Write-Host \"STATE=$($t.State)\" } else { Write-Host 'STATE=NOTFOUND' }"
    )
    res = _ps_run(ssh, script, timeout=30)
    if not res.success:
        return None, (res.stderr or res.stdout or "backup-nag task check failed").strip()[:300]
    return _ps_tag(res.stdout, "STATE") or "NOTFOUND", ""


def _fix_backup_nag(ssh) -> tuple[bool, str]:
    script = (
        "$ErrorActionPreference = 'Stop';"
        f"Disable-ScheduledTask -TaskPath {_ps_quote(_BACKUP_TASK_PATH)} "
        f"-TaskName {_ps_quote(_BACKUP_TASK_NAME)} | Out-Null;"
        "Write-Host 'BACKUP_TASK_DISABLED'"
    )
    res = _ps_run(ssh, script, timeout=30)
    if not res.success:
        return False, (res.stderr or res.stdout or "Disable-ScheduledTask failed").strip()[:300]
    return True, ""


def check_and_fix_backup_nag(ssh) -> ItemResult:
    state, err = _check_backup_nag(ssh)
    if err:
        return ItemResult(ITEM_BACKUP_NAG, STATUS_FAILED, err)
    if state in ("NOTFOUND", "Disabled"):
        detail = (
            "task not present on this VM build"
            if state == "NOTFOUND"
            else "task already Disabled"
        )
        return ItemResult(ITEM_BACKUP_NAG, STATUS_ALREADY_OK, detail)
    ok, err = _fix_backup_nag(ssh)
    if not ok:
        return ItemResult(ITEM_BACKUP_NAG, STATUS_FAILED, f"was {state}; disable failed: {err}")
    return ItemResult(ITEM_BACKUP_NAG, STATUS_FIXED, f"disabled scheduled task (was {state})")


# ---------------------------------------------------------------------------
# Item 4: taskbar news/weather widget
# ---------------------------------------------------------------------------


def _check_news_widget(ssh) -> tuple[Optional[tuple[str, str]], str]:
    script = (
        "$feeds = (Get-ItemProperty -Path "
        f"{_ps_quote(_FEEDS_KEY)} -Name ShellFeedsTaskbarViewMode "
        "-ErrorAction SilentlyContinue).ShellFeedsTaskbarViewMode;"
        "$da = (Get-ItemProperty -Path "
        f"{_ps_quote(_EXPLORER_ADVANCED_KEY)} -Name TaskbarDa "
        "-ErrorAction SilentlyContinue).TaskbarDa;"
        "Write-Host \"FEEDS=$feeds\";"
        "Write-Host \"TASKBARDA=$da\""
    )
    res = _ps_run(ssh, script, timeout=30)
    if not res.success:
        return None, (res.stderr or res.stdout or "news-widget check failed").strip()[:300]
    return (_ps_tag(res.stdout, "FEEDS"), _ps_tag(res.stdout, "TASKBARDA")), ""


def _fix_news_widget(ssh) -> tuple[bool, str]:
    script = (
        "$ErrorActionPreference = 'Stop';"
        f"New-Item -Path {_ps_quote(_FEEDS_KEY)} -Force | Out-Null;"
        f"Set-ItemProperty -Path {_ps_quote(_FEEDS_KEY)} "
        "-Name ShellFeedsTaskbarViewMode -Value 2 -Type DWord;"
        f"New-Item -Path {_ps_quote(_EXPLORER_ADVANCED_KEY)} -Force | Out-Null;"
        f"Set-ItemProperty -Path {_ps_quote(_EXPLORER_ADVANCED_KEY)} "
        "-Name TaskbarDa -Value 0 -Type DWord;"
        # Taskbar reads these at Explorer start — restart it so the change
        # applies live instead of waiting for the next logon.
        "Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue;"
        "Start-Sleep -Milliseconds 500;"
        "Start-Process explorer;"
        "Write-Host 'NEWS_WIDGET_OK'"
    )
    res = _ps_run(ssh, script, timeout=30)
    if not res.success:
        return False, (res.stderr or res.stdout or "news-widget registry write failed").strip()[:300]
    return True, ""


def check_and_fix_news_widget(ssh) -> ItemResult:
    values, err = _check_news_widget(ssh)
    if err:
        return ItemResult(ITEM_NEWS_WIDGET, STATUS_FAILED, err)
    assert values is not None
    feeds, taskbar_da = values
    if feeds == "2" and taskbar_da == "0":
        return ItemResult(
            ITEM_NEWS_WIDGET, STATUS_ALREADY_OK,
            "ShellFeedsTaskbarViewMode=2, TaskbarDa=0",
        )
    ok, err = _fix_news_widget(ssh)
    if not ok:
        return ItemResult(
            ITEM_NEWS_WIDGET, STATUS_FAILED,
            f"was FEEDS={feeds or '(unset)'} TASKBARDA={taskbar_da or '(unset)'}; fix failed: {err}",
        )
    return ItemResult(
        ITEM_NEWS_WIDGET, STATUS_FIXED,
        "set ShellFeedsTaskbarViewMode=2, TaskbarDa=0, restarted explorer",
    )


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


_CHECKS = (
    (ITEM_CLOCK, check_and_fix_clock),
    (ITEM_ONEDRIVE, check_and_fix_onedrive),
    (ITEM_BACKUP_NAG, check_and_fix_backup_nag),
    (ITEM_NEWS_WIDGET, check_and_fix_news_widget),
)


def _dry_run_result(vm_name: str) -> HygieneResult:
    items = [
        ItemResult(ITEM_CLOCK, STATUS_DRY_RUN, "[dry-run] would check/reset VM clock from host UTC"),
        ItemResult(ITEM_ONEDRIVE, STATUS_DRY_RUN, "[dry-run] would check/set OneDrive DisableFileSyncNGSC policy"),
        ItemResult(ITEM_BACKUP_NAG, STATUS_DRY_RUN, "[dry-run] would check/disable Windows Backup nag scheduled task"),
        ItemResult(ITEM_NEWS_WIDGET, STATUS_DRY_RUN, "[dry-run] would check/set taskbar feeds+widget registry values"),
    ]
    for item in items:
        _log(item.detail)
    return HygieneResult(vm=vm_name, items=items)


def run_vm_hygiene_checks(ssh, vm_name: str) -> HygieneResult:
    """Run all four checks/fixes over an already-constructed ``ssh`` client."""
    items: list[ItemResult] = []
    for _name, fn in _CHECKS:
        try:
            result = fn(ssh)
        except Exception as e:  # pragma: no cover - defensive
            result = ItemResult(_name, STATUS_FAILED, f"unexpected error: {e}")
        items.append(result)
        _log(f"{result.item}: {result.status} — {result.detail}")
    return HygieneResult(vm=vm_name, items=items)


def vm_hygiene(
    vm_name: str, vm_ip: str, vm_user: str, *, dry_run: bool,
) -> HygieneResult:
    """Entry point shared by the CLI and MCP handlers.

    ``vm_ip``/``vm_user`` are used to build the ``SSHClient`` directly so
    tests can stub ``SSHClient`` without loading a real config.
    """
    if dry_run:
        return _dry_run_result(vm_name)

    from automation.transport.ssh import SSHClient

    ssh = SSHClient(vm_ip, user=vm_user, connect_timeout=10)
    return run_vm_hygiene_checks(ssh, vm_name)


def _load_config(config_path: Optional[str]):
    from automation.config import load_config
    import os

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


def run_vm_hygiene(args: argparse.Namespace) -> int:
    try:
        cfg = _load_config(getattr(args, "config", None))
    except Exception as e:
        print(f"[mosdat vm-hygiene] ERROR: {e}", file=sys.stderr)
        return 5

    vm_name = args.vm
    if vm_name not in cfg.vm_by_name:
        names = ", ".join(sorted(cfg.vm_by_name.keys())) or "(none)"
        print(
            f"[mosdat vm-hygiene] ERROR: unknown VM '{vm_name}'. Available: {names}",
            file=sys.stderr,
        )
        return 5

    vm = cfg.vm_by_name[vm_name]
    if not vm.is_windows:
        print(
            f"[mosdat vm-hygiene] ERROR: {NON_WINDOWS_ERROR} (os_type={vm.os_type!r})",
            file=sys.stderr,
        )
        return 5

    dry_run = bool(getattr(args, "dry_run", False))
    result = vm_hygiene(vm.name, vm.ip, vm.user or "root", dry_run=dry_run)

    print(f"[mosdat vm-hygiene] {vm.name}:")
    for item in result.items:
        print(f"  {item.item:<12} {item.status:<10} {item.detail}")
    if result.error:
        print(f"[mosdat vm-hygiene] ERROR: {result.error}", file=sys.stderr)

    return exit_code_for(result)


def add_vm_hygiene_subparser(sub) -> None:
    """Wire ``mosdat vm-hygiene`` onto an existing subparsers action."""
    p = sub.add_parser(
        "vm-hygiene",
        help=(
            "Fix default-Windows focus-thief UI (clock drift, OneDrive nag, "
            "Windows Backup toast, taskbar news widget) that silently breaks "
            "VLM toast/notification testing"
        ),
        description=(
            "Checks and idempotently fixes four confirmed Windows-VM "
            "hygiene issues: VM clock drift (reset from this host's UTC "
            "clock — no NTP required), OneDrive first-run sign-in nag "
            "(policy registry key), the 'Turn on Windows Backup' toast "
            "(scheduled-task suppression), and the taskbar news/weather "
            "widget (registry + Explorer restart). Already-clean items "
            "report already-ok and are left untouched. Windows VMs only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  mosdat vm-hygiene windows10 --config examples/rocketchat.toml\n"
            "  mosdat vm-hygiene windows11 --dry-run\n"
            "\n"
            "Exit codes: 0 all items already-ok/fixed, 2 one or more items "
            "failed, 5 invalid args / unknown VM / non-Windows VM.\n"
        ),
    )
    p.add_argument("vm", help="VM name (must have os_type=windows in --config)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Log the planned checks without touching the VM",
    )
    p.add_argument(
        "--config",
        default=None,
        help="mosdat TOML config (used to resolve the VM name to IP/user)",
    )
