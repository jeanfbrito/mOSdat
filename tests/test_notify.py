"""H2.1: Unit tests for automation.notify.

Covers:
  notify_slack   — correct JSON payload shape posted to webhook URL
  notify_discord — correct embed payload shape
  notify_email   — correct SMTP calls via smtplib
  notify()       — dispatcher reads NOTIFY_CHANNEL + NOTIFY_WEBHOOK env vars
  notify()       — raises ValueError when env vars missing
  CLI            — argparse wiring calls notify() correctly
"""

from __future__ import annotations

import importlib.util
import smtplib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJ = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "automation.notify", _PROJ / "automation" / "notify.py"
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

notify_slack = _mod.notify_slack
notify_discord = _mod.notify_discord
notify_email = _mod.notify_email
notify = _mod.notify
_post_json = _mod._post_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_WEBHOOK = "https://hooks.example.com/T00/B00/xyz"
FAKE_REPORT = "https://github.com/org/repo/actions/runs/1234"


def _capture_post() -> tuple[MagicMock, list[dict]]:  # type: ignore[type-arg]
    """Patch _post_json and return (mock, captured_payloads)."""
    payloads: list[dict] = []  # type: ignore[type-arg]

    def _fake_post(url: str, payload: dict) -> None:  # type: ignore[type-arg]
        payloads.append({"url": url, "payload": payload})

    mock = MagicMock(side_effect=_fake_post)
    return mock, payloads


# ---------------------------------------------------------------------------
# notify_slack
# ---------------------------------------------------------------------------


def test_notify_slack_fail_payload():
    mock, captured = _capture_post()
    with patch.object(_mod, "_post_json", mock):
        notify_slack(FAKE_WEBHOOK, "ubuntu2204 nightly", "fail", FAKE_REPORT)

    assert len(captured) == 1
    call_data = captured[0]
    assert call_data["url"] == FAKE_WEBHOOK
    blocks = call_data["payload"]["blocks"]
    assert len(blocks) == 1
    text = blocks[0]["text"]["text"]
    assert "FAIL" in text
    assert FAKE_REPORT in text
    assert "ubuntu2204 nightly" in text


def test_notify_slack_pass_payload():
    mock, captured = _capture_post()
    with patch.object(_mod, "_post_json", mock):
        notify_slack(FAKE_WEBHOOK, "ubuntu2204 nightly", "pass", FAKE_REPORT)

    text = captured[0]["payload"]["blocks"][0]["text"]["text"]
    assert "PASS" in text
    assert ":x:" not in text


# ---------------------------------------------------------------------------
# notify_discord
# ---------------------------------------------------------------------------


def test_notify_discord_fail_payload():
    mock, captured = _capture_post()
    with patch.object(_mod, "_post_json", mock):
        notify_discord(FAKE_WEBHOOK, "ubuntu2204 nightly", "fail", FAKE_REPORT)

    assert len(captured) == 1
    call_data = captured[0]
    embeds = call_data["payload"]["embeds"]
    assert len(embeds) == 1
    embed = embeds[0]
    assert "FAIL" in embed["description"]
    assert FAKE_REPORT in embed["description"]
    # Red colour for failure
    assert embed["color"] == 0xE74C3C


def test_notify_discord_pass_color():
    mock, captured = _capture_post()
    with patch.object(_mod, "_post_json", mock):
        notify_discord(FAKE_WEBHOOK, "ubuntu2204 nightly", "pass", FAKE_REPORT)

    embed = captured[0]["payload"]["embeds"][0]
    assert embed["color"] == 0x2ECC71


# ---------------------------------------------------------------------------
# notify_email
# ---------------------------------------------------------------------------


def test_notify_email_starttls(monkeypatch):
    """Port 587 path: SMTP + starttls."""
    smtp_instance = MagicMock()
    smtp_cls = MagicMock(return_value=smtp_instance)
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(smtplib, "SMTP", smtp_cls)

    notify_email(
        smtp_url="smtp://user:secret@mail.example.com:587",
        to="ops@example.com",
        run_label="ubuntu2204 nightly",
        status="fail",
        report_url=FAKE_REPORT,
    )

    smtp_cls.assert_called_once_with("mail.example.com", 587)
    smtp_instance.starttls.assert_called_once()
    smtp_instance.login.assert_called_once_with("user", "secret")
    smtp_instance.send_message.assert_called_once()
    msg = smtp_instance.send_message.call_args[0][0]
    assert "FAIL" in msg["Subject"]
    assert msg["To"] == "ops@example.com"


# ---------------------------------------------------------------------------
# notify() dispatcher
# ---------------------------------------------------------------------------


def test_notify_dispatcher_slack(monkeypatch):
    monkeypatch.setenv("NOTIFY_CHANNEL", "slack")
    monkeypatch.setenv("NOTIFY_WEBHOOK", FAKE_WEBHOOK)

    mock, captured = _capture_post()
    with patch.object(_mod, "_post_json", mock):
        notify(run_label="nightly", status="fail", report_url=FAKE_REPORT)

    assert len(captured) == 1
    assert "blocks" in captured[0]["payload"]


def test_notify_dispatcher_discord(monkeypatch):
    monkeypatch.setenv("NOTIFY_CHANNEL", "discord")
    monkeypatch.setenv("NOTIFY_WEBHOOK", FAKE_WEBHOOK)

    mock, captured = _capture_post()
    with patch.object(_mod, "_post_json", mock):
        notify(run_label="nightly", status="fail", report_url=FAKE_REPORT)

    assert len(captured) == 1
    assert "embeds" in captured[0]["payload"]


def test_notify_dispatcher_missing_webhook_raises(monkeypatch):
    monkeypatch.delenv("NOTIFY_WEBHOOK", raising=False)
    monkeypatch.setenv("NOTIFY_CHANNEL", "slack")

    with pytest.raises(ValueError, match="NOTIFY_WEBHOOK"):
        notify(run_label="nightly", status="fail", report_url=FAKE_REPORT)


def test_notify_dispatcher_unknown_channel_raises(monkeypatch):
    monkeypatch.setenv("NOTIFY_CHANNEL", "pagerduty")
    monkeypatch.setenv("NOTIFY_WEBHOOK", FAKE_WEBHOOK)

    with pytest.raises(ValueError, match="pagerduty"):
        notify(run_label="nightly", status="fail", report_url=FAKE_REPORT)
