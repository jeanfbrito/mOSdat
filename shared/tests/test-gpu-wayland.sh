#!/bin/bash
# Test: REAL Wayland with GPU passthrough
# Uses the VM's actual desktop session (GNOME/KDE with GPU)
# NOT virtual framebuffers - this is the real deal
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

echo "=== Test: Real GPU Wayland Session ==="

# Find the logged-in user's display
USER_SESSION=$(loginctl list-sessions --no-legend | head -1 | awk '{print $1}')
if [[ -z "$USER_SESSION" ]]; then
    echo "RESULT:gpu-wayland:FAIL:NO_SESSION"
    exit 1
fi

# Get session environment
eval "$(loginctl show-session "$USER_SESSION" -p Type -p Display -p Remote)"

echo "Session: $USER_SESSION, Type: $Type, Display: ${Display:-none}"

# Check if we're on Wayland
if [[ "$Type" != "wayland" ]]; then
    echo "WARNING: Session is $Type, not wayland"
fi

# Get the actual display socket
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_SESSION_TYPE="$Type"

# If X11 fallback exists (XWayland), set DISPLAY
if [[ -n "${Display:-}" ]]; then
    export DISPLAY="$Display"
fi

# Check for GPU
if lspci | grep -qi nvidia; then
    echo "GPU: NVIDIA detected"
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null || echo "nvidia-smi not available"
else
    echo "WARNING: No NVIDIA GPU visible in lspci"
fi

echo "Environment:"
echo "  WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
echo "  XDG_SESSION_TYPE=$XDG_SESSION_TYPE"
echo "  DISPLAY=${DISPLAY:-unset}"
echo "  XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"

# Check Wayland socket exists
if [[ ! -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]]; then
    echo "RESULT:gpu-wayland:FAIL:NO_WAYLAND_SOCKET"
    exit 1
fi

echo "Wayland socket: $XDG_RUNTIME_DIR/$WAYLAND_DISPLAY (exists)"

# Kill any existing instances
pkill -f "$PROCESS_NAME" 2>/dev/null || true
sleep 1

# Run the app with real Wayland
echo "Starting app with real Wayland session..."
code=0
timeout "$TIMEOUT" "$APP_PATH" $APP_ARGS >/dev/null 2>&1 || code=$?

report_result "gpu-wayland" "$code"
