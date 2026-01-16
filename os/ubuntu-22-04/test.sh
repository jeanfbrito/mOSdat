#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

print_usage() {
    cat << 'EOF'
Run Wayland/X11 crash tests on Ubuntu 22.04 VM

Usage: ./test.sh [OPTIONS]

Options:
  --test NAME     Run specific test (wayland-real, wayland-fake, wayland-nodisp, x11, all)
  --output FILE   Write results to JSON
  --label LABEL   Label for test run
  -h, --help      Show this help
EOF
}

TEST_NAME="all"
OUTPUT_FILE=""
TEST_LABEL=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --test) TEST_NAME="$2"; shift 2 ;;
        --output) OUTPUT_FILE="$2"; shift 2 ;;
        --label) TEST_LABEL="$2"; shift 2 ;;
        -h|--help) print_usage; exit 0 ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

log "Getting VM IP..."
VM_IP=$(vm_get_ip "$VMID")

if [[ -z "$VM_IP" ]]; then
    log_error "Could not get VM IP"
    exit 1
fi

log "========================================"
log "Running Tests on Ubuntu 22.04"
log "========================================"
log "VM ID:   ${VMID}"
log "VM IP:   ${VM_IP}"
log "Test:    ${TEST_NAME}"
log "========================================"

TEST_SCRIPT='
TIMEOUT='"${TEST_TIMEOUT}"'
TEST_NAME="'"${TEST_NAME}"'"

get_xauth() {
    local xwayland_cmd=$(pgrep -a Xwayland 2>/dev/null | head -1)
    echo "$xwayland_cmd" | grep -oP -- "-auth \K[^ ]+" 2>/dev/null || echo ""
}

run_test() {
    local name="$1"
    local session_type="$2"
    local wayland_display="$3"
    
    export XDG_RUNTIME_DIR=/run/user/1000
    export XDG_SESSION_TYPE="$session_type"
    export DISPLAY=":0"
    export XAUTHORITY="$(get_xauth)"
    
    if [[ "$wayland_display" == "REAL" ]]; then
        export WAYLAND_DISPLAY="${REAL_WAYLAND:-wayland-0}"
    elif [[ "$wayland_display" == "UNSET" ]]; then
        unset WAYLAND_DISPLAY
    else
        export WAYLAND_DISPLAY="$wayland_display"
    fi
    
    echo "=== Test: $name ==="
    echo "SESSION=$XDG_SESSION_TYPE WAYLAND=${WAYLAND_DISPLAY:-UNSET}"
    
    OUTPUT=$(timeout $TIMEOUT /opt/Rocket.Chat/rocketchat-desktop 2>&1) || EXIT_CODE=$?
    EXIT_CODE=${EXIT_CODE:-0}
    
    local PLATFORM="unknown"
    echo "$OUTPUT" | grep -q "Using Wayland" && PLATFORM="Wayland"
    echo "$OUTPUT" | grep -q "ozone-platform=x11" && PLATFORM="X11-forced"
    
    if [[ $EXIT_CODE -eq 139 ]] || [[ $EXIT_CODE -eq 134 ]] || [[ $EXIT_CODE -eq 6 ]]; then
        echo "RESULT:$name:FAIL:SEGFAULT:$EXIT_CODE:$PLATFORM"
    elif [[ $EXIT_CODE -eq 124 ]]; then
        echo "RESULT:$name:PASS:TIMEOUT:$EXIT_CODE:$PLATFORM"
    elif [[ $EXIT_CODE -eq 0 ]]; then
        echo "RESULT:$name:PASS:CLEAN:$EXIT_CODE:$PLATFORM"
    else
        echo "RESULT:$name:UNKNOWN:OTHER:$EXIT_CODE:$PLATFORM"
    fi
    echo ""
}

export REAL_WAYLAND="$WAYLAND_DISPLAY"

case "$TEST_NAME" in
    wayland-real)  run_test "wayland-real" "wayland" "REAL" ;;
    wayland-fake)  run_test "wayland-fake" "wayland" "wayland-fake-nonexistent" ;;
    wayland-nodisp) run_test "wayland-nodisp" "wayland" "UNSET" ;;
    x11)           run_test "x11" "x11" "UNSET" ;;
    all)
        run_test "wayland-real" "wayland" "REAL"
        run_test "wayland-fake" "wayland" "wayland-fake-nonexistent"
        run_test "wayland-nodisp" "wayland" "UNSET"
        run_test "x11" "x11" "UNSET"
        ;;
esac
'

log "Running tests..."
TEST_OUTPUT=$(docker run --rm --network host alpine sh -c "
    apk add --no-cache openssh-client sshpass >/dev/null 2>&1
    sshpass -p '${VM_PASSWORD}' ssh -o StrictHostKeyChecking=no ${VM_USER}@${VM_IP} '$TEST_SCRIPT'
" 2>/dev/null)

echo ""
echo "$TEST_OUTPUT"
echo ""

RESULTS=$(echo "$TEST_OUTPUT" | grep "^RESULT:" || true)

PASS=0; FAIL=0; UNKNOWN=0
while IFS=: read -r _ name result _ _ _; do
    case "$result" in
        PASS) ((PASS++)) ;;
        FAIL) ((FAIL++)) ;;
        *) ((UNKNOWN++)) ;;
    esac
done <<< "$RESULTS"

log "Summary: PASS=$PASS FAIL=$FAIL UNKNOWN=$UNKNOWN"

if [[ -n "$OUTPUT_FILE" ]]; then
    HAS_GPU=$(vm_has_gpu "$VMID" && echo "true" || echo "false")
    cat > "$OUTPUT_FILE" << EOF
{
    "timestamp": "$(timestamp)",
    "label": "${TEST_LABEL:-}",
    "os": "ubuntu-22-04",
    "vm_id": $VMID,
    "vm_ip": "$VM_IP",
    "has_gpu": $HAS_GPU,
    "summary": {"pass": $PASS, "fail": $FAIL, "unknown": $UNKNOWN},
    "tests": [
$(echo "$RESULTS" | while IFS=: read -r _ name result detail code platform; do
    echo "        {\"name\": \"$name\", \"result\": \"$result\", \"exit_code\": $code},"
done | sed '$ s/,$//')
    ]
}
EOF
    log "Results: $OUTPUT_FILE"
fi

[[ $FAIL -gt 0 ]] && exit 1
exit 0
