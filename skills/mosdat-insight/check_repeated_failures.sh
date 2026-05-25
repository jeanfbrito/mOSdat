#!/usr/bin/env bash
# check_repeated_failures.sh — PostToolUse hook (Agent matcher)
# Counts step-N failures in session. After threshold, searches knowledge dirs
# and emits stderr message for Claude Code to inject into context.
#
# Claude Code passes tool result JSON on stdin when hook fires.
# Exit 0 always — never block the tool call.

set -euo pipefail

# --- Config ---
THRESHOLD=3
SESSION_ID="${CLAUDE_SESSION_ID:-default}"
COUNTER_FILE="/tmp/insight_failures_${SESSION_ID}.json"

# Opt-out
[[ "${INSIGHT_DISABLED:-0}" == "1" ]] && exit 0

# --- Read stdin (tool result JSON) ---
INPUT=$(cat)

# Only proceed if this is a failed agent result
RESULT_STATUS=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    # Claude Code agent tool output varies by version; try common shapes
    out = d.get('output', d.get('result', d.get('content', '')))
    if isinstance(out, list):
        out = ' '.join(str(x) for x in out)
    out = str(out).lower()
    if any(w in out for w in ['failed', 'failure', 'error', 'step failed', 'verify failed']):
        print('failed')
    else:
        print('ok')
except Exception:
    print('ok')
" 2>/dev/null || echo "ok")

[[ "$RESULT_STATUS" != "failed" ]] && exit 0

# --- Extract step identifier from tool output ---
STEP_KEY=$(echo "$INPUT" | python3 -c "
import json, sys, re
try:
    d = json.load(sys.stdin)
    out = d.get('output', d.get('result', d.get('content', '')))
    if isinstance(out, list):
        out = ' '.join(str(x) for x in out)
    # Try to extract step number
    m = re.search(r'step[:\s#-]*(\d+)', str(out), re.IGNORECASE)
    if m:
        print('step_' + m.group(1))
    else:
        # Fall back to first 60 chars of verify/check line
        m2 = re.search(r'(verify|check|assert)[^\n]{0,60}', str(out), re.IGNORECASE)
        if m2:
            print(re.sub(r'\W+', '_', m2.group(0)[:60].lower()))
        else:
            print('step_unknown')
except Exception:
    print('step_unknown')
" 2>/dev/null || echo "step_unknown")

# --- Increment counter ---
python3 - <<PYEOF
import json, os, sys

path = "$COUNTER_FILE"
key = "$STEP_KEY"

try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    data = {}

data[key] = data.get(key, 0) + 1

with open(path, 'w') as f:
    json.dump(data, f)

count = data[key]
sys.exit(0 if count < $THRESHOLD else 1)
PYEOF
THRESHOLD_HIT=$?

[[ $THRESHOLD_HIT -eq 0 ]] && exit 0

# --- Threshold hit: search knowledge dirs ---
CWD="${CLAUDE_CWD:-$(pwd)}"
KNOWLEDGE_DIRS=()
[[ -d "$CWD/.knowledge" ]] && KNOWLEDGE_DIRS+=("$CWD/.knowledge")
[[ -d "$HOME/.knowledge" ]] && KNOWLEDGE_DIRS+=("$HOME/.knowledge")
[[ -d "$CWD/shared/recipes" ]] && KNOWLEDGE_DIRS+=("$CWD/shared/recipes")

# Extract search terms from step key
SEARCH_TERMS=$(echo "$STEP_KEY" | tr '_' ' ' | sed 's/step [0-9]*//g' | xargs)

MATCHES=""
for DIR in "${KNOWLEDGE_DIRS[@]}"; do
    if [[ -n "$SEARCH_TERMS" ]]; then
        FOUND=$(grep -rl "$SEARCH_TERMS" "$DIR" 2>/dev/null | head -3 || true)
    else
        FOUND=$(find "$DIR" -name "*.md" -newer "$COUNTER_FILE" 2>/dev/null | head -3 || true)
    fi
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        # Extract topic from frontmatter
        TOPIC=$(grep '^topic:' "$f" 2>/dev/null | head -1 | sed 's/topic: *//' | tr -d '"' || basename "$f" .md)
        MATCHES+="- $f — $TOPIC\n"
    done <<< "$FOUND"
done

if [[ -n "$MATCHES" ]]; then
    echo -e "\n[insight] related lessons found after repeated failures at $STEP_KEY:\n$MATCHES\nConsider running /insight to capture this failure or reviewing the lessons above before retrying.\n" >&2
fi

# Reset counter for this step after surfacing
python3 - <<PYEOF2
import json
path = "$COUNTER_FILE"
key = "$STEP_KEY"
try:
    with open(path) as f:
        data = json.load(f)
    data[key] = 0
    with open(path, 'w') as f:
        json.dump(data, f)
except Exception:
    pass
PYEOF2

exit 0
