# Visual Regression Runbook

Visual regression catches UI redesigns that VLM-based tests tolerate. Each scenario step's
screenshot is compared against a stored reference using SSIM. A score below the threshold
(default 0.95) fails the check.

---

## Capture references

Run a functional test with `--screenshots <dir>` to save step screenshots, then capture them
as references:

```bash
# Save screenshots from a functional run
mosdat functional config.toml --vms fedora42 --test rocketchat-smoke-linux --screenshots /tmp/run1

# Capture references for one scenario
mosdat visual --capture /tmp/run1/fedora42 --refs-dir shared/references
```

This writes `shared/references/<scenario_dir_name>/step_0.png`, `step_1.png`, ... for each
`*.png` file found in the scenario directory (sorted, 0-indexed).

---

## Check against references

```bash
mosdat visual --check /tmp/run2/fedora42 --refs-dir shared/references
```

Output per step:

```
[visual] step 0: PASS  SSIM=0.9987  (threshold=0.95)
[visual] step 1: FAIL  SSIM=0.8421  (threshold=0.95)
```

Exit code 0 = all passed. Exit code 1 = one or more steps failed or a reference is missing.

---

## Adjust threshold

```bash
mosdat visual --check /tmp/run2/fedora42 --threshold 0.90
```

Use a lower threshold (e.g. 0.90) for scenarios with dynamic content (timestamps, avatars).
Use 0.98+ for pixel-stable UI like login screens.

---

## When to update references

Update references when:

- A UI redesign is **intentional** and reviewed by the team.
- A layout change is expected (e.g. after a dependency upgrade).

Do **not** update references to silence a failing check without understanding why it failed.
The purpose of visual regression is to surface unintentional changes.

To update: re-run capture with the new screenshots overwriting the old references.

---

## Reference storage

References live in `shared/references/<scenario>/step_N.png` and should be committed to the
repository so CI can compare against them.

The `.gitkeep` in `shared/references/` ensures the directory is tracked even when empty.
