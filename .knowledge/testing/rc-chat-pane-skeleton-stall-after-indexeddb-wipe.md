# RC chat pane stalls on grey skeleton bars indefinitely after IndexedDB wipe

**Context**: Smoke test login (step 7) PASSED, sidebar rendered correctly with channels listed and `general` highlighted. But chat pane right-side stayed stuck on grey skeleton placeholder bars for >200s across multiple runs. Step 8 (General-channel-loaded verify) failed three times.

**Insight**: Cleanup script in `rocketchat-smoke-linux.yaml` Stage 4 used to wipe `~/.local/share/Rocket.Chat*`. That directory holds Meteor's IndexedDB / message cache. Wiping it forces a full workspace re-sync from the Rocket.Chat server on every smoke run — and on a heavy workspace the chat pane hangs on skeleton placeholders waiting for subscriptions. Auth lives in `~/.config/Rocket.Chat/Local Storage` and the libsecret keyring (which ARE wiped), so logout is preserved without touching the IndexedDB cache.

**Implication**:
- Cleanup steps that intend to enforce "logged-out state" only need to wipe `.config` + libsecret keyring. Do NOT wipe `~/.local/share/Rocket.Chat*`.
- If chat pane skeleton stalls reappear, check whether something else has invalidated the IndexedDB (e.g. RC schema change between releases, profile path changes between deb/snap/flatpak).
- Running smoke test FIRST time on a freshly provisioned VM will always be slow at step 8 — there's no cached IndexedDB yet. Subsequent runs reuse cache. Worth pre-warming on CI runners.

**Affects**: `shared/scenarios/functional/rocketchat-smoke-linux.yaml` Stage 4 cleanup. The current scenario already documents this — preserved here so the next person doesn't re-add the wipe under "make cleanup more thorough".
