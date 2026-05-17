"""cursor_motion.py — human-like cursor path generator for VNC pointer events.

Usage:
    from automation.transport.cursor_motion import generate_path

    for x, y, dt_ms in generate_path((cx, cy), (tx, ty)):
        vnc.move(x, y)
        time.sleep(dt_ms / 1000)
    vnc.click(tx, ty)
"""

from __future__ import annotations

import math
import os
import random
import time
from typing import Literal

Point = tuple[float, float]
Step = tuple[int, int, float]  # (x, y, dt_ms)

PROFILES = ("instant", "linear", "bezier", "windmouse")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_seed(seed: int | str) -> int:
    if seed == "auto":
        return os.getpid() ^ int(time.time() * 1000) & 0xFFFF
    if isinstance(seed, int):
        return seed
    if isinstance(seed, str):
        try:
            return int(seed)
        except ValueError:
            pass
    raise ValueError(f"Invalid seed: {seed!r}. Must be 'auto', an int, or a numeric string.")


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _bezier_sample(pts: list[Point], t: float) -> Point:
    """De Casteljau evaluation."""
    p = list(pts)
    while len(p) > 1:
        p = [(_lerp(p[i][0], p[i + 1][0], t),
              _lerp(p[i][1], p[i + 1][1], t)) for i in range(len(p) - 1)]
    return p[0]


def _ease_in_out(t: float) -> float:
    """Smooth-step: slow start, fast middle, slow end."""
    return t * t * (3 - 2 * t)


def _scaled_defaults(dist: float,
                     duration_ms: float | None,
                     frame_count: int | None) -> tuple[float, int]:
    if duration_ms is None:
        duration_ms = min(300.0, max(80.0, dist * 1.5))
    if frame_count is None:
        frame_count = max(8, min(16, int(dist / 8)))
    return duration_ms, frame_count


# ---------------------------------------------------------------------------
# Profile implementations
# ---------------------------------------------------------------------------

def _profile_instant(start: Point, end: Point) -> list[Step]:
    return [(int(round(end[0])), int(round(end[1])), 0.0)]


def _profile_linear(start: Point,
                    end: Point,
                    duration_ms: float,
                    frame_count: int,
                    emit_cap_ms: float) -> list[Step]:
    dt = duration_ms / frame_count
    dt = max(dt, emit_cap_ms)
    steps: list[Step] = []
    for i in range(frame_count + 1):
        t = i / frame_count
        x = _lerp(start[0], end[0], t)
        y = _lerp(start[1], end[1], t)
        steps.append((int(round(x)), int(round(y)), dt))
    # Pin last step exactly on end
    ex, ey = int(round(end[0])), int(round(end[1]))
    steps[-1] = (ex, ey, steps[-1][2])
    return steps


def _profile_bezier(start: Point,
                    end: Point,
                    duration_ms: float,
                    frame_count: int,
                    jitter_amplitude: float,
                    control_offset_ratio: float,
                    emit_cap_ms: float,
                    seed: int | str) -> list[Step]:
    rng = random.Random(_resolve_seed(seed))

    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy)

    if dist == 0:
        return [(int(round(end[0])), int(round(end[1])), 0.0)]

    # Perpendicular unit vector
    px, py = -dy / dist, dx / dist

    # One control point: offset perpendicular, near midpoint
    offset = rng.uniform(-1, 1) * control_offset_ratio * dist
    mid_t = rng.uniform(0.35, 0.65)
    mx = _lerp(start[0], end[0], mid_t) + px * offset
    my = _lerp(start[1], end[1], mid_t) + py * offset
    ctrl_pts = [start, (mx, my), end]

    # Cap frame_count to [8, 16]
    frame_count = max(8, min(16, frame_count))
    dt_ms = max(emit_cap_ms, duration_ms / frame_count)

    steps: list[Step] = []
    for i in range(frame_count + 1):
        t_raw = i / frame_count
        t = _ease_in_out(t_raw)
        x, y = _bezier_sample(ctrl_pts, t)
        # Jitter perpendicular to chord
        j = rng.gauss(0, jitter_amplitude / 3)
        x += px * j
        y += py * j
        steps.append((int(round(x)), int(round(y)), dt_ms))

    # Pin landing exactly
    ex, ey = int(round(end[0])), int(round(end[1]))
    steps[-1] = (ex, ey, steps[-1][2])
    return steps


