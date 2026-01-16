#!/bin/bash
# Test: Real Wayland with Weston headless compositor
# Simulates: Fedora/Ubuntu Wayland desktop user (requires GPU for Electron GPU init)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

cleanup
setup_xvfb
setup_weston "wayland-test"

export WAYLAND_DISPLAY=wayland-test
export XDG_SESSION_TYPE=wayland
export DISPLAY=:99

echo "=== Test: Wayland (Weston) ==="
echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_SESSION_TYPE=$XDG_SESSION_TYPE DISPLAY=$DISPLAY"
echo "Requires GPU - will fail on headless VMs without GPU passthrough"

code=$(run_app)

if [[ $code -eq 139 ]] || [[ $code -eq 134 ]]; then
    echo "RESULT:wayland:SKIP:NO_GPU:$code"
else
    echo "RESULT:wayland:PASS:$code"
fi
