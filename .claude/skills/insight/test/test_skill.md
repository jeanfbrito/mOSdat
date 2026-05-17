# /insight — Manual Verification Checklist

Run these in order. Each check is pass/fail.

## 1. Skill loads

Type `/insight` in Claude Code.

Expected: Claude responds with a prompt asking for symptoms / root cause / pivot / cost.
- [ ] Pass: prompts for structured input
- [ ] Fail: no response or "unknown command"

## 2. Capture a new entry (project-shared)

When prompted, provide:
- Symptoms: "VLM localize clicks on Settings kebab are flaky"
- Root cause: "Sidebar popup is transient; VLM hallucinates coords"
- Pivot: "Write config.json directly, skip Settings UI"
- Cost: "3 attempts × 20min = 1hr"
- Scope: project-shared

Expected: Claude writes a `.md` file to `<current-project>/.knowledge/` with correct frontmatter (date, tags, topic, kind, scope, category, confidence, cost) and reports the path.
- [ ] Pass: file created, frontmatter present, body under 15 lines
- [ ] Fail: no file created, or frontmatter missing fields

## 3. Capture a new entry (user-personal)

Type `/insight` again, choose scope = user-personal.
Choose tech = electron, describe a generic Electron lesson.

Expected: file written to `~/.knowledge/electron/<slug>.md`.
- [ ] Pass: file in correct location
- [ ] Fail: file in wrong location or missing

Bonus: check `~/.knowledge/index.md` — entry path should be appended.
- [ ] Pass: index updated
- [ ] Fail: index missing or not updated

## 4. Surface existing insights

Ask Claude: "Before we start debugging this electron menu issue, surface any existing insights."

Expected: Claude searches `.knowledge/` dirs, reports relevant slugs with 1-line summaries.
- [ ] Pass: surfaces at least the examples/pr3325-settings-bypass.md entry (if in a mOSdat project context)
- [ ] Fail: no search performed, or search returns nothing when lessons exist

## 5. Autouse hook (requires hook wired — see INSTALL.md)

Manually test the script:
```bash
# Run 3x to hit threshold
for i in 1 2 3; do
  echo '{"output": "step 2 failed: verify check timed out"}' | \
    CLAUDE_SESSION_ID=hooktest \
    ~/.claude/skills/insight/check_repeated_failures.sh 2>&1
done
```

Expected: third run emits `[insight] related lessons found...` to stderr.
- [ ] Pass: message emitted on run 3
- [ ] Fail: no message, or message on wrong run

Clean up: `rm -f /tmp/insight_failures_hooktest.json`

## 6. Entry format conformance

Open any entry written during steps 2-3. Verify:
- [ ] Frontmatter block present (--- delimited)
- [ ] All required fields: date, project, tags, topic, kind, scope, category, confidence, cost
- [ ] Body sections: Context, Root cause, Pivot, Rule
- [ ] Body under 15 lines
- [ ] topic is a searchable one-liner (not "bug fix" or "issue")

## Done criteria

All 6 checks pass. Specifically:
- Entries appear in correct knowledge dirs
- Frontmatter matches .knowledge convention (matches pr3325-bypass example)
- Hook counter script exits 0 silently for <3 failures, emits on =3
- Claude surfaces lessons from .knowledge before retrying a failing step
