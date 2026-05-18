---
date: "2026-05-18"
project: mosdat
topic: Recording frame filter dropped cursor motion (mean-pixel-diff averaged sparse signal to noise)
kind: war-story
scope: project-shared
confidence: high
---

`mosdat functional --record-gif` produced GIFs missing all cursor motion. raw=103, filtered=7. The "any frame with change" filter was dropping nearly every motion frame.

Root cause in `automation/recording/session_recorder.py`:
- Metric: `mean_abs_diff` on 64×64 grayscale thumbnail.
- A cursor moving 1-2 pixels on a 1280×720 frame downsamples to a sub-pixel change on the 64×64 thumb; bilinear blurs it to ~0.06 intensity mean.
- Default threshold 3.0. Mean 0.06 ≪ 3.0 → frame dropped.

Fix:
- Switch metric to `max_abs_diff` (`ImageStat.Stat(diff).extrema[0][1]`) — cursor lands as 3-6 px on a 256×256 thumb with max diff ~100+.
- Bump thumb resolution 64×64 → 256×256.
- Default threshold 3.0 → 1.0 (drop only pixel-identical frames).

Takeaway: for "did anything visible change" filters, use MAX-pixel-diff or count-of-changed-pixels above a tiny epsilon. MEAN averages sparse motion into noise floor. Fixed in commit `baab0c5`.
