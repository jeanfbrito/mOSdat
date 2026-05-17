# Installing mosdat-authoring

The skill ships as a project-tracked directory at
`.claude/skills/mosdat-authoring/` inside this repository. Claude Code
auto-discovers it when launched inside the project — no install needed
for local use.

## To use this skill across all your projects

Symlink the project copy into your global ~/.claude/skills/ dir:

```bash
ln -s "$(pwd)/.claude/skills/mosdat-authoring" ~/.claude/skills/mosdat-authoring
```

Verify:
```bash
ls -la ~/.claude/skills/mosdat-authoring/SKILL.md
```

The symlink keeps the global install in lockstep with the tracked
project source — every commit lands in both places.

## No autouse hook

mosdat-authoring is an explicit-invocation skill. It fires when you:

- Invoke `/mosdat-authoring` directly.
- Ask "write tests for X in mOSdat", "author scenario for X", or "add mOSdat coverage for PR #N".

It does NOT fire automatically after failures (that is mosdat-insight's role).

## Gitignore note

mOSdat's `.gitignore` does not suppress `.claude/skills/**`, so this skill
is tracked in version control alongside the codebase. Changes to the skill
are part of normal commits.

## Dependencies

- `python3` (stdlib) — used for schema validation in the workflow
- `mosdat` CLI — the primary tool referenced throughout
- No external packages required for the skill itself
