"""Verify/check helpers for FunctionalRunner.

This module is an internal mixin — import only via automation.runners.functional.
"""

import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from PIL import Image
    from automation.runners.functional import FunctionalStep


class _VerifyMixin:
    """Mixin supplying VLM verification helpers to FunctionalRunner."""

    # ---- Default diff prompt ----

    _DEFAULT_DIFF_PROMPT = (
        "comparing the LEFT crop (before click) to the RIGHT crop (after click): "
        "the RIGHT crop now shows a visible change indicating an input field has gained focus — "
        "a text cursor / caret, or a coloured/thicker focus ring around an input box, or a "
        "placeholder text that has disappeared. If the two crops look essentially identical, answer no."
    )

    # ---- B3: Popup sweeper ----

    _APPORT_CRASH_QUESTION = (
        "is this an Ubuntu/GNOME apport crash report dialog with text like "
        "'The application Rocket.Chat has closed unexpectedly', "
        "'System program problem detected', or an Apport bug-report dialog "
        "with a Cancel button (indicates the application terminated abnormally)"
    )
    _APPORT_CANCEL_TARGET = (
        "the 'Cancel' button on the Apport crash report dialog "
        "(typically on the lower-left or lower-right of the small Apport window)"
    )

    def _sweep_popups(self, step_num, max_attempts: int = 2) -> int:
        """Dismiss modal dialogs / popups before a localize step (B3).

        For each popup detected:
          1. Probe if it's an apport crash dialog. If yes, VLM-localize the
             Cancel button and click it (Bezier motion).
          2. Otherwise send Escape (legacy path).

        Returns the count of popups dismissed.
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
                if self._handle_apport_or_fail(screenshot, step_num):
                    dismissed += 1
                    time.sleep(1.0)
                    continue
                self.injector.key("escape")
                time.sleep(0.5)
                dismissed += 1
            except Exception as e:
                from automation.runners.functional import AppCrashedError
                if isinstance(e, AppCrashedError):
                    raise
                self.log(f"    → popup sweep error ({e}), skipping")
                break
        self._emit("popup_sweep", step_num=step_num, dismissed=dismissed)
        return dismissed

    def _default_app_process_name(self) -> str:
        injector = getattr(self, "injector", None)
        if getattr(injector, "is_windows", False):
            return "Rocket.Chat"
        return "rocketchat-desktop"

    def _fail_if_app_process_dead(self, step_num) -> bool:
        """SSH-side check: is the app-under-test process still running?

        Fast (~100ms) signal that the app crashed even when no apport dialog
        is visible (apport queues only one dump per executable). Raises
        AppCrashedError on dead process.
        """
        from automation.runners.functional import AppCrashedError
        try:
            process_name = (
                getattr(self, "_app_process_name", None)
                or self._default_app_process_name()
            )
            t0 = time.perf_counter()
            running = self.injector.process_running(process_name)
            latency_ms = round((time.perf_counter() - t0) * 1000)
            self._emit("process_probe", step_num=step_num,
                       process=process_name, running=running,
                       latency_ms=latency_ms)
            if running:
                return False
        except Exception as e:
            self.log(f"    → process probe error ({e}), skipping process check")
            return False
        self.log(f"    → APP CRASHED — process '{process_name}' not running")
        self._emit("app_crashed", step_num=step_num,
                   source="process_not_running",
                   process=process_name)
        raise AppCrashedError(
            f"Application process '{process_name}' is not running at step "
            f"{step_num}. The app under test crashed silently (no apport "
            "dialog this run). Scenario marked failed — fix needed in app code."
        )

    def _handle_apport_or_fail(self, screenshot, step_num) -> bool:
        """Detect apport crash dialog, VLM-click Cancel to leave the VM clean,
        then raise AppCrashedError so the scenario fails with a clear signal.

        Click-Cancel is for VM hygiene (next scenario starts clean), not
        recovery — an apport dialog means the app crashed and the scenario
        is invalid. The test report must mark this run as crashed so the
        regression in the application code is visible.

        Returns False (so callers can fall through to other handling) when
        no apport dialog is visible. Otherwise always raises AppCrashedError.
        """
        from automation.runners.functional import AppCrashedError
        t0 = time.perf_counter()
        is_apport = self.vlm.verify(screenshot, self._APPORT_CRASH_QUESTION)
        self._emit("vlm_verify", step_num=step_num, attempt=1,
                   question=self._APPORT_CRASH_QUESTION[:80],
                   answer="yes" if is_apport else "no",
                   latency_ms=round((time.perf_counter() - t0) * 1000),
                   kind="apport_crash_probe")
        if not is_apport:
            return False

        screen_size = (screenshot.width, screenshot.height)
        cleaned = False
        try:
            t1 = time.perf_counter()
            coords = self.vlm.localize(screenshot, self._APPORT_CANCEL_TARGET, screen_size=screen_size)
            self._emit("vlm_localize", step_num=step_num, attempt=1,
                       target=self._APPORT_CANCEL_TARGET[:80],
                       coords=list(coords) if coords else None,
                       latency_ms=round((time.perf_counter() - t1) * 1000),
                       kind="apport_cancel_localize")
            if coords:
                x, y = int(coords[0]), int(coords[1])
                self.injector.click(x, y, motion="bezier")
                self._emit("apport_cancel_click", step_num=step_num, coords=[x, y])
                self.log(f"    → apport Cancel clicked at ({x},{y}) (VM cleanup before halt)")
                cleaned = True
        except Exception as e:
            self.log(f"    → apport cleanup click failed ({e}); halting anyway")

        self._emit("app_crashed", step_num=step_num,
                   source="apport_dialog_detected",
                   cleanup_clicked=cleaned)
        self.log("    → APP CRASHED — Rocket.Chat terminated abnormally, scenario marked failed")
        raise AppCrashedError(
            "Rocket.Chat crashed — apport dialog detected at step "
            f"{step_num}. The app under test has a regression that needs "
            "fixing in the application code; the scenario is marked failed."
        )

    def _verify_call(
        self,
        screenshot: "Image.Image",
        question: str,
        step: "FunctionalStep",
        step_num,
        label: str = "verify",
    ) -> bool:
        """A2: Route verify through quorum if step.verify_consistent, else plain verify."""
        if step.verify_consistent:
            result, responses = self.vlm.verify_consistent(screenshot, question)
            if not result and self.screenshot_dir:
                ts = datetime.now().strftime("%H%M%S")
                split_path = self.screenshot_dir / f"{ts}_step{step_num}_{label}_split.txt"
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                split_path.write_text(
                    f"question: {question}\n\n"
                    + "\n\n---\n\n".join(f"sample {i+1}:\n{r}" for i, r in enumerate(responses))
                )
                self.log(f"  Verify split logged: {split_path}")
                self._emit("verify_split", step_num=step_num, question=question[:80],
                           responses=[r[:200] for r in responses])
            return result
        return self.vlm.verify(screenshot, question)

    def _resolve_click_mode(self, step: "FunctionalStep") -> tuple:
        """Return (diff_enabled, yesno_enabled) considering CLI override."""
        ov = self._click_verify_override
        if ov == "auto":
            return (step.verify_click_diff, bool(step.verify_click))
        if ov == "off":
            return (False, False)
        if ov == "yesno":
            if not step.verify_click:
                self.log("    → --click-verify=yesno but step has no verify_click prompt; skipping yesno")
            return (False, bool(step.verify_click))
        if ov == "diff":
            return (True, False)
        if ov == "diff+yesno":
            if not step.verify_click:
                self.log("    → --click-verify=diff+yesno but step has no verify_click prompt; skipping yesno")
            return (True, bool(step.verify_click))
        return (step.verify_click_diff, bool(step.verify_click))

    def _resolve_canary(self, step: "FunctionalStep") -> bool:
        """Return whether canary is active for this step considering CLI override."""
        ov = self._canary_override
        if ov == "auto":
            return step.canary
        if ov == "off":
            return False
        if ov == "on":
            if not step.canary_verify:
                self.log("    → --canary=on but step has no canary_verify prompt; skipping canary")
                return False
            return True
        return step.canary

    def _check_click(
        self,
        step: "FunctionalStep",
        attempt: int,
        x: int,
        y: int,
        screenshot_before: "Image.Image",
        step_num,
        diff_enabled: bool,
        yesno_enabled: bool,
    ) -> bool:
        """Run configured click-verification checks. Returns True if all pass."""
        if diff_enabled:
            from PIL import Image
            screenshot_after, screen_size = self.screenshotter.capture()
            w, h = screen_size
            r = step.verify_click_diff_crop
            box = (
                max(0, x - r), max(0, y - r),
                min(w, x + r), min(h, y + r),
            )
            crop_before = screenshot_before.crop(box)
            crop_after = screenshot_after.crop(box)

            # Stitch horizontally with 2px black separator
            sep_w = 2
            composite_w = crop_before.width + sep_w + crop_after.width
            composite_h = max(crop_before.height, crop_after.height)
            composite = Image.new("RGB", (composite_w, composite_h), (0, 0, 0))
            composite.paste(crop_before, (0, 0))
            composite.paste(crop_after, (crop_before.width + sep_w, 0))

            if self.screenshot_dir:
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                debug_path = self.screenshot_dir / f"step{step_num}_click_diff_attempt{attempt}.png"
                composite.save(debug_path)
                self.log(f"    Click diff composite: {debug_path}")

            prompt = step.verify_click_diff_prompt or self._DEFAULT_DIFF_PROMPT
            t0 = time.perf_counter()
            diff_ok = self.vlm.verify(composite, prompt)
            latency_ms = round((time.perf_counter() - t0) * 1000)
            self._emit("vlm_verify", step_num=step_num, attempt=attempt,
                       question=prompt[:80], answer="yes" if diff_ok else "no",
                       latency_ms=latency_ms, kind="verify_click_diff")
            if not diff_ok:
                return False
            return True  # diff passed — skip yesno

        if yesno_enabled:
            self.log(f"    → verify click '{step.verify_click[:60]}'")
            t0 = time.perf_counter()
            click_ok = self._wait_for_state(step.verify_click, min(step.verify_timeout, 8), step_num, step=step)
            latency_ms = round((time.perf_counter() - t0) * 1000)
            self._emit("vlm_verify", step_num=step_num, attempt=attempt,
                       question=step.verify_click[:80], answer="yes" if click_ok else "no",
                       latency_ms=latency_ms, kind="verify_click")
            return click_ok

        return True

    def _check_canary(
        self,
        step: "FunctionalStep",
        attempt: int,
        step_num,
    ) -> bool:
        """Type canary char, verify it landed in the correct field. Returns True if yes."""
        self.injector.type_text(step.canary_char)
        time.sleep(0.3)
        screenshot, _ = self.screenshotter.capture()
        if self.screenshot_dir:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            canary_path = self.screenshot_dir / f"step{step_num}_canary_attempt{attempt}.png"
            screenshot.save(canary_path)
            self.log(f"    Canary screenshot: {canary_path}")
        t0 = time.perf_counter()
        landed = self.vlm.verify(screenshot, step.canary_verify)
        latency_ms = round((time.perf_counter() - t0) * 1000)
        self._emit("vlm_verify", step_num=step_num, attempt=attempt,
                   question=step.canary_verify[:80], answer="yes" if landed else "no",
                   latency_ms=latency_ms, kind="canary_verify")
        if not landed:
            try:
                for _ in range(len(step.canary_char)):
                    self.injector.key("backspace")
                    time.sleep(0.05)
            except Exception:
                pass
            return False
        return True

    def _check_state(
        self,
        screenshot: "Image.Image",
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

    def _wait_for_state_with_meta(
        self,
        question: str,
        timeout: int,
        step_num,
        must_be_false: Optional[str] = None,
        step: Optional["FunctionalStep"] = None,
    ) -> tuple:
        """I14: Like _wait_for_state but also returns (verdict, raw_vlm_response, cache_hit).

        On success returns the raw response from the winning poll call.
        On failure returns (False, last_raw, last_cache_hit).
        """
        deadline = time.time() + timeout
        interval = min(2, timeout // 3) or 1
        first_poll = True
        last_raw = ""
        last_cache_hit = False
        while time.time() < deadline:
            time.sleep(interval)
            if not first_poll:
                self.screenshotter.wait_for_stable(max_seconds=2.0)
            first_poll = False
            try:
                screenshot, _ = self.screenshotter.capture()
                self._save_screenshot(screenshot, f"step{step_num}_verify_poll")
                if step and step.verify_consistent:
                    # verify_consistent doesn't expose raw/cache — degrade gracefully
                    result, responses = self.vlm.verify_consistent(screenshot, question)
                    last_raw = responses[0] if responses else ""
                    last_cache_hit = False
                else:
                    result, last_raw, last_cache_hit = self.vlm.verify_with_meta(screenshot, question)
                    # I14: coerce raw_vlm/cache_hit to JSON-safe primitives so
                    # downstream event emit doesn't choke on Mock objects.
                    if not isinstance(last_raw, str):
                        last_raw = str(last_raw) if last_raw is not None else ""
                    last_cache_hit = bool(last_cache_hit)
                if not result:
                    continue
                if must_be_false and self.vlm.verify(screenshot, must_be_false):
                    continue
                return True, last_raw, last_cache_hit
            except Exception:
                pass
        return False, last_raw, last_cache_hit

    def _verify_accept_any_with_meta(
        self,
        prompts: list[str],
        timeout: int,
        step_num,
    ) -> tuple:
        """I14: Like _verify_accept_any but returns (accepted, per_prompt_verdicts).

        per_prompt_verdicts is a list of dicts:
          {prompt: str, verdict: "yes"|"no", raw_vlm_response: str}
        containing results from the final pass over all prompts.
        """
        import time as _time
        deadline = _time.time() + timeout
        pass_num = 0
        final_verdicts: list = []
        while _time.time() < deadline:
            pass_num += 1
            screenshot, _ = self.screenshotter.capture()
            self._save_screenshot(screenshot, f"step{step_num}_accept_any_pass{pass_num}")
            final_verdicts = []
            for i, prompt in enumerate(prompts):
                if _time.time() >= deadline:
                    break
                t0 = _time.perf_counter()
                try:
                    verdict, raw, _cache_hit = self.vlm.verify_with_meta(screenshot, prompt)
                except Exception:
                    verdict, raw, _cache_hit = False, "", False
                latency_ms = round((_time.time() - t0) * 1000)
                answer = "yes" if verdict else "no"
                self.log(f"    → accept_any[{i}] ({answer}): {prompt[:60]}")
                self._emit(
                    "vlm_verify",
                    step_num=step_num,
                    attempt=pass_num,
                    question=prompt[:80],
                    answer=answer,
                    latency_ms=latency_ms,
                    kind="accept_any",
                    prompt_index=i,
                )
                final_verdicts.append({
                    "prompt": prompt,
                    "verdict": answer,
                    "raw_vlm_response": raw[:1024] if raw else "",
                })
                if verdict:
                    return True, final_verdicts
            _time.sleep(2)
        return False, final_verdicts

    def _verify_accept_any(
        self,
        prompts: list[str],
        timeout: int,
        step_num,
    ) -> bool:
        """I7: Iterate prompts against current screenshot; succeed on first yes.

        Total time budget = timeout (shared across all prompt attempts).
        Takes one screenshot per pass over the prompt list, then repeats
        until any prompt returns yes or the budget expires.
        Logs each prompt's verdict so the events.jsonl captures per-prompt results.
        """
        import time as _time
        deadline = _time.time() + timeout
        pass_num = 0
        while _time.time() < deadline:
            pass_num += 1
            screenshot, _ = self.screenshotter.capture()
            self._save_screenshot(screenshot, f"step{step_num}_accept_any_pass{pass_num}")
            for i, prompt in enumerate(prompts):
                if _time.time() >= deadline:
                    break
                t0 = _time.perf_counter()
                verdict = self.vlm.verify(screenshot, prompt)
                latency_ms = round((_time.time() - t0) * 1000)
                answer = "yes" if verdict else "no"
                self.log(
                    f"    → accept_any[{i}] ({answer}): {prompt[:60]}"
                )
                self._emit(
                    "vlm_verify",
                    step_num=step_num,
                    attempt=pass_num,
                    question=prompt[:80],
                    answer=answer,
                    latency_ms=latency_ms,
                    kind="accept_any",
                    prompt_index=i,
                )
                if verdict:
                    return True
            _time.sleep(2)
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
        (not before the first poll).
        """
        deadline = time.time() + timeout
        interval = min(2, timeout // 3) or 1
        first_poll = True
        while time.time() < deadline:
            time.sleep(interval)
            if not first_poll:
                self.screenshotter.wait_for_stable(max_seconds=2.0)
            first_poll = False
            try:
                screenshot, _ = self.screenshotter.capture()
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
