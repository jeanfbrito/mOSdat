# Installing the Insight Autouse Hook

## What this adds

A `PostToolUse` hook that fires after every `Agent` tool call. When the same step fails 3 times in a session, it searches your knowledge directories and emits a context injection with relevant lesson slugs.

## Install location

The skill ships in two equivalent locations:

- **Project-scoped** (tracked in this repo): `<project>/.claude/skills/insight/` — auto-discovered by Claude Code when working inside this project.
- **Global** (user-level): `~/.claude/skills/insight/` — copy the contents from the project location if you want the skill available across all projects.

The instructions below use `~/.claude/skills/insight/` paths; substitute `<project>/.claude/skills/insight/` if installing project-locally.

## Step 1 — Verify the script

```bash
ls -la ~/.claude/skills/insight/check_repeated_failures.sh
# Should be executable. If not:
chmod +x ~/.claude/skills/insight/check_repeated_failures.sh
```

## Step 2 — Add to ~/.claude/settings.json

Open `~/.claude/settings.json` and add the following inside the existing `"PostToolUse"` array (alongside the existing Read|Edit|Write and Bash matchers):

```jsonc
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/skills/insight/check_repeated_failures.sh"
          }
        ]
      }
    ]
  }
}
```

### Full PostToolUse block after merge

Your current `PostToolUse` has two entries. After adding insight, it should look like:

```jsonc
"PostToolUse": [
  {
    "matcher": "Read|Edit|Write",
    "hooks": [
      {
        "type": "command",
        "command": "/home/jean/go/bin/mastermind suggest --from-hook"
      }
    ]
  },
  {
    "matcher": "Bash",
    "hooks": [
      {
        "type": "command",
        "command": "node \"/home/jean/.claude/hooks/gitnexus/gitnexus-hook.cjs\"",
        "timeout": 10,
        "statusMessage": "Checking GitNexus index freshness..."
      }
    ]
  },
  {
    "matcher": "Agent",
    "hooks": [
      {
        "type": "command",
        "command": "~/.claude/skills/insight/check_repeated_failures.sh"
      }
    ]
  }
]
```

## Step 3 — Test the hook

```bash
# Dry-run: pipe a fake failed agent result
echo '{"output": "step 2 failed: verify check timed out"}' | \
  INSIGHT_DISABLED=0 CLAUDE_SESSION_ID=test \
  ~/.claude/skills/insight/check_repeated_failures.sh
# Expect: no output (only 1 failure, threshold is 3)

# Simulate hitting threshold
for i in 1 2 3; do
  echo '{"output": "step 2 failed: verify check timed out"}' | \
    CLAUDE_SESSION_ID=test \
    ~/.claude/skills/insight/check_repeated_failures.sh 2>&1
done
# Third run should emit: "[insight] related lessons found..."
# Then clean up:
rm -f /tmp/insight_failures_test.json
```

## Opt-out

- Per-session: set `INSIGHT_DISABLED=1` in your shell before launching Claude Code.
- Per-dispatch: include `--no-autoinsight` in any user prompt or agent brief. The script checks for this in the tool output context (future: parse UserPromptSubmit hook input).

## Dependencies

- `python3` (stdlib only — json, re, os, sys)
- `grep`, `find`, `bash` — standard on Linux/macOS
- No external packages required.

## Environment variables read

| Variable | Default | Purpose |
|---|---|---|
| `CLAUDE_SESSION_ID` | `"default"` | Isolates counters per session |
| `CLAUDE_CWD` | `$(pwd)` | CWD for project knowledge search |
| `INSIGHT_DISABLED` | `0` | Set to `1` to disable entirely |
