---
date: "2026-05-23"
project: mosdat
topic: Made `SSHClient` a context manager** (`__enter__`/`__exit__` call `close_persistent`). Cheap addition, lets future call
kind: decision
scope: project-shared
confidence: low
---

- `/home/jean/projects/linux-testing/mOSdat/tests/test_ssh_control_master.py` — new (16 tests).

### Key decisions
- **Made `SSHClient` a context manager** (`__enter__`/`__exit__` call `close_persistent`). Cheap addition, lets future call sites use `with SSHClient(..., persistent=True) as ssh:`. At the 5 wiring sites I kept explicit `try/finally + close_persistent()` because the runner+SSH lifetimes don't always fit one `with` block (VNC context manager is the outer scope) and changing the control flow there would have exceeded the surgical scope.
- **Issue-confirm helper kept its tuple shape** — attached `_ssh_atspi` as an attribute on the runner to avoid breaking existing `monkeypatch.object(_ic_mod, "_build_runner_for_vm", ...)` test patches.
- **Did NOT make existing shell `ssh` persistent** anywhere (per the brief — heavy commands could hold the master longer than wanted).
- **`persistent` is keyword-only** with default `False`, so all 17 unrelated `SSHClient(...)` call sites continue to work unchanged.

### New tests (all in `tests/test_ssh_control_master.py`)
