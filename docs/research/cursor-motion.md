# Cursor Motion — Research Summary (for M2)

## Goal

Replace the current teleport (`vnc.move(x, y)` once) with a short burst of
pointer events that traces a plausible curved path. Goal: hover handlers fire
and transient popups don't auto-dismiss between sequential clicks. Anti-cheat
realism is NOT a goal. Moves must complete in ≤ 300 ms and emit ≤ 60 events
(16 ms minimum inter-event gap). The call site is `InputInjector.move(x, y)`
in `automation/vlm/input.py`, which forwards to `VncClient.move(x, y)` →
`_pointer_event(x, y, mask)` per step.

---

## Pattern Survey

### 1. WindMouse

**Source:** ben.land (2021-04-25) — https://ben.land/post/2021/04/25/windmouse-human-mouse-movement/
Python library: https://github.com/AsfhtgkDavid/windmouse (GPLv3)
Original SRL Pascal: circa 2009–2010 (RuneScape botting community, SRL forum — URL no longer canonical).

**Algorithm:**

The cursor is modelled as a particle with velocity acted on by two forces:
- **Gravity** — constant magnitude G₀, always points from current position to destination.
- **Wind** — stochastic lateral force; magnitude fluctuates, smoothly changes direction.

**Pseudocode (Python-ish, ~40 lines):**

```python
import math, random

sqrt3 = math.sqrt(3)
sqrt5 = math.sqrt(5)

def wind_mouse(x0, y0, x1, y1,
               G0=9, W0=3, M0=15, D0=12,
               emit=None):
    """
    emit(x, y) called for each intermediate point.
    G0  gravity magnitude (pull toward target)
    W0  wind fluctuation magnitude
    M0  max step size (velocity clip)
    D0  distance threshold: below this, wind is damped only (no new random)
    """
    cx, cy = float(x0), float(y0)
    vx = vy = Wx = Wy = 0.0
    while (dist := math.hypot(x1-cx, y1-cy)) >= 1:
        W_mag = min(W0, dist)          # cap wind near target
        if dist >= D0:                 # far: add random wind component
            Wx = Wx/sqrt3 + (2*random.random()-1)*W_mag/sqrt5
            Wy = Wy/sqrt3 + (2*random.random()-1)*W_mag/sqrt5
        else:                          # close: damp only, no new random
            Wx /= sqrt3
            Wy /= sqrt3
            M0 = max(3.0, M0/sqrt5)   # slow down approaching target
        # gravity: constant pull toward dest
        vx += Wx + G0*(x1-cx)/dist
        vy += Wy + G0*(y1-cy)/dist
        # clip velocity
        v = math.hypot(vx, vy)
        if v > M0:
            vx = vx/v * M0
            vy = vy/v * M0
        cx += vx
        cy += vy
        if emit:
            emit(round(cx), round(cy))
    if emit:
        emit(round(x1), round(y1))    # guarantee landing on exact target
```

**Parameters:**

| Name | Default | Meaning |
|---|---|---|
| `G0` | 9 | Gravity magnitude — how strongly cursor is pulled toward target |
| `W0` | 3 | Wind fluctuation magnitude — lateral randomness |
| `M0` | 15 | Max step size (velocity clip) — caps speed |
| `D0` | 12 | Distance below which wind switches from random to damped-only |

**Pros for mOSdat:** Physics-plausible curved paths with speed variation; naturally slows near target; step count distance-proportional.
**Cons for mOSdat:** Step count unpredictable; needs bounds clamping; more complex than needed.

---

### 2. Bezier with Jitter (ghost-cursor / pyclick style) — RECOMMENDED

**Sources:**
- ghost-cursor JS: https://github.com/Xetera/ghost-cursor
- pyclick Python: https://github.com/patrikoss/pyclick (`pyclick/humancurve.py`)

**Algorithm:**

Generate a cubic (or higher-order) Bezier curve between start and end with 1–2
randomly placed control points offset perpendicularly to the line. Sample the
curve at `N` evenly-spaced `t` values. Optionally apply:
1. **Distortion** — add gaussian noise perpendicular to each sample point.
2. **Tween** — remap the `t` progression through an easing function (ease-in/out).

**pyclick-style Python snippet (~55 lines):**

