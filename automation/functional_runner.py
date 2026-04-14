"""Functional UI test runner using Holo2 for element localization and verification."""

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from .holo2_client import Holo2Client
from .input_injector import InputInjector
from .screenshot import Screenshotter


@dataclass
class FunctionalStep:
    localize: Optional[str] = None         # element description to find and click (optional if launch-only)
    then_type: Optional[str] = None        # text to type after clicking
    then_key: Optional[str] = None         # key to press after typing (enter/tab/etc.)
    then_key_pre: Optional[str] = None    # key to press BEFORE typing (e.g. ctrl+a to clear field)
    verify: Optional[str] = None           # yes/no question to confirm the outcome
    verify_timeout: int = 10               # seconds to wait for the expected state
    retries: int = 3                       # attempts before the step is marked failed
    launch: Optional[str] = None           # executable path/command to launch before localizing
    wait: int = 0                          # seconds to wait after launch before proceeding
    focus: Optional[str] = None           # window title to bring to front after launch+wait


class StepFailed(Exception):
    pass


class FunctionalRunner:
    def __init__(
        self,
        holo2: Holo2Client,
        screenshotter: Screenshotter,
        injector: InputInjector,
        screenshot_dir: Optional[Path] = None,
        log_fn: Callable[[str], None] = print,
    ):
        self.holo2 = holo2
        self.screenshotter = screenshotter
        self.injector = injector
        self.screenshot_dir = screenshot_dir
        self.log = log_fn

    def _save_screenshot(self, img: Image.Image, label: str) -> None:
        if not self.screenshot_dir:
            return
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H%M%S")
        path = self.screenshot_dir / f"{ts}_{label}.png"
        img.save(path)
        self.log(f"  Screenshot: {path}")

    def run_step(self, step: FunctionalStep, step_num: int) -> None:
        """Execute a single test step with retries.

        Raises StepFailed if all retries are exhausted.
        """
        label = step.localize[:60] if step.localize else (step.launch or "")[:60]

        # Launch is a one-shot action — do it before the retry loop
        if step.launch:
            self.log(f"  Step {step_num}: launch '{step.launch[:80]}'")
            self.injector.launch(step.launch)
            if step.wait:
                self.log(f"    → waiting {step.wait}s for app to start…")
                time.sleep(step.wait)
            if step.focus:
                self.log(f"    → focus '{step.focus}'")
                self.injector.focus_app(step.focus)

        for attempt in range(1, step.retries + 1):
            retry_label = f" (attempt {attempt}/{step.retries})" if attempt > 1 else ""

            try:
                if step.localize:
                    self.log(f"  Step {step_num}: locate '{label}'{retry_label}")
                    screenshot, screen_size = self.screenshotter.capture()
                    x, y = self.holo2.localize(screenshot, step.localize, screen_size)
                    self.log(f"    → click ({x}, {y})")
                    self.injector.click(x, y)
                    time.sleep(0.4)

                    if step.then_key_pre:
                        self.log(f"    → key '{step.then_key_pre}' (pre)")
                        self.injector.key(step.then_key_pre)
                        time.sleep(0.15)

                    if step.then_type:
                        self.log(f"    → type '{step.then_type[:40]}'")
                        self.injector.type_text(step.then_type)
                        time.sleep(0.2)

                    if step.then_key:
                        self.log(f"    → key '{step.then_key}'")
                        self.injector.key(step.then_key)
                        time.sleep(0.3)

                if step.verify:
                    if not step.localize:
                        self.log(f"  Step {step_num}: verify '{step.verify[:60]}'{retry_label}")
                    verified = self._wait_for_state(step.verify, step.verify_timeout, step_num)
                    if verified:
                        self.log(f"    ✓ verified: {step.verify[:60]}")
                        return
                    screenshot, _ = self.screenshotter.capture()
                    self._save_screenshot(screenshot, f"step{step_num}_fail_attempt{attempt}")
                    if attempt < step.retries:
                        self.log(f"    ✗ not verified, retrying...")
                        continue
                    raise StepFailed(
                        f"Step {step_num}: '{step.verify}' was never true "
                        f"after {step.retries} attempts"
                    )
                return  # no verify needed

            except StepFailed:
                raise
            except Exception as e:
                if attempt < step.retries:
                    self.log(f"    ✗ error: {e}, retrying...")
                    time.sleep(1)
                else:
                    raise StepFailed(f"Step {step_num}: {e}") from e

    def _wait_for_state(self, question: str, timeout: int, step_num: int) -> bool:
        """Poll until Holo2 confirms the expected state or timeout expires."""
        deadline = time.time() + timeout
        interval = min(2, timeout // 3) or 1
        while time.time() < deadline:
            time.sleep(interval)
            try:
                screenshot, _ = self.screenshotter.capture()
                if self.holo2.verify(screenshot, question):
                    return True
            except Exception:
                pass
        return False

    def run_test(
        self,
        steps: list[FunctionalStep],
        name: str,
        vars: Optional[dict] = None,
    ) -> tuple[bool, str]:
        """Run a sequence of steps.

        Returns (passed, summary_log).
        """
        vars = vars or {}
        log_lines: list[str] = []

        def _log(msg: str) -> None:
            self.log(msg)
            log_lines.append(msg)

        _log(f"[functional] {name}")
        _log(f"  {len(steps)} steps")

        resolved = _resolve_vars(steps, vars)

        for i, step in enumerate(resolved, start=1):
            try:
                self.run_step(step, i)
            except StepFailed as e:
                _log(f"  FAIL: {e}")
                # Save final state screenshot
                try:
                    screenshot, _ = self.screenshotter.capture()
                    self._save_screenshot(screenshot, f"step{i}_final_fail")
                except Exception:
                    pass
                return False, "\n".join(log_lines)

        _log(f"  PASS: all {len(steps)} steps completed")
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
            verify_timeout=s.verify_timeout,
            retries=s.retries,
            launch=_sub(s.launch, vars) if s.launch else None,
            wait=s.wait,
            focus=s.focus,
        ))
    return resolved


def _sub(text: Optional[str], vars: dict) -> Optional[str]:
    if not text:
        return text
    for k, v in vars.items():
        text = text.replace(f"{{{k}}}", v)
    return text


def load_test_yaml(path: Path) -> tuple[str, list[FunctionalStep], dict]:
    """Load a YAML functional test file.

    Returns (name, steps, vars).
    """
    import yaml  # optional dep

    with open(path) as f:
        data = yaml.safe_load(f)

    name = data.get("name", path.stem)
    vars = data.get("vars", {})
    steps = []
    for raw in data.get("steps", []):
        steps.append(FunctionalStep(
            localize=raw.get("localize"),
            then_type=raw.get("then_type") or raw.get("type"),
            then_key=raw.get("then_key") or raw.get("key"),
            then_key_pre=raw.get("then_key_pre") or raw.get("key_pre"),
            verify=raw.get("verify"),
            verify_timeout=int(raw.get("verify_timeout", 10)),
            retries=int(raw.get("retries", 3)),
            launch=raw.get("launch"),
            wait=int(raw.get("wait", 0)),
            focus=raw.get("focus"),
        ))
    return name, steps, vars
