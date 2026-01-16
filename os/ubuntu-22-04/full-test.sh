#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

echo "=== Full Test Suite: Ubuntu 22.04 ==="

echo "Building old version..."
"${SCRIPT_DIR}/build.sh" --ref "$OLD_VERSION_REF" --clean
"${SCRIPT_DIR}/deploy.sh"
echo "Testing old version..."
"${SCRIPT_DIR}/test.sh" all || true

echo ""
echo "Building new version..."
"${SCRIPT_DIR}/build.sh" --ref "$NEW_VERSION_REF" --clean
"${SCRIPT_DIR}/deploy.sh"
echo "Testing new version..."
"${SCRIPT_DIR}/test.sh" all

echo "=== Done ==="
