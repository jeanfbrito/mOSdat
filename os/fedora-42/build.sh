#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

FORMAT="${1:-all}"
GIT_REF="${2:-}"
CLEAN="${3:-}"

cd "$REPO_PATH"

[[ -n "$GIT_REF" ]] && git fetch origin 2>/dev/null || true && git checkout "$GIT_REF" --quiet
[[ "$CLEAN" == "--clean" ]] && rm -rf dist/ app/
[[ ! -d "node_modules" ]] && yarn install

yarn build

case "$FORMAT" in
    rpm)
        yarn electron-builder --publish never --linux rpm
        ;;
    appimage)
        yarn electron-builder --publish never --linux AppImage
        ;;
    all)
        yarn electron-builder --publish never --linux rpm AppImage
        ;;
    *)
        echo "Usage: $0 [rpm|appimage|all] [git-ref] [--clean]"
        exit 1
        ;;
esac

echo ""
echo "Built packages:"
ls -la dist/*.rpm dist/*.AppImage 2>/dev/null || true
