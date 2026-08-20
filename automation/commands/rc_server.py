"""``mosdat server-provision`` — start a Rocket.Chat server for a published image ref.

Pulls ``ghcr.io/rocketchat/rocket.chat:<ref>`` and starts a two-service Compose
stack (mongo + rocketchat) on the mosdat host. The host URL advertised to
callers is ``http://<advertise-host>:<ephemeral-port>`` — never ``localhost`` —
so LAN VMs can reach it (FR-010).

``provision_server`` has two paths; there is no separate status-check API.

Path A — no existing Compose project for this ref (first provision):
    daemon check, pull image, pick a free host port (bind-probe, before
    compose up, so ``ROOT_URL`` is correct at container start), compose up,
    then **block** polling ``GET <url>/livez`` until HTTP 200 or ``timeout``
    seconds elapse (default 180). First call is blocking for both CLI and
    MCP.

Path B — matching project already exists (``project_name(ref)`` is in
``_list_managed_projects()``): do not pull, do not compose up/down. Discover
the host port, probe ``/livez`` **once**, and return immediately:
    200 → ``state="ready"``; otherwise ``state="starting"`` (with url if the
    port is known). This is how ``state: "starting"`` is observed — a caller
    that re-invokes while the first call is still polling gets current state
    without racing a second ``compose up``.

Exit codes:
    0  — ready / reused (including reuse of an in-progress instance still starting)
    1  — no published server image for the reference (FR-002)
    2  — startup timeout / compose/pull/other failure
    5  — invalid args / Docker daemon is not running
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests


IMAGE_REPO = "ghcr.io/rocketchat/rocket.chat"
DEFAULT_STARTUP_TIMEOUT_S = 180
POLL_INTERVAL_S = 2.0
DAEMON_ERROR = "Docker daemon is not running — start Docker Desktop first"
COMPOSE_TEMPLATE = Path(__file__).resolve().parent / "templates" / "rc-server-compose.yml"

_PROXMOX_LAN_HINT = ("192.168.13.85", 1)
_NO_IMAGE_TEMPLATE = (
    "no published server image for '{ref}' — this reference cannot be tested "
    "this way (fork PR, or no server-side change published)"
)


@dataclass
class ProvisionResult:
    ref: str
    state: str  # "starting" | "ready" | "failed"
    url: Optional[str] = None
    error: str = ""
    elapsed_ms: int = 0


@dataclass
class TeardownResult:
    ref: str
    torn_down: bool
    error: str = ""


@dataclass
class ListInstance:
    ref: str
    state: str  # "starting" | "ready"
    url: Optional[str] = None
    elapsed_ms: int = 0


def normalize_ref(ref: str) -> str:
    text = (ref or "").strip()
    if text.isdigit():
        return f"pr-{text}"
    return text


def resolve_image(ref: str) -> str:
    return f"{IMAGE_REPO}:{normalize_ref(ref)}"


def project_name(ref: str) -> str:
    raw = re.sub(r"[^a-z0-9_-]+", "-", normalize_ref(ref).lower())
    raw = re.sub(r"-{2,}", "-", raw).strip("-")
    return f"mosdat-rc-{raw}" if raw else "mosdat-rc"


def _no_image_error(ref: str) -> str:
    return _NO_IMAGE_TEMPLATE.format(ref=normalize_ref(ref))


def _run(
    cmd: list[str], timeout: float, env: Optional[dict] = None,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return 124, out, err or f"timed out after {timeout}s"


def _docker_daemon_running() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=8,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _docker_pull(image: str) -> tuple[bool, str]:
    rc, out, err = _run(["docker", "pull", image], timeout=300)
    if rc == 0:
        return True, ""
    return False, f"{out}{err}".strip()


def _is_no_image_error(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    if "manifest unknown" in t:
        return True
    if "manifest for" in t and "not found" in t:
        return True
    if "not found" in t:
        return True
    return False


def _compose_up(project: str, compose_file: Path, env: dict) -> tuple[bool, str]:
    merged = os.environ.copy()
    merged.update(env)
    rc, out, err = _run(
        ["docker", "compose", "-p", project, "-f", str(compose_file), "up", "-d"],
        timeout=300,
        env=merged,
    )
    if rc == 0:
        return True, ""
    return False, f"{out}{err}".strip()


def _compose_down(project: str, compose_file: Path) -> tuple[bool, str]:
    # `down` still needs the compose file's ${...} substitutions to resolve to
    # something non-empty to pass validation (a service can't have a blank
    # image), even though it identifies what to remove via the project's
    # existing container/volume labels, not these values — placeholders are
    # safe. Verified live: without this, `down` fails with "service
    # 'rocketchat' has neither an image nor a build context specified"
    # before it ever reaches an actual container.
    merged = os.environ.copy()
    merged.setdefault("RC_IMAGE", "unused")
    merged.setdefault("ROOT_URL", "http://unused")
    merged.setdefault("HOST_PORT", "0")
    rc, out, err = _run(
        ["docker", "compose", "-p", project, "-f", str(compose_file), "down", "-v"],
        timeout=120,
        env=merged,
    )
    if rc == 0:
        return True, ""
    return False, f"{out}{err}".strip()


def _discover_host_port(
    project: str, service: str, container_port: int,
) -> Optional[int]:
    rc, out, _err = _run(
        [
            "docker", "compose", "-p", project, "-f", str(COMPOSE_TEMPLATE),
            "port", service, str(container_port),
        ],
        timeout=15,
    )
    if rc != 0:
        return None
    text = (out or "").strip()
    if not text:
        return None
    last_line = text.splitlines()[-1].strip()
    if ":" not in last_line:
        return None
    tail = last_line.rsplit(":", 1)[-1].strip()
    try:
        return int(tail)
    except ValueError:
        return None


def _list_managed_projects() -> list[str]:
    rc, out, _err = _run(["docker", "compose", "ls", "--format", "json"], timeout=15)
    if rc != 0 or not (out or "").strip():
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("Name") or item.get("name") or ""
        if (
            isinstance(name, str)
            and name.startswith("mosdat-rc-")
            and name not in seen
        ):
            seen.add(name)
            names.append(name)
    return names


def _pick_free_port() -> int:
    """Bind-probe an ephemeral TCP port and release it immediately.

    Lets mosdat learn the host port *before* ``compose up`` so it can pass a
    correct ``ROOT_URL`` at container startup (see module docstring / FR-010).
    Small race window between probe-and-release and Docker binding it is
    accepted — acceptable at this project's documented scale.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _advertise_host() -> str:
    env = (os.environ.get("MOSDAT_HOST_IP") or "").strip()
    if env:
        return env
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(_PROXMOX_LAN_HINT)
            ip = sock.getsockname()[0]
            if ip:
                return ip
        finally:
            sock.close()
    except OSError:
        pass
    return "127.0.0.1"


