---
date: "2026-05-02"
project: mOSdat
tags:
  - mosdat
  - appimage
  - templating
  - ssh
topic: mOSdat AppImage `{file}` placeholder requires SSH glob resolution at runtime
kind: lesson
scope: project-shared
category: mosdat/templating
confidence: high
accessed: 1
last_accessed: "2026-05-03"
---

## Lesson (2026-05-02 manjaro)
TOML AppImage entries declare `app_path = "/tmp/{file}"` where `{file}` should be substituted with the actual filename matched by `file_glob` (e.g. `rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage`). The runner originally never substituted — launch step received the literal `/tmp/{file}` and failed with binary-not-found.

## Fix (commit 14addce)
After install completes, glob the VM's temp dir over SSH:
```python
result = ssh.run(f"ls {temp_dir}/{pkg.file_glob} 2>/dev/null | head -1")
resolved_file = result.stdout.strip()
if resolved_file:
    vm_vars["app_path"] = vm_vars["app_path"].replace("{file}", os.path.basename(resolved_file))
```
Silent fallback — leave unresolved if glob fails; launch step will fail with a clear message.

## Takeaway
mOSdat's templating is consumed at YAML-render time, but `{file}` for AppImage needs a runtime glob (filename varies per build). Bake substitution into the runner's pre-launch step, not into static config.
