---
date: "2026-05-23"
project: mosdat
topic: '`shared/scenarios/functional/3325-diagnostics-panel.yaml` — 13 active AT-SPI steps that PASS today (covers existing'
kind: open-loop
scope: project-shared
confidence: medium
---

## What landed

- `shared/scenarios/functional/3325-diagnostics-panel.yaml` — 13 active AT-SPI steps that PASS today (covers existing telephony widgets: checkbox, server combobox, shortcut entry). Diagnostics-specific steps 14-19 commented out with `# TODO: requires PR build with diagnostics panel`, ready to uncomment when binary deployed.
- Header comments document: `qa_flow: TEL-QA-004`, PR URL, drift state.
- All telephony widgets accessible via AT-SPI (no canvas opacity).
- Tree dumps saved: `/tmp/rc-tree-baseline.txt`, `/tmp/rc-tree-settings.txt`.

## Regression
