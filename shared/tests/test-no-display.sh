#!/bin/bash
# Test: No display server at all
# Simulates: Headless server, TTY login
# Expected: App will crash (no GUI possible) - this is acceptable
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

cleanup

unset DISPLAY
unset WAYLAND_DISPLAY
unset XDG_SESSION_TYPE

echo "=== Test: No display ==="
echo "DISPLAY=(unset) WAYLAND_DISPLAY=(unset)"
echo "Note: Crash is acceptable here - no display means no GUI possible"

code=$(run_app)

if [[ $code -eq 139 ]] || [[ $code -eq 134 ]]; then
    echo "RESULT:no-display:EXPECTED:CRASH:$code"
else
    echo "RESULT:no-display:PASS:$code"
fi
