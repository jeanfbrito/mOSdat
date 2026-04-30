#!/bin/bash
# Test: Wayland env vars set but X11 available - should fallback
# Simulates: Broken Wayland but X11 works, app should use X11
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

cleanup
setup_xvfb

export WAYLAND_DISPLAY=wayland-nonexistent
export XDG_SESSION_TYPE=wayland
export DISPLAY=:99

echo "=== Test: Wayland vars + X11 fallback ==="
echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY (broken) DISPLAY=$DISPLAY (works)"
echo "Expected: Should fallback to X11"

code=$(run_app)
report_result "wayland-fallback" "$code"
