"""Visual regression module — SSIM-based screenshot comparison.

Uses numpy (confirmed transitive dep) + Pillow for SSIM computation.
SSIM formula: (2*mu_x*mu_y + C1)(2*sigma_xy + C2) / ((mu_x^2 + mu_y^2 + C1)(sigma_x^2 + sigma_y^2 + C2))
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _load_gray_array(path: Path):
    """Load image as float32 grayscale numpy array in [0, 1]."""
    import numpy as np
    from PIL import Image

    img = Image.open(path).convert("L")
    return np.asarray(img, dtype=np.float32) / 255.0


def _resize_to_match(a, b):
    """Resize b to a's shape if they differ (nearest-neighbor, in numpy)."""
    import numpy as np
    from PIL import Image

    if a.shape == b.shape:
        return a, b
    # Resize b to match a using PIL then re-convert
    h, w = a.shape
    img_b = Image.fromarray((b * 255).astype("uint8"), mode="L")
    img_b = img_b.resize((w, h), Image.NEAREST)
    return a, np.asarray(img_b, dtype=np.float32) / 255.0


def compute_ssim(img_a: Path, img_b: Path) -> float:
    """Return SSIM in [0, 1] between two image files.

    Implements the single-scale Wang et al. SSIM using a uniform 8x8 window.
    Constants: K1=0.01, K2=0.03, L=1 (float image range).
    Tolerates size mismatches by downscaling the larger image.
    """
    import numpy as np

    a = _load_gray_array(img_a)
    b = _load_gray_array(img_b)
    a, b = _resize_to_match(a, b)

    K1, K2, L = 0.01, 0.03, 1.0
    C1 = (K1 * L) ** 2
    C2 = (K2 * L) ** 2

    win = 8  # window size; stride = win (non-overlapping patches)
    h, w = a.shape
    ssim_vals = []

    for y in range(0, h - win + 1, win):
        for x in range(0, w - win + 1, win):
            pa = a[y : y + win, x : x + win]
            pb = b[y : y + win, x : x + win]
            mu_a = pa.mean()
            mu_b = pb.mean()
            sigma_a2 = pa.var()
            sigma_b2 = pb.var()
            sigma_ab = float(np.mean((pa - mu_a) * (pb - mu_b)))
            num = (2 * mu_a * mu_b + C1) * (2 * sigma_ab + C2)
            den = (mu_a**2 + mu_b**2 + C1) * (sigma_a2 + sigma_b2 + C2)
            ssim_vals.append(num / den)

    if not ssim_vals:
        # Image smaller than one window — fall back to global stats
        mu_a, mu_b = a.mean(), b.mean()
        sigma_a2, sigma_b2 = a.var(), b.var()
        sigma_ab = float(np.mean((a - mu_a) * (b - mu_b)))
        num = (2 * mu_a * mu_b + C1) * (2 * sigma_ab + C2)
        den = (mu_a**2 + mu_b**2 + C1) * (sigma_a2 + sigma_b2 + C2)
        return float(num / den)

    return float(np.mean(ssim_vals))


def capture_reference(
    scenario: str,
    step_idx: int,
    screenshot: Path,
    refs_dir: Path,
) -> Path:
    """Copy screenshot into refs_dir/<scenario>/step_<idx>.png and return dest path."""
    dest_dir = refs_dir / scenario
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"step_{step_idx}.png"
    shutil.copy2(screenshot, dest)
    return dest


def check_against_reference(
    scenario: str,
    step_idx: int,
    screenshot: Path,
    refs_dir: Path,
    threshold: float = 0.95,
) -> tuple[bool, float]:
    """Compare screenshot to stored reference.

    Returns (passed, ssim_value).
    If no reference exists, raises FileNotFoundError.
    """
    ref = refs_dir / scenario / f"step_{step_idx}.png"
    if not ref.exists():
        raise FileNotFoundError(f"No reference for scenario={scenario!r} step={step_idx}: {ref}")
    score = compute_ssim(ref, screenshot)
    return score >= threshold, score


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cli(argv: list[str] | None = None) -> int:
    """Entrypoint for `mosdat visual ...`"""
    parser = argparse.ArgumentParser(
        prog="mosdat visual",
        description="Visual regression: capture or check step screenshots against references.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--capture",
        metavar="SCENARIO_DIR",
        help="Capture all *.png files in SCENARIO_DIR as references (sorted, 0-indexed steps).",
    )
    group.add_argument(
        "--check",
        metavar="SCENARIO_DIR",
        help="Check all *.png files in SCENARIO_DIR against stored references.",
    )
    parser.add_argument(
        "--refs-dir",
        metavar="DIR",
        default=None,
        help="Root directory for reference images (default: shared/references next to automation/).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.95,
        metavar="T",
        help="SSIM threshold [0,1] below which a check fails (default: 0.95).",
    )

    args = parser.parse_args(argv)

    # Resolve refs_dir: default to <project_root>/shared/references
    if args.refs_dir:
        refs_dir = Path(args.refs_dir)
    else:
        # automation/visual.py → project root is two levels up
        refs_dir = Path(__file__).resolve().parent.parent / "shared" / "references"

    scenario_dir = Path(args.capture or args.check)
    if not scenario_dir.exists():
        print(f"[visual] ERROR: scenario dir not found: {scenario_dir}", file=sys.stderr)
        return 1

    scenario_name = scenario_dir.name
    screenshots = sorted(scenario_dir.glob("*.png"))

    if not screenshots:
        print(f"[visual] No *.png files found in {scenario_dir}", file=sys.stderr)
        return 1

    if args.capture:
        for idx, shot in enumerate(screenshots):
            dest = capture_reference(scenario_name, idx, shot, refs_dir)
            print(f"[visual] Captured step {idx}: {dest}")
        print(f"[visual] Captured {len(screenshots)} reference(s) for scenario '{scenario_name}'.")
        return 0

    # --check
    failures = 0
    for idx, shot in enumerate(screenshots):
        try:
            passed, score = check_against_reference(
                scenario_name, idx, shot, refs_dir, threshold=args.threshold
            )
        except FileNotFoundError as e:
            print(f"[visual] MISSING ref step {idx}: {e}", file=sys.stderr)
            failures += 1
            continue
        status = "PASS" if passed else "FAIL"
        print(f"[visual] step {idx}: {status}  SSIM={score:.4f}  (threshold={args.threshold})")
        if not passed:
            failures += 1

    if failures:
        print(f"[visual] {failures}/{len(screenshots)} step(s) failed.", file=sys.stderr)
        return 1

    print(f"[visual] All {len(screenshots)} step(s) passed.")
    return 0
