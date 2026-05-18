#!/usr/bin/env bash
# Usage: tools/scoped-tests.sh [base-ref]
# Outputs newline-separated pytest paths (deduplicated, sorted).
# Empty output = no scope match (caller should run full suite).
# __FULL__ = config.py changed, caller must run full suite.

set -euo pipefail

BASE_REF="${1:-HEAD}"
REPO_ROOT="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
TESTS_DIR="$REPO_ROOT/tests"

# Get changed files vs base ref
if [ "$BASE_REF" = "HEAD" ]; then
    # Unstaged + staged changes vs HEAD
    CHANGED=$(git -C "$REPO_ROOT" diff --name-only HEAD 2>/dev/null; git -C "$REPO_ROOT" diff --name-only --cached HEAD 2>/dev/null)
else
    CHANGED=$(git -C "$REPO_ROOT" diff --name-only "$BASE_REF" 2>/dev/null)
fi

# Filter to automation/**/*.py only, skip __pycache__ and dotfiles
AUTOMATION_PY=$(echo "$CHANGED" | grep -E '^automation/.*\.py$' | grep -v '__pycache__' | grep -v '/\.' || true)

if [ -z "$AUTOMATION_PY" ]; then
    # No automation/*.py changes — empty output
    exit 0
fi

# Check for config.py — full suite sentinel
if echo "$AUTOMATION_PY" | grep -qE '^automation/config\.py$'; then
    echo "__FULL__"
    exit 0
fi

# Map each changed file to test files
MATCHES=""
while IFS= read -r changed_file; do
    # Extract stem: automation/path/to/foo.py -> foo
    stem=$(basename "$changed_file" .py)

    # Match tests/test_<stem>*.py and tests/test_*<stem>*.py
    # Use find to avoid glob expansion issues with no matches
    while IFS= read -r tf; do
        MATCHES="$MATCHES"$'\n'"$tf"
    done < <(find "$TESTS_DIR" -maxdepth 1 -name "test_${stem}*.py" -o -name "test_*${stem}*.py" 2>/dev/null | sort)
done <<< "$AUTOMATION_PY"

# Deduplicate, sort, make paths relative to repo root, strip empty lines
if [ -n "$MATCHES" ]; then
    echo "$MATCHES" | grep -v '^$' | sort -u | while IFS= read -r p; do
        # Make relative to repo root
        echo "${p#$REPO_ROOT/}"
    done
fi
