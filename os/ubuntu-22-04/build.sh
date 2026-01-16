#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

CLEAN=false
SKIP_COMPILE=false
GIT_REF=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --clean) CLEAN=true; shift ;;
        --skip-compile) SKIP_COMPILE=true; shift ;;
        --ref) GIT_REF="$2"; shift 2 ;;
        -h|--help) echo "Usage: ./build.sh [--clean] [--skip-compile] [--ref REF]"; exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

cd "$REPO_PATH"

[[ -n "$GIT_REF" ]] && git fetch origin 2>/dev/null || true && git checkout "$GIT_REF" --quiet

GIT_COMMIT=$(git rev-parse --short HEAD)

echo "Building Rocket.Chat Electron (Ubuntu 22.04) - Commit: $GIT_COMMIT"

[[ "$CLEAN" == "true" ]] && rm -rf dist/ app/
[[ ! -d "node_modules" ]] && yarn install
[[ "$SKIP_COMPILE" != "true" ]] && yarn build

yarn electron-builder --publish never --linux deb

BUILT_PKG=$(find dist/ -maxdepth 1 -name "rocketchat-*.deb" | head -1)
[[ -z "$BUILT_PKG" ]] && echo "Build failed" && exit 1

echo "Built: $BUILT_PKG"
echo "PACKAGE_PATH=$BUILT_PKG"
