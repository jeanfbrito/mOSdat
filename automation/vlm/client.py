"""VLM client for GUI element localization and visual verification."""

import logging
import math
import os
import re
import time

from openai import OpenAI

from .parsing import VLMError, _extract_content, _parse_coords
from .prompts import (
    _LOC_PREFIXES,
    _BOUNDS_CLARIFICATION,
    _DESCRIBE_ELEMENT_PROMPT,
    _LOCALIZE_PROMPT,
    _VERIFY_PROMPT,
    _encode_image,
)
from .transport import _is_failover_error

from PIL import Image

logger = logging.getLogger(__name__)

# I6: module-level opt-out flag — set by caller or env MOSDAT_VLM_NOCACHE=1
_cache_disabled: bool = os.environ.get("MOSDAT_VLM_NOCACHE", "0").strip() == "1"


def set_cache_enabled(enabled: bool) -> None:
    """I6: Enable or disable the VLM verify cache globally (e.g. from --no-cache CLI flag)."""
    global _cache_disabled
    _cache_disabled = not enabled

# Re-export so existing ``from automation.vlm.client import ...`` keeps working.
__all__ = [
    "VLMClient",
    "VLMError",
    "_parse_coords",
    "_extract_content",
    "_encode_image",
    "_is_failover_error",
]