def probe_ready(url: str, timeout: float = 5) -> bool:
    livez = f"{url.rstrip('/')}/livez"
    try:
        resp = requests.get(livez, timeout=timeout)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _timeout_label(timeout: float) -> str:
    if float(timeout) == int(timeout):
        return str(int(timeout))
    return str(timeout)


def provision_server(
    ref: str,
    *,
    dry_run: bool = False,
    timeout: float = DEFAULT_STARTUP_TIMEOUT_S,
) -> ProvisionResult:
    start = time.monotonic()

    def finish(
        state: str,
        *,
        url: Optional[str] = None,
        error: str = "",
        result_ref: Optional[str] = None,
    ) -> ProvisionResult:
        return ProvisionResult(
            ref=result_ref if result_ref is not None else normalize_ref(ref),
            state=state,
            url=url,
            error=error,
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )

    if not (ref or "").strip():
        return finish(
            "failed",
            error="invalid ref: empty or whitespace-only",
            result_ref=(ref or "").strip(),
        )

    norm = normalize_ref(ref)
    project = project_name(ref)
    image = resolve_image(ref)

    if dry_run:
        print(
            f"[mosdat server-provision] dry-run image={image} "
            f"project={project} compose={COMPOSE_TEMPLATE}",
            flush=True,
        )
        return finish("starting", url=None)

    if not _docker_daemon_running():
        return finish("failed", error=DAEMON_ERROR)

    if project in _list_managed_projects():
        port = _discover_host_port(project, "rocketchat", 3000)
        if port is None:
            return finish(
                "failed",
                error=(
                    f"could not discover host port for existing project '{project}'"
                ),
            )
        url = f"http://{_advertise_host()}:{port}"
        if probe_ready(url):
            return finish("ready", url=url)
        return finish("starting", url=url)

    ok, detail = _docker_pull(image)
    if not ok:
        if _is_no_image_error(detail):
            return finish("failed", error=_no_image_error(norm))
        return finish("failed", error=f"failed to pull {image}: {detail}")

    host = _advertise_host()
    port = _pick_free_port()
    url = f"http://{host}:{port}"

    ok, detail = _compose_up(
        project,
        COMPOSE_TEMPLATE,
        env={"RC_IMAGE": image, "HOST_PORT": str(port), "ROOT_URL": url},
    )
    if not ok:
        return finish("failed", error=f"compose up failed for '{project}': {detail}")

    deadline = start + float(timeout)
    while True:
        if probe_ready(url):
            return finish("ready", url=url)
        if time.monotonic() >= deadline:
            n = _timeout_label(timeout)
            return finish(
                "failed",
                url=url,
                error=(
                    f"server for '{norm}' did not become ready within {n}s — "
                    "last state: container running, /livez not yet answering"
                ),
            )
        time.sleep(POLL_INTERVAL_S)


