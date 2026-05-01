---
date: "2026-05-01"
project: mOSdat
tags:
  - mosdat
  - results
  - conventions
  - retention
topic: 'mosdat: results layout = results/{smoke,functional,gpu-passthrough}/&lt;date&gt;_&lt;label&gt;/'
kind: decision
scope: project-shared
category: conventions
confidence: high
accessed: 14
last_accessed: "2026-05-01"
---

## Decision (2026-04-30, commit 34b55f2)
Old: 12 dirs at top level with three naming schemes (theme, per-OS, matrix), 130MB.

New layout:
```
results/
├── INDEX.md
├── README.md          ← naming + retention policy
├── smoke/             ← multi-OS matrix + per-OS smoke
├── functional/        ← VLM-driven UI flows
└── gpu-passthrough/   ← hardware verify
```

Convention: `results/<run-type>/<YYYY-MM-DD>_<label>/`

## Enforced by code
- `automation/config.py` default `results_dir` writes to `results/smoke/...`
- `cmd_functional` in `main.py` writes screenshot dirs to `results/functional/...`
- `automation/reporting/aggregate.py` walks typed subdirs (legacy-flat fallback)

## Retention
`.gitignore` rules: `results/**/*.{png,deb,rpm,AppImage,snap,exe,msi,dmg}`. 60 binary files untracked via `git rm --cached` (kept on disk locally).

## Takeaway
Convention enforced at writers, not just docs. Aggregator handles legacy + new. Binary artifacts never enter git.
