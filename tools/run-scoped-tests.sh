#!/usr/bin/env bash
# Run scoped tests, fall back to full suite when scope can't be narrowed.
# Usage: tools/run-scoped-tests.sh [base-ref] [-- extra pytest args]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"

# Split args: base-ref vs extra pytest args (after --)
BASE_REF="HEAD"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --)
            shift
            EXTRA_ARGS=("$@")
            break
            ;;
        *)
            BASE_REF="$1"
            shift
            ;;
    esac
done

SCOPE=$("$SCRIPT_DIR/scoped-tests.sh" "$BASE_REF" || true)

if [ -z "$SCOPE" ] || [ "$SCOPE" = "__FULL__" ]; then
    if [ "$SCOPE" = "__FULL__" ]; then
        echo "[scoped-tests] config.py changed → full suite" >&2
    else
        echo "[scoped-tests] no automation changes → full suite" >&2
    fi
    exec python -m pytest tests/ -q "${EXTRA_ARGS[@]}"
else
    echo "[scoped-tests] scoped to:" >&2
    echo "$SCOPE" | sed 's/^/  /' >&2
    # shellcheck disable=SC2086
    exec python -m pytest -q "${EXTRA_ARGS[@]}" $SCOPE
fi
