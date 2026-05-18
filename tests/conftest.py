"""Shared pytest fixtures for the mOSdat unit-test suite.

All external I/O (OpenAI, VNC, SSH, ProxmoxAPI) is mocked so individual
test modules stay focused on behaviour, not wiring.

Import strategy
---------------
The project `__init__.py` files re-export heavy sub-packages that may not
be installed in the running Python environment.  Tests load modules directly
via their file path (importlib.util.spec_from_file_location) so the
__init__ chain is never traversed.  See _load_module() below.
"""

import importlib.util
import json
import socket
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# Anti-hang safeguards
# ---------------------------------------------------------------------------
# Three layers protect the suite from hanging forever on a stuck VNC/SSH/HTTP
# call. Tests that genuinely need more time mark with @pytest.mark.timeout(N).
#
#   1. socket.setdefaulttimeout(10) — any blocking socket op (paramiko, http,
#      raw VNC handshake) that forgot an explicit timeout raises socket.timeout
#      after 10s instead of blocking the worker thread forever.
#   2. pytest-timeout (pyproject.toml: timeout=30) — per-test hard budget.
#   3. Heartbeat thread — prints the currently-running test plus elapsed wall
#      time every 10s when it exceeds the interval. If pytest output goes
#      silent you can still tell what is stuck.
socket.setdefaulttimeout(10)


class _Heartbeat:
    """Background thread that periodically reports the active test."""

    def __init__(self, interval: float = 10.0):
        self.interval = interval
        self.active: "tuple[str, float] | None" = None
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="pytest-heartbeat", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def set_active(self, nodeid):
        self.active = (nodeid, time.monotonic()) if nodeid else None

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            cur = self.active
            if cur is None:
                continue
            nodeid, started = cur
            elapsed = time.monotonic() - started
            if elapsed < self.interval:
                continue
            print(
                f"\n[heartbeat] still running: {nodeid} ({elapsed:.1f}s)",
                file=sys.stderr,
                flush=True,
            )


_heartbeat = _Heartbeat()


def pytest_sessionstart(session):
    _heartbeat.start()


def pytest_sessionfinish(session, exitstatus):
    _heartbeat.stop()


def pytest_runtest_logstart(nodeid, location):
    _heartbeat.set_active(nodeid)


def pytest_runtest_logfinish(nodeid, location):
    _heartbeat.set_active(None)


_PROJ = Path(__file__).parent.parent   # .../mOSdat

# Pre-load real ``automation.runners`` package so test files using
# ``sys.modules.setdefault("automation.runners", stub)`` get a no-op,
# preserving __path__ for submodule imports like
# ``automation.runners.functional_verify``.
import automation  # noqa: E402,F401
import automation.runners  # noqa: E402,F401


def _stub(name: str, **attrs) -> types.ModuleType:
    """Create a minimal stub module and register it in sys.modules."""
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def _load_module(rel_path: str, module_name: str,
                 extra_sys_modules: dict | None = None) -> types.ModuleType:
    """Load a module by file path, injecting stub dependencies first.

    rel_path: path relative to _PROJ, e.g. "automation/vlm/client.py"
    module_name: the dotted name to register in sys.modules
    extra_sys_modules: additional {name: module} overrides applied before exec
    """
    path = _PROJ / rel_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = module_name.rsplit(".", 1)[0]
    if extra_sys_modules:
        sys.modules.update(extra_sys_modules)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# PIL image helpers
# ---------------------------------------------------------------------------

def make_solid_image(color=(100, 100, 100), size=(1920, 1080)) -> Image.Image:
    return Image.new("RGB", size, color)


# ---------------------------------------------------------------------------
# Mock VLMClient
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_vlm():
    vlm = MagicMock()
    vlm.verify.return_value = True
    vlm.localize.return_value = (480, 270)
    vlm.localize_verified.return_value = (480, 270)
    vlm.localize_consistent.return_value = (480, 270)
    vlm.verify_consistent.return_value = (True, ["yes", "yes", "yes"])
    return vlm


@pytest.fixture()
def fake_screenshot():
    return make_solid_image()


@pytest.fixture()
def mock_screenshotter(fake_screenshot):
    ss = MagicMock()
    ss.capture.return_value = (fake_screenshot, (1920, 1080))
    ss.wait_for_stable.return_value = True
    return ss


@pytest.fixture()
def mock_injector():
    inj = MagicMock()
    inj.process_running.return_value = True
    inj.is_windows = False
    return inj


@pytest.fixture()
def mock_proxmox_api():
    api = MagicMock()
    api.config.node = "pve"
    api.post.return_value = {"data": "UPID:pve:snap1"}
    api.get.return_value = {"data": {"status": "stopped", "exitstatus": "OK"}}
    api.delete.return_value = {"data": "UPID:pve:del1"}
    return api


@pytest.fixture()
def screenshot_dir(tmp_path: Path) -> Path:
    d = tmp_path / "screenshots"
    d.mkdir()
    return d


@pytest.fixture()
def events_path(tmp_path: Path) -> Path:
    return tmp_path / "events.jsonl"


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Live-VM test markers
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run live tests that require VMs (test_3308 etc.)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="needs --live flag")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
