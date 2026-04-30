#!/bin/bash
# Run all GPU passthrough tests
# These tests require:
# 1. GPU attached to VM via hostpci
# 2. VM booted with graphical session
# 3. User auto-logged in
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo "GPU Passthrough Display Tests"
echo "========================================"
echo "App: ${APP_PATH:?APP_PATH must be set}"
echo "Timeout: ${TEST_TIMEOUT:-10}s"
echo "========================================"
echo ""

# Verify GPU is visible
echo "=== GPU Check ==="
if lspci | grep -qi nvidia; then
    echo "NVIDIA GPU detected:"
    lspci | grep -i nvidia
    if command -v nvidia-smi &>/dev/null; then
        nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
    fi
else
    echo "ERROR: No NVIDIA GPU visible - is GPU passthrough enabled?"
    exit 1
fi
echo ""

pass=0
fail=0

run_test() {
    local script="$1"
    local output
    echo "--- Running: $script ---"
    output=$("$SCRIPT_DIR/$script" 2>&1) || true
    echo "$output"
    
    if echo "$output" | grep -q "RESULT:.*:PASS"; then
        ((pass++))
    else
        ((fail++))
    fi
    echo ""
}

# Run GPU tests
run_test "test-gpu-wayland.sh"
run_test "test-gpu-x11.sh"

# Also run the crash scenario tests (these work regardless of GPU)
run_test "test-wayland-fake.sh"
run_test "test-wayland-fallback.sh"

echo "========================================"
echo "Summary: PASS=$pass FAIL=$fail"
echo "========================================"

[[ $fail -eq 0 ]]
