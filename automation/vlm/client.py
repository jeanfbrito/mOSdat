"""VLM client for GUI element localization and visual verification."""

import base64
import json
import logging
import time
from io import BytesIO

from openai import OpenAI
from PIL import Image

logger = logging.getLogger(__name__)


class VLMError(Exception):
    pass


def _is_failover_error(exc: Exception) -> bool:
    """Return True if the exception warrants failover to the next endpoint.

    Retryable: connection errors, 5xx, 429 rate-limit, timeout.
    Non-retryable: 4xx (except 429), parse errors, schema errors.
    """
    import httpx
    from openai import APIConnectionError, APITimeoutError, RateLimitError
    from openai import APIStatusError

    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, RateLimitError):  # 429
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500  # 5xx only
    # httpx transport-level errors
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)):
        return True
    return False


# Models specialized for localization that hallucinate on yes/no verify prompts.
_LOC_PREFIXES = ("holo2", "ui-tars", "osatlas", "seeclick", "cogvlm-cogagent")


_LOCALIZE_PROMPT = (
    "Localize an element on the GUI image according to the provided target "
    "and output a click position.\n"
    " * You must output a valid JSON following the format: "
    '{"x": <integer 0-1000>, "y": <integer 0-1000>}\n'
    "Your target is:"
)

_VERIFY_PROMPT = "Look at this screenshot carefully. Answer only 'yes' or 'no': "

_BOUNDS_CLARIFICATION = (
    "The element is on the visible screen, not off-screen. Coordinates must "
    "fit within the image bounds.\n\n"
)


def _encode_image(img: Image.Image) -> str:
    """Encode a PIL image as base64 PNG."""
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


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
    import re as _re

    text = raw.strip()
    # Find JSON (object or array). Prefer the last fenced/standalone block.
    candidates = []
    for m in _re.finditer(r"\[[\s\S]*\]|\{[\s\S]*\}", text):
        candidates.append(m.group(0))
    if not candidates:
        raise VLMError(f"No JSON found in response: {raw[:200]!r}")
    # Fix malformed values like "x":":<number>" → "x":<number>
    json_str = _re.sub(r':\s*"[:\s]*(\d+)"', r': \1', candidates[-1])
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


