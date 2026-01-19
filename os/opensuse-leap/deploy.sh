#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

PACKAGE_PATH="${1:-$(find "${REPO_PATH}/dist/" -maxdepth 1 -name "rocketchat-*.rpm" 2>/dev/null | head -1)}"

[[ -z "$PACKAGE_PATH" || ! -f "$PACKAGE_PATH" ]] && echo "Package not found. Run build.sh first" && exit 1

PACKAGE_NAME=$(basename "$PACKAGE_PATH")

echo "Deploying $PACKAGE_NAME to openSUSE Leap ($VM_IP)"

scp -o StrictHostKeyChecking=no "$PACKAGE_PATH" "${VM_USER}@${VM_IP}:/tmp/"
ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "sudo zypper --non-interactive install --allow-unsigned-rpm /tmp/${PACKAGE_NAME}"

echo "Deployed: $PACKAGE_NAME"
