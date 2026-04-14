#!/bin/bash
set -euo pipefail

OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/config.sh"

echo "=== Full Test Suite: Windows 10 ==="

echo "Building old version..."
"${OS_SCRIPT_DIR}/build.sh" "$OLD_VERSION_REF" --clean
"${OS_SCRIPT_DIR}/deploy.sh"
echo "Testing old version..."
"${OS_SCRIPT_DIR}/test.sh" || true

echo ""
echo "Building new version..."
"${OS_SCRIPT_DIR}/build.sh" "$NEW_VERSION_REF" --clean
"${OS_SCRIPT_DIR}/deploy.sh"
echo "Testing new version..."
"${OS_SCRIPT_DIR}/test.sh"

echo "=== Done ==="