def _profile_windmouse(start: Point,
                       end: Point,
                       emit_cap_ms: float,
                       duration_ms: float,
                       seed: int | str,
                       G0: float = 9.0,
                       W0: float = 3.0,
                       M0: float = 15.0,
                       D0: float = 12.0) -> list[Step]:
    rng = random.Random(_resolve_seed(seed))

    sqrt3 = math.sqrt(3)
    sqrt5 = math.sqrt(5)

    cx, cy = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    vx = vy = Wx = Wy = 0.0

    raw: list[tuple[int, int]] = []
    iterations = 0
    max_iter = 200

    while math.hypot(x1 - cx, y1 - cy) >= 1 and iterations < max_iter:
        dist_cur = math.hypot(x1 - cx, y1 - cy)
        W_mag = min(W0, dist_cur)
        m0 = M0  # local copy so we don't mutate the outer param each step
        if dist_cur >= D0:
            Wx = Wx / sqrt3 + (2 * rng.random() - 1) * W_mag / sqrt5
            Wy = Wy / sqrt3 + (2 * rng.random() - 1) * W_mag / sqrt5
        else:
            Wx /= sqrt3
            Wy /= sqrt3
            m0 = max(3.0, m0 / sqrt5)
        vx += Wx + G0 * (x1 - cx) / dist_cur
        vy += Wy + G0 * (y1 - cy) / dist_cur
        v = math.hypot(vx, vy)
        if v > m0:
            vx = vx / v * m0
            vy = vy / v * m0
        cx += vx
        cy += vy
        raw.append((int(round(cx)), int(round(cy))))
        iterations += 1

    # Guarantee landing on exact target
    raw.append((int(round(x1)), int(round(y1))))

    # Trim to fit duration_ms budget
    budget_steps = max(2, int(duration_ms / emit_cap_ms))
    if len(raw) > budget_steps:
        # Keep first + last + uniformly spaced subset in between
        indices = [0]
        inner = budget_steps - 2
        if inner > 0:
            for k in range(1, inner + 1):
                idx = int(round(k * (len(raw) - 1) / (inner + 1)))
                indices.append(idx)
        indices.append(len(raw) - 1)
        raw = [raw[i] for i in sorted(set(indices))]

    steps: list[Step] = [(x, y, emit_cap_ms) for x, y in raw]
    return steps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_path(
    start: Point,
    end: Point,
    *,
    profile: Literal["instant", "linear", "bezier", "windmouse"] = "bezier",
    duration_ms: float | None = None,
    frame_count: int | None = None,
    jitter_amplitude: float = 2.0,
    control_offset_ratio: float = 0.4,
    emit_cap_ms: float = 16.0,
    seed: int | str = "auto",
) -> list[Step]:
    """Return list of (x, y, dt_ms) steps from start to end.

    Caller:
        for x, y, dt in generate_path(start, end):
            vnc.move(x, y)
            time.sleep(dt / 1000)

    Profiles:
        instant   — single step, zero dt (current behaviour)
        linear    — straight line, constant dt
        bezier    — quadratic Bezier + perpendicular jitter (default)
        windmouse — physics gravity/wind model
    """
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile {profile!r}. Valid: {PROFILES}")

    if profile == "instant":
        return _profile_instant(start, end)

    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    dur, fc = _scaled_defaults(dist, duration_ms, frame_count)

    if profile == "linear":
        return _profile_linear(start, end, dur, fc, emit_cap_ms)

    if profile == "bezier":
        return _profile_bezier(
            start, end,
            duration_ms=dur,
            frame_count=fc,
            jitter_amplitude=jitter_amplitude,
            control_offset_ratio=control_offset_ratio,
            emit_cap_ms=emit_cap_ms,
            seed=seed,
        )

    if profile == "windmouse":
        return _profile_windmouse(
            start, end,
            emit_cap_ms=emit_cap_ms,
            duration_ms=dur,
            seed=seed,
        )

    raise ValueError(f"Unknown profile {profile!r}")  # pragma: no cover
