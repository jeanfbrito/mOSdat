"""F1c — capability manifest for binary input probing results.

Stores and retrieves the output of ``mosdat trace --write-manifest`` so that
``mosdat lint`` can consult known-good/known-bad accelerator states without
re-running the probe against the VM.

Manifest path: ``shared/binary_capabilities/<asar_sha>.json``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _capabilities_dir() -> Path:
    return _PROJECT_ROOT / "shared" / "binary_capabilities"


def manifest_path(binary_sha: str) -> Path:
    """Return the canonical path for a capability manifest given the asar SHA."""
    return _capabilities_dir() / f"{binary_sha}.json"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def load_manifest(sha: str) -> Optional[dict]:
    """Load manifest for *sha*. Returns ``None`` if the file doesn't exist."""
    path = manifest_path(sha)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_manifest(sha: str, data: dict) -> Path:
    """Write *data* to ``shared/binary_capabilities/<sha>.json``.

    Creates the directory if needed. Overwrites any existing manifest for
    the same SHA. Returns the path written.
    """
    path = manifest_path(sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return path


# ---------------------------------------------------------------------------
# SHA computation via SSH
# ---------------------------------------------------------------------------

def get_for_vm(ssh) -> str:
    """Compute the SHA-256 of app.asar on *ssh* VM.

    Searches common Electron install locations for app.asar, then runs
    ``sha256sum`` on the first match. Returns the hex digest (first 16 chars
    used as the manifest key for readability).

    Raises ``RuntimeError`` if no asar is found or SHA cannot be computed.
    """
    find_cmd = (
        "find /opt/Rocket.Chat /opt/rocketchat /usr/lib/rocketchat "
        "    ~/.local/lib/rocketchat ~/.var/app 2>/dev/null "
        "    -name 'app.asar' 2>/dev/null | head -1"
    )
    result = ssh.run(find_cmd, timeout=15)
    asar_path = result.stdout.strip()
    if not asar_path:
        raise RuntimeError("app.asar not found on VM; is the app installed?")

    sha_result = ssh.run(f"sha256sum {asar_path} 2>/dev/null | awk '{{print $1}}'", timeout=30)
    sha = sha_result.stdout.strip()
    if not sha or len(sha) < 16:
        raise RuntimeError(f"sha256sum failed on {asar_path}: {sha_result.stderr.strip()}")

    # Return truncated SHA for usable filenames (first 16 hex chars = 64-bit collision resistance)
    return sha[:16]


# ---------------------------------------------------------------------------
# Manifest builder helpers
# ---------------------------------------------------------------------------

def build_manifest(
    asar_sha: str,
    vm: str,
    accelerators: dict,
    popups: Optional[dict] = None,
    persisted_state_keys: Optional[list] = None,
    test_ids_present: bool = False,
    hover_required_elements: Optional[list] = None,
) -> dict:
    """Build a manifest dict ready for ``write_manifest``."""
    manifest: dict = {
        "asar_sha": asar_sha,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "vm": vm,
        "accelerators": accelerators,
        "popups": popups or {},
        "persisted_state_keys": persisted_state_keys or [],
        "test_ids_present": test_ids_present,
    }
    if hover_required_elements is not None:
        manifest["hover_required_elements"] = hover_required_elements
    return manifest
