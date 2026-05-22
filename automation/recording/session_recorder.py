"""Background VNC session recorder with post-run change-frame filtering.

Stage 3a — Window/cursor state bundling (opt-in via `record_window_state=True`):

When the recorder is constructed with a live `ssh` SSHClient AND
`record_window_state=True`, a low-frequency sampler thread (default 5 Hz)
polls `wmctrl -l`, `xdotool getactivewindow getwindowname`, and
`xdotool getmouselocation --shell` on the VM via single SSH round-trips.
The most recent sample is stored under a `threading.Lock` and read
(non-blocking) by the per-frame `_append_index` writer.

This keeps the per-frame VNC capture loop latency-independent of SSH:
captures continue at full requested fps; window/cursor metadata lags by up
to one sampler period (~200 ms). The sampler thread is joined cleanly on
`stop_and_export()`. If the remote commands fail (missing wmctrl/xdotool,
no DISPLAY), the sampler logs once and disables itself for the rest of the
session — the recording succeeds, the optional metadata is just absent.

Backward compat: when the feature is OFF (default), no SSH calls happen and
`index.jsonl` lines retain the historical `{frame, ts}` schema verbatim.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import xxhash

from PIL import Image

from .frame_bus import LatestFrameBus


@dataclass
class RecordingArtifacts:
    mp4_path: Optional[Path]
    gif_path: Optional[Path]
    filtered_dir: Path
    manifest_path: Path
    raw_frames: int
    filtered_frames: int
    warning: Optional[str] = None


class SessionRecorder:
    """Capture frames in a background loop, then export compact replay artifacts."""

    def __init__(
        self,
        screenshotter,
        recording_dir: Path,
        fps: float = 10.0,
        keep_raw: bool = False,
        log_fn: Optional[Callable[[str], None]] = None,
        ssh: Optional[Any] = None,
        record_window_state: bool = False,
        window_state_sample_hz: float = 5.0,
    ) -> None:
        self.screenshotter = screenshotter
        self.recording_dir = Path(recording_dir)
        self.raw_dir = self.recording_dir / "raw"
        self.filtered_dir = self.recording_dir / "filtered"
        self.index_path = self.recording_dir / "index.jsonl"
        self.manifest_path = self.recording_dir / "manifest.json"
        self.fps = max(1.0, float(fps))
        self.interval = 1.0 / self.fps
        self.keep_raw = keep_raw
        self._log = log_fn or (lambda _msg: None)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._frame_id = 0
        self._capture_errors = 0
        self._started_at: Optional[str] = None
        self._ended_at: Optional[str] = None
        self._bus: Optional[LatestFrameBus] = None
        # Stage 3a: optional window/cursor state sampler.
        self._ssh = ssh
        self._record_window_state = bool(record_window_state) and ssh is not None
        self._sample_interval = 1.0 / max(0.5, float(window_state_sample_hz))
        self._sampler_thread: Optional[threading.Thread] = None
        self._sampler_stop = threading.Event()
        self._sample_lock = threading.Lock()
        self._latest_sample: dict[str, Any] = {}
        self._sampler_disabled = False  # set after first failure to skip subsequent polls

    def start(self) -> None:
        if self._running:
            return
        self.recording_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.filtered_dir.mkdir(parents=True, exist_ok=True)
        if self.index_path.exists():
            self.index_path.unlink()
        self._stop.clear()
        self._frame_id = 0
        self._capture_errors = 0
        self._started_at = datetime.now().isoformat()
        self._bus = LatestFrameBus()
        if hasattr(self.screenshotter, "attach_bus"):
            self.screenshotter.attach_bus(self._bus)
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._running = True
        self._thread.start()
        if self._record_window_state:
            self._sampler_stop.clear()
            self._sampler_disabled = False
            with self._sample_lock:
                self._latest_sample = {}
            self._sampler_thread = threading.Thread(
                target=self._sampler_loop, daemon=True
            )
            self._sampler_thread.start()
            self._log(
                f"recorder started ({self.fps:.2f} FPS, window-state sampler "
                f"{1.0 / self._sample_interval:.1f} Hz)"
            )
        else:
            self._log(f"recorder started ({self.fps:.2f} FPS)")

    def stop_and_export(self, make_gif: bool = False) -> RecordingArtifacts:
        self._stop_capture()
        raw_frames = sorted(self.raw_dir.glob("frame_*.png"))
        kept_frames = self._filter_and_copy(raw_frames)
        warning = None

        mp4_path = self.recording_dir / "session.mp4"
        gif_path = self.recording_dir / "session.gif" if make_gif else None
        exported_mp4 = None
        exported_gif = None

        if kept_frames:
            mp4_err = self._export_mp4(mp4_path, kept_frames)
            if mp4_err:
                warning = mp4_err
            else:
                exported_mp4 = mp4_path

            if make_gif:
                gif_err = self._export_gif(gif_path, kept_frames)
                if gif_err and warning is None:
                    warning = gif_err
                elif not gif_err:
                    exported_gif = gif_path
        else:
            warning = "no frames captured"

        self._write_manifest(
            raw_count=len(raw_frames),
            filtered_count=len(kept_frames),
            mp4_path=exported_mp4,
            gif_path=exported_gif,
            warning=warning,
        )

        if not self.keep_raw and self.raw_dir.exists():
            shutil.rmtree(self.raw_dir, ignore_errors=True)

        if warning:
            self._log(f"recorder warning: {warning}")
        else:
            self._log(
                f"recorder exported {len(kept_frames)} frames"
                + (f" -> {exported_mp4}" if exported_mp4 else "")
            )

        return RecordingArtifacts(
            mp4_path=exported_mp4,
            gif_path=exported_gif,
            filtered_dir=self.filtered_dir,
            manifest_path=self.manifest_path,
            raw_frames=len(raw_frames),
            filtered_frames=len(kept_frames),
            warning=warning,
        )

    def _stop_capture(self) -> None:
        if not self._running:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        # Stage 3a: stop the window-state sampler cleanly. Join with a bounded
        # timeout — a misbehaving SSH call must not hold up scenario teardown.
        if self._sampler_thread is not None:
            self._sampler_stop.set()
            self._sampler_thread.join(timeout=5)
            self._sampler_thread = None
        if hasattr(self.screenshotter, "detach_bus"):
            self.screenshotter.detach_bus()
        self._bus = None
        self._ended_at = datetime.now().isoformat()
        self._running = False
        self._log("recorder stopped")

    def _capture_loop(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now < next_tick:
                self._stop.wait(min(next_tick - now, 0.05))
                continue
            next_tick = now + self.interval
            try:
                # Use _capture_vnc_direct when available so the recorder loop
                # (the bus producer) never reads from the bus it writes to.
                _do_capture = getattr(
                    self.screenshotter, "_capture_vnc_direct", self.screenshotter.capture
                )
                img, _ = _do_capture()
                capture_time = time.monotonic()
                self._frame_id += 1
                frame_name = f"frame_{self._frame_id:06d}.png"
                frame_path = self.raw_dir / frame_name
                img.save(frame_path)
                self._append_index(frame_name)
                if self._bus is not None:
                    self._bus.push(img, capture_time)
            except Exception as exc:
                self._capture_errors += 1
                if self._capture_errors == 1 or self._capture_errors % 10 == 0:
                    self._log(f"capture error #{self._capture_errors}: {exc}")

    def _append_index(self, frame_name: str) -> None:
        record: dict[str, Any] = {
            "frame": frame_name,
            "ts": datetime.now().isoformat(),
        }
        # Stage 3a: always carry ns-precise timestamp when the sampler is on,
        # so post-mortem tooling can sequence frames with sub-ms granularity.
        if self._record_window_state:
            record["timestamp_ns"] = time.time_ns()
            with self._sample_lock:
                sample = dict(self._latest_sample)
            # Only inject keys that were successfully captured. A missing
            # sample (sampler hasn't produced one yet, or SSH call failed)
            # leaves the line at the legacy `{frame, ts, timestamp_ns}` shape
            # — readers tolerate the absence.
            for key in ("active_window", "open_windows", "cursor_x", "cursor_y"):
                if key in sample:
                    record[key] = sample[key]
        with self.index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    # ------------------------------------------------------------------
    # Stage 3a: window/cursor state sampler
    # ------------------------------------------------------------------

    def _sampler_loop(self) -> None:
        """Poll wmctrl/xdotool on the VM at low frequency; publish latest sample.

        Decoupled from the capture loop so per-frame writes never block on
        SSH round-trip latency. The sample is bundled into a single SSH call
        per tick to keep startup cost amortized.
        """
        while not self._sampler_stop.is_set():
            if self._sampler_disabled:
                # Permanent skip: VM lacks wmctrl/xdotool or DISPLAY is missing.
                # Sleep on the stop event so shutdown still terminates promptly.
                self._sampler_stop.wait(self._sample_interval)
                continue
            sample = self._collect_window_state()
            if sample is not None:
                with self._sample_lock:
                    self._latest_sample = sample
            self._sampler_stop.wait(self._sample_interval)

    def _collect_window_state(self) -> Optional[dict[str, Any]]:
        """One SSH call → parsed wmctrl + xdotool blob, or None on failure.

        On the first failure, sets `_sampler_disabled` and logs once. Subsequent
        ticks short-circuit (the recording itself is not impacted).
        """
        if self._ssh is None:
            return None
        # Single shell script. Newline-delimited sections so we can parse
        # without depending on JSON being installed on the VM.
        # X11 preamble: SSH sessions have no DISPLAY/XAUTHORITY by default;
        # mutter-Xwayland writes its auth cookie to /run/user/1000/. XAUTH
        # may be absent on non-mutter sessions — DISPLAY=:0 alone may still
        # work, so don't abort on missing file.
        script = (
            "XAUTH=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1); "
            "export DISPLAY=:0 XAUTHORITY=\"$XAUTH\"; "
            "echo '<<<ACTIVE>>>'; "
            "xdotool getactivewindow getwindowname 2>/dev/null || true; "
            "echo '<<<WINDOWS>>>'; "
            "wmctrl -l 2>/dev/null || true; "
            "echo '<<<CURSOR>>>'; "
            "xdotool getmouselocation --shell 2>/dev/null || true; "
            "echo '<<<END>>>'"
        )
        try:
            result = self._ssh.run(script, timeout=3)
        except Exception as exc:  # SSH layer should not raise, but be defensive.
            self._sampler_disabled = True
            self._log(f"recorder window-state sampler disabled (ssh error: {exc})")
            return None
        if not getattr(result, "success", False):
            self._sampler_disabled = True
            stderr = (getattr(result, "stderr", "") or "").strip().splitlines()
            tail = stderr[-1] if stderr else f"rc={getattr(result, 'returncode', '?')}"
            self._log(f"recorder window-state sampler disabled ({tail})")
            return None
        parsed = self._parse_window_state(result.stdout or "")
        # If neither wmctrl nor xdotool produced anything, the markers are
        # present but the sections are empty → tools are missing. Disable.
        if not parsed:
            self._sampler_disabled = True
            self._log(
                "recorder window-state sampler disabled "
                "(wmctrl/xdotool produced no output)"
            )
            return None
        return parsed

    @staticmethod
    def _parse_window_state(blob: str) -> dict[str, Any]:
        """Parse the marker-delimited shell output into a sample dict.

        Returns {} if none of the four pieces could be extracted.
        """
        sections: dict[str, list[str]] = {"ACTIVE": [], "WINDOWS": [], "CURSOR": []}
        current: Optional[str] = None
        for raw_line in blob.splitlines():
            line = raw_line.rstrip("\r")
            if line == "<<<ACTIVE>>>":
                current = "ACTIVE"; continue
            if line == "<<<WINDOWS>>>":
                current = "WINDOWS"; continue
            if line == "<<<CURSOR>>>":
                current = "CURSOR"; continue
            if line == "<<<END>>>":
                current = None; continue
            if current is not None and line != "":
                sections[current].append(line)

        out: dict[str, Any] = {}

        # Active window: xdotool getactivewindow getwindowname → exact title.
        if sections["ACTIVE"]:
            out["active_window"] = sections["ACTIVE"][0]

        # Open windows: `wmctrl -l` lines look like
        #   "0x04200007  0 hostname Window Title Here"
        # Drop window-id (col 0), desktop (col 1), client-machine (col 2);
        # keep the rest as the title. Cap to 20 entries.
        if sections["WINDOWS"]:
            titles: list[str] = []
            for line in sections["WINDOWS"]:
                parts = line.split(None, 3)
                if len(parts) >= 4:
                    titles.append(parts[3])
                elif len(parts) == 3:
                    titles.append("")  # window with empty title
            if titles:
                out["open_windows"] = titles[:20]

        # Cursor: xdotool --shell outputs `X=123\nY=456\nSCREEN=0\nWINDOW=...`
        if sections["CURSOR"]:
            for line in sections["CURSOR"]:
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key == "X":
                    try:
                        out["cursor_x"] = int(val)
                    except ValueError:
                        pass
                elif key == "Y":
                    try:
                        out["cursor_y"] = int(val)
                    except ValueError:
                        pass

        return out

    def _filter_and_copy(self, raw_frames: list[Path]) -> list[Path]:
        if self.filtered_dir.exists():
            for p in self.filtered_dir.glob("frame_*.png"):
                p.unlink()
        self.filtered_dir.mkdir(parents=True, exist_ok=True)

        if not raw_frames:
            return []
        ts_by_name = self._load_index_timestamps()
        if len(raw_frames) == 1:
            dest = self.filtered_dir / "frame_000001.png"
            shutil.copy2(raw_frames[0], dest)
            ts = ts_by_name.get(raw_frames[0].name)
            if ts is not None:
                os.utime(dest, (ts, ts))
            return [dest]

        last_idx = len(raw_frames) - 1
        last_kept_hash: Optional[int] = None
        kept = []
        out_num = 1
        for idx, src in enumerate(raw_frames):
            with Image.open(src) as im:
                h = xxhash.xxh3_64_intdigest(im.tobytes())
            is_first = idx == 0
            is_last = idx == last_idx
            if is_first or is_last or h != last_kept_hash:
                dest = self.filtered_dir / f"frame_{out_num:06d}.png"
                shutil.copy2(src, dest)
                ts = ts_by_name.get(src.name)
                if ts is not None:
                    os.utime(dest, (ts, ts))
                kept.append(dest)
                out_num += 1
                last_kept_hash = h
        return kept

    def _load_index_timestamps(self) -> dict[str, float]:
        out: dict[str, float] = {}
        if not self.index_path.exists():
            return out
        try:
            for raw_line in self.index_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                name = rec.get("frame")
                ts_raw = rec.get("ts")
                if not isinstance(name, str) or not isinstance(ts_raw, str):
                    continue
                try:
                    out[name] = datetime.fromisoformat(ts_raw).timestamp()
                except ValueError:
                    continue
        except OSError:
            return {}
        return out

    @staticmethod
    def _has_ffmpeg() -> bool:
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def _build_concat_file(self, kept_frames: list[Path]) -> Path:
        concat_path = self.filtered_dir / "_concat.txt"
        lines: list[str] = []

        if len(kept_frames) == 1:
            lines.append(f"file '{kept_frames[0].name}'")
            lines.append("duration 2.0")
            lines.append(f"file '{kept_frames[0].name}'")
            concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return concat_path

        frame_times = [p.stat().st_mtime for p in kept_frames]
        durations = []
        # Hold cap = 1.0s: xxh3 dedupe already collapsed identical frames,
        # so any gap >1s means "screen was idle that long" — no info gain
        # from stretching replay to match real wall-clock idle stretches.
        for idx in range(len(frame_times) - 1):
            dt = frame_times[idx + 1] - frame_times[idx]
            durations.append(min(1.0, max(1.0 / self.fps, dt)))
        tail = max(0.8, min(1.0, (sum(durations) / len(durations)) if durations else 1.0))
        durations.append(tail)

        total = sum(durations)
        if total < 2.0 and total > 0:
            scale = 2.0 / total
            durations = [d * scale for d in durations]

        for idx, frame in enumerate(kept_frames):
            lines.append(f"file '{frame.name}'")
            lines.append(f"duration {durations[idx]:.3f}")
        lines.append(f"file '{kept_frames[-1].name}'")

        concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return concat_path

    def _export_mp4(self, output_path: Path, kept_frames: list[Path]) -> Optional[str]:
        if not self._has_ffmpeg():
            return "ffmpeg not found; skipped MP4 export"
        concat_path = self._build_concat_file(kept_frames)
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path.name),
            "-vsync",
            "vfr",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(self.filtered_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-1:] or ["unknown ffmpeg error"]
            return f"MP4 export failed: {tail[0]}"
        return None

    def _export_gif(self, output_path: Optional[Path], kept_frames: list[Path]) -> Optional[str]:
        if output_path is None:
            return None
        if not self._has_ffmpeg():
            return "ffmpeg not found; skipped GIF export"
        concat_path = self._build_concat_file(kept_frames)
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path.name),
            "-vsync",
            "vfr",
            str(output_path),
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(self.filtered_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-1:] or ["unknown ffmpeg error"]
            return f"GIF export failed: {tail[0]}"
        return None

    def _write_manifest(
        self,
        raw_count: int,
        filtered_count: int,
        mp4_path: Optional[Path],
        gif_path: Optional[Path],
        warning: Optional[str],
    ) -> None:
        payload = {
            "started_at": self._started_at,
            "ended_at": self._ended_at,
            "fps": self.fps,
            "hash_algo": "xxh3_64",
            "raw_frames": raw_count,
            "filtered_frames": filtered_count,
            "capture_errors": self._capture_errors,
            "mp4": str(mp4_path) if mp4_path else None,
            "gif": str(gif_path) if gif_path else None,
            "warning": warning,
        }
        self.manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
