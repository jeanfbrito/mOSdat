#!/bin/bash
# Test: X11 with Xvfb
# Simulates: Normal X11 desktop user (Ubuntu default)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

cleanup
setup_xvfb

export DISPLAY=:99
export XDG_SESSION_TYPE=x11
unset WAYLAND_DISPLAY

echo "=== Test: X11 (Xvfb) ==="
echo "DISPLAY=$DISPLAY XDG_SESSION_TYPE=$XDG_SESSION_TYPE"

code=$(run_app)
report_result "x11" "$code"
