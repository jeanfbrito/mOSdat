"""Background VNC session recorder with post-run change-frame filtering."""

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
from typing import Callable, Optional

from PIL import Image, ImageChops, ImageStat


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
        diff_threshold: float = 3.0,
        keep_raw: bool = False,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.screenshotter = screenshotter
        self.recording_dir = Path(recording_dir)
        self.raw_dir = self.recording_dir / "raw"
        self.filtered_dir = self.recording_dir / "filtered"
        self.index_path = self.recording_dir / "index.jsonl"
        self.manifest_path = self.recording_dir / "manifest.json"
        self.fps = max(1.0, float(fps))
        self.interval = 1.0 / self.fps
        self.diff_threshold = float(diff_threshold)
        self.keep_raw = keep_raw
        self._log = log_fn or (lambda _msg: None)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._frame_id = 0
        self._capture_errors = 0
        self._started_at: Optional[str] = None
        self._ended_at: Optional[str] = None

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
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._running = True
        self._thread.start()
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
                img, _ = self.screenshotter.capture()
                self._frame_id += 1
                frame_name = f"frame_{self._frame_id:06d}.png"
                frame_path = self.raw_dir / frame_name
                img.save(frame_path)
                self._append_index(frame_name)
            except Exception as exc:
                self._capture_errors += 1
                if self._capture_errors == 1 or self._capture_errors % 10 == 0:
                    self._log(f"capture error #{self._capture_errors}: {exc}")

    def _append_index(self, frame_name: str) -> None:
        record = {
            "frame": frame_name,
            "ts": datetime.now().isoformat(),
        }
        with self.index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    @staticmethod
    def _thumb(image_path: Path) -> Image.Image:
        with Image.open(image_path) as img:
            return img.convert("L").resize((64, 64), Image.Resampling.BILINEAR)

    @staticmethod
    def _mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
        if a.size != b.size or a.mode != b.mode:
            return 255.0
        diff = ImageChops.difference(a, b)
        return float(ImageStat.Stat(diff).mean[0])

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

        keep_indices = {0, len(raw_frames) - 1}
        last_kept_thumb = self._thumb(raw_frames[0])

        for idx in range(1, len(raw_frames) - 1):
            curr_thumb = self._thumb(raw_frames[idx])
            mean_diff = self._mean_abs_diff(last_kept_thumb, curr_thumb)
            if mean_diff >= self.diff_threshold:
                keep_indices.add(idx)
                last_kept_thumb = curr_thumb

        kept = []
        out_num = 1
        for idx, src in enumerate(raw_frames):
            if idx in keep_indices:
                dest = self.filtered_dir / f"frame_{out_num:06d}.png"
                shutil.copy2(src, dest)
                ts = ts_by_name.get(src.name)
                if ts is not None:
                    os.utime(dest, (ts, ts))
                kept.append(dest)
                out_num += 1
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
        for idx in range(len(frame_times) - 1):
            dt = frame_times[idx + 1] - frame_times[idx]
            durations.append(min(10.0, max(1.0 / self.fps, dt)))
        tail = max(0.8, min(2.5, (sum(durations) / len(durations)) if durations else 1.2))
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
            "diff_threshold": self.diff_threshold,
            "raw_frames": raw_count,
            "filtered_frames": filtered_count,
            "capture_errors": self._capture_errors,
            "mp4": str(mp4_path) if mp4_path else None,
            "gif": str(gif_path) if gif_path else None,
            "warning": warning,
        }
        self.manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
