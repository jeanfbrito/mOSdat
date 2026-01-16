#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo "Display Scenario Tests"
echo "========================================"
echo "App: ${APP_PATH:-/opt/Rocket.Chat/rocketchat-desktop}"
echo "Timeout: ${TEST_TIMEOUT:-10}s"
echo "========================================"
echo ""

pass=0
fail=0
expected=0

run_test() {
    local script="$1"
    local output
    output=$("$SCRIPT_DIR/$script" 2>&1)
    echo "$output"
    
    if echo "$output" | grep -q "RESULT:.*:PASS"; then
        ((pass++))
    elif echo "$output" | grep -q "RESULT:.*:EXPECTED\|RESULT:.*:SKIP"; then
        ((expected++))
    else
        ((fail++))
    fi
    echo ""
}

run_test "test-x11.sh"
run_test "test-wayland.sh"
run_test "test-wayland-fake.sh"
run_test "test-wayland-fallback.sh"
run_test "test-no-display.sh"

echo "========================================"
echo "Summary: PASS=$pass FAIL=$fail EXPECTED=$expected"
echo "========================================"

[[ $fail -eq 0 ]]
