#!/bin/bash
# Test: REAL X11 with GPU passthrough (via XWayland or native X11)
# Uses the VM's actual X display with GPU rendering
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

echo "=== Test: Real GPU X11 Session ==="

# Find the logged-in user's display
USER_SESSION=$(loginctl list-sessions --no-legend | head -1 | awk '{print $1}')
if [[ -z "$USER_SESSION" ]]; then
    echo "RESULT:gpu-x11:FAIL:NO_SESSION"
    exit 1
fi

# Get session environment
eval "$(loginctl show-session "$USER_SESSION" -p Display -p Type)"

echo "Session: $USER_SESSION, Type: $Type"

# Find DISPLAY - either from session or from Xauthority
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

if [[ -n "${Display:-}" ]]; then
    export DISPLAY="$Display"
elif [[ "$Type" == "wayland" ]]; then
    # On Wayland, XWayland is usually :0 or :1
    export DISPLAY=":0"
fi

# Find XAUTHORITY
if [[ -z "${XAUTHORITY:-}" ]]; then
    if [[ -f "$HOME/.Xauthority" ]]; then
        export XAUTHORITY="$HOME/.Xauthority"
    elif [[ -f "$XDG_RUNTIME_DIR/gdm/Xauthority" ]]; then
        export XAUTHORITY="$XDG_RUNTIME_DIR/gdm/Xauthority"
    fi
fi

# Check for GPU
if lspci | grep -qi nvidia; then
    echo "GPU: NVIDIA detected"
else
    echo "WARNING: No NVIDIA GPU visible in lspci"
fi

echo "Environment:"
echo "  DISPLAY=${DISPLAY:-unset}"
echo "  XAUTHORITY=${XAUTHORITY:-unset}"
echo "  XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-unset}"

# Verify DISPLAY is set before continuing
if [[ -z "${DISPLAY:-}" ]]; then
    echo "ERROR: DISPLAY not set - cannot run X11 test"
    echo "RESULT:gpu-x11:FAIL:NO_DISPLAY"
    exit 1
fi

# Force X11 mode
unset WAYLAND_DISPLAY
export XDG_SESSION_TYPE=x11

# Kill any existing instances
pkill -f "$PROCESS_NAME" 2>/dev/null || true
sleep 1

# Run the app with real X11
echo "Starting app with real X11 session..."
code=0
timeout "$TIMEOUT" "$APP_PATH" $APP_ARGS >/dev/null 2>&1 || code=$?

report_result "gpu-x11" "$code"
