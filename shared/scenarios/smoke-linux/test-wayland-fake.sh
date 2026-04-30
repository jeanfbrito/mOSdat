#!/bin/bash
# Test: THE BUG - WAYLAND_DISPLAY set but socket doesn't exist, X11 available
# Simulates: Compositor crashed, socket removed, but X11 works
# This is GitHub issue #3154 - the fix makes it fallback to X11
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

cleanup
setup_xvfb

export WAYLAND_DISPLAY=wayland-nonexistent
export XDG_SESSION_TYPE=wayland
export DISPLAY=:99
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "$XDG_RUNTIME_DIR" 2>/dev/null || true

echo "=== Test: Fake Wayland + X11 fallback (THE BUG #3154) ==="
echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY (socket does NOT exist)"
echo "DISPLAY=$DISPLAY (X11 available for fallback)"
echo "Expected: Should fallback to X11, NOT crash"

code=$(run_app)
report_result "wayland-fake" "$code"
