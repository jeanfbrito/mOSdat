# Installing mosdat-insight

The skill ships as a project-tracked directory at
`.claude/skills/mosdat-insight/` inside this repository. Claude Code
auto-discovers it when launched inside the project — no install needed
for local use.

## To use this skill across all your projects

Symlink the project copy into your global ~/.claude/skills/ dir:

```bash
ln -s "$(pwd)/.claude/skills/mosdat-insight" ~/.claude/skills/mosdat-insight
```

Verify:
```bash
ls -la ~/.claude/skills/mosdat-insight/SKILL.md
```

The symlink keeps the global install in lockstep with the tracked
project source — every commit lands in both places.

## Enabling the autouse PostToolUse hook

The hook fires after every Agent tool call. Add the following to your
`~/.claude/settings.json` PostToolUse array (alongside any existing
matchers):

```jsonc
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/skills/mosdat-insight/check_repeated_failures.sh"
          }
        ]
      }
    ]
  }
}
```

### Full PostToolUse block after merge

Your current `PostToolUse` has two entries. After adding mosdat-insight, it should look like:

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
        "command": "~/.claude/skills/mosdat-insight/check_repeated_failures.sh"
      }
    ]
  }
]
```

## Dry-run test

```bash
# Dry-run: pipe a fake failed agent result
echo '{"output": "step 2 failed: verify check timed out"}' | \
  INSIGHT_DISABLED=0 CLAUDE_SESSION_ID=test \
  ~/.claude/skills/mosdat-insight/check_repeated_failures.sh
# Expect: no output (only 1 failure, threshold is 3)

# Simulate hitting threshold
for i in 1 2 3; do
  echo '{"output": "step 2 failed: verify check timed out"}' | \
    CLAUDE_SESSION_ID=test \
    ~/.claude/skills/mosdat-insight/check_repeated_failures.sh 2>&1
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
