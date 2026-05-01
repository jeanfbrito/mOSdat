"""Functional UI test runner using VLM for element localization and verification."""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from ..vlm.client import VLMClient
from ..vlm.input import InputInjector
from ..vlm.screenshot import Screenshotter

# Regex for redacting sensitive text values in event log
_SENSITIVE_RE = re.compile(r"password|token|secret", re.IGNORECASE)


@dataclass
class FunctionalStep:
    localize: Optional[str] = None         # element description to find and click (optional if launch-only)
    then_type: Optional[str] = None        # text to type after clicking
    then_key: Optional[str] = None         # key to press after typing (enter/tab/etc.)
    then_key_pre: Optional[str] = None    # key to press BEFORE typing (e.g. ctrl+a to clear field)
    verify: Optional[str] = None           # yes/no question that must be TRUE (post-step)
    verify_not: Optional[str] = None       # yes/no question that must be FALSE (catches error banners, hallucinated success)
    verify_input: Optional[str] = None     # yes/no check that fires AFTER then_type and BEFORE then_key — blocks Enter until typing actually landed in the right field
    verify_timeout: int = 10               # seconds to wait for the expected state
    retries: int = 3                       # attempts before the step is marked failed
    launch: Optional[str] = None           # executable path/command to launch before localizing
    wait: int = 0                          # seconds to wait after launch before proceeding
    focus: Optional[str] = None           # window title to bring to front after launch+wait
    shell: Optional[str] = None            # arbitrary shell/PowerShell command to run on the VM (one-shot)
    if_visible: Optional[str] = None      # VLM visibility check; if True → execute `then_steps`, if False → skip silently
    then_steps: Optional[list] = None     # steps to execute when if_visible is True (list of FunctionalStep)
    verify_consistent: bool = False        # A2: if True, use 3-sample quorum vote for verify calls
    precheck_click: bool = False           # A4: if True, use localize_verified (pre-click crop verify)
    localize_consistent: bool = False      # C5: if True, use 3-sample coord cluster for localize
    launch_window: Optional[str] = None   # B6: window name hint for launch verification (derived from launch basename if omitted)
    launch_timeout: Optional[int] = None  # B6: polling budget for launch verification (defaults to step.wait)
    on_failure_agent: Optional[dict] = None  # C1: agent fallback on retry exhaustion
    checkpoint: Optional[str] = None      # C2: snapshot VM at this step; value is the snapshot name


class StepFailed(Exception):
    pass


