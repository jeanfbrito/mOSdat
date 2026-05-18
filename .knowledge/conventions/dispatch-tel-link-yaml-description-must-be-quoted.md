---
date: "2026-05-17"
project: mosdat
topic: 'One note: `dispatch-tel-link.yaml` description required quoting (`"Dispatch a tel: or callto: ..."`) because bare'
kind: decision
scope: project-shared
confidence: low
---

`mosdat routines list` shows all 4 with correct descriptions and tags. Schema tests: 7/7 passed.

One note: `dispatch-tel-link.yaml` description required quoting (`"Dispatch a tel: or callto: ..."`) because bare colons in YAML scalar values are parsed as mapping indicators — found and fixed before final validation.</result>
<usage><total_tokens>41018</total_tokens><tool_uses>34</tool_uses><duration_ms>142711</duration_ms></usage>
</task-notification>

Assistant: R2 (4 base routines) shipped. R3 + R4 still running.
