"""Tests for CLI argument parsing of the 'confirm' subcommand in automation/main.py."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._ic_bootstrap import (
    _ic_mod,
    _PROJ,
    _restore_real_automation_modules,  # noqa: F401 — autouse fixture
)


class TestCLIArgParsing:
    """Load main.py and verify that 'confirm' args parse correctly."""

    def _build_parser(self):
        spec = importlib.util.spec_from_file_location(
            "automation.main",
            _PROJ / "automation" / "main.py",
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "automation"
        sys.modules.setdefault("automation.issue_confirm", _ic_mod)

        with (
            patch("automation.config.load_config", return_value=MagicMock()),
            patch("automation.state.StateManager", MagicMock()),
        ):
            spec.loader.exec_module(mod)
        return mod

    def test_confirm_args_parse(self):
        """mosdat confirm 3308 --vm fedora42 --no-state-snapshot --output /tmp/foo"""
        self._build_parser()

        parser = argparse.ArgumentParser(prog="mosdat")
        sub = parser.add_subparsers(dest="command")

        confirm_p = sub.add_parser("confirm")
        confirm_p.add_argument("issue")
        confirm_p.add_argument("--vm", required=True)
        confirm_p.add_argument("--iterations", type=int, default=None)
        confirm_p.add_argument("--mode", choices=["confirm", "verify-fix", "regression"],
                               default="confirm")
        confirm_p.add_argument("--scenario", type=Path, default=None)
        confirm_p.add_argument("--output", type=Path, default=None)
        confirm_p.add_argument("--refresh-issue-context", action="store_true",
                               dest="refresh_issue_context")
        confirm_p.add_argument("--html", action="store_true")
        confirm_p.add_argument("--no-state-snapshot", action="store_true",
                               dest="no_state_snapshot")

        args = parser.parse_args([
            "confirm", "3308", "--vm", "fedora42",
            "--no-state-snapshot", "--output", "/tmp/foo",
        ])

        assert args.command == "confirm"
        assert args.issue == "3308"
        assert args.vm == "fedora42"
        assert args.no_state_snapshot is True
        assert args.output == Path("/tmp/foo")
        assert args.mode == "confirm"
        assert args.refresh_issue_context is False
        assert args.html is False
        assert args.iterations is None
