---
date: "2026-05-17"
project: mosdat
topic: 'body ''Add `accelerator: "CmdOrCtrl+,"` to the Settings menu item so the standard macOS Preferences shortcut (⌘,) also'
kind: war-story
scope: project-shared
confidence: medium
---

git push origin feat/preferences-accelerator-linux
gh pr create --draft \
  --title 'feat(menu): register CmdOrCtrl+, accelerator for Settings on all platforms' \
  --body 'Add `accelerator: "CmdOrCtrl+,"` to the Settings menu item so the standard macOS Preferences shortcut (⌘,) also works on Linux and Windows. Single-line change in menuBar.ts / menus.ts. Root cause of keyboard-navigation failures in automated tests on Linux.'
```

Once you have the repo cloned, I can do the actual file edit (step 3) directly from here — just let me know.</result>
<usage><total_tokens>18086</total_tokens><tool_uses>1</tool_uses><duration_ms>70588</duration_ms></usage>
</task-notification>
