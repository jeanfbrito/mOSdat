---
date: "2026-05-18"
project: mosdat
topic: Routine input serialization broke by str(v) coercion + jinja tojson double-encoding
kind: war-story
scope: project-shared
confidence: high
---

`automation/routines/runner.py:135` did `render_vars = {**parent_vars, **{k: str(v) for k, v in resolved_inputs.items()}}`. For a list-of-dicts `servers` input on `launch-rocketchat`, `str(v)` produced Python repr with single quotes (`"[{'title': 'Workspace', ...}]"`). Then jinja `{{ servers | tojson }}` JSON-encoded that **string** — yielding a double-quoted shell-broken payload. `json.loads` returned a string, downstream `s[0]['url']` raised TypeError, config-writer step exited 1, RC launched without config and died silently (process_not_running at first verify, no clear error in runner log).

Fix: pass native types in both runner.py:135 and `automation/runners/var_subst.py:81`. Only coerce SCALARS to str; leave lists/dicts native so `tojson` receives the actual structure.

Takeaway: when routing values through a Jinja env, NEVER blanket-`str()` complex types. Jinja prints scalars cleanly via `{{ x }}` and handles lists/dicts correctly via filters like `tojson`. Coercion-on-entry breaks filter semantics. Fixed in commit `8c47ccd`.
