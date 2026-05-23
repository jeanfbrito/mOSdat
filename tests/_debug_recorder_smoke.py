"""Synthetic smoke for SessionRecorder. Not collected by pytest (leading underscore).

Spawns a stub screenshotter that produces frames at 10 FPS for ~5 seconds:
- Many static frames (white background, fixed UI mock).
- Occasional motion (a small rectangle moves every ~0.3 s).
- One bigger change (text label flips) at the midpoint.

Exits with the manifest path printed so caller can inspect ratio + open MP4.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.recording.session_recorder import SessionRecorder  # noqa: E402


class StubScreenshotter:
    """Produces 1280x720 mock frames. Mostly static, occasional motion."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self._lock = threading.Lock()

    def capture(self) -> tuple[Image.Image, tuple[int, int]]:
        with self._lock:
            elapsed = time.monotonic() - self._t0
            img = Image.new("RGB", (1280, 720), color=(245, 245, 250))
            draw = ImageDraw.Draw(img)
            draw.rectangle((40, 40, 1240, 100), fill=(30, 90, 200))
            draw.rectangle((40, 130, 1240, 680), outline=(180, 180, 190), width=2)

            label = "Phase A" if elapsed < 2.5 else "Phase B"
            draw.text((60, 60), label, fill=(255, 255, 255))

            tick = int(elapsed * 3)
            x = 80 + (tick * 40) % 1000
            draw.rectangle((x, 300, x + 80, 380), fill=(220, 60, 60))

            return img, (1280, 720)


def main() -> int:
    out_dir = ROOT / "tests" / "_debug_recording_out"
    if out_dir.exists():
        import shutil
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    rec = SessionRecorder(
        screenshotter=StubScreenshotter(),
        recording_dir=out_dir,
        fps=10.0,
        log_fn=lambda m: print(f"[rec] {m}"),
    )

    rec.start()
    time.sleep(5.0)
    art = rec.stop_and_export(make_gif=False)

    print("---")
    print(f"raw_frames     : {art.raw_frames}")
    print(f"filtered_frames: {art.filtered_frames}")
    print(f"keep_ratio     : {art.filtered_frames}/{art.raw_frames}"
          f" = {(art.filtered_frames / art.raw_frames * 100):.1f}%"
          if art.raw_frames else "n/a")
    print(f"manifest       : {art.manifest_path}")
    print(f"mp4            : {art.mp4_path}")
    if art.warning:
        print(f"WARNING        : {art.warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
