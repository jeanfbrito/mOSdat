"""Regression test for RC.Electron #3308 screen-share picker.

Marked @pytest.mark.live — requires the fedora42 VM and is skipped
unless `--live` is passed.
"""
import pytest

from automation.issue_confirm import ConfirmInvocation, run_confirm


pytestmark = pytest.mark.live


def test_3308_screenshare_picker_should_not_open_on_launch():
    """Verify-fix: bug must NOT be visible on launch.
    Today this FAILS (bug present on Flatpak 4.14.0). After PR #3313 lands
    and a fixed flatpak is installed on fedora42, this test should PASS.
    """
    inv = ConfirmInvocation(
        issue_id_or_url="3308",
        vm_name="fedora42",
        iterations=2,
        mode="verify-fix",
        repro_command="pytest -m issue --live",
    )
    artifacts = run_confirm(inv)
    assert artifacts.verdict == "NOT_REPRODUCED", (
        f"Bug {artifacts.verdict} — see {artifacts.report_md_path}"
    )
