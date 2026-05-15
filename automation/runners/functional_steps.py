"""Step dispatch helpers for FunctionalRunner.

This module is an internal mixin — import only via automation.runners.functional.
StepFailed is defined here (not in functional.py) so all mixin modules can import
it without a circular dependency.
"""

import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from automation.runners.functional import FunctionalStep


class StepFailed(Exception):
    pass


class _StepsMixin:
    """Mixin supplying step-level dispatch logic to FunctionalRunner.

    Verify/check helpers live in _VerifyMixin (functional_verify.py).
    """

    def _run_launch_step(self, step: "FunctionalStep", step_num) -> None:
        """Handle the launch: action including process/window wait loop."""
        from automation.runners.launch import launch_probe_name, natural_window_name


        self.log(f"  Step {step_num}: launch '{step.launch[:80]}'")
        app_basename = launch_probe_name(
            step.launch,
            step.launch_window,
            self.injector.is_windows,
        )
        self._emit("launch", step_num=step_num, app=step.launch[:80], args="")
        self.injector.launch(step.launch)

        launch_budget = step.launch_timeout if step.launch_timeout is not None else step.wait
        window_name = natural_window_name(app_basename)
        if launch_budget > 0:
            self.log(f"    → waiting up to {launch_budget}s for '{app_basename}' to start…")
            t_launch = time.perf_counter()
            process_present = False
            window_present = False
            deadline_launch = time.time() + launch_budget
            # F1: cap VLM verify calls; 1 call per 10s, minimum 2
            MAX_LAUNCH_VLM_CALLS = max(2, launch_budget // 10)
            n_calls = 0
            while time.time() < deadline_launch:
                time.sleep(1)
                if time.time() >= deadline_launch:
                    break
                try:
                    process_present = self.injector.process_running(app_basename)
                except Exception:
                    process_present = False
                if process_present:
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

    def _run_standalone_input(self, step: "FunctionalStep", step_num) -> None:
        """Handle standalone key/type steps (no localize, launch, or shell)."""
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
            # A1: stability wait after then_key
            self.screenshotter.wait_for_stable(max_seconds=3.0)
        if step.wait:
            self.log(f"    → waiting {step.wait}s")
            time.sleep(step.wait)

    def _run_if_visible(self, step: "FunctionalStep", step_num, step_start_ts: float) -> None:
        """Handle if_visible step and emit step_end."""
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
            self.log("    → visible: executing then_steps")
            for sub_i, sub_step in enumerate(step.then_steps or [], start=1):
                self.run_step(sub_step, f"{step_num}.{sub_i}")
        else:
            self.log("    → not visible: skipping")
        duration_ms = round((time.perf_counter() - step_start_ts) * 1000)
        self._emit("step_end", step_num=step_num,
                   status="ok" if visible else "skipped",
                   attempts=1, duration_ms=duration_ms)

    def _run_localize_body(
        self,
        step: "FunctionalStep",
        attempt: int,
        step_num,
        label: str,
        retry_label: str,
    ) -> Optional[str]:
        """Execute localize/click/type/key actions within the retry loop.

        Returns None on success (caller continues to verify).
        Returns "continue" if retry loop should continue.
        Raises StepFailed on hard failures.
        """


        if self.popup_sweep:
            self._sweep_popups(step_num)
        self.log(f"  Step {step_num}: locate '{label}'{retry_label}")
        diff_enabled, yesno_enabled = self._resolve_click_mode(step)
        screenshot_before_click = None
        if diff_enabled:
            screenshot_before_click, _ = self.screenshotter.capture()
        screenshot, screen_size = self.screenshotter.capture()
        self._save_screenshot(screenshot, f"step{step_num}_localize")

        t0_loc = time.perf_counter()
        if step.localize_consistent:
            x, y = self.vlm.localize_consistent(screenshot, step.localize, screen_size)
            latency_ms_loc = round((time.perf_counter() - t0_loc) * 1000)
            self._emit("vlm_localize_consistent", step_num=step_num, attempt=attempt,
                       target=step.localize[:80], centroid=(x, y),
                       latency_ms=latency_ms_loc)
            if step.precheck_click:
                crop_size = 100
                w_s, h_s = screen_size
                box = (
                    max(0, x - crop_size), max(0, y - crop_size),
                    min(w_s, x + crop_size), min(h_s, y + crop_size),
                )
                crop = screenshot.crop(box)
                from automation.vlm.client import VLMError
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

        if step.hover:
            self.log(f"    → hover ({x}, {y})")
            self._emit("hover", step_num=step_num, x=x, y=y)
            self.injector.move(x, y)
        else:
            click_button = 3 if step.click == "right" else 1
            self.log(f"    → {step.click or 'left'} click ({x}, {y})")
            self._emit("click", step_num=step_num, x=x, y=y, button=step.click or "left")
            self.injector.click(x, y, button=click_button)
        self._save_click_overlay(screenshot, x, y, step_num)
        time.sleep(0.4)

        if diff_enabled or yesno_enabled:
            _before = screenshot_before_click if diff_enabled else screenshot
            click_ok = self._check_click(
                step, attempt, x, y, _before, step_num, diff_enabled, yesno_enabled,
            )
            if not click_ok:
                screenshot_vc, _ = self.screenshotter.capture()
                self._save_screenshot(screenshot_vc, f"step{step_num}_click_fail_attempt{attempt}")
                self.log("    ✗ click verify failed, retrying")
                if attempt < step.retries:
                    return "continue"
                raise StepFailed(f"Step {step_num}: click verify never passed")
            self.log("    ✓ click verified")

        if step.then_key_pre:
            self.log(f"    → key '{step.then_key_pre}' (pre)")
            self.injector.key(step.then_key_pre)
            time.sleep(0.15)

        if step.then_type:
            canary_active = self._resolve_canary(step)
            if canary_active and step.canary_verify:
                self.log(f"    → canary verify (char='{step.canary_char}')")
                canary_ok = self._check_canary(step, attempt, step_num)
                if not canary_ok:
                    self.log("    ✗ canary verify failed, retrying")
                    if attempt < step.retries:
                        return "continue"
                    raise StepFailed(f"Step {step_num}: canary verify never passed")
                self.log("    ✓ canary verified — backspacing canary char")
                for _ in range(len(step.canary_char)):
                    self.injector.key("backspace")
                    time.sleep(0.05)

            self.log(f"    → type '{step.then_type[:40]}'")
            self._emit("type", step_num=step_num,
                       text_redacted=self._redact(step.then_type))
            self.injector.type_text(step.then_type)
            time.sleep(0.2)

        if step.verify_input:
            self.log(f"    → verify input '{step.verify_input[:60]}'")
            if not self._wait_for_state(step.verify_input, min(step.verify_timeout, 10), step_num, step=step):
                screenshot, _ = self.screenshotter.capture()
                self._save_screenshot(screenshot, f"step{step_num}_input_fail_attempt{attempt}")
                if attempt < step.retries:
                    self.log("    ✗ input not verified, retrying...")
                    return "continue"
                raise StepFailed(
                    f"Step {step_num}: verify_input '{step.verify_input}' never became true"
                )
            self.log("    ✓ input verified")

        if step.then_key:
            self.log(f"    → key '{step.then_key}'")
            self._emit("key", step_num=step_num, key=step.then_key)
            self.injector.key(step.then_key)
            self.screenshotter.wait_for_stable(max_seconds=3.0)

        return None  # success — proceed to verify

    def _invoke_agent_fallback(self, step: "FunctionalStep", step_num) -> bool:
        """Run agent fallback if configured. Returns True if agent recovered the step."""
        if not step.on_failure_agent:
            return False
        self.log(f"  Step {step_num}: handing off to agent fallback")
        from automation.vlm.agent import AgentLoop
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
            return True
        self._emit("agent_fallback", step_num=step_num, success=False,
                   turns=result.turns_used, reason=result.reason)
        return False

    def run_step(self, step: "FunctionalStep", step_num) -> None:
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
        step_start_ts = time.perf_counter()
        self._emit("step_start", step_num=step_num, label=label, kind=kind)

        # Shell is a one-shot action
        if step.shell:
            self.log(f"  Step {step_num}: shell '{step.shell[:80]}'")
            self._emit("shell", step_num=step_num, cmd_truncated=step.shell[:80])
            self.injector.shell(step.shell)

        # Launch is a one-shot action
        if step.launch:
            self._run_launch_step(step, step_num)

        # Standalone key/type steps (no localize)
        if not step.localize and not step.launch and not step.shell:
            self._run_standalone_input(step, step_num)

        # if_visible: one-shot check, no retry
        if step.if_visible is not None:
            self._run_if_visible(step, step_num, step_start_ts)
            return

        # Focus before input/localize
        if step.focus:
            self.log(f"    → focus '{step.focus}'")
            self.injector.focus_app(step.focus)
            time.sleep(0.4)

        for attempt in range(1, step.retries + 1):
            retry_label = f" (attempt {attempt}/{step.retries})" if attempt > 1 else ""

            try:
                # On retries, check if expected state already settled
                if attempt > 1 and step.verify:
                    screenshot_chk, _ = self.screenshotter.capture()
                    if self._check_state(screenshot_chk, step.verify, 3, step_num,
                                         must_be_false=step.verify_not, step=step):
                        self.log("    ✓ already verified (state settled between attempts)")
                        return

                if step.localize:
                    directive = self._run_localize_body(step, attempt, step_num, label, retry_label)
                    if directive == "continue":
                        continue

                if step.verify:
                    if not step.localize:
                        self.log(f"  Step {step_num}: verify '{step.verify[:60]}'{retry_label}")
                    t0_verify = time.perf_counter()
                    verified = self._wait_for_state(step.verify, step.verify_timeout, step_num,
                                                    must_be_false=step.verify_not, step=step)
                    latency_ms_verify = round((time.perf_counter() - t0_verify) * 1000)
                    if verified:
                        self.log(f"    ✓ verified: {step.verify[:60]}")
                        self._emit("vlm_verify", step_num=step_num, attempt=attempt,
                                   question=step.verify[:80], answer="yes",
                                   latency_ms=latency_ms_verify, kind="verify")
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
                        self.log("    ✗ not verified, retrying...")
                        self._emit("retry", step_num=step_num, attempt=attempt, reason="verify_failed")
                        continue
                    duration_ms = round((time.perf_counter() - step_start_ts) * 1000)
                    self._emit("step_end", step_num=step_num, status="failed",
                               attempts=attempt, duration_ms=duration_ms)
                    if self._invoke_agent_fallback(step, step_num):
                        return
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
                    if self._invoke_agent_fallback(step, step_num):
                        return
                    raise StepFailed(f"Step {step_num}: {e}") from e
