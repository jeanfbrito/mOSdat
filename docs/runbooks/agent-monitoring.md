# Long-running agent monitoring

The lesson learned: do NOT dispatch BG agents for live-VM tasks and wait blindly. mosdat already emits real-time progress to `events.jsonl` per step. Use it.

## Pattern

### 1. Launch in background

```bash
nohup mosdat functional examples/rocketchat.toml --vms <vm> --test <name> \
  > /tmp/smoke-bg.log 2>&1 &
echo "pid=$!"
```

### 2. Poll mid-run from a SEPARATE shell

```bash
latest=$(ls -td results/functional/*/ | head -1)
ev=$(find "$latest" -name 'events.jsonl' | head -1)

# Liveness
ps -p <pid> -o pid= 2>/dev/null || echo "DONE"

# Activity
echo "events.jsonl stale: $(($(date +%s) - $(stat -c %Y "$ev")))s"
tail -3 "$ev"
```

### 3. Staleness threshold

If `events.jsonl` mtime is more than 60s stale AND the pid is alive, the runner is hung in a single step (likely VLM call or SSH). Investigate or kill.

If pid is gone but tail shows no PASS/FAIL, runner crashed silently — read the stdout log (`/tmp/smoke-bg.log`).

## For non-mosdat shell scripts

Wrap with explicit heartbeat lines:

```bash
HB=/tmp/agent-hb-$$.log
hb() { echo "[HB $(date -Iseconds)] $*" >> "$HB"; }

hb "step=install-flatpak begin"
flatpak install --user --noninteractive flathub chat.rocket.RocketChat -y
hb "step=install-flatpak done"

hb "step=smoke-run begin vm=fedora42"
mosdat functional examples/rocketchat.toml --vms fedora42 --test foo
hb "step=smoke-run done exit=$?"
```

Then `tail -c 500 "$HB"` from another shell shows progress.

## From Python

Use `automation.heartbeat.Heartbeat`:

```python
from automation.heartbeat import Heartbeat

hb = Heartbeat("flatpak-install")
hb.step("uninstall-old")
# ... do work ...
hb.step("install-new", version="4.14.0")
# ... do work ...
hb.done(exit=0)
```

Default file: `/tmp/mosdat-hb-<label>-<pid>.log`. Override with `HEARTBEAT_FILE` env var.

## Anti-patterns

- ❌ Dispatch a BG agent for a 5-min task and wait for the completion notification with no mid-run check.
- ❌ Read the agent's `<task-id>.output` symlink — that's a JSONL transcript that overflows context. Use `stat -L` for mtime, `tail -c 200` for tiny peek.
- ❌ Re-dispatch a "stuck" agent without first checking `stat -L` mtime. The agent may be active.

## Quick-reference one-liner

```bash
# Status snapshot for any running mosdat smoke
latest=$(ls -td /home/jean/projects/linux-testing/mOSdat/results/functional/*/ | head -1)
ev=$(find "$latest" -name 'events.jsonl' | head -1)
printf 'pid=%s mtime_stale=%ss last_event=%s\n' \
  "$(pgrep -af 'mosdat functional' | head -1)" \
  "$(($(date +%s) - $(stat -c %Y "$ev" 2>/dev/null || echo 0)))" \
  "$(tail -1 "$ev" 2>/dev/null)"
```
