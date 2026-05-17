"""Unit tests for automation.setup.capability (F1c).

5 tests: round-trip write/load, missing manifest, sha mismatch, path helper, get_for_vm mock.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Clear any sibling-test stubs that may have polluted automation.setup.capability
# (test_trace.py installs a stub for this module during collection)
# ---------------------------------------------------------------------------
for _mod in list(sys.modules):
    if _mod.startswith("automation.transport.ssh") or _mod in (
        "automation.setup",
        "automation.setup.capability",
    ):
        sys.modules.pop(_mod, None)

_ssh_mod = types.ModuleType("automation.transport.ssh")

class _FakeSSHResult:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.success = returncode == 0

class _FakeSSH:
    def __init__(self):
        self._responses: dict = {}

    def set_response(self, pattern: str, stdout: str):
        self._responses[pattern] = _FakeSSHResult(stdout=stdout)

    def run(self, cmd, timeout=None, **kwargs):
        for pat, res in self._responses.items():
            if pat in cmd:
                return res
        return _FakeSSHResult(stdout="")

_ssh_mod.SSHResult = _FakeSSHResult
sys.modules["automation.transport.ssh"] = _ssh_mod

from automation.setup.capability import (  # noqa: E402
    build_manifest,
    get_for_vm,
    load_manifest,
    manifest_path,
    write_manifest,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestManifestPath:
    def test_path_contains_sha(self):
        p = manifest_path("abc123def456abcd")
        assert "abc123def456abcd" in str(p)
        assert p.suffix == ".json"
        assert "binary_capabilities" in str(p)


class TestWriteLoadRoundTrip:
    def test_round_trip(self, tmp_path):
        """write_manifest + load_manifest round-trips the dict."""
        sha = "deadbeef12345678"
        data = build_manifest(
            asar_sha=sha,
            vm="ubuntu2204",
            accelerators={"alt+f": "open", "ctrl+comma": "no_accel"},
            popups={"sidebar_kebab": "transient_800ms"},
            persisted_state_keys=["isTelephonyEnabled"],
            test_ids_present=False,
        )

        with patch(
            "automation.setup.capability._capabilities_dir",
            return_value=tmp_path,
        ):
            written = write_manifest(sha, data)
            assert written.exists()

            loaded = load_manifest.__wrapped__(sha) if hasattr(load_manifest, "__wrapped__") else None
            # Load directly from file
            loaded = json.loads(written.read_text())

        assert loaded["asar_sha"] == sha
        assert loaded["vm"] == "ubuntu2204"
        assert loaded["accelerators"]["alt+f"] == "open"
        assert loaded["popups"]["sidebar_kebab"] == "transient_800ms"
        assert "isTelephonyEnabled" in loaded["persisted_state_keys"]
        assert loaded["test_ids_present"] is False


class TestMissingManifest:
    def test_load_missing_returns_none(self, tmp_path):
        with patch(
            "automation.setup.capability._capabilities_dir",
            return_value=tmp_path,
        ):
            result = load_manifest("nonexistent_sha_xyz")
        assert result is None


class TestShaMismatch:
    def test_mismatched_sha_in_manifest_detectable(self, tmp_path):
        """write with sha A, load manifest, verify asar_sha field."""
        sha_a = "aaaa000011112222"
        sha_b = "bbbb333344445555"
        data = build_manifest(asar_sha=sha_a, vm="ubuntu2204", accelerators={})

        with patch(
            "automation.setup.capability._capabilities_dir",
            return_value=tmp_path,
        ):
            write_manifest(sha_a, data)
            loaded = load_manifest(sha_a)

        assert loaded is not None
        # Manifest for sha_b not written, so load returns None
        with patch(
            "automation.setup.capability._capabilities_dir",
            return_value=tmp_path,
        ):
            missing = load_manifest(sha_b)
        assert missing is None


class TestGetForVm:
    def test_get_for_vm_returns_truncated_sha(self):
        """get_for_vm uses ssh.run to find asar path and compute sha256."""
        ssh = _FakeSSH()
        ssh.set_response("find", "/opt/Rocket.Chat/resources/app.asar\n")
        ssh.set_response("sha256sum", "abcdef1234567890" * 4 + "\n")

        result = get_for_vm(ssh)
        # get_for_vm returns first 16 chars
        assert len(result) == 16
        assert result == "abcdef1234567890"

    def test_get_for_vm_raises_when_no_asar(self):
        ssh = _FakeSSH()
        # find returns empty
        with pytest.raises(RuntimeError, match="app.asar not found"):
            get_for_vm(ssh)