```python
import math, random
from typing import Callable

Point = tuple[float, float]

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

def _bezier_sample(pts: list[Point], t: float) -> Point:
    """De Casteljau evaluation."""
    p = list(pts)
    while len(p) > 1:
        p = [(_lerp(p[i][0], p[i+1][0], t),
              _lerp(p[i][1], p[i+1][1], t)) for i in range(len(p)-1)]
    return p[0]

def _ease_in_out(t: float) -> float:
    """Smooth-step: slow start, fast middle, slow end."""
    return t * t * (3 - 2 * t)

def generate_bezier_path(
    start: Point,
    end: Point,
    *,
    frame_count: int = 12,
    control_offset_ratio: float = 0.4,
    jitter_amplitude: float = 2.0,
    tween: Callable[[float], float] = _ease_in_out,
    seed: int | None = None,
) -> list[Point]:
    rng = random.Random(seed)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy)
    # perpendicular unit vector
    if dist > 0:
        px, py = -dy/dist, dx/dist
    else:
        return [start, end]
    # one control point: offset perpendicular, slightly past midpoint
    offset = rng.uniform(-1, 1) * control_offset_ratio * dist
    mid_t  = rng.uniform(0.35, 0.65)
    mx = _lerp(start[0], end[0], mid_t) + px * offset
    my = _lerp(start[1], end[1], mid_t) + py * offset
    ctrl_pts = [start, (mx, my), end]
    # sample
    path = []
    for i in range(frame_count + 1):
        t_raw = i / frame_count
        t = tween(t_raw)
        x, y = _bezier_sample(ctrl_pts, t)
        # jitter perpendicular to chord (not along motion — avoids overshoot)
        j = rng.gauss(0, jitter_amplitude / 3)
        x += px * j
        y += py * j
        path.append((x, y))
    path[-1] = end   # pin landing point exactly
    return path
```

**Parameters:**

| Name | Default | Meaning |
|---|---|---|
| `frame_count` | 12 | Number of pointer events emitted |
| `control_offset_ratio` | 0.4 | Control point deviation as fraction of total distance |
| `jitter_amplitude` | 2.0 px | Gaussian noise perpendicular to chord (sigma = amp/3) |
| `tween` | `_ease_in_out` | Remap t → t′; controls acceleration profile |
| `seed` | None (random) | Reproducibility for debugging |

**ghost-cursor additions** (not needed for mOSdat): Fitts's Law speed scaling; automatic overshoot + correction at distances > 500px.

**pyclick additions** (optional): `distortionMean`, `distortionStdev`, `distortionFrequency` — gaussian bumps at random intervals along the curve; `knotsCount` — adds internal knot points for multi-segment curves.

**Pros for mOSdat:** Deterministic frame count → easy 60fps budget; simple auditable code (~50 lines, no deps); naturally smooth + just-enough organic feel; hover events fire well before final position.
**Cons:** Single control point may look mechanical on long moves (>200px); fix with second control point or `knotsCount=2`.

---

### 3. PyAutoGUI Tweens

**Source:** https://pyautogui.readthedocs.io/en/latest/mouse.html

PyAutoGUI supports `moveTo(x, y, duration=1.0, tween=pyautogui.easeOutQuad)`.
The tween is a function `f(t: float) -> float` mapping 0..1 → 0..1.
Available: `linear`, `easeInQuad`, `easeOutQuad`, `easeInOutQuad`, `easeInBounce`, `easeInElastic`, `easeOutElastic`.

Movement is strictly along the straight line from start to end — no curve deviation, no jitter.

**Pros:** Trivial to implement.
**Cons for mOSdat:** mOSdat uses RFB `_pointer_event`, not PyAutoGUI; importing it would add OS-level dependency (PyAutoGUI moves host cursor). Straight-line + easing alone is enough to trigger hover handlers but doesn't prevent tooltip dismissal on direction changes — the curve is what matters.

---

### 4. Minimum-Jerk Trajectory

**Source:** Flash & Hogan (1985), J. Neuroscience. https://pubmed.ncbi.nlm.nih.gov/22137550/

Fifth-order polynomial minimises integral of jerk² (rate of change of
acceleration) over the motion interval. Produces bell-shaped velocity profile.
Formula: `pos(t) = 10t³ - 15t⁴ + 6t⁵` for t ∈ [0,1] (normalised).

