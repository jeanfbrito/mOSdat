#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

print_usage() {
    cat << 'EOF'
Build Rocket.Chat Electron for Ubuntu 22.04

Usage: ./build.sh [OPTIONS]

Options:
  --clean         Remove dist/ and app/ before building
  --skip-compile  Skip TypeScript compilation
  --ref REF       Git ref to checkout before building
  -h, --help      Show this help

Examples:
  ./build.sh
  ./build.sh --clean --ref fix-x11-ubuntu2204
EOF
}

CLEAN=false
SKIP_COMPILE=false
GIT_REF=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --clean) CLEAN=true; shift ;;
        --skip-compile) SKIP_COMPILE=true; shift ;;
        --ref) GIT_REF="$2"; shift 2 ;;
        -h|--help) print_usage; exit 0 ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

cd "$REPO_PATH"

if [[ -n "$GIT_REF" ]]; then
    log "Checking out: $GIT_REF"
    git fetch origin 2>/dev/null || true
    git checkout "$GIT_REF" --quiet
fi

GIT_BRANCH=$(git branch --show-current 2>/dev/null || git rev-parse --short HEAD)
GIT_COMMIT=$(git rev-parse --short HEAD)

log "========================================"
log "Building Rocket.Chat Electron (Ubuntu 22.04)"
log "========================================"
log "Branch: ${GIT_BRANCH}"
log "Commit: ${GIT_COMMIT}"
log "Format: ${PACKAGE_FORMAT}"
log "========================================"

if [[ "$CLEAN" == "true" ]]; then
    log "Cleaning previous build..."
    rm -rf dist/ app/
fi

if [[ ! -d "node_modules" ]]; then
    log "Installing dependencies..."
    yarn install
fi

if [[ "$SKIP_COMPILE" != "true" ]]; then
    log "Compiling TypeScript..."
    yarn build
fi

log "Building ${PACKAGE_FORMAT} package..."
yarn electron-builder --publish never --linux "$PACKAGE_FORMAT"

BUILT_PKG=$(find dist/ -maxdepth 1 -name "rocketchat-*.${PACKAGE_FORMAT}" | head -1)

if [[ -z "$BUILT_PKG" ]]; then
    log_error "Build failed - no package found"
    exit 1
fi

log_success "Build complete: $BUILT_PKG"
echo "PACKAGE_PATH=$BUILT_PKG"
