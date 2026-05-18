---
name: mosdat-pr-preflight
description: Force a binary-freshness check before any PR-scoped mosdat scenario. Triggers on "test PR N", "run 3325-*", "rodar <PR>-*". Refuses to skip the build+deploy+asar-verify step — stale binaries produce false-negative scenario failures that look like framework bugs.
---

## Why this exists

On 2026-05-18, scenario `3325-master-toggle.yaml` ran 25/28 steps then failed the verify step because PR #3325 (telephony feature) was never built and deployed to the VM. A stale binary from a prior test was on the box. We spent N minutes diagnosing what wasn't a real test failure — the framework worked, the binary didn't contain the feature. This skill enforces a binary-freshness gate before every PR-scoped scenario to prevent wasting time on false-negative failures.

## Workflow

1. **Identify PR number**
   - If user says "test PR 3325" or "run 3325-X", extract `3325`.
   - If user provides a scenario filename like `3325-master-toggle.yaml`, parse the PR number from the filename prefix.
   - Confirm the PR number is valid (4–5 digits).

2. **Check PR state**
   ```bash
   gh pr view <N> --repo RocketChat/Rocket.Chat.Electron \
     --json headRefOid,updatedAt,headRefName,state,mergeable,commits
   ```
   - Confirm state is `OPEN` (abort if MERGED/CLOSED/DRAFT).
   - Record HEAD SHA and last-updated timestamp.

3. **Pick a verify symbol**
   - Scan the PR diff:
     ```bash
     gh pr diff <N> --repo RocketChat/Rocket.Chat.Electron \
       | grep -E '^\+\s*(export const|function|class|interface)\s+\w+' \
       | head -10
     ```
   - Propose the most uniquely-named symbol that is NEW in the diff (TS/JS identifier).
   - If user disagrees, accept their override or use the Common Verify Symbols table below.

4. **Run the build + deploy + verify**
   ```bash
   python automation/main.py build \
     --pr <N> \
     --deploy <vm> \
     --verify-symbol <sym> \
     --config examples/rocketchat.toml
   ```
   - This clones the PR branch, builds the .deb, deploys to the VM, and greps `app.asar` for the symbol.
   - If the symbol is NOT found in `app.asar` on the VM, exit 1: **abort the scenario**. Do NOT proceed.
   - If the symbol IS found, the binary is fresh and safe to test.

5. **Only THEN run the scenario**
   ```bash
   python automation/main.py functional \
     examples/rocketchat.toml \
     --test <scenario-filename> \
     --vms <vm> \
     ...
   ```
   - Run only after step 4 succeeds with exit code 0.

**Refuse to skip step 4.** If the user says "skip preflight" or "no rebuild", clarify why they need to skip, then REQUIRE confirmation that they understand stale binaries will produce false-negative test failures that look like framework bugs.

## How to pick `--verify-symbol`

1. Read the PR diff with:
   ```bash
   gh pr diff <N> --repo RocketChat/Rocket.Chat.Electron
   ```

2. Scan for NEW function/class/const/interface names (lines starting with `+`).

3. Propose the most **unique** identifier — avoid generic names like `init`, `setup`, `config`. Prefer domain-specific names:
   - `isTelephonyEnabled` (PR 3325) — telephony feature flag
   - `setAsDefaultProtocolClient` (PR 3325) — protocol registration
   - Example: if a PR adds `enableVoiceChat()`, that's a good verify symbol.

4. If no good candidate exists in the diff, ask the user: *"What feature is being tested? I'll pick a symbol that represents it."*

5. User can always override: *"Use `XYZ` instead of `ABC`."* Accept without argument.

## When to skip

**Refuse to skip the build+deploy+asar-verify step UNLESS:**
- User explicitly says: `"skip preflight"` or `"no rebuild"`.
- When refusing, say: *"Stale binaries produce false-negative failures that look like framework bugs. Are you absolutely sure you want to skip the binary check? Say yes only if you're running a known-good binary that was already verified."*
- If user confirms, proceed to step 5 (run the scenario).

Otherwise, ALWAYS run steps 1–4.

## Common verify symbols

| PR | Feature | Verify Symbol |
|----|---------|---------------|
| 3325 | Telephony (H.323/SIP) | `isTelephonyEnabled` or `setAsDefaultProtocolClient` |
| TBD | (Add as we learn them) | — |
