"""Timestamped progress pulses for long-running operations.

Pattern: any agent or shell script writes pulses to a file. Orchestrator polls
it via `tail -c 600 <file>` to see what's happening without waiting for completion.

Usage from Python:
    hb = Heartbeat("smoke-iter")
    hb.step("vm-reboot-start")
    ...
    hb.step("vm-reboot-done", uptime=42)

Usage from shell (no dep):
    echo "[HB $(date -Iseconds)] step=<name> $*" >> "$HEARTBEAT_FILE"

Default file path: /tmp/mosdat-hb-<label>-<pid>.log
Override via HEARTBEAT_FILE env var.

Read pattern (orchestrator):
    stat -L mtime → staleness
    tail -c 600 → last activity tail
    Alert if mtime > N seconds stale.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import Any


class Heartbeat:
    def __init__(self, label: str, log_dir: str | Path = "/tmp") -> None:
        env_path = os.environ.get("HEARTBEAT_FILE")
        self.path = Path(env_path) if env_path else Path(log_dir) / f"mosdat-hb-{label}-{os.getpid()}.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._emit("init", label=label)

    def step(self, name: str, **fields: Any) -> None:
        self._emit("step", name=name, **fields)

    def pulse(self, **fields: Any) -> None:
        self._emit("pulse", **fields)

    def done(self, **fields: Any) -> None:
        self._emit("done", **fields)

    def _emit(self, kind: str, **fields: Any) -> None:
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")
        kv = " ".join(f"{k}={v}" for k, v in fields.items())
        line = f"[HB {ts} {kind} {kv}]\n"
        with open(self.path, "a") as fp:
            fp.write(line)
