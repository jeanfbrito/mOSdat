"""Response parsing helpers for the VLM client."""

import json
import re


class VLMError(Exception):
    pass


def _extract_content(resp) -> str:
    """Extract message content, falling back to reasoning_content if content is empty.

    Reasoning models (Qwen3.6, Kimi K2.x, DeepSeek R1) put the visible response
    in reasoning_content and leave content empty.  This helper picks whichever
    field has text.
    """
    msg = resp.choices[0].message
    raw = msg.content or ""
    if not raw:
        raw = getattr(msg, "reasoning_content", "") or ""
    return raw


def _parse_coords(raw: str) -> dict:
    """Extract click coords from model response.

    Returns {"x": int, "y": int, "space": "norm01k"|"pixel"}.
      - "norm01k": values on 0-1000 normalized grid (VLM, qwen3-vl).
      - "pixel": values in screenshot pixel space (qwen3.6 bbox_2d).

    Handles:
    - </think> prefix from chain-of-thought models
    - ```json fenced blocks
    - {"x":<int|list>, "y":<int|list>} (point/bbox-as-xy)
    - [{"bbox_2d":[x1,y1,x2,y2], "label":...}] (qwen3.6 native format)
    - Malformed values like {"x":":<number>","y":<number>} (extra colon/quote)
    """
    if not raw:
        raise VLMError(f"No JSON found in response: {raw[:200]!r}")
    # Strip thinking block
    if "</think>" in raw:
        raw = raw[raw.rfind("</think>") + len("</think>"):]

    text = raw.strip()
    # Find JSON (object or array). Prefer the last fenced/standalone block.
    candidates = []
    for m in re.finditer(r"\[[\s\S]*\]|\{[\s\S]*\}", text):
        candidates.append(m.group(0))
    if not candidates:
        raise VLMError(f"No JSON found in response: {raw[:200]!r}")
    # Fix malformed values like "x":":<number>" → "x":<number>
    json_str = re.sub(r':\s*"[:\s]*(\d+)"', r': \1', candidates[-1])
    obj = json.loads(json_str)
    if isinstance(obj, list):
        if not obj:
            raise VLMError(f"Empty list: {raw[:200]!r}")
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
                raise VLMError(f"Empty coordinate list: {v!r}")
            return sum(flat) // len(flat)
        return int(float(v))

    # qwen3.6 bbox_2d: [x1,y1,x2,y2] on 0-1000 normalized grid.
    if isinstance(obj, dict) and "bbox_2d" in obj:
        bb = obj["bbox_2d"]
        if not (isinstance(bb, (list, tuple)) and len(bb) >= 4):
            raise VLMError(f"Malformed bbox_2d: {bb!r}")
        x1, y1, x2, y2 = (int(float(bb[i])) for i in range(4))
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        space = "pixel" if (cx > 1000 or cy > 1000) else "norm01k"
        return {"x": cx, "y": cy, "space": space}

    # Bbox-as-two-ranges: {"x": [x1, x2], "y": [y1, y2]} or partial
    # (some VLMs return a bounding box split across x/y keys instead of a
    # scalar point; take midpoint of each range).  Also handles the degenerate
    # case where only one axis is a list and the other is absent or scalar.
    if isinstance(obj, dict) and ("x" in obj or "y" in obj):
        raw_x = obj.get("x")
        raw_y = obj.get("y")
        if isinstance(raw_x, (list, tuple)) or isinstance(raw_y, (list, tuple)):
            x = _to_scalar(raw_x) if raw_x is not None else 500
            y = _to_scalar(raw_y) if raw_y is not None else 500
            # Pixel-space detection: check max raw value across both axes —
            # a range like [800, 1200] midpoints to 1000 which equals the
            # norm01k ceiling, so check the raw extremes before collapsing.
            def _raw_max(v):
                if isinstance(v, (list, tuple)):
                    return max(int(float(i)) for i in v)
                return int(float(v)) if v is not None else 0
            pixel = _raw_max(raw_x) > 1000 or _raw_max(raw_y) > 1000
            if pixel:
                return {"x": x, "y": y, "space": "pixel"}
            return {"x": x, "y": y, "space": "norm01k"}

    # Point format.
    if not (isinstance(obj, dict) and "x" in obj and "y" in obj):
        raise VLMError(f"Unrecognized coord format: {obj!r}")
    x = _to_scalar(obj["x"])
    y = _to_scalar(obj["y"])
    # Auto-detect space: values >1000 cannot be normalized.
    if x > 1000 or y > 1000:
        return {"x": x, "y": y, "space": "pixel"}
    return {"x": x, "y": y, "space": "norm01k"}
