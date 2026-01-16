#!/bin/bash
set -euo pipefail

OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/config.sh"

TEST="${1:-all}"
TESTS_DIR="${OS_SCRIPT_DIR}/../../shared/tests"

echo "Testing on Fedora 42 ($VM_IP)"

scp -o StrictHostKeyChecking=no -r "$TESTS_DIR" "${VM_USER}@${VM_IP}:/tmp/"

case "$TEST" in
    all)
        ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "/tmp/tests/run-all.sh"
        ;;
    *)
        ssh -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "/tmp/tests/test-${TEST}.sh"
        ;;
esac
