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
    """Extract JSON coordinates from model response.

    Handles:
    - </think> prefix from chain-of-thought models
    - Malformed values like {"x":":<number>","y":<number>} (extra colon/quote)
    - Values outside 0-1000 range (raises so the caller can retry)
    """
    if not raw:
        raise Holo2Error(f"No JSON found in response: {raw[:200]!r}")
    # Strip thinking block
    if "</think>" in raw:
        raw = raw[raw.rfind("</think>") + len("</think>"):]
    # Find JSON object
    start = raw.rfind("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise Holo2Error(f"No JSON found in response: {raw[:200]!r}")
    json_str = raw[start:end]
    # Fix malformed values: "x":":<number>" or "x":":number" → "x":<number>
    import re as _re
    json_str = _re.sub(r':\s*"[:\s]*(\d+)"', r': \1', json_str)
    coords = json.loads(json_str)

    def _to_scalar(v):
        """Coerce a coord to a single integer. qwen3-vl returns bboxes as
        lists like [x1, x2]; Holo2 returns a single int. Take the midpoint."""
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

    x = _to_scalar(coords["x"])
    y = _to_scalar(coords["y"])
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        raise Holo2Error(f"Coordinates out of range: x={x}, y={y}")
    return {"x": x, "y": y}


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
