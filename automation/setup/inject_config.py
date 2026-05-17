"""I1 — declarative pre-staging of Electron app userData before launch.

Wipes ``~/.config/<AppName>`` AND ``~/.config/<AppName> (development)`` on the VM
and writes ``servers.json`` + a redux-persist-aware ``config.json``. This kills
the ~80-line boilerplate (heredoc/printf/missing-migrations-version footgun)
duplicated across every PR-3325-style scenario.

Why both userData dirs: PR/dev builds of Electron apps write to a
``(development)``-suffixed dir while prod builds use the bare name. See
``.knowledge/mosdat/scenarios/rc-pr3325-userdata-dir-development-suffix.md``.

Why ``__internal__.migrations.version``: redux-persist re-runs every migration
from ``0_0_0`` when the version key is missing, which resets ``currentView`` to
``add-new-server`` and discards custom keys. See
``.knowledge/electron/redux-persist/migrations-version-mandatory.md``.

JSON writes use ``printf '%s\n' '<json>'`` (NOT heredoc) to dodge shell
expansion footguns inside SSH command-line escaping.
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any, Iterable, Mapping, Optional


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_PRODUCT_NAME_RE = re.compile(r'"productName"\s*[:,]?\s*"([^"]+)"')
_VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?)\b")


def _asar_path_from_install_path(install_path: str) -> str:
    """Given ``/opt/Rocket.Chat/rocketchat-desktop`` (binary) or ``/opt/Rocket.Chat``
    (dir), return the canonical ``<dir>/resources/app.asar`` path.
    """
    # Strip a trailing binary name if present.
    cleaned = install_path.rstrip("/")
    # Heuristic: if it looks like a file path (has a basename that contains a
    # dash or no slash separator after the last /opt-style segment), treat its
    # parent as the install dir. Otherwise treat the whole thing as a dir.
    if cleaned.endswith(".asar"):
        return cleaned
    # If install_path ends with a likely-binary name, drop it.
    # Heuristic: Electron Linux binaries are typically <slug>-desktop with no
    # extension. Dir names like "Rocket.Chat" contain dots but aren't binaries,
    # so we only strip when the basename matches the desktop-suffix convention.
    base = cleaned.rsplit("/", 1)[-1]
    if base and "-desktop" in base.lower():
        cleaned = cleaned.rsplit("/", 1)[0]
    return f"{cleaned}/resources/app.asar"


def detect_userdata_dirs(
    ssh,
    install_path: str,
    *,
    app_name: Optional[str] = None,
) -> list[str]:
    """Return both candidate userData paths for the app:
    ``~/.config/<AppName>`` and ``~/.config/<AppName> (development)``.

    Discovery order for ``<AppName>``:
      1. Explicit ``app_name`` argument.
      2. ``productName`` extracted from ``app.asar`` via ``strings ... | grep``.
      3. RuntimeError if neither resolves.

    We do NOT probe whether each path exists — both are wiped regardless,
    because PR/dev builds may write to either and a stale dir would clobber
    the next launch.
    """
    name = app_name
    if not name:
        asar = _asar_path_from_install_path(install_path)
        # ``strings`` will be present on essentially every Linux VM we target.
        # ``grep -A0`` is enough because the JSON has ``"productName":"..."`` on
        # one line in app.asar's header (it's the package.json bytes embedded).
        cmd = (
            f"strings {shlex.quote(asar)} 2>/dev/null "
            f"| grep -m1 -E '\"productName\"' || true"
        )
        result = ssh.run(cmd, timeout=30)
        match = _PRODUCT_NAME_RE.search(result.stdout or "")
        if match:
            name = match.group(1)
    if not name:
        raise RuntimeError(
            f"could not detect Electron productName from {install_path!r}; "
            "pass --inject-app-name explicitly"
        )
    # Use $HOME at runtime so the dirs resolve against the SSH user's home.
    return [
        f"$HOME/.config/{name}",
        f"$HOME/.config/{name} (development)",
    ]


def detect_app_version(
    ssh,
    install_path: str,
    *,
    binary: Optional[str] = None,
) -> str:
    """Return the app's semver-ish version string (e.g. ``"4.14.1"``).

    Strategy:
      1. ``<binary> --version`` — cheapest, deterministic for Electron apps.
      2. ``cat <install_dir>/resources/app-update.yml`` (electron-updater).
      3. ``strings <app.asar> | grep -m1 '"version"'`` (package.json fallback).
    Raises RuntimeError if none yield a parseable version.
    """
    # 1. Binary --version
    bin_path = binary
    if not bin_path:
        cleaned = install_path.rstrip("/")
        if cleaned.endswith(".asar"):
            bin_path = None
        else:
            base = cleaned.rsplit("/", 1)[-1]
            if base and "." not in base and "desktop" in base.lower():
                bin_path = cleaned
            else:
                # Try to find a -desktop binary under the install dir.
                find = ssh.run(
                    f"ls {shlex.quote(cleaned)}/*-desktop 2>/dev/null | head -1",
                    timeout=10,
                )
                bin_path = (find.stdout or "").strip() or None
    if bin_path:
        r = ssh.run(
            f"{shlex.quote(bin_path)} --version 2>/dev/null || true",
            timeout=15,
        )
        m = _VERSION_RE.search(r.stdout or "")
        if m:
            return m.group(1)
    # 2. app-update.yml
    asar = _asar_path_from_install_path(install_path)
    install_dir = asar.rsplit("/resources/", 1)[0]
    r = ssh.run(
        f"cat {shlex.quote(install_dir)}/resources/app-update.yml 2>/dev/null || true",
        timeout=10,
    )
    m = _VERSION_RE.search(r.stdout or "")
    if m:
        return m.group(1)
    # 3. package.json embedded in app.asar
    r = ssh.run(
        f"strings {shlex.quote(asar)} 2>/dev/null "
        f"| grep -m1 -E '\"version\"\\s*:\\s*\"[0-9]' || true",
        timeout=30,
    )
    m = _VERSION_RE.search(r.stdout or "")
    if m:
        return m.group(1)
    raise RuntimeError(
        f"could not detect app version for {install_path!r}; "
        "pass an explicit migrations_version when calling write_config_json"
    )


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _printf_write(ssh, remote_path: str, content: str, timeout: int = 30):
    """Write ``content`` to ``remote_path`` via ``printf '%s\\n' '...'`` over SSH.

    Single quotes inside the payload are escaped with ``'\\''`` so the outer
    single-quoted shell literal stays intact.
    """
    escaped = content.replace("'", "'\\''")
    # Outer wrapping for the redirection must NOT double-quote the path because
    # the path contains a literal space for ``(development)``. Use single quotes
    # around the path; SSH passes the whole string to a remote shell which
    # re-parses it.
    cmd = f"printf '%s\\n' '{escaped}' > '{remote_path}'"
    return ssh.run(cmd, timeout=timeout)


def wipe_userdata(ssh, dirs: Iterable[str]) -> None:
    """``rm -rf`` then ``mkdir -p`` each dir. Tolerates spaces in path."""
    parts = []
    for d in dirs:
        # Use single-quoted path; shell expands $HOME because we DON'T quote it
        # at the python layer (single-quote start/end around path including
        # $HOME means $HOME would NOT expand). Trick: split the $HOME prefix.
        if d.startswith("$HOME/"):
            tail = d[len("$HOME/"):]
            quoted = f'"$HOME/{tail}"'
        else:
            quoted = shlex.quote(d)
        parts.append(f"rm -rf {quoted} && mkdir -p {quoted}")
    cmd = " && ".join(parts)
    r = ssh.run(cmd, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"wipe_userdata failed (rc={r.returncode}): {r.stderr}")


def write_servers_json(
    ssh,
    dirs: Iterable[str],
    servers: Mapping[str, str],
) -> str:
    """Write ``servers.json`` (RC's persisted server list) to each dir.

    ``servers`` is ``{title: url}``. RC's on-disk format is a JSON array of
    ``{"url": ..., "title": ..., "order": N, "lastPath": "/"}``.

    Returns the JSON string written (for logging/test inspection).
    """
    entries = [
        {"url": url, "title": title, "order": idx, "lastPath": "/"}
        for idx, (title, url) in enumerate(servers.items())
    ]
    payload = json.dumps(entries, indent=2)
    for d in dirs:
        if d.startswith("$HOME/"):
            remote = d  # $HOME expansion happens remotely
        else:
            remote = d
        target = f"{remote}/servers.json"
        r = _printf_write(ssh, target, payload)
        if r.returncode != 0:
            raise RuntimeError(
                f"write_servers_json failed at {target} (rc={r.returncode}): {r.stderr}"
            )
    return payload


def _merge_config(
    user_config: Mapping[str, Any],
    *,
    currentView_url: str,
    migrations_version: str,
    first_server_url: Optional[str],
) -> dict[str, Any]:
    """Merge user-provided config with the defaults required by the Electron
    app to boot past the "Add a server" wall.

    User keys win over defaults. Internal migrations.version is ALWAYS pinned.
    """
    merged: dict[str, Any] = {
        "currentView": {"url": currentView_url},
        "isMenuBarEnabled": True,
        "isShowWindowOnUnreadChangedEnabled": True,
        "isSideBarEnabled": True,
        "isTrayIconEnabled": True,
    }
    if first_server_url:
        merged["lastSelectedServerUrl"] = first_server_url
    # User config overrides everything above EXCEPT __internal__.migrations.version.
    for k, v in user_config.items():
        merged[k] = v
    # Pin migrations.version last — non-negotiable.
    internal = dict(merged.get("__internal__") or {})
    migrations = dict(internal.get("migrations") or {})
    migrations["version"] = migrations_version
    internal["migrations"] = migrations
    merged["__internal__"] = internal
    return merged


def write_config_json(
    ssh,
    dirs: Iterable[str],
    config: Mapping[str, Any],
    *,
    currentView_url: str,
    migrations_version: str,
    first_server_url: Optional[str] = None,
) -> str:
    """Merge ``config`` with required defaults, validate JSON, write to each dir.

    Returns the JSON string written.
    """
    merged = _merge_config(
        config,
        currentView_url=currentView_url,
        migrations_version=migrations_version,
        first_server_url=first_server_url or currentView_url,
    )
    payload = json.dumps(merged, indent=2)
    # Validate round-trip.
    json.loads(payload)
    for d in dirs:
        target = f"{d}/config.json"
        r = _printf_write(ssh, target, payload)
        if r.returncode != 0:
            raise RuntimeError(
                f"write_config_json failed at {target} (rc={r.returncode}): {r.stderr}"
            )
    return payload


# ---------------------------------------------------------------------------
# CLI parsing helpers
# ---------------------------------------------------------------------------


def parse_inject_config(value: str) -> dict[str, Any]:
    """Parse ``--inject-config`` argument — either inline JSON or ``@path``."""
    if not value:
        return {}
    if value.startswith("@"):
        with open(value[1:], "r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("--inject-config must decode to a JSON object")
    return data


def parse_inject_servers(value: str) -> dict[str, str]:
    """Parse ``--inject-servers`` — either ``TITLE=URL,TITLE=URL`` or ``@path``
    pointing to a JSON object ``{title: url}``.
    """
    if not value:
        return {}
    if value.startswith("@"):
        with open(value[1:], "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("--inject-servers @file must be a JSON object")
        return {str(k): str(v) for k, v in data.items()}
    out: dict[str, str] = {}
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(
                f"--inject-servers entry {chunk!r} must be TITLE=URL"
            )
        title, url = chunk.split("=", 1)
        out[title.strip()] = url.strip()
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def inject(
    ssh,
    *,
    install_path: str,
    config: Mapping[str, Any],
    servers: Mapping[str, str],
    app_name: Optional[str] = None,
    migrations_version: Optional[str] = None,
    log_fn=None,
) -> dict[str, Any]:
    """One-shot orchestrator. Detect dirs + version, wipe, write servers, write config.

    Returns a dict report: ``{userdata_dirs, migrations_version, servers_json,
    config_json}`` for tests/logging.
    """
    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    _log(f"[inject] Detecting userData dirs from {install_path}")
    dirs = detect_userdata_dirs(ssh, install_path, app_name=app_name)
    _log(f"[inject]   dirs: {dirs}")

    if not migrations_version:
        _log("[inject] Detecting app version for migrations pin")
        migrations_version = detect_app_version(ssh, install_path)
    _log(f"[inject]   migrations.version = {migrations_version}")

    _log("[inject] Wiping userData dirs")
    wipe_userdata(ssh, dirs)

    if servers:
        _log(f"[inject] Writing servers.json ({len(servers)} server(s))")
        servers_payload = write_servers_json(ssh, dirs, servers)
    else:
        servers_payload = ""

    first_url = next(iter(servers.values())) if servers else ""
    _log("[inject] Writing config.json")
    config_payload = write_config_json(
        ssh,
        dirs,
        config,
        currentView_url=first_url or "",
        migrations_version=migrations_version,
        first_server_url=first_url or None,
    )
    _log("[inject] Done")
    return {
        "userdata_dirs": list(dirs),
        "migrations_version": migrations_version,
        "servers_json": servers_payload,
        "config_json": config_payload,
    }