**Why not using it for mOSdat:**
Too smooth. Real human movements have micro-corrections and slight curve deviations; minimum-jerk by itself produces uncannily uniform path. No curve offset, so hover triggers on straight line only. Adding noise on top reinvents Bezier-with-jitter. Smooth-step tween already approximates the bell-shaped profile.

---

## Recommendation for mOSdat

**Pick: Bezier with jitter (`profile="bezier"`).**

Rationale: deterministic frame count, trivial to audit, no external deps, handles the actual problem (hover before click, no teleport) without over-engineering.

| Parameter | Default | Notes |
|---|---|---|
| `profile` | `"bezier"` | Also `"instant"` (current), `"linear"`, `"windmouse"` (optional) |
| `duration_ms` | `max(80, dist * 1.5)` capped at 300 | Scale with distance; 100px → ~150ms |
| `frame_count` | `max(8, min(16, int(dist / 8)))` | Distance-scaled; 8 min, 16 max |
| `emit_cap_ms` | 16 | 60fps ceiling; sleep between events |
| `jitter_amplitude` | 2 px | Gaussian, perpendicular to chord |
| `control_offset_ratio` | 0.4 | Lateral Bezier deviation |
| `seed` | `"auto"` → `os.getpid() ^ int(time.time() * 1000) & 0xFFFF` | Deterministic for CI replay |

---

## Reference Implementations (Links)

- ghost-cursor: https://github.com/Xetera/ghost-cursor
- pyclick humancurve.py: https://github.com/patrikoss/pyclick/blob/master/pyclick/humancurve.py
- pyclick humanclicker.py: https://github.com/patrikoss/pyclick/blob/master/pyclick/humanclicker.py
- WindMouse (ben.land): https://ben.land/post/2021/04/25/windmouse-human-mouse-movement/
- WindMouse Python library: https://github.com/AsfhtgkDavid/windmouse
- PyAutoGUI mouse docs: https://pyautogui.readthedocs.io/en/latest/mouse.html

---

## API Surface Proposal for `automation/transport/cursor_motion.py`

```python
"""cursor_motion.py — human-like cursor path generator for VNC pointer events.

Usage:
    from automation.transport.cursor_motion import generate_path

    for x, y, dt_ms in generate_path((cx, cy), (tx, ty)):
        vnc.move(x, y)
        time.sleep(dt_ms / 1000)
    vnc.click(tx, ty)
"""

from __future__ import annotations
import math, os, random, time
from typing import Callable

Point = tuple[float, float]
Step  = tuple[int, int, float]   # (x, y, dt_ms)

PROFILES = ("instant", "linear", "bezier", "windmouse")


def generate_path(
    start: Point,
    end:   Point,
    *,
    profile:              str   = "bezier",
    duration_ms:          float | None = None,   # None → distance-scaled
    frame_count:          int   | None = None,   # None → distance-scaled
    jitter_amplitude:     float = 2.0,
    control_offset_ratio: float = 0.4,
    emit_cap_ms:          float = 16.0,
    seed:                 int | str = "auto",
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
        windmouse — physics gravity/wind model (more steps, less predictable)
    """
    ...
```

Internal helpers (private):
- `_profile_instant(start, end) -> list[Step]`
- `_profile_linear(start, end, duration_ms, frame_count, emit_cap_ms) -> list[Step]`
- `_profile_bezier(start, end, duration_ms, frame_count, jitter_amplitude, control_offset_ratio, tween, seed) -> list[Step]`
- `_profile_windmouse(start, end, G0, W0, M0, D0, emit_cap_ms, seed) -> list[Step]`
- `_ease_in_out(t) -> float`
- `_bezier_sample(pts, t) -> Point`
- `_resolve_seed(seed) -> int`

**Integration point:** `InputInjector.move_smooth(x, y, **kw)` in `automation/vlm/input.py` calls `generate_path((self._cx, self._cy), (x, y), **kw)` then drives `self.vnc.move` + `time.sleep` in a loop. `InputInjector` must track `_cx, _cy` (current cursor position) to pass a valid start. Alternatively `move_smooth` accepts an explicit `start` kwarg.
