"""Functional UI test runner using VLM for element localization and verification."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw

from automation.vlm.client import VLMClient
from automation.vlm.input import InputInjector
from automation.vlm.screenshot import Screenshotter
# Stage 1D: type-only import; the actual instance is supplied by the
# caller (functional_cmd, harness, mcp_tools, etc.). Kept inside TYPE_CHECKING
# to avoid pulling SSHClient at import time for tests that stub modules.
from typing import TYPE_CHECKING as _TC
if _TC:
    from automation.atspi import AtspiClient as _AtspiClient  # noqa: F401
    from automation.uia import UiaClient as _UiaClient  # noqa: F401
from automation.runners.scenario_loader import (
    FunctionalStep,
    load_test_yaml,
    parse_on_failure_agent as _parse_on_failure_agent,
    parse_step as _parse_step,
    resolve_vars as _resolve_vars,
)
from automation.runners.functional_verify import _VerifyMixin
from automation.runners.functional_steps import _StepsMixin, StepFailed, AppCrashedError
from automation.runners.functional_lifecycle import _LifecycleMixin

# Regex for redacting sensitive text values in event log
_SENSITIVE_RE = re.compile(r"password|token|secret", re.IGNORECASE)

# StepFailed is imported from functional_steps (canonical definition).
# Re-exported here for backward compatibility with callers that do:
#   from automation.runners.functional import StepFailed


@dataclass
class BugConfirmationResult:
    """Outcome of a bug-confirmation scenario run.

    Verdict mapping (for the orchestrator, not this dataclass):
      precondition_met=False                    -> INCONCLUSIVE
      precondition_met=True, bug_visible=True   -> BUG_CONFIRMED
      precondition_met=True, bug_visible=False  -> BUG_NOT_VISIBLE
    """
    precondition_met: bool
    bug_visible: bool
    bug_signal_screenshot: Optional[Path]
    precondition_screenshot: Optional[Path]
    final_screenshot: Path
    step_failures: list  # non-fatal step failures observed during the run
    elapsed_ms: int

    @property
    def verdict(self) -> str:
        if not self.precondition_met:
            return "INCONCLUSIVE"
        return "BUG_CONFIRMED" if self.bug_visible else "BUG_NOT_VISIBLE"




class FunctionalRunner(_VerifyMixin, _StepsMixin, _LifecycleMixin):

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
        click_verify_override: str = "auto",
        canary_override: str = "auto",
        x11_mode: str = "off",
        app_process_name: str = "",
        atspi: "Optional[_AtspiClient]" = None,
        uia: "Optional[_UiaClient]" = None,
    ):
        self.vlm = vlm
        self.screenshotter = screenshotter
        self.injector = injector
        # Stage 1D: coordinate-free dispatch driver. None = AT-SPI disabled;
        # any step.atspi/step.verify_atspi will RuntimeError loudly.
        self.atspi = atspi
        # Windows analog of AT-SPI. Mutually exclusive in practice (a VM is
        # either Linux or Windows), but the runner accepts both kwargs so the
        # 5 wiring sites pass whichever matches the VM's os_type. See
        # self.a11y for the unified accessor used by step dispatch.
        self.uia = uia
        self._app_process_name = app_process_name
        self.screenshot_dir = screenshot_dir
        self.log = log_fn
        self.popup_sweep = popup_sweep
        # I4: implicit X11 preamble injection when x11_mode == "auto"
        self._x11_mode = x11_mode
        # I14: opt-in config.json snapshots after shell steps
        self._config_snapshots: bool = False
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
        # CLI overrides for click-verify and canary modes
        self._click_verify_override = click_verify_override
        self._canary_override = canary_override

    @property
    def a11y(self):
        """Active accessibility driver — UiaClient on Windows VMs, AtspiClient
        on Linux VMs. Returns None when neither was wired; step dispatch raises
        a RuntimeError naming both clients in that case.

        Both clients expose the same public API (find/click/verify/wait_for/
        tree_dump). Callers pass `input_injector=self.injector` for symmetry;
        UiaClient accepts the kwarg and ignores it (VM-side pointer mode).
        """
        return self.uia or self.atspi

    def _require_a11y(self, field_name: str):
        """Return the active a11y driver or raise a RuntimeError that names
        both possible clients. Centralized so the four call-sites
        (step.atspi / step.verify_atspi / step.wait_for) report identical
        wiring hints.
        """
        drv = self.a11y
        if drv is None:
            raise RuntimeError(
                f"step.{field_name} is set but FunctionalRunner was constructed "
                "without an AtspiClient or UiaClient — wire it via "
                "FunctionalRunner(..., atspi=...) for Linux VMs or "
                "FunctionalRunner(..., uia=...) for Windows VMs"
            )
        return drv


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
        # Guard snapshot() call — checkpointing is best-effort
        try:
            self._vm_ops.snapshot(self._vmid, name)
        except Exception as e:
            self.log(f"    → WARNING: could not create snapshot '{name}': {e}")
            return
        self._checkpoints.append((name, step_num))
        self._created_snapshots.append(name)
        self._emit("checkpoint_created", step_num=step_num, name=name)
        self.log(f"    → checkpoint '{name}' created at step {step_num}")

