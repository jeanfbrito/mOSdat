#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

print_usage() {
    cat << 'EOF'
Run full test matrix on Ubuntu 22.04

Usage: ./full-test.sh [OPTIONS]

Options:
  --old-ref REF     Git ref for old version (default: 22c1646)
  --new-ref REF     Git ref for new version (default: fix-x11-ubuntu2204)
  --skip-build      Use existing packages
  --skip-gpu        Skip GPU tests
  -h, --help        Show this help
EOF
}

OLD_REF="$OLD_VERSION_REF"
NEW_REF="$NEW_VERSION_REF"
SKIP_BUILD=false
SKIP_GPU=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --old-ref) OLD_REF="$2"; shift 2 ;;
        --new-ref) NEW_REF="$2"; shift 2 ;;
        --skip-build) SKIP_BUILD=true; shift ;;
        --skip-gpu) SKIP_GPU=true; shift ;;
        -h|--help) print_usage; exit 0 ;;
        *) log_error "Unknown: $1"; exit 1 ;;
    esac
done

TIMESTAMP=$(timestamp)
RESULTS_DIR="${RESULTS_PATH}/${TIMESTAMP}-ubuntu2204"
mkdir -p "$RESULTS_DIR"

log "========================================"
log "Ubuntu 22.04 Full Test Suite"
log "========================================"
log "Results: ${RESULTS_DIR}"
log "========================================"

run_phase() {
    local package="$1"
    local label="$2"
    local with_gpu="$3"
    
    local gpu_label=$([[ "$with_gpu" == "true" ]] && echo "gpu" || echo "no-gpu")
    
    log ">>> ${label} (GPU: ${with_gpu})"
    
    if [[ "$with_gpu" == "true" ]]; then
        "${SCRIPT_DIR}/gpu-control.sh" --attach
    else
        "${SCRIPT_DIR}/gpu-control.sh" --detach
    fi
    
    sleep 5
    "${SCRIPT_DIR}/deploy.sh" --package "$package"
    "${SCRIPT_DIR}/test.sh" --label "${label}-${gpu_label}" \
        --output "${RESULTS_DIR}/${label}-${gpu_label}.json" || true
}

if [[ "$SKIP_BUILD" != "true" ]]; then
    log "Building old version..."
    "${SCRIPT_DIR}/build.sh" --clean --ref "$OLD_REF"
    cp "$(find "${REPO_PATH}/dist/" -name "*.deb" | head -1)" "${RESULTS_DIR}/old.deb"
    
    log "Building new version..."
    "${SCRIPT_DIR}/build.sh" --clean --ref "$NEW_REF"
    cp "$(find "${REPO_PATH}/dist/" -name "*.deb" | head -1)" "${RESULTS_DIR}/new.deb"
fi

run_phase "${RESULTS_DIR}/old.deb" "old" "false"
[[ "$SKIP_GPU" != "true" ]] && run_phase "${RESULTS_DIR}/old.deb" "old" "true"
run_phase "${RESULTS_DIR}/new.deb" "new" "false"
[[ "$SKIP_GPU" != "true" ]] && run_phase "${RESULTS_DIR}/new.deb" "new" "true"

log_success "Complete! Results: ${RESULTS_DIR}"
