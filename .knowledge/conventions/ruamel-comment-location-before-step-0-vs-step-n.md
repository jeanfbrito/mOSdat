---
date: "2026-05-17"
project: mosdat
topic: '1. **ruamel comment location**: Comments before step 0 live on `steps_seq.ca.comment[1]`; comments before step N&gt;0'
kind: insight
scope: project-shared
confidence: medium
---

Key decisions:

1. **ruamel comment location**: Comments before step 0 live on `steps_seq.ca.comment[1]`; comments before step N&gt;0 live on item N-1's `ca.items[last_key][2]` (ruamel's "after-value-on-next-line" slot). This is not documented — discovered by inspection.

2. **Box regex guard**: Added `\w` requirement inside the label group (`.*\w.*`) to reject pure-separator lines like `# ════════` that would otherwise match with a single `═` as the label.

3. **`step_label` vs `label`**: The existing `step_start` `label` field carries localize/shell text (used internally by the runner). Added a new `step_label` field (`"N: human label"`) alongside it so the HTML renderer can distinguish them without breaking existing event consumers.
