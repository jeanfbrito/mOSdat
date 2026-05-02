"""Unit tests for VLMClient public methods.

Covers:
  A2 verify_consistent — quorum / majority vote
  A3 verify_model enforcement for localization-specialized models
  A4 localize_verified — precheck crop verify
  A5 coord bounds sanity in localize()
  C4 _call_with_failover — retries on retryable errors
  C5 localize_consistent — centroid clustering
"""

import sys
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import importlib as _importlib
import PIL.Image as _pil_image_mod
if getattr(_pil_image_mod, "Image", None) is object:
    _importlib.reload(_pil_image_mod)
from PIL import Image

# ---------------------------------------------------------------------------
# Load client module directly (bypass vlm/__init__ which re-exports and
# requires the full installed package to be present)
# ---------------------------------------------------------------------------
_PROJ = Path(__file__).parent.parent

# openai must be importable; stub it if absent
if "openai" not in sys.modules:
    _oai = types.ModuleType("openai")
    _oai.OpenAI = MagicMock
    _oai.APIConnectionError = type("APIConnectionError", (Exception,), {})
    _oai.APITimeoutError = type("APITimeoutError", (Exception,), {})
    _oai.RateLimitError = type("RateLimitError", (Exception,), {})
    _oai.APIStatusError = type("APIStatusError", (Exception,), {"status_code": 400})
    sys.modules["openai"] = _oai
else:
    import openai  # noqa: F401 — ensure the real one is used if present

# httpx stub
if "httpx" not in sys.modules:
    _httpx = types.ModuleType("httpx")
    _httpx.ConnectError = type("ConnectError", (Exception,), {})
    _httpx.TimeoutException = type("TimeoutException", (Exception,), {})
    _httpx.RemoteProtocolError = type("RemoteProtocolError", (Exception,), {})
    sys.modules["httpx"] = _httpx

_client_spec = importlib.util.spec_from_file_location(
    "automation.vlm.client",
    _PROJ / "automation" / "vlm" / "client.py",
)
_client_mod = importlib.util.module_from_spec(_client_spec)
_client_mod.__package__ = "automation.vlm"
sys.modules["automation.vlm.client"] = _client_mod
_client_spec.loader.exec_module(_client_mod)

VLMClient = _client_mod.VLMClient
VLMError = _client_mod.VLMError
_parse_coords = _client_mod._parse_coords


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_image(size=(1920, 1080)) -> Image.Image:
    return Image.new("RGB", size, (128, 128, 128))


def _make_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_client(model="qwen3-vl", verify_model="qwen3-vl"):
    """Return a VLMClient with a mocked OpenAI instance."""
    with patch.object(_client_mod, "OpenAI") as MockOpenAI:
        fake_openai = MagicMock()
        MockOpenAI.return_value = fake_openai
        c = VLMClient(
            base_url="http://fake:5001/v1",
            model=model,
            verify_model=verify_model,
        )
    # Re-point the internal client to the same fake
    c._clients = [c.client]
    return c, c.client


# ---------------------------------------------------------------------------
# A2: verify_consistent — quorum
# ---------------------------------------------------------------------------

class TestVerifyConsistent:
    def test_majority_yes_returns_true(self):
        c, oai = _make_client()
        oai.chat.completions.create.side_effect = [
            _make_response("yes"), _make_response("yes"), _make_response("no"),
        ]
        result, responses = c.verify_consistent(_fake_image(), "is the button visible")
        assert result is True
        assert len(responses) == 3

    def test_majority_no_returns_false(self):
        c, oai = _make_client()
        oai.chat.completions.create.side_effect = [
            _make_response("no"), _make_response("no"), _make_response("yes"),
        ]
        result, _ = c.verify_consistent(_fake_image(), "is button visible")
        assert result is False

    def test_tie_2_samples_returns_false(self):
        c, oai = _make_client()
        oai.chat.completions.create.side_effect = [
            _make_response("yes"), _make_response("no"),
        ]
        result, _ = c.verify_consistent(_fake_image(), "q", samples=2)
        assert result is False

    def test_unparseable_counts_as_false_but_yes_majority_wins(self):
        c, oai = _make_client()
        oai.chat.completions.create.side_effect = [
            _make_response("maybe"), _make_response("yes"), _make_response("yes"),
        ]
        result, _ = c.verify_consistent(_fake_image(), "q")
        assert result is True

    def test_all_unparseable_returns_false(self):
        c, oai = _make_client()
        oai.chat.completions.create.side_effect = [
            _make_response("hmm"), _make_response("unclear"), _make_response("?"),
        ]
        result, _ = c.verify_consistent(_fake_image(), "q")
        assert result is False


