"""VM lifecycle helpers for FunctionalRunner.

This module is an internal mixin — import only via automation.runners.functional.
"""

import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from automation.runners.functional import FunctionalStep, BugConfirmationResult

from automation.runners.scenario_loader import resolve_vars as _resolve_vars
from automation.runners.functional_steps import StepFailed


class _LifecycleMixin:
    """Mixin supplying VM lifecycle, debug capture, and top-level run logic."""

    # ---- B7: VM health probe ----

    def _probe_vm_health(self) -> bool:
        """Non-invasive responsiveness check (B7). NO mouse/keyboard injection.

        F2: Replaces the invasive click(50,50) approach that caused false-positive
        frozen detection on idle desktops and real side-effects on the desktop.

        1. SSH: proves OS layer alive by running a harmless echo command.
        2. VNC: proves framebuffer is readable and non-degenerate (size >= 100px).
        Returns True if both checks pass, False if either fails.
        """
        # 1. SSH responsive — proves OS layer alive
        try:
            self.injector.shell("echo mosdat_health_ping", timeout=5)
        except Exception as e:
            self.log(f"  Health probe: SSH unresponsive ({e})")
            return False

        # 2. VNC capture works AND returns a non-degenerate frame
        try:
            img, size = self.screenshotter.capture()
            if not img or size[0] < 100 or size[1] < 100:
                self.log(f"  Health probe: VNC framebuffer suspect (size={size})")
                return False
        except Exception as e:
            self.log(f"  Health probe: VNC unresponsive ({e})")
            return False

        return True

    def _cleanup_snapshots(self, retain: str) -> None:
        """C2: Delete snapshots created this run per retain policy."""
        if not self._created_snapshots or self._vm_ops is None or self._vmid is None:
            return
        if retain == "delete":
            for snap_name in self._created_snapshots:
                try:
                    self._vm_ops.delete_snapshot(self._vmid, snap_name)
                    self.log(f"  [checkpoint] deleted snapshot '{snap_name}'")
                except Exception as e:
                    self.log(f"  [checkpoint] WARNING: failed to delete snapshot '{snap_name}': {e}")
        # "keep" and "keep-named" both keep all user-named snapshots (default: keep-named)

    def _prepare_display(self) -> None:
        """Wake display and disable screensaver/DPMS for the scenario.

        Mouse jiggle via VNC is the universal wake (works without display
        server access). xset/gsettings via SSH disables future blanking on
        Linux. Failures are silent — best effort.
        """
        # 1. Mouse jiggle to wake DPMS
        try:
            self.injector.move(640, 400)
            self.injector.move(642, 402)
        except Exception:
            try:
                self.injector.click(640, 400)
            except Exception:
                pass

        # 2. Disable screensaver / DPMS on Linux (silent on Windows)
        if not getattr(self.injector, "is_windows", False):
            try:
                self.injector.shell(
                    'for x in "/run/user/$(id -u)/gdm/Xauthority" "$HOME/.Xauthority"; do '
                    '    [ -e "$x" ] && export XAUTHORITY="$x" && break; '
                    "done; "
                    "xset -display :0 s off s noblank -dpms 2>/dev/null || true; "
                    "gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true; "
                    "gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true; "
                    "echo display:prepared",
                    timeout=10,
                )
            except Exception:
                pass

        self._emit("display_prepared", method="vnc_jiggle+xset_dpms_off")

    # ---- I2: VM-side debug artifact capture ----

    def _capture_vm_debug(
        self,
        results_dir: Path,
        vm_name: str,
    ) -> None:
        """Collect VM-side debug artifacts into results_dir/vm-debug/ on FAIL (I2).

        Never raises — warnings logged so caller's FAIL path continues unimpeded.
        Linux: dmesg_tail, journalctl_recent, rc_logs, xset_q, xsession_errors.
        Windows: event_log_app, top_processes, rc_logs.
        """
        debug_dir = results_dir / vm_name / "vm-debug"
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log(f"  [vm-debug] WARNING: could not create debug dir {debug_dir}: {e}")
            return

        is_win = getattr(self.injector, "is_windows", False)

        if is_win:
            self._capture_vm_debug_windows(debug_dir)
        else:
            self._capture_vm_debug_linux(debug_dir)

    def _ssh_run(self, cmd: str, timeout: int = 20) -> str:
        """Run cmd via injector.ssh.run() and return stdout.

        For Windows, wraps via _ps_encoded so PowerShell special chars survive.
        Returns empty string on any error — callers check for emptiness.
        """
        if getattr(self.injector, "is_windows", False):
            try:
                from automation.vlm.input import _ps_encoded
                cmd = _ps_encoded(cmd)
            except (ImportError, AttributeError):
                pass  # stub env or unavailable — pass cmd through unencoded
        try:
            result = self.injector.ssh.run(cmd, timeout=timeout)
            return result.stdout or ""
        except Exception:
            return ""

    def _capture_vm_debug_linux(self, debug_dir: Path) -> None:
        """Collect Linux VM-side debug artifacts."""
        # 1. dmesg tail
        try:
            out = self._ssh_run("dmesg | tail -200 2>/dev/null || true", timeout=15)
            if out:
                (debug_dir / "dmesg_tail.txt").write_text(out)
        except Exception as e:
            self.log(f"  [vm-debug] WARNING: dmesg failed: {e}")

        # 2. journalctl recent (5 min, no kernel filter so app-level events included)
        try:
            out = self._ssh_run(
                "journalctl --since='5 min ago' --no-pager 2>/dev/null || true",
                timeout=20,
            )
            if out:
                (debug_dir / "journalctl_recent.txt").write_text(out)
        except Exception as e:
            self.log(f"  [vm-debug] WARNING: journalctl failed: {e}")

        # 3. Rocket.Chat logs — last 200 lines of each .log file
        try:
            out = self._ssh_run(
                r'for f in "$HOME/.config/Rocket.Chat/logs/"*.log; do'
                r'  [ -f "$f" ] || continue;'
                r'  echo "=== $f ===";'
                r'  tail -200 "$f";'
                r'done 2>/dev/null || true',
                timeout=15,
            )
            if out:
                rc_dir = debug_dir / "rc_logs"
                rc_dir.mkdir(exist_ok=True)
                (rc_dir / "rc_logs.txt").write_text(out)
        except Exception as e:
            self.log(f"  [vm-debug] WARNING: RC log collection failed: {e}")

        # 4. xset q (X11 only — silently absent on Wayland)
        try:
            out = self._ssh_run("DISPLAY=:0 xset q 2>/dev/null || true", timeout=10)
            if out.strip():
                (debug_dir / "xset_q.txt").write_text(out)
        except Exception as e:
            self.log(f"  [vm-debug] WARNING: xset q failed: {e}")

        # 5. ~/.xsession-errors if present
        try:
            out = self._ssh_run(
                '[ -f "$HOME/.xsession-errors" ] && cat "$HOME/.xsession-errors" || true',
                timeout=10,
            )
            if out.strip():
                (debug_dir / "xsession_errors.txt").write_text(out)
        except Exception as e:
            self.log(f"  [vm-debug] WARNING: .xsession-errors collection failed: {e}")

    def _capture_vm_debug_windows(self, debug_dir: Path) -> None:
        """Collect Windows VM-side debug artifacts (via _ps_encoded SSH run)."""
        # 1. Application event log
        try:
            out = self._ssh_run(
                "Get-EventLog -LogName Application -Newest 50 | "
                "Format-List TimeGenerated,EntryType,Source,Message",
                timeout=30,
            )
            if out:
                (debug_dir / "event_log_app.txt").write_text(out)
        except Exception as e:
            self.log(f"  [vm-debug] WARNING: Get-EventLog failed: {e}")

        # 2. Top 20 processes by working set
        try:
            out = self._ssh_run(
                "Get-Process | Select-Object Name,Id,WS | "
                "Sort-Object WS -Descending | Select-Object -First 20 | "
                "Format-Table -AutoSize",
                timeout=20,
            )
            if out:
                (debug_dir / "top_processes.txt").write_text(out)
        except Exception as e:
            self.log(f"  [vm-debug] WARNING: Get-Process failed: {e}")

        # 3. Rocket.Chat logs
        try:
            out = self._ssh_run(
                r'$logDir = "$env:APPDATA\Rocket.Chat\logs"; '
                r'if (Test-Path $logDir) { '
                r'  Get-ChildItem "$logDir\*.log" | ForEach-Object { '
                r'    Write-Output "=== $($_.Name) ==="; '
                r'    Get-Content $_.FullName -Tail 200 '
                r'  } '
                r'} else { Write-Output "RC log dir not found: $logDir" }',
                timeout=20,
            )
            if out:
                rc_dir = debug_dir / "rc_logs"
                rc_dir.mkdir(exist_ok=True)
                (rc_dir / "rc_logs.txt").write_text(out)
        except Exception as e:
            self.log(f"  [vm-debug] WARNING: RC log collection (Windows) failed: {e}")

    def _run_bug_confirmation_scenario(
        self,
        steps,
        name: str,
        bug_signal: str,
        precondition_check: str,
        vars: Optional[dict] = None,
    ):
        """Execute a bug-confirmation scenario.

        Steps run as in functional mode, BUT steps with must_pass=False treat
        verify/verify_not failures as non-fatal — they are logged and appended
        to step_failures. Steps with must_pass=True (default) retain the
        existing fail-fast behaviour.

        After all steps: capture final screenshot, fire precondition_check and
        bug_signal as single short-budget VLM calls (<=8s each), return a
        BugConfirmationResult.
        """
        from automation.runners.functional import BugConfirmationResult

        t_start = time.perf_counter()
        vars = vars or {}
        step_failures: list[dict] = []

        self.log(f"[bug-confirmation] {name}")
        self.log(f"  {len(steps)} steps")

        self._prepare_display()
        resolved = _resolve_vars(steps, vars)
        self._checkpoints = []
        self._created_snapshots = []

        for step_index, step in enumerate(resolved):
            i = step_index + 1
            must_pass = getattr(step, "must_pass", True)
            try:
                self.run_step(step, i)
            except StepFailed as e:
                if must_pass:
                    raise
                self.log(f"  Step {i}: non-fatal failure (must_pass=False): {e}")
                step_failures.append({
                    "step_num": i,
                    "kind": "step_failed",
                    "attempt": step.retries,
                    "reason": "StepFailed",
                    "error_text": str(e),
                })
            except Exception as e:
                if must_pass:
                    raise StepFailed(f"Step {i}: {e}") from e
                self.log(f"  Step {i}: non-fatal error (must_pass=False): {e}")
                step_failures.append({
                    "step_num": i,
                    "kind": "exception",
                    "attempt": step.retries,
                    "reason": "exception",
                    "error_text": str(e),
                })

        # Capture final screenshot
        try:
            final_img, _ = self.screenshotter.capture()
        except Exception:
            final_img = None

        final_screenshot_path: Optional[Path] = None
        if self.screenshot_dir and final_img is not None:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%H%M%S")
            final_screenshot_path = self.screenshot_dir / f"{ts}_final_screenshot.png"
            final_img.save(final_screenshot_path)
            self.log(f"  Final screenshot: {final_screenshot_path}")

        # Fire precondition_check (short budget <=8s)
        precondition_screenshot_path: Optional[Path] = None
        precondition_met = False
        try:
            pre_img, _ = self.screenshotter.capture()
            t0 = time.perf_counter()
            precondition_met = self.vlm.verify(pre_img, precondition_check)
            latency_ms = round((time.perf_counter() - t0) * 1000)
            self._emit("vlm_verify", step_num="final", attempt=1,
                       question=precondition_check[:80],
                       answer="yes" if precondition_met else "no",
                       latency_ms=latency_ms, kind="precondition_check")
            if self.screenshot_dir and pre_img is not None:
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%H%M%S")
                precondition_screenshot_path = self.screenshot_dir / f"{ts}_precondition_screenshot.png"
                pre_img.save(precondition_screenshot_path)
                self.log(f"  Precondition screenshot: {precondition_screenshot_path}")
        except Exception as e:
            self.log(f"  precondition_check VLM call failed: {e}")
            precondition_met = False

        # Fire bug_signal (short budget <=8s)
        bug_signal_screenshot_path: Optional[Path] = None
        bug_visible = False
        try:
            bug_img, _ = self.screenshotter.capture()
            t0 = time.perf_counter()
            bug_visible = self.vlm.verify(bug_img, bug_signal)
            latency_ms = round((time.perf_counter() - t0) * 1000)
            self._emit("vlm_verify", step_num="final", attempt=1,
                       question=bug_signal[:80],
                       answer="yes" if bug_visible else "no",
                       latency_ms=latency_ms, kind="bug_signal")
            if self.screenshot_dir and bug_img is not None:
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%H%M%S")
                bug_signal_screenshot_path = self.screenshot_dir / f"{ts}_bug_signal_screenshot.png"
                bug_img.save(bug_signal_screenshot_path)
                self.log(f"  Bug signal screenshot: {bug_signal_screenshot_path}")
        except Exception as e:
            self.log(f"  bug_signal VLM call failed: {e}")
            bug_visible = False

        elapsed_ms = round((time.perf_counter() - t_start) * 1000)

        result = BugConfirmationResult(
            precondition_met=precondition_met,
            bug_visible=bug_visible,
            bug_signal_screenshot=bug_signal_screenshot_path,
            precondition_screenshot=precondition_screenshot_path,
            final_screenshot=final_screenshot_path,
            step_failures=step_failures,
            elapsed_ms=elapsed_ms,
        )
        self.log(f"  verdict: {result.verdict}")
        return result

    def run_test(
        self,
        steps,
        name: str,
        vars: Optional[dict] = None,
        results_dir: Optional[Path] = None,
        vm_name: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Run a sequence of steps.

        Returns (passed, summary_log).

        Raises ValueError if screenshot_dir was not provided (A6: mandatory for
        forensic auditing in production runs).

        I2: On FAIL, if results_dir and vm_name are provided, collect VM-side
        debug artifacts into results_dir/<vm_name>/vm-debug/.
        """
        if self.screenshot_dir is None:
            raise ValueError("screenshot_dir is required for forensic auditing")
        vars = vars or {}
        log_lines: list[str] = []

        def _log(msg: str) -> None:
            self.log(msg)
            log_lines.append(msg)

        _log(f"[functional] {name}")
        _log(f"  {len(steps)} steps")

        # G2: wake display and disable screensaver/DPMS before any step runs
        self._prepare_display()

        resolved = _resolve_vars(steps, vars)

        # C2: reset checkpoint tracking for this run
        self._checkpoints = []
        self._created_snapshots = []

        # C2: step cursor — supports rewind by adjusting where we resume from
        step_index = 0
        while step_index < len(resolved):
            i = step_index + 1  # 1-indexed step number
            step = resolved[step_index]
            try:
                self.run_step(step, i)
                step_index += 1
            except StepFailed as e:
                _log(f"  FAIL: {e}")
                # Save final state screenshot
                try:
                    screenshot, _ = self.screenshotter.capture()
                    self._save_screenshot(screenshot, f"step{i}_final_fail")
                except Exception:
                    pass

                # C2: rewind to last checkpoint if enabled and available
                if (
                    self._checkpoints_enabled
                    and self._checkpoints_rewind
                    and self._checkpoints
                    and self._vm_ops is not None
                    and self._vmid is not None
                ):
                    ckpt_name, ckpt_step_num = self._checkpoints.pop()
                    _log(f"  [checkpoint] Rewinding to checkpoint '{ckpt_name}' (step {ckpt_step_num})")
                    try:
                        self._vm_ops.rollback(self._vmid, ckpt_name)
                    except Exception as rb_err:
                        _log(f"  [checkpoint] WARNING: rollback failed: {rb_err} — re-raising original failure")
                        self._cleanup_snapshots(self._checkpoints_retain)
                        # I2: capture VM debug artifacts before returning FAIL
                        if results_dir and vm_name:
                            try:
                                self._capture_vm_debug(results_dir, vm_name)
                            except Exception as _dbg_err:
                                self.log(f"  [vm-debug] WARNING: capture failed: {_dbg_err}")
                        return False, "\n".join(log_lines)
                    # Wait for VM to be reachable again
                    _log("  [checkpoint] Waiting for VM to come back after rollback...")
                    ip = self._vm_ops.api.wait_for_ip(self._vmid, timeout=120)
                    if not ip:
                        _log("  [checkpoint] FATAL: VM did not return after rollback — aborting")
                        self._emit("checkpoint_rewind", name=ckpt_name,
                                   from_step=i, to_step=ckpt_step_num + 1,
                                   status="fatal_no_ip")
                        self._cleanup_snapshots(self._checkpoints_retain)
                        # I2: capture VM debug artifacts before returning FAIL
                        if results_dir and vm_name:
                            try:
                                self._capture_vm_debug(results_dir, vm_name)
                            except Exception as _dbg_err:
                                self.log(f"  [vm-debug] WARNING: capture failed: {_dbg_err}")
                        return False, "\n".join(log_lines)
                    self._emit("checkpoint_rewind", name=ckpt_name,
                               from_step=i, to_step=ckpt_step_num + 1)
                    _log(f"  [checkpoint] Rewound — resuming from step {ckpt_step_num + 1}")
                    # Resume after the checkpoint step (0-indexed)
                    step_index = ckpt_step_num  # ckpt_step_num is 1-indexed; index = ckpt_step_num
                    continue
                else:
                    self._cleanup_snapshots(self._checkpoints_retain)
                    # I2: capture VM debug artifacts before returning FAIL
                    if results_dir and vm_name:
                        try:
                            self._capture_vm_debug(results_dir, vm_name)
                        except Exception as _dbg_err:
                            self.log(f"  [vm-debug] WARNING: capture failed: {_dbg_err}")
                    return False, "\n".join(log_lines)

        _log(f"  PASS: all {len(resolved)} steps completed")
        self._cleanup_snapshots(self._checkpoints_retain)
        return True, "\n".join(log_lines)
