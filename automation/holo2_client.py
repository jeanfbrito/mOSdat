"""Holo2 VLM client for GUI element localization and visual verification."""

import base64
import json
from io import BytesIO

from openai import OpenAI
from PIL import Image


class Holo2Error(Exception):
    pass


_LOCALIZE_PROMPT = (
    "Localize an element on the GUI image according to the provided target "
    "and output a click position.\n"
    " * You must output a valid JSON following the format: "
    '{"x": <integer 0-1000>, "y": <integer 0-1000>}\n'
    "Your target is:"
)

_VERIFY_PROMPT = "Look at this screenshot carefully. Answer only 'yes' or 'no': "


def _encode_image(img: Image.Image) -> str:
    """Encode a PIL image as base64 PNG."""
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _parse_coords(raw: str) -> dict:
    """Extract click coords from model response.

    Returns {"x": int, "y": int, "space": "norm01k"|"pixel"}.
      - "norm01k": values on 0-1000 normalized grid (Holo2, qwen3-vl).
      - "pixel": values in screenshot pixel space (qwen3.6 bbox_2d).

    Handles:
    - </think> prefix from chain-of-thought models
    - ```json fenced blocks
    - {"x":<int|list>, "y":<int|list>} (point/bbox-as-xy)
    - [{"bbox_2d":[x1,y1,x2,y2], "label":...}] (qwen3.6 native format)
    - Malformed values like {"x":":<number>","y":<number>} (extra colon/quote)
    """
    if not raw:
        raise Holo2Error(f"No JSON found in response: {raw[:200]!r}")
    # Strip thinking block
    if "</think>" in raw:
        raw = raw[raw.rfind("</think>") + len("</think>"):]
    import re as _re

    text = raw.strip()
    # Find JSON (object or array). Prefer the last fenced/standalone block.
    candidates = []
    for m in _re.finditer(r"\[[\s\S]*\]|\{[\s\S]*\}", text):
        candidates.append(m.group(0))
    if not candidates:
        raise Holo2Error(f"No JSON found in response: {raw[:200]!r}")
    # Fix malformed values like "x":":<number>" → "x":<number>
    json_str = _re.sub(r':\s*"[:\s]*(\d+)"', r': \1', candidates[-1])
    obj = json.loads(json_str)
    if isinstance(obj, list):
        if not obj:
            raise Holo2Error(f"Empty list: {raw[:200]!r}")
        obj = obj[0]

    def _to_scalar(v):
        if isinstance(v, (list, tuple)):
            flat = []
            for item in v:
                if isinstance(item, (list, tuple)):
                    flat.extend(int(float(i)) for i in item)
                else:
                    flat.append(int(float(item)))
            if not flat:
                raise Holo2Error(f"Empty coordinate list: {v!r}")
            return sum(flat) // len(flat)
        return int(float(v))

    # qwen3.6 bbox_2d: [x1,y1,x2,y2] on 0-1000 normalized grid.
    if isinstance(obj, dict) and "bbox_2d" in obj:
        bb = obj["bbox_2d"]
        if not (isinstance(bb, (list, tuple)) and len(bb) >= 4):
            raise Holo2Error(f"Malformed bbox_2d: {bb!r}")
        x1, y1, x2, y2 = (int(float(bb[i])) for i in range(4))
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        space = "pixel" if (cx > 1000 or cy > 1000) else "norm01k"
        return {"x": cx, "y": cy, "space": space}

    # Point format.
    if not (isinstance(obj, dict) and "x" in obj and "y" in obj):
        raise Holo2Error(f"Unrecognized coord format: {obj!r}")
    x = _to_scalar(obj["x"])
    y = _to_scalar(obj["y"])
    # Auto-detect space: values >1000 cannot be normalized.
    if x > 1000 or y > 1000:
        return {"x": x, "y": y, "space": "pixel"}
    return {"x": x, "y": y, "space": "norm01k"}


class Holo2Client:
    def __init__(self, base_url: str, model: str = "holo2-4b", verify_model: str | None = None):
        """Args:
            model: VLM for element localization (expects coordinate output).
            verify_model: VLM for yes/no state verification. Defaults to `model`.
                Use a general-purpose VLM here (e.g. qwen3-vl-abliterated) —
                localization-specialized models hallucinate on yes/no prompts.
        """
        self.client = OpenAI(base_url=base_url, api_key="unused")
        self.model = model
        self.verify_model = verify_model or model

    def localize(
        self,
        screenshot: Image.Image,
        target: str,
        screen_size: tuple[int, int],
    ) -> tuple[int, int]:
        """Find a UI element and return its pixel coordinates.

        Args:
            screenshot: Current screen capture.
            target: Natural language description of the element to find.
            screen_size: (width, height) of the screen in pixels.

        Returns:
            (pixel_x, pixel_y) ready to pass to InputInjector.click().
        """
        b64 = _encode_image(screenshot)
        last_err: Exception = Holo2Error("no attempts made")
        # Some VLMs (gemma, sometimes Holo2 after a swap) return empty strings
        # intermittently. Retry more aggressively with small prompt tweaks to
        # bust any server-side KV cache.
        suffixes = ["", " .", "\n\nRespond only with the JSON."]
        max_attempts = 6
        for attempt in range(max_attempts):
            suffix = suffixes[attempt % len(suffixes)]
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            {"type": "text", "text": f"{_LOCALIZE_PROMPT}\n{target}{suffix}"},
                        ],
                    }],
                    max_tokens=256,
                    timeout=90,
                )
                raw = resp.choices[0].message.content or ""
                coords = _parse_coords(raw)
                w, h = screen_size
                if coords.get("space") == "pixel":
                    return coords["x"], coords["y"]
                pixel_x = int(coords["x"] / 1000 * w)
                pixel_y = int(coords["y"] / 1000 * h)
                return pixel_x, pixel_y
            except Exception as e:
                last_err = e
                if attempt < max_attempts - 1:
                    import time as _t
                    _t.sleep(0.5 if isinstance(e, Holo2Error) and "No JSON" in str(e) else 2)
        raise last_err

    def verify(self, screenshot: Image.Image, question: str) -> bool:
        """Ask a yes/no visual question about the current screen state.

        Args:
            screenshot: Current screen capture.
            question: A yes/no question about what should be visible.

        Returns:
            True if the model answers "yes".
        """
        b64 = _encode_image(screenshot)
        resp = self.client.chat.completions.create(
            model=self.verify_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": f"{_VERIFY_PROMPT}{question}"},
                ],
            }],
            # Larger VLMs (holo3, qwen3-vl) emit a thinking block before the
            # final yes/no. 64 tokens truncates mid-think → empty answer.
            max_tokens=1024,
            timeout=180,
        )
        raw = resp.choices[0].message.content or ""
        if "</think>" in raw:
            raw = raw[raw.rfind("</think>") + len("</think>"):]
        low = raw.lower().strip()
        # Prefer decisive yes-over-no when both appear (common when the model
        # reasons "... is not X ... yes, Y is visible"): look at whichever
        # word appears closer to the end.
        last_yes = low.rfind("yes")
        last_no = low.rfind("no")
        if last_yes == -1 and last_no == -1:
            return False
        return last_yes > last_no
