#!/bin/bash
# Shared test functions - sourced by individual test scripts

if [ -z "$APP_PATH" ]; then
    echo "ERROR: APP_PATH environment variable is required."
    echo "Set it to the application binary path (e.g., APP_PATH=/opt/MyApp/myapp)"
    exit 1
fi

APP_ARGS="${APP_ARGS:-}"
PROCESS_NAME="${PROCESS_NAME:-$(basename "$APP_PATH")}"
TIMEOUT="${TEST_TIMEOUT:-10}"

cleanup() {
    pkill -f "$PROCESS_NAME" 2>/dev/null || true
    pkill -f "Xvfb" 2>/dev/null || true
    pkill -f "weston" 2>/dev/null || true
    sleep 1
}

setup_xvfb() {
    Xvfb :99 -screen 0 1920x1080x24 -ac &>/dev/null &
    sleep 2
}

setup_weston() {
    local socket="${1:-wayland-test}"
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    mkdir -p "$XDG_RUNTIME_DIR" 2>/dev/null || true
    chmod 700 "$XDG_RUNTIME_DIR" 2>/dev/null || true
    weston --backend=headless-backend.so --socket="$socket" --width=1920 --height=1080 &>/dev/null &
    sleep 3
}

run_app() {
    local exit_code=0
    # shellcheck disable=SC2086
    timeout "$TIMEOUT" "$APP_PATH" $APP_ARGS >/dev/null 2>&1 || exit_code=$?
    echo "$exit_code"
}

report_result() {
    local name="$1"
    local exit_code="$2"

    # Segfault=139, Abort=134
    if [[ $exit_code -eq 139 ]] || [[ $exit_code -eq 134 ]]; then
        echo "RESULT:$name:FAIL:CRASH:$exit_code"
        return 1
    elif [[ $exit_code -eq 124 ]]; then
        echo "RESULT:$name:PASS:TIMEOUT:$exit_code"
        return 0
    else
        echo "RESULT:$name:PASS:$exit_code"
        return 0
    fi
}
