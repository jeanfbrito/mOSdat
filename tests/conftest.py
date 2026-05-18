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


# ---------------------------------------------------------------------------
# Test order: push stub-polluting files to the end of the suite
# ---------------------------------------------------------------------------
# Several test files install sys.modules stubs at import time
# (test_negative installs PIL/openai/httpx stubs; test_concurrent_safety and
# test_proxmox_vm install automation.proxmox/transport stubs; test_build_cmd
# pops PIL/automation.transport/automation.vlm before re-importing). Each
# has a teardown_module() that tries to restore originals, but the "original"
# they captured can itself be a stub from an earlier polluting file — so
# restoring puts a stub back instead of the real module, and subsequent files
# (test_capability, test_cursor_motion_integration, test_human_move,
# test_runner_features) see a broken automation.setup.capability /
# automation.vlm.input / PIL.Image.
#
# Reordering so these files run LAST means their teardown is the very last
# thing the suite does; no later module can be poisoned by their state.
_POLLUTING_FILES = frozenset({
    "test_negative",
    "test_concurrent_safety",
    "test_proxmox_vm",
    "test_build_cmd",
})


# Merged with the --live skip logic below (a single hook per name is allowed
# at module scope; the later def silently wins). The reorder runs first so
# the resulting item order is "polluting files last", then the --live marker
# pass walks the now-final order.
def _reorder_polluting_last(items):
    items.sort(key=lambda i: 1 if Path(str(i.fspath)).stem in _POLLUTING_FILES else 0)


# ---------------------------------------------------------------------------
# Restore real modules after collection
# ---------------------------------------------------------------------------
# Some test files (notably test_trace.py) install MagicMock stubs into
# sys.modules at MODULE-IMPORT time. Pytest imports every test file during
# the collection phase, before any test runs. So by the time test_capability
# (alphabetically earlier) actually executes, sys.modules already points to
# test_trace's stubs — even though test_trace itself hasn't run yet. The
# per-file teardown_module() that restores reality fires AFTER its own tests
# finish, which is too late.
#
# Fix: after collection completes (all imports done) and before any test
# runs, force-reload the modules that import-time stubs commonly poison.
# Each test file that genuinely wants a stub re-installs it through its own
# import-time block as before — the order pytest uses still re-imports those
# files' stubs whenever needed. We just refuse to let one file's stubs
# persist past collection.
_RESTORE_ON_COLLECTION_FINISH = (
    "automation.setup",
    "automation.setup.capability",
    "automation.config",
    "automation.vlm",
    "automation.vlm.client",
    "automation.vlm.input",
    "automation.vlm.screenshot",
    "automation.transport.ssh",
    "automation.transport.vnc",
    "automation.proxmox",
    "automation.proxmox.api",
    "automation.proxmox.vm",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
)


# Snapshot the genuine PIL.Image class identity NOW, before any test file is
# imported. Several test files (test_if_visible, test_negative-when-PIL-absent)
# mutate the live module with `PIL.Image.Image = object` and never restore it.
# Other test files use `from PIL import Image as ...` at their own top, which
# binds the *module object*, so subsequent reload() swaps in sys.modules do
# not update those bindings. The reliable repair is to write the real Image
# class back onto the module object that's already shared by name-imported
# bindings.
import PIL.Image as _PIL_IMAGE_REAL_MOD
_PIL_IMAGE_REAL_CLASS = _PIL_IMAGE_REAL_MOD.Image
_PIL_IMAGE_REAL_NEW = getattr(_PIL_IMAGE_REAL_MOD, "new", None)
_PIL_IMAGE_REAL_OPEN = getattr(_PIL_IMAGE_REAL_MOD, "open", None)
with open("/tmp/conftest-debug.log", "w") as _f:
    _f.write(f"snapshot Image class: {_PIL_IMAGE_REAL_CLASS}\n")
    _f.write(f"snapshot new: {_PIL_IMAGE_REAL_NEW}\n")


def pytest_collection_finish(session):
    import sys as _sys
    with open("/tmp/conftest-debug.log", "a") as _f:
        cur = _sys.modules.get("PIL.Image")
        _f.write(f"\n=== finish hook called ===\n")
        _f.write(f"cached _PIL_IMAGE_REAL_MOD: {_PIL_IMAGE_REAL_MOD}\n")
        _f.write(f"sys.modules[PIL.Image]: {cur}\n")
        _f.write(f"same object? {cur is _PIL_IMAGE_REAL_MOD}\n")
        _f.write(f"cur.Image: {getattr(cur, 'Image', '<missing>')}\n")
        _f.write(f"cur.new: {getattr(cur, 'new', '<missing>')}\n")
        _f.write(f"_PIL_IMAGE_REAL_NEW: {_PIL_IMAGE_REAL_NEW}\n")

    # Surgical repair: test_if_visible (and test_negative when PIL was not
    # yet loaded) mutates the live PIL.Image module with
    # `PIL.Image.Image = object` and never restores it. Other test files
    # imported `from PIL import Image as X` while the module was healthy,
    # then the corruption arrives during collection and persists into the
    # run phase. Putting the real `Image` class back onto BOTH the cached
    # module reference and the current sys.modules entry repairs every
    # name-bound holder transparently (they share the same module object).
    import sys as _sys
    candidates = {_PIL_IMAGE_REAL_MOD, _sys.modules.get("PIL.Image")}
    for live in candidates:
        if live is None:
            continue
        if getattr(live, "Image", None) is object:
            try:
                live.Image = _PIL_IMAGE_REAL_CLASS
            except Exception:
                pass
        if getattr(live, "new", None) is None and _PIL_IMAGE_REAL_NEW is not None:
            try:
                live.new = _PIL_IMAGE_REAL_NEW
            except Exception:
                pass
        if getattr(live, "open", None) is None and _PIL_IMAGE_REAL_OPEN is not None:
            try:
                live.open = _PIL_IMAGE_REAL_OPEN
            except Exception:
                pass

    # Module-stub repair for known-poisoned automation.* modules. Tests
    # like test_doctor and test_trace install types.ModuleType stubs via
    # sys.modules.setdefault during their import-time block; those stubs
    # persist across the rest of the suite and break sibling tests that
    # expect the real module's classes/functions to be present. Heuristic:
    # the real module always has `__file__`; the types.ModuleType stub
    # does not. When we detect a stub, pop it + force-reimport.
    import importlib
    _stub_indicators = {
        # name : sentinel attribute that the real module provides
        "automation.transport.ssh": "SSHClient",
        "automation.transport": "ssh",
        "automation.setup.capability": "_capabilities_dir",
        "automation.setup": None,  # no public attr, repair via re-import
    }
    for _name, _sentinel in _stub_indicators.items():
        _mod = _sys.modules.get(_name)
        if _mod is None:
            continue
        _is_stub = not getattr(_mod, "__file__", None)
        if _sentinel and not _is_stub and not hasattr(_mod, _sentinel):
            _is_stub = True
        if _is_stub:
            _sys.modules.pop(_name, None)
            try:
                importlib.import_module(_name)
            except ImportError:
                pass


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
    # Move stub-polluting files to the end so their teardown is the last
    # thing the suite does (see _POLLUTING_FILES comment above).
    _reorder_polluting_last(items)

    # Skip @pytest.mark.live tests unless --live was passed.
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="needs --live flag")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
