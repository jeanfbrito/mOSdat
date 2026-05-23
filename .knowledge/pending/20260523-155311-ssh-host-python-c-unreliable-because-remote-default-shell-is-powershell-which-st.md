---
date: "2026-05-23"
project: mosdat
topic: '`ssh host python -c "..."`** unreliable because remote default shell is PowerShell, which strips/reinterprets the quote'
kind: decision
scope: project-shared
confidence: low
---

## Two non-blocking issues uncovered
- **winget broken** on this VM (`0x8a15000f`). Future installs on this box: use direct installers, or run `winget source reset --force` to repair.
- **`ssh host python -c "..."`** unreliable because remote default shell is PowerShell, which strips/reinterprets the quote layer. The literal command in the task spec fails for this reason — not a pywinauto problem. Use SCP'd `.py` files or properly escaped PowerShell-aware quoting.

VM not shut down. Win11 not touched.</result>
<usage><total_tokens>57571</total_tokens><tool_uses>20</tool_uses><duration_ms>229539</duration_ms></usage>
</task-notification>
