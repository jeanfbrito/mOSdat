#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

PACKAGE_PATH="${1:-$(find "${REPO_PATH}/dist/" -maxdepth 1 -name "rocketchat-*.AppImage" 2>/dev/null | head -1)}"

[[ -z "$PACKAGE_PATH" || ! -f "$PACKAGE_PATH" ]] && echo "AppImage not found. Run build.sh first" && exit 1

PACKAGE_NAME=$(basename "$PACKAGE_PATH")

echo "Deploying $PACKAGE_NAME to Arch Linux ($VM_IP)"

scp -o StrictHostKeyChecking=no "$PACKAGE_PATH" "${VM_USER}@${VM_IP}:/tmp/"
ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "chmod +x /tmp/${PACKAGE_NAME}"

echo "Deployed: $PACKAGE_NAME"
echo "Run with: /tmp/${PACKAGE_NAME}"
