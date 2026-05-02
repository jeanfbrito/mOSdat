"""VLM-driven agent loop for self-healing functional steps."""

import json
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .client import VLMClient, VLMError
from .input import InputInjector
from .screenshot import Screenshotter


@dataclass
class AgentResult:
    success: bool
    turns_used: int
    actions: list  # list of dicts: {"action": "click|type|key|wait|done|giveup", ...}
    reason: str


_ACTION_PROMPT = (
    "You are driving a desktop GUI to achieve a goal. Look at the screenshot "
    "and pick the SINGLE next action that makes progress. Output ONLY a JSON "
    "object, no prose, no markdown.\n\n"
    "Goal: {goal}\n\n"
    "Available actions:\n"
    '  {{"action": "click", "target": "<element description>"}}  — click on described element\n'
    '  {{"action": "type", "text": "<text>"}}  — type into currently focused field\n'
    '  {{"action": "key", "key": "enter|tab|escape|super|ctrl+a|..."}}  — press a key\n'
    '  {{"action": "wait", "seconds": <int 1-5>}}  — wait for UI to settle\n'
    '  {{"action": "done", "reason": "<short>"}}  — goal achieved\n'
    '  {{"action": "giveup", "reason": "<short>"}}  — cannot proceed\n\n'
    "Output exactly one JSON object."
)


def _parse_action(raw: str) -> dict:
    """Extract first JSON object from raw VLM output.

    Handles:
    - Markdown fenced blocks (```json ... ```)
    - Raw JSON objects
    - Prose-then-JSON (picks first { ... } block)

    Returns a dict with at least an "action" key.
    Raises ValueError if no parseable JSON object is found.
    """
    if not raw:
        raise ValueError("Empty response from VLM")

    # Strip thinking block if present
    if "</think>" in raw:
        raw = raw[raw.rfind("</think>") + len("</think>"):]

    text = raw.strip()

    # Find all {...} candidates (non-greedy inner match via regex on the full string)
    # We want the FIRST well-formed JSON object.
    candidates = list(re.finditer(r"\{[\s\S]*?\}", text))
    # Also try greedy match to catch nested structures
    greedy = list(re.finditer(r"\{[\s\S]*\}", text))
    all_candidates = candidates + greedy

    for m in all_candidates:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "action" in obj:
                return obj
        except json.JSONDecodeError:
            continue

    raise ValueError(f"No valid action JSON found in: {raw[:200]!r}")


class AgentLoop:
    """Goal-driven loop: screenshot → action → execute → verify progress.

    Uses two VLM calls per turn:
      1. Action picker: given goal G and screenshot, pick next action.
      2. Success check: success_check question evaluated on the new screenshot;
         exits on 'yes'.
    """

    def __init__(
        self,
        vlm: VLMClient,
        screenshotter: Screenshotter,
        injector: InputInjector,
        log_fn: Callable[[str], None] = print,
        emit_event: Optional[Callable[..., None]] = None,
    ):
        self.vlm = vlm
        self.screenshotter = screenshotter
        self.injector = injector
        self.log = log_fn
        self.emit = emit_event or (lambda *a, **kw: None)

    def run(
        self,
        goal: str,
        budget_turns: int = 10,
        success_check: Optional[str] = None,
        step_num=None,
    ) -> AgentResult:
        """Execute autonomous turns toward goal until success or budget exhausted."""
        actions_log: list = []

        for turn in range(1, budget_turns + 1):
            # 1. Capture screenshot
            try:
                screenshot, screen_size = self.screenshotter.capture()
            except Exception as e:
                self.log(f"    [agent turn {turn}] screenshot failed: {e}")
                continue

            # 2. Emit agent_turn_start
            screenshot_path = None
            self.emit("agent_turn_start", step_num=step_num, turn=turn,
                      screenshot_path=screenshot_path)

            # 3. Ask VLM action picker
            from .client import _encode_image
            b64 = _encode_image(screenshot)
            prompt = _ACTION_PROMPT.format(goal=goal)
            raw_action = ""
            parsed_action = {"action": "giveup", "reason": "parse failure"}
            try:
                resp = self.vlm._call_with_failover(
                    "chat.completions.create",
                    model=self.vlm.verify_model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            {"type": "text", "text": prompt},
                        ],
                    }],
                    max_tokens=256,
                    temperature=0.0,
                    timeout=90,
                )
                raw_action = resp.choices[0].message.content or ""
                parsed_action = _parse_action(raw_action)
            except Exception as e:
                self.log(f"    [agent turn {turn}] action VLM failed: {e}")
                parsed_action = {"action": "giveup", "reason": f"VLM error: {e}"}

            action_type = parsed_action.get("action", "giveup")
            self.log(f"    [agent turn {turn}] action: {parsed_action}")

            # 4. Emit agent_action
            self.emit("agent_action", step_num=step_num, turn=turn, **parsed_action)
            actions_log.append(parsed_action)

            # 5. Dispatch action
            if action_type == "done":
                reason = parsed_action.get("reason", "")
                return AgentResult(
                    success=True,
                    turns_used=turn,
                    actions=actions_log,
                    reason=reason,
                )

            if action_type == "giveup":
                reason = parsed_action.get("reason", "agent gave up")
                return AgentResult(
                    success=False,
                    turns_used=turn,
                    actions=actions_log,
                    reason=reason,
                )

            try:
                if action_type == "click":
                    target = parsed_action.get("target", "")
                    try:
                        x, y = self.vlm.localize(screenshot, target, screen_size)
                        self.injector.click(x, y)
                    except VLMError as e:
                        self.log(f"    [agent turn {turn}] localize failed: {e} — skipping turn")
                        # One bad turn doesn't kill the loop
                        continue

                elif action_type == "type":
                    text = parsed_action.get("text", "")
                    self.injector.type_text(text)

                elif action_type == "key":
                    key = parsed_action.get("key", "")
                    self.injector.key(key)

                elif action_type == "wait":
                    seconds = int(parsed_action.get("seconds", 1))
                    seconds = max(1, min(seconds, 5))  # clamp to 1-5
                    time.sleep(seconds)

                else:
                    self.log(f"    [agent turn {turn}] unknown action '{action_type}' — treating as giveup")
                    return AgentResult(
                        success=False,
                        turns_used=turn,
                        actions=actions_log,
                        reason=f"unknown action type: {action_type}",
                    )

            except Exception as e:
                self.log(f"    [agent turn {turn}] dispatch error: {e} — skipping turn")
                continue

            # 6. Post-action stability wait (1.5s max via existing primitive)
            self.screenshotter.wait_for_stable(max_seconds=1.5)

            # 7. Success check
            if success_check:
                try:
                    new_screenshot, _ = self.screenshotter.capture()
                    answer = self.vlm.verify(new_screenshot, success_check)
                    answer_str = "yes" if answer else "no"
                    self.emit("agent_success_check", step_num=step_num, turn=turn,
                              answer=answer_str)
                    self.log(f"    [agent turn {turn}] success_check: {answer_str}")
                    if answer:
                        return AgentResult(
                            success=True,
                            turns_used=turn,
                            actions=actions_log,
                            reason="success_check passed",
                        )
                except Exception as e:
                    self.log(f"    [agent turn {turn}] success_check error: {e}")

        # Budget exhausted
        return AgentResult(
            success=False,
            turns_used=budget_turns,
            actions=actions_log,
            reason=f"budget of {budget_turns} turns exhausted",
        )