def teardown_server(ref: str) -> TeardownResult:
    """Stop and remove a provisioned instance's Compose project.

    Idempotent: no matching project is not an error (per contract, "already
    gone is not an error") — only a real teardown attempt that fails (compose
    down errors, daemon down) is reported as an error.
    """
    norm = normalize_ref(ref)
    project = project_name(ref)

    if not _docker_daemon_running():
        return TeardownResult(ref=norm, torn_down=False, error=DAEMON_ERROR)

    if project not in _list_managed_projects():
        return TeardownResult(ref=norm, torn_down=False)

    ok, detail = _compose_down(project, COMPOSE_TEMPLATE)
    if not ok:
        return TeardownResult(
            ref=norm,
            torn_down=False,
            error=f"compose down failed for '{project}': {detail}",
        )
    return TeardownResult(ref=norm, torn_down=True)


def list_instances() -> list[ListInstance]:
    """Discover all mosdat-managed Rocket.Chat server instances.

    Raises ``RuntimeError(DAEMON_ERROR)`` when the Docker daemon itself is
    not running, mirroring how ``provision_server`` signals daemon-down as a
    top-level failure rather than an empty/partial result — callers (the CLI
    entry, the MCP handler) turn this into their own error envelope/exit
    code instead of silently reporting zero instances.
    """
    if not _docker_daemon_running():
        raise RuntimeError(DAEMON_ERROR)

    instances: list[ListInstance] = []
    for project in _list_managed_projects():
        ref = project[len("mosdat-rc-"):] if project.startswith("mosdat-rc-") else project
        port = _discover_host_port(project, "rocketchat", 3000)
        url = f"http://{_advertise_host()}:{port}" if port is not None else None
        state = "ready" if (url and probe_ready(url)) else "starting"
        instances.append(ListInstance(ref=ref, state=state, url=url, elapsed_ms=0))
    return instances


def run_server_provision(args: argparse.Namespace) -> int:
    ref = getattr(args, "ref", "") or ""
    dry_run = bool(getattr(args, "dry_run", False))
    timeout = getattr(args, "timeout", DEFAULT_STARTUP_TIMEOUT_S)
    if timeout is None:
        timeout = DEFAULT_STARTUP_TIMEOUT_S
    result = provision_server(ref, dry_run=dry_run, timeout=float(timeout))
    if result.state in ("ready", "starting"):
        if result.url:
            print(
                f"[mosdat server-provision] {result.state} {result.ref} {result.url}",
                flush=True,
            )
        else:
            print(
                f"[mosdat server-provision] {result.state} {result.ref}",
                flush=True,
            )
        return 0
    print(f"[mosdat server-provision] ERROR: {result.error}", file=sys.stderr)
    err = result.error or ""
    if err.startswith("no published server image"):
        return 1
    if err == DAEMON_ERROR or err.startswith("invalid ref"):
        return 5
    return 2


