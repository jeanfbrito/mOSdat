#!/bin/bash
set -euo pipefail

OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/config.sh"

GIT_REF="${1:-}"
CLEAN="${2:-}"

cd "$REPO_PATH"

[[ -n "$GIT_REF" ]] && git fetch origin 2>/dev/null || true && git checkout "$GIT_REF" --quiet
[[ "$CLEAN" == "--clean" ]] && rm -rf dist/ app/
[[ ! -d "node_modules" ]] && yarn install

yarn build
yarn electron-builder --publish never --win nsis

echo ""
echo "Built packages:"
ls -la dist/*.exe 2>/dev/null || true
