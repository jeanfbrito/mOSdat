"""Tests for automation/transport/cursor_motion.py"""
import math
import pytest

from automation.transport.cursor_motion import (
    PROFILES,
    generate_path,
    _resolve_seed,
)


# ---------------------------------------------------------------------------
# instant
# ---------------------------------------------------------------------------

def test_instant_returns_single_step():
    path = generate_path((10.0, 20.0), (110.0, 220.0), profile="instant")
    assert path == [(110, 220, 0.0)]


def test_instant_zero_distance():
    path = generate_path((5.0, 5.0), (5.0, 5.0), profile="instant")
    assert path == [(5, 5, 0.0)]


# ---------------------------------------------------------------------------
# linear
# ---------------------------------------------------------------------------

def test_linear_endpoints_pinned():
    path = generate_path((0.0, 0.0), (100.0, 0.0), profile="linear", frame_count=10)
    # First step should be close to start (t=0)
    x0, y0, _ = path[0]
    assert x0 == 0 and y0 == 0
    # Last step must be exactly end
    xl, yl, _ = path[-1]
    assert (xl, yl) == (100, 0)


def test_linear_frame_count_respected():
    fc = 12
    path = generate_path((0.0, 0.0), (100.0, 0.0), profile="linear", frame_count=fc,
                          duration_ms=200.0)
    # frame_count + 1 points (0 .. frame_count inclusive)
    assert len(path) == fc + 1


def test_linear_dt_constant():
    path = generate_path((0.0, 0.0), (80.0, 0.0), profile="linear",
                         frame_count=10, duration_ms=200.0, emit_cap_ms=0.0)
    dts = [step[2] for step in path]
    assert all(abs(dt - dts[0]) < 1e-9 for dt in dts)


def test_linear_emit_cap_applied():
    # When duration/frames < emit_cap, cap wins
    path = generate_path((0.0, 0.0), (80.0, 0.0), profile="linear",
                         frame_count=10, duration_ms=10.0, emit_cap_ms=20.0)
    dts = [step[2] for step in path]
    assert all(dt >= 20.0 for dt in dts)


# ---------------------------------------------------------------------------
# bezier
# ---------------------------------------------------------------------------

def test_bezier_endpoints_pinned():
    path = generate_path((0.0, 0.0), (100.0, 50.0), profile="bezier", seed=42)
    x0, y0, _ = path[0]
    assert (x0, y0) == (0, 0)
    xl, yl, _ = path[-1]
    assert (xl, yl) == (100, 50)


def test_bezier_seeded_reproducible():
    a = generate_path((0.0, 0.0), (200.0, 100.0), profile="bezier", seed=7)
    b = generate_path((0.0, 0.0), (200.0, 100.0), profile="bezier", seed=7)
    assert a == b


def test_bezier_different_seeds_differ():
    a = generate_path((0.0, 0.0), (200.0, 100.0), profile="bezier", seed=1)
    b = generate_path((0.0, 0.0), (200.0, 100.0), profile="bezier", seed=2)
    assert a != b


def test_bezier_distance_scaled_frames():
    # dist=80 → frame_count = max(8, min(16, int(80/8))) = 10 → path len 11
    path = generate_path((0.0, 0.0), (80.0, 0.0), profile="bezier", seed=1)
    assert 8 <= len(path) - 1 <= 16  # steps = frame_count+1


def test_bezier_distance_scaled_duration():
    path = generate_path((0.0, 0.0), (100.0, 0.0), profile="bezier", seed=1)
    total_dt = sum(s[2] for s in path)
    assert 80.0 <= total_dt <= 300.0 * 2  # generous upper bound due to emit_cap


def test_bezier_emit_cap_enforced():
    path = generate_path((0.0, 0.0), (200.0, 100.0), profile="bezier",
                         emit_cap_ms=16.0, seed=5)
    # All dt_ms >= 16.0
    dts = [s[2] for s in path]
    assert all(dt >= 16.0 for dt in dts), f"Some dt < 16.0: {dts}"


def test_bezier_monotonic_toward_target():
    end = (200.0, 100.0)
    path = generate_path((0.0, 0.0), end, profile="bezier", seed=3)
    dists = [math.hypot(x - end[0], y - end[1]) for x, y, _ in path]
    # Allow up to 30% of steps to be non-monotonic (jitter)
    non_mono = sum(1 for i in range(1, len(dists)) if dists[i] > dists[i - 1])
    assert non_mono <= len(dists) * 0.30


def test_bezier_zero_distance_returns_endpoint():
    path = generate_path((50.0, 75.0), (50.0, 75.0), profile="bezier", seed=1)
    assert len(path) == 1
    assert path[0][:2] == (50, 75)


# ---------------------------------------------------------------------------
# windmouse
# ---------------------------------------------------------------------------

def test_windmouse_endpoints_pinned():
    path = generate_path((0.0, 0.0), (150.0, 80.0), profile="windmouse", seed=42)
    xl, yl, _ = path[-1]
    assert (xl, yl) == (150, 80)


def test_windmouse_converges_in_bounded_iterations():
    path = generate_path((0.0, 0.0), (300.0, 150.0), profile="windmouse", seed=99)
    # Must stay under 200 raw iterations (budget trim may reduce further)
    assert len(path) < 200


def test_windmouse_seeded_reproducible():
    a = generate_path((0.0, 0.0), (200.0, 100.0), profile="windmouse", seed=7)
    b = generate_path((0.0, 0.0), (200.0, 100.0), profile="windmouse", seed=7)
    assert a == b


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_unknown_profile_rejected():
    with pytest.raises(ValueError, match="Unknown profile"):
        generate_path((0.0, 0.0), (100.0, 100.0), profile="magic")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Seed resolution
# ---------------------------------------------------------------------------

def test_auto_seed_resolves_to_int():
    result = _resolve_seed("auto")
    assert isinstance(result, int)


def test_numeric_string_seed_accepted():
    result = _resolve_seed("12345")
    assert result == 12345


def test_invalid_seed_raises():
    with pytest.raises(ValueError):
        _resolve_seed("notanumber")
