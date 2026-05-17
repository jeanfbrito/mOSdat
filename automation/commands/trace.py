"""``mosdat trace <toml> --vms <vm>`` — input capability probe.

Launches the app binary on the VM, exercises common input methods
(menu accelerators, common shortcuts, webview focus stealing, sidebar kebab,
chrome focus repeat), takes before/after screenshots, and reports which work.

Exit codes:
  0 — probe completed (results may include SWALLOWED/NO-ACCEL)
  1 — one or more input methods were unexpectedly SWALLOWED (use as CI gate)
  2 — setup/connection error

Use ``--write-manifest`` to persist results to
``shared/binary_capabilities/<asar_sha>.json`` for ``mosdat lint`` to consult.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from automation.config import load_config
from automation.transport.ssh import SSHClient

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_MENU_ACCELS = ["alt+f", "alt+e", "alt+v", "alt+w", "alt+h", "F10"]
_COMMON_SHORTCUTS = ["ctrl+comma", "ctrl+f", "ctrl+r"]

# Threshold for PIL grayscale mean-diff to classify OPEN vs SWALLOWED
_DIFF_THRESHOLD = 3.0


@dataclass
class ProbeResult:
    key: str
    status: str          # "OPEN" | "SWALLOWED" | "NO-ACCEL" | "ERROR"
    detail: str = ""


@dataclass
class TraceReport:
    binary_sha: str
    vm: str
    results: list[ProbeResult] = field(default_factory=list)

    def to_manifest_accelerators(self) -> dict:
        mapping = {"OPEN": "open", "SWALLOWED": "swallowed_in_webview", "NO-ACCEL": "no_accel"}
        return {r.key: mapping.get(r.status, "unknown") for r in self.results}


# ---------------------------------------------------------------------------
# Screenshot diff helper
# ---------------------------------------------------------------------------

def _screenshot_diff(before: bytes, after: bytes) -> float:
    """Return mean absolute pixel diff (grayscale) between two BMP/PNG byte blobs."""
    try:
        from PIL import Image, ImageChops
        import io
        img_a = Image.open(io.BytesIO(before)).convert("L")
        img_b = Image.open(io.BytesIO(after)).convert("L")
        if img_a.size != img_b.size:
            img_b = img_b.resize(img_a.size)
        diff = ImageChops.difference(img_a, img_b)
        import statistics
        pixels = list(diff.getdata())
        if not pixels:
            return 0.0
        return statistics.mean(pixels)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# VNC screenshot helper (falls back to ssh xwd if VNC not available)
# ---------------------------------------------------------------------------

def _capture_vnc(ssh, config, vm) -> Optional[bytes]:
    """Take a screenshot via the Proxmox VNC client. Returns raw bytes or None."""
    try:
        from automation.proxmox.api import ProxmoxAPI
        from automation.transport.vnc import VncClient
        api = ProxmoxAPI(
            host=config.proxmox.host,
            port=config.proxmox.port,
            user=config.proxmox.user,
            password=config.proxmox.password,
        )
        with VncClient(api, vmid=vm.vmid) as vnc:
            img_bytes, _ = vnc.capture()
            return img_bytes
    except Exception:
        return None


def _send_key_vnc(ssh, config, vm, key: str) -> bool:
    """Send a key combo via VNC. Returns True on success."""
    try:
        from automation.proxmox.api import ProxmoxAPI
        from automation.transport.vnc import VncClient
        api = ProxmoxAPI(
            host=config.proxmox.host,
            port=config.proxmox.port,
            user=config.proxmox.user,
            password=config.proxmox.password,
        )
        with VncClient(api, vmid=vm.vmid) as vnc:
            vnc.key(key)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Focus helpers
# ---------------------------------------------------------------------------

_FOCUS_TITLE_BAR_CMD = (
    "XAUTH=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1); "
    "[ -z \"$XAUTH\" ] && XAUTH=/run/user/1000/gdm/Xauthority; "
    "export DISPLAY=:0 XAUTHORITY=\"$XAUTH\"; "
    "WID=$(wmctrl -l | grep -i 'Rocket' | head -1 | awk '{print $1}'); "
    "[ -n \"$WID\" ] && wmctrl -i -a \"$WID\" || true"
)

_FOCUS_FORM_CMD = (
    "XAUTH=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1); "
    "[ -z \"$XAUTH\" ] && XAUTH=/run/user/1000/gdm/Xauthority; "
    "export DISPLAY=:0 XAUTHORITY=\"$XAUTH\"; "
    "xdotool key Tab 2>/dev/null || true"
)


def _launch_app(ssh, binary_path: str) -> None:
    cmd = (
        "XAUTH=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1); "
        "[ -z \"$XAUTH\" ] && XAUTH=/run/user/1000/gdm/Xauthority; "
        f"export DISPLAY=:0 XAUTHORITY=\"$XAUTH\"; "
        f"nohup {binary_path} --no-sandbox --disable-gpu --ozone-platform=x11 "
        ">/dev/null 2>&1 &"
    )
    ssh.run(cmd, timeout=10)


def _kill_app(ssh) -> None:
    cmd = (
        "pkill -KILL -f '/opt/Rocket.Chat' 2>/dev/null || true; "
        "pkill -KILL -f 'rocketchat-desktop' 2>/dev/null || true; "
        "sleep 1"
    )
    ssh.run(cmd, timeout=10)


# ---------------------------------------------------------------------------
# Probe a single key combo
# ---------------------------------------------------------------------------

def _probe_key(
    ssh,
    config,
    vm,
    key: str,
    use_vnc: bool = True,
) -> ProbeResult:
    """Probe one key combo: capture before, send key, wait, capture after, diff."""
    before = _capture_vnc(ssh, config, vm) if use_vnc else None
    if before is None:
        return ProbeResult(key=key, status="ERROR", detail="VNC capture failed")

    sent = _send_key_vnc(ssh, config, vm, key) if use_vnc else False
    if not sent:
        # Fallback: xdotool via SSH
        xdotool_key = key.replace("ctrl+comma", "ctrl+comma").replace("F10", "F10")
        r = ssh.run(
            f"XAUTH=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1); "
            f"[ -z \"$XAUTH\" ] && XAUTH=/run/user/1000/gdm/Xauthority; "
            f"export DISPLAY=:0 XAUTHORITY=\"$XAUTH\"; "
            f"xdotool key {xdotool_key} 2>/dev/null",
            timeout=8,
        )
        _ = r  # result not used beyond fire-and-forget

    time.sleep(0.8)

    after = _capture_vnc(ssh, config, vm) if use_vnc else None
    if after is None:
        return ProbeResult(key=key, status="ERROR", detail="VNC after-capture failed")

    diff = _screenshot_diff(before, after)
    if diff > _DIFF_THRESHOLD:
        return ProbeResult(key=key, status="OPEN", detail=f"diff={diff:.1f}")
    else:
        # Distinguish SWALLOWED (key registered but action no-op in current focus)
        # from NO-ACCEL (key not even registered by app) — heuristic: if modifier key
        # combos that any Electron app should handle show no diff, call it SWALLOWED;
        # for Ctrl+, which may genuinely not be registered, call it NO-ACCEL.
        if key in ("ctrl+comma",):
            return ProbeResult(key=key, status="NO-ACCEL", detail=f"diff={diff:.1f}")
        return ProbeResult(key=key, status="SWALLOWED", detail=f"diff={diff:.1f} (webview focus?)")


# ---------------------------------------------------------------------------
# Webview focus stealing test
# ---------------------------------------------------------------------------

def _probe_webview_focus(ssh, config, vm, key: str) -> tuple[ProbeResult, ProbeResult]:
    """Test key with form-focused vs title-bar-focused context.

    Returns (form_result, titlebar_result).
    """
    # Title-bar focused context
    ssh.run(_FOCUS_TITLE_BAR_CMD, timeout=8)
    time.sleep(0.3)
    title_result = _probe_key(ssh, config, vm, key)
    title_result.detail = f"title-bar-focus: {title_result.detail}"

    # Dismiss any open menu (Escape)
    _send_key_vnc(ssh, config, vm, "escape")
    time.sleep(0.3)

    # Form focused context (Tab into webview)
    ssh.run(_FOCUS_FORM_CMD, timeout=8)
    time.sleep(0.3)
    form_result = _probe_key(ssh, config, vm, key)
    form_result.key = f"{key}(form-focused)"
    form_result.detail = f"form-focus: {form_result.detail}"

    return form_result, title_result


# ---------------------------------------------------------------------------
# VLM kebab probe
# ---------------------------------------------------------------------------

def _probe_kebab_via_vlm(ssh, config, vm) -> ProbeResult:
    """Attempt to localize the sidebar kebab via VLM and report confidence."""
    try:
        from automation.vlm.client import VLMClient
        from automation.vlm.screenshot import Screenshotter
        import io
        from PIL import Image

        vlm = VLMClient(
            base_url=config.vlm.base_url,
            model=config.vlm.model,
        )
        # Take a screenshot via VNC
        raw = _capture_vnc(ssh, config, vm)
        if raw is None:
            return ProbeResult(key="kebab popup", status="ERROR", detail="no screenshot")

        img = Image.open(io.BytesIO(raw))
        coords = vlm.localize(img, "three-dot kebab menu button at the bottom of the sidebar")
        if coords is None:
            return ProbeResult(key="kebab popup", status="SWALLOWED", detail="VLM returned no coords")

        x, y = coords
        # Click and check if popup appears within 800ms (transient)
        _send_key_vnc(ssh, config, vm, "escape")  # dismiss any open menu first
        time.sleep(0.2)
        try:
            from automation.proxmox.api import ProxmoxAPI
            from automation.transport.vnc import VncClient
            api = ProxmoxAPI(
                host=config.proxmox.host,
                port=config.proxmox.port,
                user=config.proxmox.user,
                password=config.proxmox.password,
            )
            with VncClient(api, vmid=vm.vmid) as vnc:
                before = vnc.capture()[0]
                vnc.click(x, y)
                time.sleep(0.8)
                after = vnc.capture()[0]
        except Exception as e:
            return ProbeResult(key="kebab popup", status="ERROR", detail=str(e)[:60])

        diff = _screenshot_diff(before, after)
        if diff > _DIFF_THRESHOLD:
            return ProbeResult(key="kebab popup", status="OPEN", detail="transient (auto-dismiss in 800ms)")
        return ProbeResult(key="kebab popup", status="SWALLOWED", detail=f"diff={diff:.1f}")

    except Exception as exc:
        return ProbeResult(key="kebab popup", status="ERROR", detail=str(exc)[:80])


# ---------------------------------------------------------------------------
# Chrome focus + repeat input
# ---------------------------------------------------------------------------

def _probe_chrome_focus_repeat(ssh, config, vm, key: str = "ctrl+f") -> ProbeResult:
    """Test xdotool windowactivate + key repeat from chrome focus."""
    # Focus via xdotool windowactivate
    cmd = (
        "XAUTH=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1); "
        "[ -z \"$XAUTH\" ] && XAUTH=/run/user/1000/gdm/Xauthority; "
        "export DISPLAY=:0 XAUTHORITY=\"$XAUTH\"; "
        "WID=$(xdotool search --name 'Rocket' 2>/dev/null | head -1); "
        "[ -n \"$WID\" ] && xdotool windowactivate --sync $WID 2>/dev/null || true"
    )
    ssh.run(cmd, timeout=8)
    time.sleep(0.3)
    result = _probe_key(ssh, config, vm, key)
    result.key = f"{key}(chrome-focus)"
    result.detail = f"windowactivate+repeat: {result.detail}"
    return result


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _print_report(report: TraceReport) -> None:
    print(f"\n[trace {report.vm} against sha={report.binary_sha}]")
    col_w = max((len(r.key) for r in report.results), default=20) + 2
    for r in report.results:
        status_str = r.status
        detail = f"  — {r.detail}" if r.detail else ""
        if r.status == "SWALLOWED":
            detail += "  → workaround: click title bar first" if "webview" in r.detail else ""
        print(f"  {r.key:<{col_w}} {status_str}{detail}")
    print()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_trace(args) -> int:
    """Entry point for ``mosdat trace``. Returns 0, 1 (swallowed), or 2 (error)."""
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[trace] ERROR: config not found: {config_path}", file=sys.stderr)
        return 2

    try:
        config = load_config(config_path)
    except Exception as exc:
        print(f"[trace] ERROR loading config: {exc}", file=sys.stderr)
        return 2

    vm_names = [v.strip() for v in args.vms.split(",")]
    exit_code = 0

    for vm_name in vm_names:
        if vm_name not in config.vm_by_name:
            print(f"[trace] ERROR: unknown VM '{vm_name}'", file=sys.stderr)
            return 2

        vm = config.vm_by_name[vm_name]
        ssh = SSHClient(vm.ip, vm.user, connect_timeout=10)

        # Determine binary path
        binary_path = config.app.binary
        for pkg in vm.packages:
            if pkg.app_path and "{file}" not in pkg.app_path:
                binary_path = pkg.app_path
                break

        print(f"[trace] VM={vm_name}  binary={binary_path}")
        print("[trace] Killing any existing RC instance...")
        _kill_app(ssh)

        # Compute asar SHA for manifest
        asar_sha = "unknown"
        try:
            from automation.setup.capability import get_for_vm
            asar_sha = get_for_vm(ssh)
        except Exception as exc:
            print(f"[trace] WARNING: could not compute asar SHA: {exc}")

        print(f"[trace] Launching {binary_path}...")
        _launch_app(ssh, binary_path)
        print("[trace] Waiting 20s for app to settle...")
        time.sleep(20)

        # Focus app window
        ssh.run(_FOCUS_TITLE_BAR_CMD, timeout=8)
        time.sleep(0.5)

        results: list[ProbeResult] = []
        use_vnc = True  # If VNC unavailable, fall back to xdotool

        # --- Menu accelerators ---
        print("[trace] Probing menu accelerators...")
        for key in _MENU_ACCELS:
            ssh.run(_FOCUS_TITLE_BAR_CMD, timeout=8)
            time.sleep(0.3)
            r = _probe_key(ssh, config, vm, key, use_vnc=use_vnc)
            results.append(r)
            # Dismiss any opened menu
            _send_key_vnc(ssh, config, vm, "escape")
            time.sleep(0.2)

        # --- Common shortcuts ---
        print("[trace] Probing common shortcuts...")
        for key in _COMMON_SHORTCUTS:
            ssh.run(_FOCUS_TITLE_BAR_CMD, timeout=8)
            time.sleep(0.3)
            r = _probe_key(ssh, config, vm, key, use_vnc=use_vnc)
            results.append(r)
            _send_key_vnc(ssh, config, vm, "escape")
            time.sleep(0.2)

        # --- Webview focus stealing test ---
        print("[trace] Probing webview focus stealing (alt+f)...")
        form_r, title_r = _probe_webview_focus(ssh, config, vm, "alt+f")
        results.append(title_r)
        results.append(form_r)

        # --- VLM kebab probe ---
        print("[trace] Probing sidebar kebab via VLM...")
        results.append(_probe_kebab_via_vlm(ssh, config, vm))

        # --- Chrome focus repeat ---
        print("[trace] Probing xdotool windowactivate + key repeat...")
        results.append(_probe_chrome_focus_repeat(ssh, config, vm, "ctrl+f"))

        report = TraceReport(binary_sha=asar_sha, vm=vm_name, results=results)
        _print_report(report)

        # Write manifest if requested
        if getattr(args, "write_manifest", False):
            try:
                from automation.setup.capability import write_manifest, build_manifest
                data = build_manifest(
                    asar_sha=asar_sha,
                    vm=vm_name,
                    accelerators=report.to_manifest_accelerators(),
                    popups={
                        r.key: r.detail
                        for r in results
                        if r.key == "kebab popup" and r.status != "ERROR"
                    },
                )
                out_path = write_manifest(asar_sha, data)
                print(f"[trace] Manifest written: {out_path}")
            except Exception as exc:
                print(f"[trace] WARNING: failed to write manifest: {exc}")

        # Exit code: 1 if any unexpected SWALLOWED result (not the webview-focus variant)
        unexpected = [r for r in results if r.status == "SWALLOWED" and "form-focus" not in r.key]
        if unexpected:
            exit_code = 1

        _kill_app(ssh)

    return exit_code