# ---------------------------------------------------------------------------
# A3: verify_model enforcement
# ---------------------------------------------------------------------------

class TestVerifyModelEnforcement:
    def test_loc_specialized_without_verify_model_raises(self):
        with patch.object(_client_mod, "OpenAI"):
            with pytest.raises(ValueError, match="hallucinates"):
                VLMClient(base_url="http://fake/v1", model="holo2-4b")

    def test_loc_specialized_with_explicit_verify_model_ok(self):
        with patch.object(_client_mod, "OpenAI"):
            c = VLMClient(base_url="http://fake/v1", model="holo2-4b", verify_model="qwen3-vl")
        assert c.verify_model == "qwen3-vl"

    def test_ui_tars_prefix_blocked(self):
        with patch.object(_client_mod, "OpenAI"):
            with pytest.raises(ValueError):
                VLMClient(base_url="http://fake/v1", model="ui-tars-7b")

    def test_non_loc_model_warns_and_reuses(self):
        with patch.object(_client_mod, "OpenAI"), \
             patch.object(_client_mod, "logger"):
            c = VLMClient(base_url="http://fake/v1", model="qwen3-vl")
        assert c.verify_model == "qwen3-vl"


# ---------------------------------------------------------------------------
# A4: localize_verified — precheck crop
# ---------------------------------------------------------------------------

class TestLocalizeVerified:
    def test_passes_when_crop_verify_confirms(self):
        c, oai = _make_client()
        oai.chat.completions.create.side_effect = [
            _make_response('{"x": 500, "y": 500}'),  # localize
            _make_response("yes"),                     # verify crop
        ]
        with patch("time.sleep"):
            x, y = c.localize_verified(_fake_image(), "the Submit button", (1920, 1080))
        assert x == 960 and y == 540

    def test_raises_when_crop_verify_denies(self):
        c, oai = _make_client()
        oai.chat.completions.create.side_effect = [
            _make_response('{"x": 500, "y": 500}'),
            _make_response("no"),
        ]
        with patch("time.sleep"):
            with pytest.raises(VLMError, match="Pre-click verify failed"):
                c.localize_verified(_fake_image(), "Submit button", (1920, 1080))


# ---------------------------------------------------------------------------
# A5: coord bounds sanity
# ---------------------------------------------------------------------------

class TestCoordBounds:
    def test_out_of_bounds_coord_raises_after_retries(self):
        c, oai = _make_client()
        oai.chat.completions.create.return_value = _make_response('{"x": 999, "y": 999}')
        # norm01k 999 → pixel_x = 999/1000*1920 = 1918 — within bounds,
        # so use truly OOB pixel space coords instead
        oai.chat.completions.create.return_value = _make_response('{"x": 2500, "y": 2500}')
        with patch("time.sleep"):
            with pytest.raises((VLMError, Exception)):
                c.localize(_fake_image(), "something", (1920, 1080))

    def test_valid_norm01k_coord_returns_pixel(self):
        c, oai = _make_client()
        oai.chat.completions.create.return_value = _make_response('{"x": 500, "y": 500}')
        with patch("time.sleep"):
            x, y = c.localize(_fake_image(), "element", (1920, 1080))
        assert x == 960 and y == 540


# ---------------------------------------------------------------------------
# C4: _call_with_failover
# ---------------------------------------------------------------------------

class TestCallWithFailover:
    def test_succeeds_on_first_try(self):
        c, oai = _make_client()
        expected = _make_response("yes")
        oai.chat.completions.create.return_value = expected
        result = c._call_with_failover("chat.completions.create", model="m", messages=[])
        assert result is expected

    def test_retries_on_connection_error(self):
        import openai as _oai
        c, oai = _make_client()
        good = _make_response("yes")
        conn_err = _oai.APIConnectionError.__new__(_oai.APIConnectionError)
        oai.chat.completions.create.side_effect = [conn_err, good]
        with patch("time.sleep"):
            result = c._call_with_failover("chat.completions.create", model="m", messages=[])
        assert result is good

    def test_exhausts_all_attempts_raises_vlm_error(self):
        import openai as _oai
        c, oai = _make_client()
        conn_err = _oai.APIConnectionError.__new__(_oai.APIConnectionError)
        oai.chat.completions.create.side_effect = conn_err
        with patch("time.sleep"):
            with pytest.raises(VLMError, match="exhausted"):
                c._call_with_failover("chat.completions.create", model="m", messages=[])

    def test_non_retryable_4xx_reraises_immediately(self):
        import openai as _oai
        c, oai = _make_client()
        try:
            err = _oai.APIStatusError(
                message="bad request",
                response=MagicMock(status_code=400),
                body={},
            )
        except Exception:
            # If real openai not installed, use our stub
            err = _oai.APIStatusError.__new__(_oai.APIStatusError)
            err.status_code = 400
        oai.chat.completions.create.side_effect = err
        with pytest.raises(Exception):
            c._call_with_failover("chat.completions.create", model="m", messages=[])


