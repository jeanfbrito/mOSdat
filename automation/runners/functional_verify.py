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

    def _sweep_popups(self, step_num, max_attempts: int = 2) -> int:
        """Dismiss modal dialogs / popups before a localize step (B3).

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
