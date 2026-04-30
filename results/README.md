# Test Results

Run output organized by type. Each run produces a dated dir with logs,
JSON state, optional screenshots, and a summary report.

## Layout

```
results/
├── INDEX.md             — handcurated cross-run summary
├── smoke/               — multi-OS smoke matrix runs and per-OS smoke
├── functional/          — VLM-driven UI flow runs
└── gpu-passthrough/     — hardware verification runs
```

## Naming convention

```
results/<run-type>/<YYYY-MM-DD>_<label>/
```

Where:
- `<run-type>` is one of `smoke`, `functional`, `gpu-passthrough`
- `<label>` describes scope: `full-matrix-<version>`, `<distro>`, `<app>`, etc.

Examples:
- `results/smoke/2026-04-14_full-matrix-4.12.0-alpha.2/`
- `results/functional/2026-04-14_rocketchat/`
- `results/gpu-passthrough/2026-01-19/`

## Retention

Binary artifacts are NOT tracked in git. Each run dir keeps text-only
artifacts: logs, JSON state, REPORT.md, RESULTS.md.

The following are gitignored:
- `*.png` (screenshots — reproducible from logs)
- `*.deb`, `*.rpm`, `*.AppImage`, `*.snap` (rebuildable packages)
- `*.exe`, `*.msi`, `*.dmg` (rebuildable packages)

If you need to share screenshots or packages from a run, archive externally
(GitHub Releases, S3, etc) and link from the run's REPORT.md.
