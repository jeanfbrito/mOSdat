# Scheduled CI Smoke — Runbook

## Overview

`.github/workflows/scheduled-smoke.yml` runs the RocketChat smoke scenario
nightly at 06:00 UTC and on every RC release ping. On failure it posts a
notification to the configured channel and uploads the full `results/functional/`
tree as a workflow artifact.

---

## Required GitHub Secrets

Set these in **Settings → Secrets and variables → Actions** for the repository.

| Secret | Description |
|--------|-------------|
| `MOSDAT_VLM_BASE_URL` | Base URL of the llama-swap / OpenAI-compatible VLM endpoint (e.g. `https://vlm.internal:8080`) |
| `MOSDAT_PROXMOX_HOST` | Proxmox API hostname or IP |
| `MOSDAT_PROXMOX_USER` | Proxmox API user (e.g. `root@pam`) |
| `MOSDAT_PROXMOX_PASSWORD` | Proxmox API password |
| `MOSDAT_FUNCTIONAL_WORKSPACE_URL` | RocketChat workspace URL to test against |
| `MOSDAT_FUNCTIONAL_TEST_USER` | Login username for the smoke account |
| `MOSDAT_FUNCTIONAL_TEST_PASSWORD` | Login password for the smoke account |
| `NOTIFY_CHANNEL` | `slack`, `discord`, or `email` |
| `NOTIFY_WEBHOOK` | Incoming webhook URL (Slack or Discord) |
| `NOTIFY_EMAIL_SMTP` | SMTP URL, e.g. `smtp://user:pass@mail.example.com:587` (email channel only) |
| `NOTIFY_EMAIL_TO` | Recipient address (email channel only) |

---

## Triggering Manually

1. Go to **Actions → Scheduled Smoke** in the GitHub UI.
2. Click **Run workflow** → choose branch → **Run workflow**.

Or via CLI:

```bash
gh workflow run scheduled-smoke.yml --ref main
```

---

## Triggering on RC Release (downstream ping)

Send a `repository_dispatch` event with type `rc-release`:

```bash
gh api repos/ORG/REPO/dispatches \
  --method POST \
  -f event_type=rc-release \
  -f client_payload='{"version":"6.12.0-rc.1"}'
```

---

## Interpreting the Artifact

After the run completes (pass or fail), download the artifact
`functional-results-<run-id>` from the **Summary** page.

Structure:

```
results/functional/
  <timestamp>_functional/
    ubuntu2204/
      step-01-*.png          # screenshots per step
      step-02-*.png
      report.html            # open in browser for full narrative
```

Open `report.html` locally. Each step shows: description, screenshot, pass/fail.
Steps highlighted in red are where the scenario diverged from expected state.

---

## Notification Payload

**Slack** — single section block with run label, status badge, and link.

**Discord** — single embed with green (pass) or red (fail) colour.

**Email** — plain-text message with subject `[mOSdat] smoke FAIL — <label>`.

The notifier is also callable standalone for ad-hoc testing:

```bash
NOTIFY_CHANNEL=slack NOTIFY_WEBHOOK=https://hooks.slack.com/... \
  python -m automation.notify \
    --status fail \
    --label "ubuntu2204 nightly" \
    --report-url "https://github.com/ORG/REPO/actions/runs/12345"
```