class FunctionalRunner:
    def __init__(
        self,
        vlm: VLMClient,
        screenshotter: Screenshotter,
        injector: InputInjector,
        screenshot_dir: Optional[Path] = None,
        log_fn: Callable[[str], None] = print,
        popup_sweep: bool = False,
        checkpoint_config: Optional[dict] = None,
        vm_ops=None,
        vmid: Optional[int] = None,
    ):
        self.vlm = vlm
        self.screenshotter = screenshotter
        self.injector = injector
        self.screenshot_dir = screenshot_dir
        self.log = log_fn
        self.popup_sweep = popup_sweep
        # B2: events.jsonl always-on when screenshot_dir is set
        self._events_path: Optional[Path] = (screenshot_dir / "events.jsonl") if screenshot_dir else None
        # F3: truncate events.jsonl at init so same-date re-runs don't append
        if self._events_path and self._events_path.exists():
            self._events_path.unlink()
        # C2: checkpoint config
        _ckpt = checkpoint_config or {}
        self._checkpoints_enabled: bool = bool(_ckpt.get("enabled", False))
        self._checkpoints_retain: str = _ckpt.get("retain", "keep-named")
        self._checkpoints_rewind: bool = bool(_ckpt.get("rewind_on_failure", True))
        self._vm_ops = vm_ops   # VMOperations instance for snapshot calls
        self._vmid: Optional[int] = vmid
        # Internal tracking: list of (name, step_num) in creation order
        self._checkpoints: list = []
        # Track which snapshots were created this run (for cleanup)
        self._created_snapshots: list = []

    def _save_screenshot(self, img: Image.Image, label: str) -> None:
        if not self.screenshot_dir:
            return
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H%M%S")
        path = self.screenshot_dir / f"{ts}_{label}.png"
        img.save(path)
        self.log(f"  Screenshot: {path}")

    def _save_click_overlay(self, img: Image.Image, x: int, y: int, step_num) -> None:
        """A6: Save screenshot with red dot marking the click coordinate."""
        if not self.screenshot_dir:
            return
        from PIL import ImageDraw
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H%M%S")
        overlay = img.copy()
        draw = ImageDraw.Draw(overlay)
        draw.ellipse([(x - 15, y - 15), (x + 15, y + 15)], outline="red", width=4)
        draw.ellipse([(x - 4, y - 4), (x + 4, y + 4)], fill="red")
        path = self.screenshot_dir / f"{ts}_step{step_num}_click.png"
        overlay.save(path)
        self.log(f"  Click overlay: {path}")

    # ---- B2: Event stream ----

    def _emit(self, event_type: str, **fields) -> None:
        """Append a JSON event line to events.jsonl (B2)."""
        if not self._events_path:
            return
        record = {
            "ts": datetime.now().isoformat(),
            "event": event_type,
            **fields,
        }
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        with self._events_path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    @staticmethod
    def _redact(text: Optional[str]) -> str:
        """Redact sensitive text values before writing to the event log."""
        if not text:
            return ""
        if _SENSITIVE_RE.search(text):
            return "<REDACTED>"
        return text[:80]

    # ---- B3: Popup sweeper ----

    def _sweep_popups(self, step_num, max_attempts: int = 2) -> int:
        """Dismiss modal dialogs / popups before a localize step (B3).

        Returns the count of popups dismissed.
        Emits popup_sweep events.
        Only called when self.popup_sweep is True.
        """
        dismissed = 0
        question = (
            "is there a modal dialog, popup, blocking overlay, update notification, "
            "or notification banner covering or partially covering the main application UI"
        )
        for _ in range(max_attempts):
            try:
                screenshot, _ = self.screenshotter.capture()
                t0 = time.perf_counter()
                found = self.vlm.verify(screenshot, question)
                latency_ms = round((time.perf_counter() - t0) * 1000)
                self._emit("vlm_verify", step_num=step_num, attempt=1, question=question[:80],
                           answer="yes" if found else "no", latency_ms=latency_ms, kind="popup_sweep")
                if not found:
                    break
                self.injector.key("escape")
                time.sleep(0.5)
                dismissed += 1
            except Exception as e:
                self.log(f"    → popup sweep error ({e}), skipping")
                break
        self._emit("popup_sweep", step_num=step_num, dismissed=dismissed)
        return dismissed

    def _verify_call(
        self,
        screenshot: Image.Image,
        question: str,
        step: "FunctionalStep",
        step_num,
        label: str = "verify",
    ) -> bool:
        """A2: Route verify through quorum if step.verify_consistent, else plain verify."""
        if step.verify_consistent:
            result, responses = self.vlm.verify_consistent(screenshot, question)
            if not result and self.screenshot_dir:
                # Log the split responses for forensics
                ts = datetime.now().strftime("%H%M%S")
                split_path = self.screenshot_dir / f"{ts}_step{step_num}_{label}_split.txt"
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                split_path.write_text(
                    f"question: {question}\n\n"
                    + "\n\n---\n\n".join(f"sample {i+1}:\n{r}" for i, r in enumerate(responses))
                )
                self.log(f"  Verify split logged: {split_path}")
                # B2: emit verify_split event when quorum disagrees
                self._emit("verify_split", step_num=step_num, question=question[:80],
                           responses=[r[:200] for r in responses])
            return result
        return self.vlm.verify(screenshot, question)

    def _do_checkpoint(self, name: str, step_num) -> None:
        """C2: Snapshot the VM at this step. Handles name collision by deleting first."""
        if not self._checkpoints_enabled or self._vm_ops is None or self._vmid is None:
            return
        self.log(f"  Step {step_num}: checkpoint '{name}'")
        # Collision guard: if name already exists, delete it first
        try:
            existing = self._vm_ops.list_snapshots(self._vmid)
            if name in existing:
                self.log(f"    → snapshot '{name}' exists from previous run — deleting first")
                self._vm_ops.delete_snapshot(self._vmid, name)
        except Exception as e:
            self.log(f"    → WARNING: could not list/delete existing snapshot '{name}': {e}")
        self._vm_ops.snapshot(self._vmid, name)
        self._checkpoints.append((name, step_num))
        self._created_snapshots.append(name)
        self._emit("checkpoint_created", step_num=step_num, name=name)
        self.log(f"    → checkpoint '{name}' created at step {step_num}")

    def run_step(self, step: FunctionalStep, step_num) -> None:
        """Execute a single test step with retries.

        Raises StepFailed if all retries are exhausted.
        """
        # C2: checkpoint step — snapshot and return immediately (no UI action)
        if step.checkpoint is not None:
            self._do_checkpoint(step.checkpoint, step_num)
            return

        label = step.localize[:60] if step.localize else (step.launch or step.shell or "")[:60]
        kind = ("localize" if step.localize else
                "launch" if step.launch else
                "shell" if step.shell else
                "if_visible" if step.if_visible is not None else
                "key")
        # B2: step_start
        step_start_ts = time.perf_counter()
        self._emit("step_start", step_num=step_num, label=label, kind=kind)

        # Shell is a one-shot action — do it before the retry loop
        if step.shell:
            self.log(f"  Step {step_num}: shell '{step.shell[:80]}'")
            self._emit("shell", step_num=step_num, cmd_truncated=step.shell[:80])
            self.injector.shell(step.shell)

        # Launch is a one-shot action — do it before the retry loop
        if step.launch:
            self.log(f"  Step {step_num}: launch '{step.launch[:80]}'")
            import os as _os
            app_basename = _os.path.basename(step.launch.split()[0])
            self._emit("launch", step_num=step_num, app=step.launch[:80], args="")
            self.injector.launch(step.launch)

            # B6: Replace blind sleep with polling loop for process + window presence
            # F4: strip common suffixes from app_basename for natural-language VLM question
            NATURAL = app_basename
            for _sfx in ("-desktop", "-bin", ".exe", "-electron", ".AppImage"):
                if NATURAL.lower().endswith(_sfx.lower()):
                    NATURAL = NATURAL[: -len(_sfx)]
                    break
            launch_budget = step.launch_timeout if step.launch_timeout is not None else step.wait
            window_name = step.launch_window or NATURAL
            if launch_budget > 0:
                self.log(f"    → waiting up to {launch_budget}s for '{app_basename}' to start…")
                t_launch = time.perf_counter()
                process_present = False
                window_present = False
                deadline_launch = time.time() + launch_budget
                # F1: cap VLM verify calls to prevent runaway with slow VLM
                MAX_LAUNCH_VLM_CALLS = 2
                n_calls = 0
                while time.time() < deadline_launch:
                    time.sleep(1)
                    # F1: re-check deadline after sleep
                    if time.time() >= deadline_launch:
                        break
                    try:
                        process_present = self.injector.process_running(app_basename)
                    except Exception:
                        process_present = False
                    if process_present:
                        # F1: check deadline BEFORE invoking blocking VLM call
                        if time.time() >= deadline_launch:
                            break
                        if n_calls >= MAX_LAUNCH_VLM_CALLS:
                            self.log(f"    → verify call cap ({MAX_LAUNCH_VLM_CALLS}) reached, stopping")
                            break
                        elapsed_so_far = round((time.perf_counter() - t_launch) * 1000)
                        self.log(f"    → verify call {n_calls + 1} ({elapsed_so_far}ms elapsed of {launch_budget}s budget)")
                        try:
                            screenshot_lv, _ = self.screenshotter.capture()
                            t0_vlm = time.perf_counter()
                            window_present = self.vlm.verify(
                                screenshot_lv,
                                f"is the {window_name} window visible on screen",
                            )
                            latency_ms_lv = round((time.perf_counter() - t0_vlm) * 1000)
                            n_calls += 1
                            self._emit("vlm_verify", step_num=step_num, attempt=n_calls,
                                       question=f"is the {window_name} window visible on screen",
                                       answer="yes" if window_present else "no",
                                       latency_ms=latency_ms_lv, kind="launch_verify")
                        except Exception:
                            n_calls += 1
                            window_present = False
                        if window_present:
                            break
                elapsed_ms = round((time.perf_counter() - t_launch) * 1000)
                self._emit("launch_verify", step_num=step_num, app=app_basename,
                           process_present=process_present, window_present=window_present,
                           elapsed_ms=elapsed_ms)
                if not (process_present and window_present):
                    raise StepFailed(
                        f"Step {step_num}: Launch failed: process not running and/or "
                        f"window not visible after {elapsed_ms / 1000:.1f}s "
                        f"(budget {launch_budget}s, calls={n_calls}, "
                        f"process={process_present}, window={window_present})"
                    )
                self.log(f"    → '{app_basename}' verified (process={process_present}, window={window_present})")
            elif step.wait:
                # wait=0 but launch_timeout explicitly 0 — keep old behaviour of no wait
                pass

        # Standalone key/type steps (no localize). Used to drive the desktop
        # launcher on Linux: `key_pre: super` → `type: "<app name>"` →
        # `key: enter`. Generic across GNOME / KDE / XFCE since Super opens
        # their launcher and Enter activates the first result.
        #
        # The 1.5s pause after key_pre covers the time the Activities /
        # Kickoff / whisker menu takes to fully paint before typing lands.
        if not step.localize and not step.launch and not step.shell:
            if step.then_key_pre:
                self.log(f"  Step {step_num}: key '{step.then_key_pre}'")
                self.injector.key(step.then_key_pre)
                time.sleep(1.5)
            if step.then_type:
                self.log(f"  Step {step_num}: type '{step.then_type[:40]}'")
                self.injector.type_text(step.then_type)
                time.sleep(0.8)
            if step.then_key:
                self.log(f"  Step {step_num}: key '{step.then_key}'")
                self.injector.key(step.then_key)
                # A1: stability wait after then_key (replaces fixed 0.3s sleep)
                self.screenshotter.wait_for_stable(max_seconds=3.0)
            if step.wait:
                self.log(f"    → waiting {step.wait}s")
                time.sleep(step.wait)

        # if_visible: check element presence once via VLM; execute then_steps if
        # visible, skip silently if not. No failure, no retry on this outer check.
        if step.if_visible is not None:
            self.log(f"  Step {step_num}: if_visible '{step.if_visible[:60]}'")
            try:
                screenshot, _ = self.screenshotter.capture()
                t0_iv = time.perf_counter()
                visible = self.vlm.verify(screenshot, f"is there a {step.if_visible}")
                latency_ms_iv = round((time.perf_counter() - t0_iv) * 1000)
                self._emit("vlm_verify", step_num=step_num, attempt=1,
                           question=f"is there a {step.if_visible}"[:80],
                           answer="yes" if visible else "no",
                           latency_ms=latency_ms_iv, kind="if_visible")
            except Exception as e:
                self.log(f"    → visibility check error ({e}), treating as not visible — skip")
                visible = False
            if visible:
                self.log(f"    → visible: executing then_steps")
                for sub_i, sub_step in enumerate(step.then_steps or [], start=1):
                    self.run_step(sub_step, f"{step_num}.{sub_i}")
            else:
                self.log(f"    → not visible: skipping")
            duration_ms = round((time.perf_counter() - step_start_ts) * 1000)
            self._emit("step_end", step_num=step_num,
                       status="ok" if visible else "skipped",
                       attempts=1, duration_ms=duration_ms)
            return

        # Focus can run with or without a launch — used to re-assert that the
        # target window is in the foreground before any input/localize, so
        # notifications or background windows can't hijack keystrokes.
        if step.focus:
            self.log(f"    → focus '{step.focus}'")
            self.injector.focus_app(step.focus)
            time.sleep(0.4)

        for attempt in range(1, step.retries + 1):
            retry_label = f" (attempt {attempt}/{step.retries})" if attempt > 1 else ""

            try:
                # On retries, FIRST check if the expected state is already
                # present — the previous attempt's action may have succeeded
                # but the page transition was slower than verify_timeout.
                # Re-executing blindly double-types and breaks the field.
                if attempt > 1 and step.verify:
                    screenshot_chk, _ = self.screenshotter.capture()
                    if self._check_state(screenshot_chk, step.verify, 3, step_num, must_be_false=step.verify_not, step=step):
                        self.log(f"    ✓ already verified (state settled between attempts)")
                        return

                if step.localize:
                    # B3: proactive popup sweep before each localize
                    if self.popup_sweep:
                        self._sweep_popups(step_num)
                    self.log(f"  Step {step_num}: locate '{label}'{retry_label}")
                    screenshot, screen_size = self.screenshotter.capture()
                    # A6: save the screenshot used for localize
                    self._save_screenshot(screenshot, f"step{step_num}_localize")
                    # C5 + A4: opt-in localization strategies
                    # localize_consistent takes precedence over precheck_click
                    # when both are set; precheck_click crop-verify then runs
                    # on the consistent centroid.
                    t0_loc = time.perf_counter()
                    if step.localize_consistent:
                        x, y = self.vlm.localize_consistent(screenshot, step.localize, screen_size)
                        latency_ms_loc = round((time.perf_counter() - t0_loc) * 1000)
                        self._emit("vlm_localize_consistent", step_num=step_num, attempt=attempt,
                                   target=step.localize[:80], centroid=(x, y),
                                   latency_ms=latency_ms_loc)
                        if step.precheck_click:
                            # A4 crop-verify on the consistent centroid
                            crop_size = 100
                            w_s, h_s = screen_size
                            box = (
                                max(0, x - crop_size),
                                max(0, y - crop_size),
                                min(w_s, x + crop_size),
                                min(h_s, y + crop_size),
                            )
                            crop = screenshot.crop(box)
                            from ..vlm.client import VLMError
                            if not self.vlm.verify(crop, f"is this {step.localize}"):
                                raise VLMError(
                                    f"Pre-click verify failed: VLM denies centroid ({x},{y}) is '{step.localize}'"
                                )
                    elif step.precheck_click:
                        x, y = self.vlm.localize_verified(screenshot, step.localize, screen_size)
                        latency_ms_loc = round((time.perf_counter() - t0_loc) * 1000)
                    else:
                        x, y = self.vlm.localize(screenshot, step.localize, screen_size)
                        latency_ms_loc = round((time.perf_counter() - t0_loc) * 1000)
                    if not step.localize_consistent:
                        self._emit("vlm_localize", step_num=step_num, attempt=attempt,
                                   target=step.localize[:80], x=x, y=y, latency_ms=latency_ms_loc)
                    self.log(f"    → click ({x}, {y})")
                    self._emit("click", step_num=step_num, x=x, y=y)
                    self.injector.click(x, y)
                    # A6: save click overlay
                    self._save_click_overlay(screenshot, x, y, step_num)
                    time.sleep(0.4)

                    if step.then_key_pre:
                        self.log(f"    → key '{step.then_key_pre}' (pre)")
                        self.injector.key(step.then_key_pre)
                        time.sleep(0.15)

                    if step.then_type:
                        # On retries, check visually whether the input already
                        # holds the target text — the previous attempt may have
                        # typed successfully but the downstream transition
                        # failed (e.g. server error). Re-typing blindly
                        # double-fills the field or misfires into a new widget.
                        already_typed = False
                        if attempt > 1:
                            try:
                                screenshot_chk, _ = self.screenshotter.capture()
                                already_typed = self.vlm.verify(
                                    screenshot_chk,
                                    f"the text '{step.then_type}' is already visible in an input field on screen",
                                )
                            except Exception:
                                already_typed = False
                        if already_typed:
                            self.log(f"    → skip type (already present): '{step.then_type[:40]}'")
                        else:
                            self.log(f"    → type '{step.then_type[:40]}'")
                            self._emit("type", step_num=step_num,
                                       text_redacted=self._redact(step.then_type))
                            self.injector.type_text(step.then_type)
                            time.sleep(0.2)

                    # Gate the submit key on a VLM check that the typed text
                    # actually landed in the intended field. Catches clicks
                    # that missed the target and typing that went to a
                    # different window / Windows Search / taskbar.
                    if step.verify_input:
                        self.log(f"    → verify input '{step.verify_input[:60]}'")
                        if not self._wait_for_state(step.verify_input, min(step.verify_timeout, 10), step_num, step=step):
                            screenshot, _ = self.screenshotter.capture()
                            self._save_screenshot(screenshot, f"step{step_num}_input_fail_attempt{attempt}")
                            if attempt < step.retries:
                                self.log(f"    ✗ input not verified, retrying...")
                                continue
                            raise StepFailed(
                                f"Step {step_num}: verify_input '{step.verify_input}' never became true"
                            )
                        self.log(f"    ✓ input verified")

                    if step.then_key:
                        self.log(f"    → key '{step.then_key}'")
                        self._emit("key", step_num=step_num, key=step.then_key)
                        self.injector.key(step.then_key)
                        # A1: stability wait after then_key (was 0.3s fixed sleep)
                        self.screenshotter.wait_for_stable(max_seconds=3.0)

                if step.verify:
                    if not step.localize:
                        self.log(f"  Step {step_num}: verify '{step.verify[:60]}'{retry_label}")
                    t0_verify = time.perf_counter()
                    verified = self._wait_for_state(step.verify, step.verify_timeout, step_num, must_be_false=step.verify_not, step=step)
                    latency_ms_verify = round((time.perf_counter() - t0_verify) * 1000)
                    if verified:
                        self.log(f"    ✓ verified: {step.verify[:60]}")
                        self._emit("vlm_verify", step_num=step_num, attempt=attempt,
                                   question=step.verify[:80], answer="yes",
                                   latency_ms=latency_ms_verify, kind="verify")
                        # A6: save verify screenshot
                        screenshot_v, _ = self.screenshotter.capture()
                        self._save_screenshot(screenshot_v, f"step{step_num}_verified")
                        duration_ms = round((time.perf_counter() - step_start_ts) * 1000)
                        self._emit("step_end", step_num=step_num, status="ok",
                                   attempts=attempt, duration_ms=duration_ms)
                        return
                    self._emit("vlm_verify", step_num=step_num, attempt=attempt,
                               question=step.verify[:80], answer="no",
                               latency_ms=latency_ms_verify, kind="verify")
                    screenshot, _ = self.screenshotter.capture()
                    self._save_screenshot(screenshot, f"step{step_num}_fail_attempt{attempt}")
                    if attempt < step.retries:
                        self.log(f"    ✗ not verified, retrying...")
                        self._emit("retry", step_num=step_num, attempt=attempt,
                                   reason="verify_failed")
                        continue
                    duration_ms = round((time.perf_counter() - step_start_ts) * 1000)
                    self._emit("step_end", step_num=step_num, status="failed",
                               attempts=attempt, duration_ms=duration_ms)
                    # C1: agent fallback on verify exhaustion
                    if step.on_failure_agent:
                        self.log(f"  Step {step_num}: handing off to agent fallback")
                        from ..vlm.agent import AgentLoop
                        agent = AgentLoop(
                            vlm=self.vlm,
                            screenshotter=self.screenshotter,
                            injector=self.injector,
                            log_fn=self.log,
                            emit_event=self._emit,
                        )
                        result = agent.run(
                            goal=step.on_failure_agent["goal"],
                            budget_turns=step.on_failure_agent.get("budget_turns", 10),
                            success_check=step.on_failure_agent.get("success_check"),
                            step_num=step_num,
                        )
                        if result.success:
                            self.log(f"  Step {step_num}: agent fallback succeeded after {result.turns_used} turns")
                            self._emit("agent_fallback", step_num=step_num, success=True, turns=result.turns_used)
                            return  # Step recovered
                        self._emit("agent_fallback", step_num=step_num, success=False,
                                   turns=result.turns_used, reason=result.reason)
                    raise StepFailed(
                        f"Step {step_num}: '{step.verify}' was never true "
                        f"after {step.retries} attempts"
                    )
                duration_ms = round((time.perf_counter() - step_start_ts) * 1000)
                self._emit("step_end", step_num=step_num, status="ok",
                           attempts=attempt, duration_ms=duration_ms)
                return  # no verify needed

            except StepFailed:
                raise
            except Exception as e:
                if attempt < step.retries:
                    self.log(f"    ✗ error: {e}, retrying...")
                    self._emit("retry", step_num=step_num, attempt=attempt, reason=str(e)[:80])
                    time.sleep(1)
                else:
                    duration_ms = round((time.perf_counter() - step_start_ts) * 1000)
                    self._emit("step_end", step_num=step_num, status="failed",
                               attempts=attempt, duration_ms=duration_ms)
                    # C1: agent fallback (raised after retry loop, before StepFailed)
                    if step.on_failure_agent:
                        self.log(f"  Step {step_num}: handing off to agent fallback")
                        from ..vlm.agent import AgentLoop
                        agent = AgentLoop(
                            vlm=self.vlm,
                            screenshotter=self.screenshotter,
                            injector=self.injector,
                            log_fn=self.log,
                            emit_event=self._emit,
                        )
                        result = agent.run(
                            goal=step.on_failure_agent["goal"],
                            budget_turns=step.on_failure_agent.get("budget_turns", 10),
                            success_check=step.on_failure_agent.get("success_check"),
                            step_num=step_num,
                        )
                        if result.success:
                            self.log(f"  Step {step_num}: agent fallback succeeded after {result.turns_used} turns")
                            self._emit("agent_fallback", step_num=step_num, success=True, turns=result.turns_used)
                            return  # Step recovered
                        self._emit("agent_fallback", step_num=step_num, success=False,
                                   turns=result.turns_used, reason=result.reason)
                    raise StepFailed(f"Step {step_num}: {e}") from e

    def _check_state(
        self,
        screenshot: Image.Image,
        question: str,
        timeout: int,
        step_num,
        must_be_false: Optional[str] = None,
        step: Optional["FunctionalStep"] = None,
    ) -> bool:
        """Single-shot state check (no polling). Used for retry pre-checks."""
        try:
            result = self._verify_call(screenshot, question, step, step_num) if step else self.vlm.verify(screenshot, question)
            if not result:
                return False
            if must_be_false:
                screenshot2, _ = self.screenshotter.capture()
                if self.vlm.verify(screenshot2, must_be_false):
                    return False
            return True
        except Exception:
            return False

    def _wait_for_state(
        self,
        question: str,
        timeout: int,
        step_num,
        must_be_false: Optional[str] = None,
        step: Optional["FunctionalStep"] = None,
    ) -> bool:
        """Poll until VLM confirms the expected state or timeout expires.

        Passes when `question` is True AND (if given) `must_be_false` is False.
        The negative check catches error banners that a hallucinating VLM
        might otherwise let through on the positive question.

        A1: calls wait_for_stable() before each retry within the polling loop
        (not before the first poll — state should still be moving from the
        action just taken).
        """
        deadline = time.time() + timeout
        interval = min(2, timeout // 3) or 1
        first_poll = True
        while time.time() < deadline:
            time.sleep(interval)
            # A1: stability gate before each retry (skip the very first poll)
            if not first_poll:
                self.screenshotter.wait_for_stable(max_seconds=2.0)
            first_poll = False
            try:
                screenshot, _ = self.screenshotter.capture()
                # A6: save verify attempt screenshot
                self._save_screenshot(screenshot, f"step{step_num}_verify_poll")
                result = self._verify_call(screenshot, question, step, step_num) if step else self.vlm.verify(screenshot, question)
                if not result:
                    continue
                if must_be_false and self.vlm.verify(screenshot, must_be_false):
                    continue
                return True
            except Exception:
                pass
        return False

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
        # Determine which names are "user-named" (came from checkpoint: steps in the YAML)
        # All entries in _created_snapshots were named by the user — auto ones would need
        # a prefix; here every snapshot was explicitly named in the YAML, so "keep-named"
        # means keep all, "delete" means delete all, "keep" means keep all.
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

    def run_test(
        self,
        steps: list[FunctionalStep],
        name: str,
        vars: Optional[dict] = None,
    ) -> tuple[bool, str]:
        """Run a sequence of steps.

        Returns (passed, summary_log).

        Raises ValueError if screenshot_dir was not provided (A6: mandatory for
        forensic auditing in production runs).
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

        passed = False
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
                        return False, "\n".join(log_lines)
                    # Wait for VM to be reachable again
                    _log(f"  [checkpoint] Waiting for VM to come back after rollback...")
                    from ..proxmox.api import ProxmoxAPI as _ProxmoxAPI
                    ip = self._vm_ops.api.wait_for_ip(self._vmid, timeout=120)
                    if not ip:
                        _log(f"  [checkpoint] FATAL: VM did not return after rollback — aborting")
                        self._emit("checkpoint_rewind", name=ckpt_name,
                                   from_step=i, to_step=ckpt_step_num + 1,
                                   status="fatal_no_ip")
                        self._cleanup_snapshots(self._checkpoints_retain)
                        return False, "\n".join(log_lines)
                    self._emit("checkpoint_rewind", name=ckpt_name,
                               from_step=i, to_step=ckpt_step_num + 1)
                    _log(f"  [checkpoint] Rewound — resuming from step {ckpt_step_num + 1}")
                    # Resume after the checkpoint step (0-indexed)
                    step_index = ckpt_step_num  # ckpt_step_num is 1-indexed; index = ckpt_step_num
                    continue
                else:
                    self._cleanup_snapshots(self._checkpoints_retain)
                    return False, "\n".join(log_lines)

        _log(f"  PASS: all {len(resolved)} steps completed")
        passed = True
        self._cleanup_snapshots(self._checkpoints_retain)
        return True, "\n".join(log_lines)


def _resolve_vars(steps: list[FunctionalStep], vars: dict) -> list[FunctionalStep]:
    """Substitute {key} placeholders in step fields."""
    resolved = []
    for s in steps:
        resolved.append(FunctionalStep(
            localize=_sub(s.localize, vars) if s.localize else None,
            then_type=_sub(s.then_type, vars) if s.then_type else None,
            then_key=s.then_key,
            then_key_pre=s.then_key_pre,
            verify=_sub(s.verify, vars) if s.verify else None,
            verify_not=_sub(s.verify_not, vars) if s.verify_not else None,
            verify_input=_sub(s.verify_input, vars) if s.verify_input else None,
            verify_timeout=s.verify_timeout,
            retries=s.retries,
            launch=_sub(s.launch, vars) if s.launch else None,
            wait=s.wait,
            focus=s.focus,
            shell=_sub(s.shell, vars) if s.shell else None,
            if_visible=_sub(s.if_visible, vars) if s.if_visible else None,
            then_steps=_resolve_vars(s.then_steps, vars) if s.then_steps else None,
            verify_consistent=s.verify_consistent,
            precheck_click=s.precheck_click,
            localize_consistent=s.localize_consistent,
            launch_window=s.launch_window,
            launch_timeout=s.launch_timeout,
            on_failure_agent=_resolve_agent_vars(s.on_failure_agent, vars),
            checkpoint=s.checkpoint,
        ))
    return resolved


def _sub(text: Optional[str], vars: dict) -> Optional[str]:
    if not text:
        return text
    for k, v in vars.items():
        text = text.replace(f"{{{k}}}", v)
    return text


def _resolve_agent_vars(agent: Optional[dict], vars: dict) -> Optional[dict]:
    """C1: Substitute {key} placeholders in on_failure_agent dict string values."""
    if not agent:
        return agent
    result = dict(agent)
    for field_name in ("goal", "success_check"):
        if isinstance(result.get(field_name), str):
            result[field_name] = _sub(result[field_name], vars)
    return result


def _parse_on_failure_agent(raw: Optional[dict]) -> Optional[dict]:
    """C1: Validate and normalize an on_failure_agent dict from YAML.

    Requires 'goal' (str). Applies defaults: budget_turns=10, success_check=None.
    Returns None if raw is None or falsy.
    Raises ValueError if goal is missing.
    """
    if not raw:
        return None
    if "goal" not in raw or not isinstance(raw["goal"], str):
        raise ValueError("on_failure_agent requires a 'goal' string field")
    return {
        "goal": raw["goal"],
        "budget_turns": int(raw.get("budget_turns", 10)),
        "success_check": raw.get("success_check"),
    }


def load_test_yaml(path, _unused=None) -> tuple[str, list[FunctionalStep], dict, dict]:
    """Load a YAML functional test file.

    Returns (name, steps, vars, checkpoints_config).

    checkpoints_config is the optional top-level ``checkpoints:`` dict from the
    YAML, defaulting to ``{}`` if absent. The caller applies ``enabled``,
    ``retain``, and ``rewind_on_failure`` from this dict.

    The ``_unused`` positional parameter is accepted (but ignored) for
    backward-compatibility with call sites that passed extra args.
    """
    import yaml  # optional dep

    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)

    name = data.get("name", path.stem)
    vars = data.get("vars", {})
    checkpoints_config = data.get("checkpoints", {}) or {}
    steps = []
    for raw in data.get("steps", []):
        steps.append(_parse_step(raw))
    return name, steps, vars, checkpoints_config


def _parse_step(raw: dict) -> "FunctionalStep":
    """Parse a single raw YAML step dict into a FunctionalStep."""
    # C2: checkpoint step — shorthand {checkpoint: "name"} with no other fields
    if "checkpoint" in raw and len(raw) == 1:
        return FunctionalStep(checkpoint=str(raw["checkpoint"]))

    then_steps = None
    if "then" in raw:
        then_steps = [_parse_step(s) for s in raw["then"]]
    return FunctionalStep(
        localize=raw.get("localize"),
        then_type=raw.get("then_type") or raw.get("type"),
        then_key=raw.get("then_key") or raw.get("key"),
        then_key_pre=raw.get("then_key_pre") or raw.get("key_pre"),
        verify=raw.get("verify"),
        verify_not=raw.get("verify_not"),
        verify_input=raw.get("verify_input"),
        verify_timeout=int(raw.get("verify_timeout", 10)),
        retries=int(raw.get("retries", 3)),
        launch=raw.get("launch"),
        wait=int(raw.get("wait", 0)),
        focus=raw.get("focus"),
        shell=raw.get("shell"),
        if_visible=raw.get("if_visible"),
        then_steps=then_steps,
        verify_consistent=bool(raw.get("verify_consistent", False)),
        precheck_click=bool(raw.get("precheck_click", False)),
        localize_consistent=bool(raw.get("localize_consistent", False)),
        launch_window=raw.get("launch_window"),
        launch_timeout=int(raw["launch_timeout"]) if "launch_timeout" in raw else None,
        on_failure_agent=_parse_on_failure_agent(raw.get("on_failure_agent")),
        checkpoint=raw.get("checkpoint"),
    )
