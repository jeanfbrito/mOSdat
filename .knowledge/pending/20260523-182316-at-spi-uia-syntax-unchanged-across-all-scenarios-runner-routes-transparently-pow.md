---
date: "2026-05-23"
project: mosdat
topic: AT-SPI/UIA syntax** unchanged across all scenarios — runner routes transparently. PowerShell shell blocks parse
kind: decision
scope: project-shared
confidence: low
---

- 003 step4d: original P/Invoke `Add-Type` here-string broke YAML `|` block-scalar indentation (scanner error at line 257). Pivoted to `(New-Object -ComObject WScript.Shell).AppActivate($rc.Id)` — one strike, recovered cleanly. No 2nd-strike escalation needed.
- Protocol probe uses `HKCU\...\UrlAssociations\&lt;scheme&gt;\UserChoice` registry read; absent key = PASS (matches xdg-mime empty semantics).

**AT-SPI/UIA syntax** unchanged across all scenarios — runner routes transparently. PowerShell shell blocks parse correctly under YAML `|` because all body lines are uniformly indented.

**Deferred**: live run (task #54, tester's job).

**Findings appended**: `/home/jean/projects/linux-testing/mOSdat/.claude/mytasks/findings.md` under `# windows10 batch A — TEL-QA 001-004 (2026-05-23)`.</result>
<usage><total_tokens>97626</total_tokens><tool_uses>31</tool_uses><duration_ms>478180</duration_ms></usage>