class VLMClient:
    def __init__(self, base_url: str, model: str = "holo2-4b", verify_model: str | None = None):
        """Args:
            base_url: Single URL or comma-separated list of URLs for failover.
                E.g. "http://primary:5001/v1" or
                "http://primary:5001/v1,http://backup:5001/v1".
            model: VLM for element localization (expects coordinate output).
            verify_model: VLM for yes/no state verification. Defaults to `model`.
                Use a general-purpose VLM here (e.g. qwen3-vl-abliterated) —
                localization-specialized models hallucinate on yes/no prompts.
        """
        # C4: parse comma-separated URLs into a list of OpenAI clients
        raw_urls = [u.strip() for u in base_url.split(",") if u.strip()]
        self._urls: list[str] = raw_urls
        self._clients: list[OpenAI] = [OpenAI(base_url=u, api_key="unused") for u in raw_urls]
        self._primary_idx: int = 0
        # Backward-compat: expose self.client pointing at the current primary
        self.client = self._clients[0]
        self.model = model
        if verify_model is None:
            model_lower = model.lower()
            if any(model_lower.startswith(p) for p in _LOC_PREFIXES):
                raise ValueError(
                    f"Localization-specialized model '{model}' hallucinates on yes/no verify. "
                    f"Set VLM_VERIFY_MODEL to a general VLM (e.g. qwen3-vl, llava-next, internvl)."
                )
            logger.warning(
                "verify_model not set; reusing localize model '%s' for verification. "
                "Consider setting VLM_VERIFY_MODEL to a general-purpose VLM.",
                model,
            )
            self.verify_model = model
        else:
            self.verify_model = verify_model

    def _call_with_failover(self, method_path: str, *args, **kwargs):
        """C4: Call an OpenAI client method with failover across all endpoints.

        `method_path` is a dot-separated attribute chain on the OpenAI client,
        e.g. "chat.completions.create".

        Retryable errors (connection, 5xx, 429, timeout) advance to the next
        URL. Non-retryable errors (4xx != 429, parse, schema) re-raise
        immediately.  Cycles through the full URL list up to 3 full passes.
        Sleeps 0.5s between attempts to avoid hammering.
        """
        n = len(self._clients)
        max_attempts = 3 * n
        for attempt in range(max_attempts):
            idx = (self._primary_idx + attempt) % n
            client = self._clients[idx]
            # Resolve nested attribute (e.g. chat.completions.create)
            obj = client
            for attr in method_path.split("."):
                obj = getattr(obj, attr)
            try:
                result = obj(*args, **kwargs)  # type: ignore[operator]  # obj is resolved via getattr chain; runtime is callable, mypy types it as object
                # Success: promote this endpoint as primary for future calls
                self._primary_idx = idx
                self.client = self._clients[idx]
                return result
            except Exception as exc:
                if _is_failover_error(exc):
                    is_last = attempt == max_attempts - 1
                    # F6: distinguish cold-start transient failures from hard failures.
                    # 502 on the first call often means the VLM server is still
                    # warming up — log as WARNING but don't alarm the user about
                    # "trying next" when there is only one endpoint configured.
                    if len(self._urls) == 1:
                        logger.warning(
                            "VLM endpoint %s failed (attempt %d/%d): %s%s",
                            self._urls[idx], attempt + 1, max_attempts, exc,
                            " — will retry" if not is_last else " — giving up",
                        )
                    else:
                        logger.warning(
                            "VLM endpoint %s failed (attempt %d/%d): %s — trying next",
                            self._urls[idx], attempt + 1, max_attempts, exc,
                        )
                    if not is_last:
                        time.sleep(0.5)
                    continue
                raise  # non-retryable: re-raise immediately
        raise VLMError(
            "All VLM endpoints exhausted: " + ", ".join(self._urls)
        )

    def list_models(self) -> list[str]:
        """H2.3: Query the endpoint's /v1/models and return all model ids.

        Uses the primary URL directly via requests to avoid the chat.completions
        call shape (models.list() exists on the OpenAI SDK but returns a paged
        iterator that may not be supported by all llama-swap-compatible servers).

        Returns:
            List of ``id`` strings for every entry in the catalog.

        Raises:
            VLMError: On connection failure, timeout, or unexpected response shape.
        """
        import requests as _requests

        primary_url = self._urls[self._primary_idx]
        # Strip trailing /v1 if present, then re-append to build a clean URL.
        base = primary_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        url = base.rstrip("/") + "/v1/models"
        try:
            resp = _requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", [])
            if not models:
                raise VLMError(f"list_models: /v1/models returned empty list from {url}")
            return [m["id"] for m in models]
        except (
            _requests.exceptions.ConnectionError,
            _requests.exceptions.Timeout,
        ) as exc:
            raise VLMError(f"list_models: connection failed to {url}: {exc}") from exc
        except VLMError:
            raise
        except Exception as exc:
            raise VLMError(f"list_models: unexpected error from {url}: {exc}") from exc

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
        last_err: Exception = VLMError("no attempts made")
        # Some VLMs (gemma, sometimes Holo2 after a swap) return empty strings
        # intermittently. Retry more aggressively with small prompt tweaks to
        # bust any server-side KV cache.
        suffixes = ["", " .", "\n\nRespond only with the JSON."]
        max_attempts = 6
        # A5: track out-of-bounds occurrences; after 3 total give up.
        oob_count = 0
        max_oob = 3  # 1 original + 2 retries
        terminal_oob_err: VLMError | None = None
        for attempt in range(max_attempts):
            if terminal_oob_err is not None:
                raise terminal_oob_err
            suffix = suffixes[attempt % len(suffixes)]
            prompt_prefix = _BOUNDS_CLARIFICATION if oob_count > 0 else ""
            try:
                resp = self._call_with_failover(
                    "chat.completions.create",
                    model=self.model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            {"type": "text", "text": f"{prompt_prefix}{_LOCALIZE_PROMPT}\n{target}{suffix}"},
                        ],
                    }],
                    max_tokens=256,
                    timeout=90,
                )
                raw = resp.choices[0].message.content or ""
                coords = _parse_coords(raw)
                w, h = screen_size
                if coords.get("space") == "pixel":
                    pixel_x, pixel_y = coords["x"], coords["y"]
                else:
                    pixel_x = int(coords["x"] / 1000 * w)
                    pixel_y = int(coords["y"] / 1000 * h)
                # A5: bounds check AFTER norm01k → pixel scaling
                if pixel_x < 0 or pixel_x > w or pixel_y < 0 or pixel_y > h:
                    oob_count += 1
                    oob_err = VLMError(
                        f"Coords {pixel_x},{pixel_y} out of screen {screen_size}"
                        + (" after 3 attempts; element likely not visible" if oob_count >= max_oob else "")
                    )
                    if oob_count >= max_oob:
                        terminal_oob_err = oob_err
                    last_err = oob_err
                    import time as _t
                    _t.sleep(0.5)
                    continue
                return pixel_x, pixel_y
            except Exception as e:
                last_err = e
                if attempt < max_attempts - 1:
                    import time as _t
                    _t.sleep(0.5 if isinstance(e, VLMError) and "No JSON" in str(e) else 2)
        if terminal_oob_err is not None:
            raise terminal_oob_err
        raise last_err

    def verify(self, screenshot: Image.Image, question: str, temperature: float = 0.0) -> bool:
        """Ask a yes/no visual question about the current screen state.

        Args:
            screenshot: Current screen capture.
            question: A yes/no question about what should be visible.
            temperature: Sampling temperature (default 0.0 for deterministic output).

        Returns:
            True if the model answers "yes".
        """
        b64 = _encode_image(screenshot)
        resp = self._call_with_failover(
            "chat.completions.create",
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
            temperature=temperature,
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

    def verify_consistent(
        self,
        screenshot: Image.Image,
        question: str,
        samples: int = 3,
        temperature: float = 0.3,
    ) -> tuple[bool, list[str]]:
        """A2: Self-consistency verify — 3-sample 2-of-3 quorum.

        Calls verify() `samples` times with the given temperature and returns
        a majority vote.  Ties or unparseable responses → False (conservative).

        Returns:
            (majority_vote, raw_responses_list)
        """
        b64 = _encode_image(screenshot)
        responses: list[str] = []
        votes: list[bool] = []
        for _ in range(samples):
            resp = self._call_with_failover(
                "chat.completions.create",
                model=self.verify_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": f"{_VERIFY_PROMPT}{question}"},
                    ],
                }],
                max_tokens=1024,
                temperature=temperature,
                timeout=180,
            )
            raw = resp.choices[0].message.content or ""
            responses.append(raw)
            if "</think>" in raw:
                raw = raw[raw.rfind("</think>") + len("</think>"):]
            low = raw.lower().strip()
            last_yes = low.rfind("yes")
            last_no = low.rfind("no")
            if last_yes == -1 and last_no == -1:
                votes.append(False)  # unparseable → conservative False
            else:
                votes.append(last_yes > last_no)
        yes_count = sum(votes)
        no_count = len(votes) - yes_count
        # Majority required; ties go to False (conservative).
        majority = yes_count > no_count
        return majority, responses

    def localize_verified(
        self,
        screenshot: Image.Image,
        target: str,
        screen_size: tuple[int, int],
    ) -> tuple[int, int]:
        """A4: Localize then verify the result with a crop around the click point.

        Raises VLMError if the cropped region does not verify as the target.
        """
        x, y = self.localize(screenshot, target, screen_size)
        crop_size = 100
        w, h = screen_size
        box = (
            max(0, x - crop_size),
            max(0, y - crop_size),
            min(w, x + crop_size),
            min(h, y + crop_size),
        )
        crop = screenshot.crop(box)
        if not self.verify(crop, f"is this {target}"):
            raise VLMError(
                f"Pre-click verify failed: VLM denies the localized point ({x},{y}) is '{target}'"
            )
        return x, y

    def localize_consistent(
        self,
        screenshot: Image.Image,
        target: str,
        screen_size: tuple[int, int],
        samples: int = 3,
        max_spread: int = 50,
    ) -> tuple[int, int]:
        """C5: Self-consistency localize — 3-sample coord cluster.

        Calls localize() `samples` times at varying temperatures (0.0, 0.3, 0.6),
        computes the centroid, and checks that all samples are within `max_spread`
        pixels of the centroid.  Scattered samples indicate hallucination; the
        method retries once with an unambiguity prefix before giving up.

        Args:
            screenshot: Current screen capture.
            target: Natural language description of the element to find.
            screen_size: (width, height) of the screen in pixels.
            samples: Number of localize calls (default 3).
            max_spread: Maximum Euclidean distance from centroid in pixels (default 50).

        Returns:
            (pixel_x, pixel_y) centroid, bounds-checked.

        Raises:
            VLMError: If samples scatter beyond max_spread after one retry.
        """
        import math

        temperatures = [0.0, 0.3, 0.6]
        b64 = _encode_image(screenshot)

        def _sample_coords(prompt_prefix: str = "") -> list[tuple[int, int]]:
            coords_list: list[tuple[int, int]] = []
            for i in range(samples):
                temp = temperatures[i % len(temperatures)]
                resp = self._call_with_failover(
                    "chat.completions.create",
                    model=self.model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            {"type": "text", "text": f"{prompt_prefix}{_LOCALIZE_PROMPT}\n{target}"},
                        ],
                    }],
                    max_tokens=256,
                    temperature=temp,
                    timeout=90,
                )
                raw = resp.choices[0].message.content or ""
                parsed = _parse_coords(raw)
                w, h = screen_size
                if parsed.get("space") == "pixel":
                    px, py = parsed["x"], parsed["y"]
                else:
                    px = int(parsed["x"] / 1000 * w)
                    py = int(parsed["y"] / 1000 * h)
                # Apply bounds check (A5 logic)
                if px < 0 or px > w or py < 0 or py > h:
                    raise VLMError(
                        f"localize_consistent: coords {px},{py} out of screen {screen_size}"
                    )
                coords_list.append((px, py))
            return coords_list

        def _centroid_and_spread(coords: list[tuple[int, int]]) -> tuple[float, float, float]:
            cx = sum(x for x, _ in coords) / len(coords)
            cy = sum(y for _, y in coords) / len(coords)
            max_dist = max(math.sqrt((x - cx) ** 2 + (y - cy) ** 2) for x, y in coords)
            return cx, cy, max_dist

        coords = _sample_coords()
        cx, cy, max_dist = _centroid_and_spread(coords)

        if max_dist > max_spread:
            logger.warning(
                "localize_consistent: samples scattered (spread=%.1fpx > %dpx), "
                "coords=%s — retrying with unambiguity prefix",
                max_dist, max_spread, coords,
            )
            retry_prefix = (
                "The element is unambiguous and has a single clear location on the screen.\n\n"
            )
            coords = _sample_coords(prompt_prefix=retry_prefix)
            cx, cy, max_dist = _centroid_and_spread(coords)
            if max_dist > max_spread:
                raise VLMError(
                    f"Localize disagreement after retry: samples={coords}, spread={max_dist:.1f}px"
                )

        # Bounds check on centroid
        w, h = screen_size
        final_x, final_y = int(cx), int(cy)
        if final_x < 0 or final_x > w or final_y < 0 or final_y > h:
            raise VLMError(
                f"localize_consistent centroid {final_x},{final_y} out of screen {screen_size}"
            )
        return final_x, final_y

    def describe_element(self, screenshot: Image.Image, x: int, y: int) -> str:
        """Describe the GUI element near (x, y) in one phrase for use as a localize target.

        Crops ±100px around (x, y) bounded by image dimensions, then asks the
        verify_model for a concise, attribute-rich description of the element at
        the centre.  Used by the interactive recorder (C3) to pre-fill the
        element description dialog.

        Args:
            screenshot: Full-screen capture at the moment of the click.
            x: Pixel x coordinate of the click (in screenshot space).
            y: Pixel y coordinate of the click (in screenshot space).

        Returns:
            A single-phrase description string (8-15 words), stripped of
            surrounding quotes and punctuation noise.
        """
        w, h = screenshot.size
        crop_size = 100
        box = (
            max(0, x - crop_size),
            max(0, y - crop_size),
            min(w, x + crop_size),
            min(h, y + crop_size),
        )
        crop = screenshot.crop(box)
        b64 = _encode_image(crop)
        prompt = (
            "You are writing a reusable UI target prompt for automation. "
            "Describe only the stable element at the center of this image. "
            "Use durable visual semantics: role, visible static label text, icon shape, color, and container. "
            "Do not mention time, date, timestamps, message order, temporary text, usernames, file names, "
            "or position relative to transient neighbors. "
            "If surrounding context is needed, use stable container or section names only. "
            "Return 6-14 words. Output only the reusable target prompt."
        )
        resp = self._call_with_failover(
            "chat.completions.create",
            model=self.verify_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            max_tokens=64,
            temperature=0.0,
            timeout=60,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Strip wrapping quotes / punctuation noise
        import re as _re
        raw = _re.sub(r'^["\'“‘]+|["\'”’.!?,;]+$', "", raw).strip()
        return raw
