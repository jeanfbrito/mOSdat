#!/bin/bash
set -euo pipefail

# Display Scenario Test Runner
# Creates controlled display environments using Xvfb and Weston

APP_PATH="${APP_PATH:-/opt/Rocket.Chat/rocketchat-desktop}"
TIMEOUT="${TEST_TIMEOUT:-10}"
RESULT_DIR="${RESULT_DIR:-/tmp/test-results}"

cleanup() {
    pkill -f "rocketchat-desktop" 2>/dev/null || true
    pkill -f "Xvfb" 2>/dev/null || true
    pkill -f "weston" 2>/dev/null || true
    sleep 1
}
trap cleanup EXIT

setup_xvfb() {
    Xvfb :99 -screen 0 1920x1080x24 -ac &>/dev/null &
    sleep 2
}

setup_weston() {
    local socket="${1:-wayland-test}"
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    mkdir -p "$XDG_RUNTIME_DIR" 2>/dev/null || true
    weston --backend=headless-backend.so --socket="$socket" --width=1920 --height=1080 &>/dev/null &
    sleep 3
}

run_app() {
    local exit_code=0
    timeout "$TIMEOUT" "$APP_PATH" --no-sandbox >/dev/null 2>&1 || exit_code=$?
    echo "$exit_code"
}

check_result() {
    local name="$1"
    local exit_code="$2"
    local expect_crash="${3:-no}"
    
    # Segfault=139, Abort=134
    local crashed=0
    [[ $exit_code -eq 139 ]] || [[ $exit_code -eq 134 ]] && crashed=1
    
    if [[ $crashed -eq 1 ]]; then
        if [[ "$expect_crash" == "yes" ]]; then
            echo "RESULT:$name:EXPECTED_CRASH:$exit_code"
        else
            echo "RESULT:$name:FAIL:CRASH:$exit_code"
        fi
        return 1
    else
        echo "RESULT:$name:PASS:$exit_code"
        return 0
    fi
}

# Test: Real X11 with Xvfb
test_x11() {
    echo "=== Test: X11 (Xvfb) ==="
    cleanup
    setup_xvfb
    export DISPLAY=:99
    export XDG_SESSION_TYPE=x11
    unset WAYLAND_DISPLAY
    
    local code=$(run_app)
    check_result "x11" "$code"
}

# Test: Real Wayland with Weston
test_wayland() {
    echo "=== Test: Wayland (Weston) ==="
    cleanup
    setup_weston "wayland-test"
    export WAYLAND_DISPLAY=wayland-test
    export XDG_SESSION_TYPE=wayland
    unset DISPLAY
    
    local code=$(run_app)
    check_result "wayland" "$code"
}

# Test: THE BUG - Wayland env set but no socket
test_wayland_fake() {
    echo "=== Test: Fake Wayland (THE BUG) ==="
    cleanup
    export WAYLAND_DISPLAY=wayland-nonexistent
    export XDG_SESSION_TYPE=wayland
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    unset DISPLAY
    
    local code=$(run_app)
    check_result "wayland-fake" "$code"
}

# Test: Wayland env set but X11 available (should fallback)
test_wayland_x11_fallback() {
    echo "=== Test: Wayland vars + X11 fallback ==="
    cleanup
    setup_xvfb
    export WAYLAND_DISPLAY=wayland-nonexistent
    export XDG_SESSION_TYPE=wayland
    export DISPLAY=:99
    
    local code=$(run_app)
    check_result "wayland-x11-fallback" "$code"
}

# Test: No display at all
test_no_display() {
    echo "=== Test: No display ==="
    cleanup
    unset DISPLAY
    unset WAYLAND_DISPLAY
    unset XDG_SESSION_TYPE
    
    local code=$(run_app)
    # Should exit gracefully, not crash
    check_result "no-display" "$code"
}

# Run all scenarios
test_all() {
    mkdir -p "$RESULT_DIR"
    
    echo "========================================"
    echo "Display Scenario Tests"
    echo "========================================"
    echo "App: $APP_PATH"
    echo "Timeout: ${TIMEOUT}s"
    echo "========================================"
    echo ""
    
    local pass=0 fail=0
    
    test_x11 && ((pass++)) || ((fail++))
    echo ""
    test_wayland && ((pass++)) || ((fail++))
    echo ""
    test_wayland_fake && ((pass++)) || ((fail++))
    echo ""
    test_wayland_x11_fallback && ((pass++)) || ((fail++))
    echo ""
    test_no_display && ((pass++)) || ((fail++))
    
    echo ""
    echo "========================================"
    echo "Summary: PASS=$pass FAIL=$fail"
    echo "========================================"
    
    [[ $fail -eq 0 ]]
}

# Run specific test or all
case "${1:-all}" in
    x11) test_x11 ;;
    wayland) test_wayland ;;
    wayland-fake) test_wayland_fake ;;
    wayland-x11-fallback) test_wayland_x11_fallback ;;
    no-display) test_no_display ;;
    all) test_all ;;
    *) echo "Usage: $0 [x11|wayland|wayland-fake|wayland-x11-fallback|no-display|all]"; exit 1 ;;
esac
