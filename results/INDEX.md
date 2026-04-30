# Test Results Index

**Generated**: 2026-04-23 21:02:44  
**Total runs aggregated**: 9  
**Last run timestamp**: 2026-04-14T17:24:12  
**Results directory**: `results`

## Overview

| Run                                   | Version        | Pass | Fail | Total | Pass% | Source     | Last Updated        |
| ------------------------------------- | -------------- | ---- | ---- | ----- | ----- | ---------- | ------------------- |
| 2026-01-16_fedora42                   | unknown        | 0    | 0    | 0     | N/A   | REPORT.md  |                     |
| 2026-01-16_ubuntu2204                 | unknown        | 0    | 0    | 0     | N/A   | REPORT.md  |                     |
| 2026-01-19_gpu-passthrough            | unknown        | 0    | 0    | 0     | N/A   | REPORT.md  |                     |
| 2026-01-19_opensuse-leap              | unknown        | 0    | 0    | 0     | N/A   | REPORT.md  |                     |
| 2026-01-19_ubuntu2404                 | unknown        | 0    | 0    | 0     | N/A   | REPORT.md  |                     |
| 2026-01-29_full-matrix-4.12.0-alpha.2 | 4.12.0-alpha.2 | 13   | 9    | 22    | 59%   | REPORT.md  | 2026-01-29 13:16:35 |
| 2026-04-09_fedora42-test              | 4.12.0-alpha.2 | 3    | 1    | 22    | 14%   | REPORT.md  | 2026-04-09T13:20:43 |
| 2026-04-09_full-matrix-4.12.0-alpha.2 | 4.12.0-alpha.2 | 0    | 4    | 22    | 0%    | REPORT.md  | 2026-04-09T13:18:26 |
| 2026-04-14_full-matrix-4.12.0-alpha.2 | 4.12.0-alpha.2 | 0    | 0    | 0     | N/A   | state.json | 2026-04-14T17:24:12 |

## Matrix

Rows = runs. Columns = OS-Package-GPU combos. Values = PASS/FAIL/SKIP/-.

| Run                                   | fedora42-appimage-gpu | fedora42-appimage-no-gpu | fedora42-rpm-gpu | fedora42-rpm-no-gpu | manjaro-appimage-gpu | manjaro-appimage-no-gpu | opensuse-appimage-gpu | opensuse-appimage-no-gpu | opensuse-rpm-gpu | opensuse-rpm-no-gpu | ubuntu2204-appimage-gpu | ubuntu2204-appimage-no-gpu | ubuntu2204-deb-gpu | ubuntu2204-deb-no-gpu | ubuntu2204-snap-gpu | ubuntu2204-snap-no-gpu | ubuntu2404-appimage-gpu | ubuntu2404-appimage-no-gpu | ubuntu2404-deb-gpu | ubuntu2404-deb-no-gpu | ubuntu2404-snap-gpu | ubuntu2404-snap-no-gpu |
| ------------------------------------- | --------------------- | ------------------------ | ---------------- | ------------------- | -------------------- | ----------------------- | --------------------- | ------------------------ | ---------------- | ------------------- | ----------------------- | -------------------------- | ------------------ | --------------------- | ------------------- | ---------------------- | ----------------------- | -------------------------- | ------------------ | --------------------- | ------------------- | ---------------------- |
| 2026-01-16_fedora42                   | -                     | -                        | -                | -                   | -                    | -                       | -                     | -                        | -                | -                   | -                       | -                          | -                  | -                     | -                   | -                      | -                       | -                          | -                  | -                     | -                   | -                      |
| 2026-01-16_ubuntu2204                 | -                     | -                        | -                | -                   | -                    | -                       | -                     | -                        | -                | -                   | -                       | -                          | -                  | -                     | -                   | -                      | -                       | -                          | -                  | -                     | -                   | -                      |
| 2026-01-19_gpu-passthrough            | -                     | -                        | -                | -                   | -                    | -                       | -                     | -                        | -                | -                   | -                       | -                          | -                  | -                     | -                   | -                      | -                       | -                          | -                  | -                     | -                   | -                      |
| 2026-01-19_opensuse-leap              | -                     | -                        | -                | -                   | -                    | -                       | -                     | -                        | -                | -                   | -                       | -                          | -                  | -                     | -                   | -                      | -                       | -                          | -                  | -                     | -                   | -                      |
| 2026-01-19_ubuntu2404                 | -                     | -                        | -                | -                   | -                    | -                       | -                     | -                        | -                | -                   | -                       | -                          | -                  | -                     | -                   | -                      | -                       | -                          | -                  | -                     | -                   | -                      |
| 2026-01-29_full-matrix-4.12.0-alpha.2 | PASS                  | PASS                     | FAIL             | PASS                | PASS                 | PASS                    | FAIL                  | PASS                     | FAIL             | PASS                | FAIL                    | PASS                       | FAIL               | PASS                  | FAIL                | PASS                   | FAIL                    | PASS                       | FAIL               | PASS                  | FAIL                | PASS                   |
| 2026-04-09_fedora42-test              | PASS                  | PASS                     | FAIL             | PASS                | -                    | -                       | -                     | -                        | -                | -                   | -                       | -                          | -                  | -                     | -                   | -                      | -                       | -                          | -                  | -                     | -                   | -                      |
| 2026-04-09_full-matrix-4.12.0-alpha.2 | FAIL                  | FAIL                     | FAIL             | FAIL                | -                    | -                       | -                     | -                        | -                | -                   | -                       | -                          | -                  | -                     | -                   | -                      | -                       | -                          | -                  | -                     | -                   | -                      |
| 2026-04-14_full-matrix-4.12.0-alpha.2 | -                     | -                        | -                | -                   | -                    | -                       | -                     | -                        | -                | -                   | -                       | -                          | -                  | -                     | -                   | -                      | -                       | -                          | -                  | -                     | -                   | -                      |

## Flaky Tests

Tests that passed in some runs and failed in others.

| Cell                     | Pass Count | Fail Count | Passed In                                                       | Failed In                             |
| ------------------------ | ---------- | ---------- | --------------------------------------------------------------- | ------------------------------------- |
| fedora42-appimage-gpu    | 2          | 1          | 2026-01-29_full-matrix-4.12.0-alpha.2, 2026-04-09_fedora42-test | 2026-04-09_full-matrix-4.12.0-alpha.2 |
| fedora42-appimage-no-gpu | 2          | 1          | 2026-01-29_full-matrix-4.12.0-alpha.2, 2026-04-09_fedora42-test | 2026-04-09_full-matrix-4.12.0-alpha.2 |
| fedora42-rpm-no-gpu      | 2          | 1          | 2026-01-29_full-matrix-4.12.0-alpha.2, 2026-04-09_fedora42-test | 2026-04-09_full-matrix-4.12.0-alpha.2 |

---

_Generated by `aggregate_results.py`_