def add_server_provision_subparser(sub) -> None:
    """Wire ``mosdat server-provision`` onto an existing subparsers action."""
    p = sub.add_parser(
        "server-provision",
        help=(
            "Provision a Rocket.Chat server matching a published image "
            "reference"
        ),
        description=(
            "Pull the published GHCR image for a PR/release/RC/develop "
            "reference and start a two-service Compose stack (mongo + "
            "rocketchat). First call is blocking (polls /livez up to "
            "--timeout, default 180s). Re-invoking for an already-managed "
            "project returns current state immediately without compose "
            "up/down. There is no separate status tool."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  mosdat server-provision develop\n"
            "  mosdat server-provision 3464 --dry-run\n"
            "  mosdat server-provision pr-3464 --timeout 180\n"
            "\n"
            "Exit codes:\n"
            "  0  ready / reused (including in-progress still starting)\n"
            "  1  no published server image for the reference (FR-002)\n"
            "  2  startup timeout / compose/pull/other failure\n"
            "  5  invalid args / Docker daemon is not running\n"
        ),
    )
    p.add_argument(
        "ref",
        help="PR (pr-<N> or bare <N>), release/RC tag, or develop",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Resolve image/project only; do not talk to Docker",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_STARTUP_TIMEOUT_S,
        metavar="SECONDS",
        help=f"Seconds to wait for /livez (default: {DEFAULT_STARTUP_TIMEOUT_S})",
    )


def run_server_teardown(args: argparse.Namespace) -> int:
    ref = getattr(args, "ref", "") or ""
    if not (ref or "").strip():
        print(
            "[mosdat server-teardown] ERROR: invalid ref: empty or whitespace-only",
            file=sys.stderr,
        )
        return 5
    result = teardown_server(ref)
    if not result.error:
        print(
            f"[mosdat server-teardown] torn_down={result.torn_down} {result.ref}",
            flush=True,
        )
        return 0
    print(f"[mosdat server-teardown] ERROR: {result.error}", file=sys.stderr)
    if result.error == DAEMON_ERROR:
        return 5
    return 2


def add_server_teardown_subparser(sub) -> None:
    """Wire ``mosdat server-teardown`` onto an existing subparsers action."""
    p = sub.add_parser(
        "server-teardown",
        help="Stop and remove a provisioned Rocket.Chat server instance",
        description=(
            "Compose down (with volumes) the managed project for a "
            "reference. Idempotent — tearing down a reference with no "
            "matching instance reports torn_down=False, not an error."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  mosdat server-teardown pr-3464\n"
            "\n"
            "Exit codes:\n"
            "  0  call succeeded (torn_down true or false)\n"
            "  2  compose down failed\n"
            "  5  invalid args / Docker daemon is not running\n"
        ),
    )
    p.add_argument(
        "ref",
        help="PR (pr-<N> or bare <N>), release/RC tag, or develop",
    )


def run_server_list(args: argparse.Namespace) -> int:
    try:
        instances = list_instances()
    except RuntimeError as e:
        print(f"[mosdat server-list] ERROR: {e}", file=sys.stderr)
        return 5
    if not instances:
        print("[mosdat server-list] no managed instances", flush=True)
        return 0
    for inst in instances:
        url = inst.url or "(unknown)"
        print(
            f"[mosdat server-list] {inst.ref} state={inst.state} url={url}",
            flush=True,
        )
    return 0


def add_server_list_subparser(sub) -> None:
    """Wire ``mosdat server-list`` onto an existing subparsers action."""
    sub.add_parser(
        "server-list",
        help="List currently managed Rocket.Chat server instances",
        description=(
            "Discover mosdat-managed Compose projects (mosdat-rc-<ref>), "
            "probe each instance's /livez once, and report ref/state/url."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  mosdat server-list\n"
            "\n"
            "Exit codes:\n"
            "  0  call succeeded (regardless of instance count)\n"
            "  5  Docker daemon is not running\n"
        ),
    )
