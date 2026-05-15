"""Tests for _exit_code() verdict→exit-code mapping in automation/issue_confirm.py."""

from __future__ import annotations

import pytest

from tests._ic_bootstrap import (
    _exit_code,
    _restore_real_automation_modules,  # noqa: F401 — autouse fixture
)


class TestExitCodeMapping:
    def test_confirm_confirmed_exit0(self):
        assert _exit_code("CONFIRMED", "confirm") == 0

    def test_confirm_not_reproduced_exit1(self):
        assert _exit_code("NOT_REPRODUCED", "confirm") == 1

    def test_verify_fix_not_reproduced_exit0(self):
        assert _exit_code("NOT_REPRODUCED", "verify-fix") == 0

    def test_verify_fix_confirmed_exit1(self):
        assert _exit_code("CONFIRMED", "verify-fix") == 1

    def test_intermittent_exit2(self):
        assert _exit_code("INTERMITTENT", "confirm") == 2
        assert _exit_code("INTERMITTENT", "verify-fix") == 2

    def test_harness_error_exit3(self):
        assert _exit_code("HARNESS_ERROR", "confirm") == 3

    def test_inconclusive_exit3(self):
        assert _exit_code("INCONCLUSIVE", "confirm") == 3
