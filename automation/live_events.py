"""Live dashboard event tailing and SSE broadcast primitives."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Callable, Optional


class SSEBroadcaster:
    """Thread-safe fan-out of SSE messages to all connected clients."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: list[list[str]] = []

    def subscribe(self) -> list[str]:
        """Register a new subscriber; returns its shared queue."""
        queue: list[str] = []
        with self._lock:
            self._queues.append(queue)
        return queue

    def unsubscribe(self, queue: list[str]) -> None:
        with self._lock:
            try:
                self._queues.remove(queue)
            except ValueError:
                pass

    def push(self, payload: str) -> None:
        """Push raw SSE payload string to all subscribers."""
        with self._lock:
            for queue in self._queues:
                queue.append(payload)

    def broadcast_event(self, data: dict) -> None:
        """Serialize dict to SSE event and fan out."""
        payload = f"data: {json.dumps(data)}\n\n"
        self.push(payload)

    def heartbeat(self) -> None:
        self.push(": heartbeat\n\n")


class EventWatcher:
    """Poll results/functional/<run>/<vm>/events.jsonl and PNG files."""

    def __init__(
        self,
        results_root: Path,
        broadcaster: SSEBroadcaster,
        refresh_ms: int = 500,
        debug_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._root = results_root
        self._bc = broadcaster
        self._refresh = refresh_ms / 1000.0
        self._debug_hook = debug_hook
        self._positions: dict[tuple[str, str], int] = {}
        self._mtimes: dict[tuple[str, str], float] = {}
        self._known_pngs: dict[tuple[str, str], set[str]] = {}
        self._stop = threading.Event()

    def _functional_dir(self) -> Path:
        return self._root / "functional"

    def _iter_vms(self):
        """Yield (run_name, vm_name, vm_path) for all active run/vm dirs."""
        functional_dir = self._functional_dir()
        if not functional_dir.exists():
            return
        try:
            for run_entry in os.scandir(functional_dir):
                if not run_entry.is_dir():
                    continue
                try:
                    for vm_entry in os.scandir(run_entry.path):
                        if not vm_entry.is_dir():
                            continue
                        yield run_entry.name, vm_entry.name, Path(vm_entry.path)
                except OSError:
                    pass
        except OSError:
            pass

    def _poll_once(self) -> None:
        for run, vm, vm_path in self._iter_vms():
            key = (run, vm)
            events_file = vm_path / "events.jsonl"

            if events_file.exists():
                try:
                    mtime = events_file.stat().st_mtime
                except OSError:
                    mtime = 0.0
                if mtime != self._mtimes.get(key, -1.0):
                    self._mtimes[key] = mtime
                    offset = self._positions.get(key, 0)
                    try:
                        with events_file.open("rb") as fh:
                            fh.seek(offset)
                            chunk = fh.read()
                            new_offset = offset + len(chunk)
                        self._positions[key] = new_offset
                        for raw_line in chunk.split(b"\n"):
                            raw_line = raw_line.strip()
                            if not raw_line:
                                continue
                            try:
                                event = json.loads(raw_line)
                            except json.JSONDecodeError:
                                continue
                            event["run"] = run
                            event["vm"] = vm
                            self._bc.broadcast_event(event)
                    except OSError:
                        pass

            vlm_input_tokens = (
                "verify_poll",
                "localize",
                "verify_input",
                "verify_click",
                "verify_click_diff",
                "canary_verify",
                "_verify_",
            )
            known = self._known_pngs.setdefault(key, set())
            try:
                for entry in os.scandir(vm_path):
                    if not entry.name.endswith(".png"):
                        continue
                    if entry.name in known:
                        continue
                    known.add(entry.name)
                    if not any(token in entry.name for token in vlm_input_tokens):
                        continue
                    step_num = None
                    match = re.search(r"_step(\d+)_", entry.name)
                    if match:
                        step_num = int(match.group(1))
                    url = f"/png/{run}/{vm}/{entry.name}"
                    self._bc.broadcast_event(
                        {"event": "screenshot", "run": run, "vm": vm,
                         "url": url, "step_num": step_num,
                         "filename": entry.name}
                    )
            except OSError:
                pass

    def run_forever(self) -> None:
        if self._debug_hook:
            self._debug_hook("watcher_start")
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self._refresh)
        if self._debug_hook:
            self._debug_hook("watcher_stop")

    def stop(self) -> None:
        self._stop.set()