# ---------------------------------------------------------------------------
# C5: localize_consistent
# ---------------------------------------------------------------------------

class TestLocalizeConsistent:
    def test_tight_cluster_returns_centroid(self):
        c, oai = _make_client()
        oai.chat.completions.create.side_effect = [
            _make_response('{"x": 500, "y": 500}'),
            _make_response('{"x": 502, "y": 498}'),
            _make_response('{"x": 499, "y": 501}'),
        ]
        with patch("time.sleep"):
            x, y = c.localize_consistent(_fake_image(), "button", (1920, 1080))
        assert 950 <= x <= 970 and 530 <= y <= 550

    def test_scattered_cluster_raises_after_retry(self):
        c, oai = _make_client()
        oai.chat.completions.create.side_effect = [
            _make_response('{"x": 100, "y": 100}'),
            _make_response('{"x": 900, "y": 900}'),
            _make_response('{"x": 500, "y": 100}'),
            _make_response('{"x": 100, "y": 900}'),
            _make_response('{"x": 900, "y": 100}'),
            _make_response('{"x": 500, "y": 900}'),
        ]
        with patch("time.sleep"), patch.object(_client_mod, "logger"):
            with pytest.raises(VLMError, match="disagreement"):
                c.localize_consistent(_fake_image(), "button", (1920, 1080))


# ---------------------------------------------------------------------------
# _parse_coords unit tests
# ---------------------------------------------------------------------------

class TestParseCoords:
    def test_plain_json(self):
        r = _parse_coords('{"x": 300, "y": 400}')
        assert r["x"] == 300 and r["y"] == 400 and r["space"] == "norm01k"

    def test_fenced_block(self):
        r = _parse_coords('```json\n{"x": 200, "y": 300}\n```')
        assert r["x"] == 200

    def test_think_block_stripped(self):
        r = _parse_coords('<think>lots of thinking</think>{"x": 100, "y": 200}')
        assert r["x"] == 100

    def test_pixel_space_detected(self):
        r = _parse_coords('{"x": 1500, "y": 800}')
        assert r["space"] == "pixel"

    def test_bbox2d_format(self):
        r = _parse_coords('[{"bbox_2d": [100, 200, 300, 400], "label": "button"}]')
        assert r["x"] == 200 and r["y"] == 300

    def test_empty_string_raises(self):
        with pytest.raises(VLMError):
            _parse_coords("")


# ---------------------------------------------------------------------------
# H2.3: list_models
# ---------------------------------------------------------------------------

def _ensure_requests_stub():
    """Ensure a requests stub is in sys.modules and return (req_mod, ConnErr, TimeoutErr)."""
    import types as _types
    import sys as _sys

    if "requests" not in _sys.modules:
        _req = _types.ModuleType("requests")
        ConnErr = type("ConnectionError", (OSError,), {})
        TimeoutErr = type("Timeout", (OSError,), {})
        _exceptions = _types.ModuleType("requests.exceptions")
        _exceptions.ConnectionError = ConnErr
        _exceptions.Timeout = TimeoutErr
        _req.exceptions = _exceptions
        _req.get = MagicMock()
        _sys.modules["requests"] = _req
        _sys.modules["requests.exceptions"] = _exceptions

    import requests as _req_mod
    return _req_mod, _req_mod.exceptions.ConnectionError, _req_mod.exceptions.Timeout


class TestListModels:
    def test_returns_all_model_ids(self):
        """list_models() returns all model ids from /v1/models."""
        c, _ = _make_client()
        payload = {
            "data": [
                {"id": "qwen3.6-35b-a3b-uncensored-vl"},
                {"id": "carnice-27b"},
                {"id": "other-model"},
            ]
        }
        fake_resp = MagicMock()
        fake_resp.json.return_value = payload
        fake_resp.raise_for_status.return_value = None

        req_mod, _, _ = _ensure_requests_stub()
        with patch.object(req_mod, "get", return_value=fake_resp):
            result = c.list_models()
        assert result == ["qwen3.6-35b-a3b-uncensored-vl", "carnice-27b", "other-model"]

    def test_raises_on_connection_error(self):
        """list_models() raises VLMError when requests raises ConnectionError."""
        c, _ = _make_client()
        req_mod, ConnErr, _ = _ensure_requests_stub()
        with patch.object(req_mod, "get", side_effect=ConnErr("refused")):
            with pytest.raises(VLMError, match="connection failed"):
                c.list_models()

