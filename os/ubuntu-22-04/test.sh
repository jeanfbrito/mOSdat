#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

TEST_NAME="${1:-all}"
TIMEOUT="${TEST_TIMEOUT:-10}"

echo "Running tests on Ubuntu 22.04 ($VM_IP) - Test: $TEST_NAME"

ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" bash << REMOTE
export TIMEOUT=$TIMEOUT
export TEST_NAME="$TEST_NAME"

get_xauth() {
    pgrep -a Xwayland 2>/dev/null | head -1 | grep -oP -- '-auth \K[^ ]+' 2>/dev/null || echo ""
}

run_test() {
    local name="\$1" session_type="\$2" wayland_display="\$3"
    
    export XDG_RUNTIME_DIR=/run/user/\$(id -u)
    export XDG_SESSION_TYPE="\$session_type"
    export DISPLAY=":0"
    export XAUTHORITY="\$(get_xauth)"
    
    if [[ "\$wayland_display" == "REAL" ]]; then
        export WAYLAND_DISPLAY="\${REAL_WAYLAND:-wayland-0}"
    elif [[ "\$wayland_display" == "UNSET" ]]; then
        unset WAYLAND_DISPLAY
    else
        export WAYLAND_DISPLAY="\$wayland_display"
    fi
    
    echo "=== Test: \$name ==="
    
    EXIT_CODE=0
    timeout \$TIMEOUT /opt/Rocket.Chat/rocketchat-desktop 2>&1 || EXIT_CODE=\$?
    
    if [[ \$EXIT_CODE -eq 139 ]] || [[ \$EXIT_CODE -eq 134 ]] || [[ \$EXIT_CODE -eq 6 ]]; then
        echo "RESULT:\$name:FAIL:SEGFAULT:\$EXIT_CODE"
    elif [[ \$EXIT_CODE -eq 124 ]]; then
        echo "RESULT:\$name:PASS:TIMEOUT:\$EXIT_CODE"
    else
        echo "RESULT:\$name:PASS:EXIT:\$EXIT_CODE"
    fi
}

export REAL_WAYLAND="\$WAYLAND_DISPLAY"

case "\$TEST_NAME" in
    wayland-real) run_test "wayland-real" "wayland" "REAL" ;;
    wayland-fake) run_test "wayland-fake" "wayland" "wayland-fake-nonexistent" ;;
    wayland-nodisp) run_test "wayland-nodisp" "wayland" "UNSET" ;;
    x11) run_test "x11" "x11" "UNSET" ;;
    all)
        run_test "wayland-real" "wayland" "REAL"
        run_test "wayland-fake" "wayland" "wayland-fake-nonexistent"
        run_test "wayland-nodisp" "wayland" "UNSET"
        run_test "x11" "x11" "UNSET"
        ;;
esac
REMOTE