class VLMClient:
    def __init__(self, base_url: str, model: str = "holo2-4b", verify_model: str | None = None, api_key: str = "", max_tokens_floor: int = 0):
        """Args:
            base_url: Single URL or comma-separated list of URLs for failover.
                E.g. "http://primary:5001/v1" or
                "http://primary:5001/v1,http://backup:5001/v1".
            model: VLM for element localization (expects coordinate output).
            verify_model: VLM for yes/no state verification. Defaults to `model`.
                Use a general-purpose VLM here (e.g. qwen3-vl-abliterated) —
                localization-specialized models hallucinate on yes/no prompts.
            api_key: Bearer key for hosted endpoints (crof.ai etc). Empty string →
                "unused" (local llama.cpp / vllm servers ignore the header).
            max_tokens_floor: Minimum max_tokens to send on chat.completions.create
                calls. Reasoning models (greg, deepseek-v4, kimi-k2) consume tokens
                in hidden reasoning before emitting visible output. Default 64 is
                too small for verify; set 1024+ when targeting reasoning models.
        """
        # C4: parse comma-separated URLs into a list of OpenAI clients
        raw_urls = [u.strip() for u in base_url.split(",") if u.strip()]
        self._urls: list[str] = raw_urls
        effective_key = api_key.strip() if api_key and api_key.strip() else "unused"
        self._clients: list[OpenAI] = [OpenAI(base_url=u, api_key=effective_key) for u in raw_urls]
        self._max_tokens_floor: int = max(0, int(max_tokens_floor))
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
        # Apply max_tokens_floor for reasoning models that burn tokens on hidden reasoning.
        if self._max_tokens_floor > 0 and "max_tokens" in kwargs:
            kwargs["max_tokens"] = max(int(kwargs["max_tokens"]), self._max_tokens_floor)
        n = len(self._clients)
        max_attempts = 3 * n
        for attempt in range(max_attempts):
            idx = (self._primary_idx + attempt) % n
            client = self._clients[idx]
            obj = client
            for attr in method_path.split("."):
                obj = getattr(obj, attr)
            try:
                result = obj(*args, **kwargs)  # type: ignore[operator]
                self._primary_idx = idx
                self.client = self._clients[idx]
                return result
            except Exception as exc:
                if _is_failover_error(exc):
                    is_last = attempt == max_attempts - 1
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
                raise
        raise VLMError("All VLM endpoints exhausted: " + ", ".join(self._urls))

    def list_models(self) -> list[str]:
        """H2.3: Query the endpoint's /v1/models and return all model ids."""
        import requests as _requests

        primary_url = self._urls[self._primary_idx]
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
        except (_requests.exceptions.ConnectionError, _requests.exceptions.Timeout) as exc:
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
        """Find a UI element and return its pixel coordinates."""
        b64 = _encode_image(screenshot)
        last_err: Exception = VLMError("no attempts made")
        suffixes = ["", " .", "\n\nRespond only with the JSON."]
        max_attempts = 6
        oob_count = 0
        max_oob = 3
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
                    messages=[{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": f"{prompt_prefix}{_LOCALIZE_PROMPT}\n{target}{suffix}"},
                    ]}],
                    max_tokens=256,
                    timeout=90,
                )
                raw = _extract_content(resp)
                coords = _parse_coords(raw)
                w, h = screen_size
                if coords.get("space") == "pixel":
                    pixel_x, pixel_y = coords["x"], coords["y"]
                else:
                    pixel_x = int(coords["x"] / 1000 * w)
                    pixel_y = int(coords["y"] / 1000 * h)
                if pixel_x < 0 or pixel_x > w or pixel_y < 0 or pixel_y > h:
                    oob_count += 1
                    oob_err = VLMError(
                        f"Coords {pixel_x},{pixel_y} out of screen {screen_size}"
                        + (" after 3 attempts; element likely not visible" if oob_count >= max_oob else "")
                    )
                    if oob_count >= max_oob:
                        terminal_oob_err = oob_err
                    last_err = oob_err
                    time.sleep(0.5)
                    continue
                return pixel_x, pixel_y
            except Exception as e:
                last_err = e
                if attempt < max_attempts - 1:
                    time.sleep(0.5 if isinstance(e, VLMError) and "No JSON" in str(e) else 2)
        if terminal_oob_err is not None:
            raise terminal_oob_err
        raise last_err

    def verify(self, screenshot: Image.Image, question: str, temperature: float = 0.0) -> bool:
        """Ask a yes/no visual question about the current screen state.

        I6: Results are cached by (image SHA256, prompt SHA256, model) with a
        24-hour TTL.  Set MOSDAT_VLM_NOCACHE=1 or call set_cache_enabled(False)
        to bypass.
        """
        b64 = _encode_image(screenshot)

        # I6: cache lookup (skip when disabled or non-zero temperature for consistency)
        if not _cache_disabled and temperature == 0.0:
            from automation.transport.vlm_cache import get_default_cache
            _vlm_cache = get_default_cache()
            key = _vlm_cache.cache_key(b64.encode(), question, self.verify_model)
            hit = _vlm_cache.get(key)
            if hit is not None:
                return hit[0]
        else:
            _vlm_cache = None
            key = None

        resp = self._call_with_failover(
            "chat.completions.create",
            model=self.verify_model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": f"{_VERIFY_PROMPT}{question}"},
            ]}],
            max_tokens=1024,
            temperature=temperature,
            timeout=180,
        )
        raw = _extract_content(resp)
        if "</think>" in raw:
            raw = raw[raw.rfind("</think>") + len("</think>"):]
        low = raw.lower().strip()
        last_yes = low.rfind("yes")
        last_no = low.rfind("no")
        if last_yes == -1 and last_no == -1:
            verdict = False
        else:
            verdict = last_yes > last_no

        # I6: store result on cache miss
        if not _cache_disabled and temperature == 0.0 and _vlm_cache is not None and key is not None:
            _vlm_cache.put(key, verdict, raw, self.verify_model)

        return verdict

    def verify_with_meta(
        self, screenshot: Image.Image, question: str, temperature: float = 0.0
    ) -> tuple:
        """I14: Like verify() but also returns (verdict, raw_response, cache_hit).

        Returns (bool, str, bool) — verdict, raw VLM text, whether result came from cache.
        Used by the functional runner to emit richer events for HTML reports.
        """
        b64 = _encode_image(screenshot)

        if not _cache_disabled and temperature == 0.0:
            from automation.transport.vlm_cache import get_default_cache
            _vlm_cache = get_default_cache()
            key = _vlm_cache.cache_key(b64.encode(), question, self.verify_model)
            hit = _vlm_cache.get(key)
            if hit is not None:
                return hit[0], hit[1] if len(hit) > 1 else "", True
        else:
            _vlm_cache = None
            key = None

        resp = self._call_with_failover(
            "chat.completions.create",
            model=self.verify_model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": f"{_VERIFY_PROMPT}{question}"},
            ]}],
            max_tokens=1024,
            temperature=temperature,
            timeout=180,
        )
        raw = _extract_content(resp)
        if "</think>" in raw:
            raw = raw[raw.rfind("</think>") + len("</think>"):]
        low = raw.lower().strip()
        last_yes = low.rfind("yes")
        last_no = low.rfind("no")
        if last_yes == -1 and last_no == -1:
            verdict = False
        else:
            verdict = last_yes > last_no

        if not _cache_disabled and temperature == 0.0 and _vlm_cache is not None and key is not None:
            _vlm_cache.put(key, verdict, raw, self.verify_model)

        return verdict, raw, False

    def verify_consistent(
        self,
        screenshot: Image.Image,
        question: str,
        samples: int = 3,
        temperature: float = 0.3,
    ) -> tuple[bool, list[str]]:
        """A2: Self-consistency verify — 3-sample 2-of-3 quorum."""
        b64 = _encode_image(screenshot)
        responses: list[str] = []
        votes: list[bool] = []
        for _ in range(samples):
            resp = self._call_with_failover(
                "chat.completions.create",
                model=self.verify_model,
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": f"{_VERIFY_PROMPT}{question}"},
                ]}],
                max_tokens=1024,
                temperature=temperature,
                timeout=180,
            )
            raw = _extract_content(resp)
            responses.append(raw)
            if "</think>" in raw:
                raw = raw[raw.rfind("</think>") + len("</think>"):]
            low = raw.lower().strip()
            last_yes = low.rfind("yes")
            last_no = low.rfind("no")
            if last_yes == -1 and last_no == -1:
                votes.append(False)
            else:
                votes.append(last_yes > last_no)
        yes_count = sum(votes)
        no_count = len(votes) - yes_count
        return yes_count > no_count, responses

    def localize_verified(
        self,
        screenshot: Image.Image,
        target: str,
        screen_size: tuple[int, int],
    ) -> tuple[int, int]:
        """A4: Localize then verify the result with a crop around the click point."""
        x, y = self.localize(screenshot, target, screen_size)
        crop_size = 100
        w, h = screen_size
        box = (max(0, x - crop_size), max(0, y - crop_size),
               min(w, x + crop_size), min(h, y + crop_size))
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
        """C5: Self-consistency localize — 3-sample coord cluster."""
        temperatures = [0.0, 0.3, 0.6]
        b64 = _encode_image(screenshot)

        def _sample_coords(prompt_prefix: str = "") -> list[tuple[int, int]]:
            coords_list: list[tuple[int, int]] = []
            for i in range(samples):
                temp = temperatures[i % len(temperatures)]
                resp = self._call_with_failover(
                    "chat.completions.create",
                    model=self.model,
                    messages=[{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": f"{prompt_prefix}{_LOCALIZE_PROMPT}\n{target}"},
                    ]}],
                    max_tokens=256,
                    temperature=temp,
                    timeout=90,
                )
                raw = _extract_content(resp)
                parsed = _parse_coords(raw)
                w, h = screen_size
                if parsed.get("space") == "pixel":
                    px, py = parsed["x"], parsed["y"]
                else:
                    px = int(parsed["x"] / 1000 * w)
                    py = int(parsed["y"] / 1000 * h)
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

        w, h = screen_size
        final_x, final_y = int(cx), int(cy)
        if final_x < 0 or final_x > w or final_y < 0 or final_y > h:
            raise VLMError(
                f"localize_consistent centroid {final_x},{final_y} out of screen {screen_size}"
            )
        return final_x, final_y

    def describe_element(self, screenshot: Image.Image, x: int, y: int) -> str:
        """Describe the GUI element near (x, y) in one phrase for use as a localize target."""
        w, h = screenshot.size
        crop_size = 100
        box = (max(0, x - crop_size), max(0, y - crop_size),
               min(w, x + crop_size), min(h, y + crop_size))
        crop = screenshot.crop(box)
        b64 = _encode_image(crop)
        resp = self._call_with_failover(
            "chat.completions.create",
            model=self.verify_model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": _DESCRIBE_ELEMENT_PROMPT},
            ]}],
            max_tokens=64,
            temperature=0.0,
            timeout=60,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"""^[\"’‘’“”]+|[\"’‘’“”.!?,;]+$""", "", raw).strip()
        return raw
