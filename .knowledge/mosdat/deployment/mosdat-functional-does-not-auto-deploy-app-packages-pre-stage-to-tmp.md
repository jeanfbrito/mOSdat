---
date: "2026-05-02"
project: mOSdat
tags:
  - mosdat
  - deployment
  - scp
  - appimage
topic: mOSdat functional does NOT auto-deploy app packages — pre-stage to /tmp/
kind: lesson
scope: project-shared
category: mosdat/deployment
confidence: high
---

## Lesson (2026-05-02 manjaro debugging)
`mosdat functional` assumes the app installer (.deb/.rpm/.AppImage/.exe) is already present in the VM's `/tmp/` (or `C:\\tmp\\` on Windows). It does NOT scp the file. The TOML `install` command runs against an existing file. The runner globs `/tmp/{file_glob}` to resolve `{file}` in `app_path` — if no file matches, `{file}` stays unresolved and launch fails.

## Implication
For first-time testing on a new VM, manually scp the package:
```bash
sshpass -p "$DEFAULT_VM_PASSWORD" scp dist/rocketchat-*.AppImage jean@<vm-ip>:/tmp/
```
Or extend mosdat with a deploy step (not yet implemented).

## Takeaway
Mosdat's mental model: VMs are pre-provisioned + pre-staged with the artifact. The tool drives test execution, not artifact distribution. Don't expect `mosdat functional` to fail with "package not found" — it fails downstream with `{file}` placeholder unresolved or app_path missing.
