#!/bin/bash
set -euo pipefail

OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/config.sh"

PACKAGE_PATH="${1:-$(find "${REPO_PATH}/dist/" -maxdepth 1 -name "rocketchat-*.exe" 2>/dev/null | head -1)}"

[[ -z "$PACKAGE_PATH" || ! -f "$PACKAGE_PATH" ]] && echo "Package not found. Run build.sh first" && exit 1

PACKAGE_NAME=$(basename "$PACKAGE_PATH")

echo "Deploying $PACKAGE_NAME to Windows 11 ($VM_IP)"

ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" \
    "powershell.exe -Command \"if (-not (Test-Path C:\\tmp)) { New-Item -ItemType Directory -Path C:\\tmp }\""

scp -o StrictHostKeyChecking=no "$PACKAGE_PATH" "${VM_USER}@${VM_IP}:C:/tmp/"

ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" \
    "powershell.exe -ExecutionPolicy Bypass -Command \"Start-Process -Wait -FilePath 'C:\\tmp\\${PACKAGE_NAME}' -ArgumentList '/S'\""

echo "Deployed: $PACKAGE_NAME"
