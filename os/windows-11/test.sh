#!/bin/bash
set -euo pipefail

OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/config.sh"

TESTS_SRC="${FRAMEWORK_PATH}/shared/tests-windows"
APP_PATH='C:\Users\jean\AppData\Local\Programs\Rocket.Chat\Rocket.Chat.exe'
APP_PROCESS="Rocket.Chat"

ps_encode() {
    echo -n "$1" | iconv -t UTF-16LE | base64 -w 0
}

echo "Transferring test scripts to Windows 11 ($VM_IP)..."
CLEAN=$(ps_encode "if (Test-Path 'C:\tmp\tests') { Remove-Item -Recurse -Force 'C:\tmp\tests' }")
ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "powershell.exe -EncodedCommand ${CLEAN}"
scp -o StrictHostKeyChecking=no -r "${TESTS_SRC}" "${VM_USER}@${VM_IP}:C:/tmp/tests"

echo "Running tests on Windows 11 ($VM_IP)..."
RUN=$(ps_encode "\$ProgressPreference='SilentlyContinue'; \$env:APP_PATH='${APP_PATH}'; \$env:PROCESS_NAME='${APP_PROCESS}'; \$env:TEST_TIMEOUT='${TEST_TIMEOUT}'; & 'C:\tmp\tests\run-all.ps1'")
ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "powershell.exe -ExecutionPolicy Bypass -EncodedCommand ${RUN}"
