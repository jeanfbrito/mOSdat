# Trend Dashboard (H2.2)

Static HTML dashboard aggregating functional run data from `results/functional/`.

## How to run

```bash
mosdat dashboard --root results/ --output results/functional/dashboard.html
```

Defaults: `--root results/` and output at `<root>/functional/dashboard.html`.

Open the HTML file in any browser — it is fully self-contained (CSS inline, ChartJS via CDN).

## What it reads

Walks `results/functional/<YYYY-MM-DD*>/<vm>/events.jsonl` for all dated run directories.
Each `events.jsonl` is a newline-delimited JSON log written by the functional runner with
`step_start`, `step_end`, and `retry` event records.

Optional: smoke and other scenario variants (upload, search, channel-create, mention) are
included automatically when their `events.jsonl` files appear alongside the standard smoke runs.

## Dashboard sections

| Section | What it shows |
|---------|---------------|
| Pass-rate trend | Line chart per VM — pass rate (%) over time across runs |
| Top-10 flakiest steps | Bar chart of retry rate (retries / total executions) |
| Mean step duration | Bar chart (top 15) + full table of average wall-clock time per step |
| Regression flags | Steps whose pass rate dropped >10 pp in the last 7 days vs the prior 30-day baseline |

## Interpreting results

- **Pass-rate trend**: a downward slope on a VM line indicates worsening reliability; flat or
  upward is healthy.
- **Retry rate**: steps near the top are flaky and consume extra VLM budget. Investigate
  localization prompt quality or timing delays for those steps.
- **Mean duration**: unusually slow steps may indicate VLM cold-start or network latency.
- **Regression flags**: any entry here warrants immediate investigation. The drop threshold
  is 10 percentage points over the last 7 days compared to the 30-day baseline.

## Retention and cleanup

The dashboard HTML is a derived artifact — regenerate it at any time from the raw
`events.jsonl` files. There is no need to commit `dashboard.html` to git.

Run directories under `results/functional/` are the source of truth. Archive or delete
runs older than your retention policy; the dashboard will automatically exclude removed
run directories on the next generation.

## Programmatic use

```python
from automation.reporting.dashboard import aggregate_runs, render_dashboard, detect_regressions
from pathlib import Path

agg = aggregate_runs(Path("results"))
regressions = detect_regressions(agg, recent_days=7, baseline_days=30)
render_dashboard(agg, Path("results/functional/dashboard.html"))
```

## Regression alerts (`--alert`)

Pass `--alert` to send a notification via `automation/notify.py` whenever regressions
are detected. Notifications are sent only when at least one of `NOTIFY_WEBHOOK` (Slack
or Discord) or `NOTIFY_EMAIL_SMTP` (email) is present in the environment.

```bash
NOTIFY_WEBHOOK=https://hooks.slack.com/... \
  mosdat dashboard --root results/ --alert
```

Without a webhook/SMTP env var, `--alert` is a safe no-op — the flag is opt-in and
default behavior is unchanged.

### Configurable thresholds

| Flag | Default | Meaning |
|------|---------|---------|
| `--threshold-multiplier N` | `1.5` | Alert when a step's recent mean duration exceeds baseline mean × N |
| `--min-pass-rate R` | `0.85` | Alert when a VM's recent mean pass rate drops below R (0.0–1.0) |
| `--report-url URL` | *(dashboard HTML path)* | URL included in the notification body |

### What triggers an alert

Three regression kinds are detected:

- **pass_rate_drop** — a step's overall pass rate dropped >10 pp in the last 7 days vs
  the prior 30-day baseline.
- **duration_regression** — a step's mean duration in the last 7 days exceeded
  `--threshold-multiplier` × the baseline mean.
- **vm_pass_rate** — a VM's recent mean pass rate fell below `--min-pass-rate`.

The notification body includes a plain-text summary listing each regression with its
step/VM name and the measured change.
