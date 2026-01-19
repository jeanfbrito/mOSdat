#!/bin/bash
set -euo pipefail

OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/config.sh"

TEST="${1:-all}"
TESTS_DIR="${OS_SCRIPT_DIR}/../../shared/tests"
APPIMAGE_NAME=$(find "${REPO_PATH}/dist/" -maxdepth 1 -name "rocketchat-*.AppImage" -printf "%f" 2>/dev/null | head -1)

[[ -z "$APPIMAGE_NAME" ]] && echo "AppImage not found. Run build.sh and deploy.sh first" && exit 1

echo "Testing on Arch Linux ($VM_IP)"

scp -o StrictHostKeyChecking=no -r "$TESTS_DIR" "${VM_USER}@${VM_IP}:/tmp/"

case "$TEST" in
    all)
        ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "APP_PATH=/tmp/${APPIMAGE_NAME} /tmp/tests/run-all.sh"
        ;;
    *)
        ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "APP_PATH=/tmp/${APPIMAGE_NAME} /tmp/tests/test-${TEST}.sh"
        ;;
esac
