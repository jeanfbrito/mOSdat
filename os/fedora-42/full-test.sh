#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

print_usage() {
    cat << 'EOF'
Run full test matrix on Fedora 42

This script:
1. Builds old version (pre-fix)
2. Tests old version without GPU
3. Tests old version with GPU
4. Builds new version (fixed)
5. Tests new version without GPU
6. Tests new version with GPU
7. Generates comparison report

Usage: ./full-test.sh [OPTIONS]

Options:
  --old-ref REF     Git ref for old version (default: 22c1646)
  --new-ref REF     Git ref for new version (default: fix-x11-ubuntu2204)
  --skip-build      Use existing packages in results dir
  --skip-gpu        Skip GPU tests
  --test-only TEST  Run specific test only (wayland-fake, etc.)
  -h, --help        Show this help
EOF
}

OLD_REF="$OLD_VERSION_REF"
NEW_REF="$NEW_VERSION_REF"
SKIP_BUILD=false
SKIP_GPU=false
TEST_ONLY="all"

while [[ $# -gt 0 ]]; do
    case $1 in
        --old-ref) OLD_REF="$2"; shift 2 ;;
        --new-ref) NEW_REF="$2"; shift 2 ;;
        --skip-build) SKIP_BUILD=true; shift ;;
        --skip-gpu) SKIP_GPU=true; shift ;;
        --test-only) TEST_ONLY="$2"; shift 2 ;;
        -h|--help) print_usage; exit 0 ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

TIMESTAMP=$(timestamp)
RESULTS_DIR="${RESULTS_PATH}/${TIMESTAMP}-fedora42"
mkdir -p "$RESULTS_DIR"

log "========================================"
log "Fedora 42 Full Test Suite"
log "========================================"
log "Timestamp:   ${TIMESTAMP}"
log "Old Version: ${OLD_REF}"
log "New Version: ${NEW_REF}"
log "Results:     ${RESULTS_DIR}"
log "========================================"

run_test_phase() {
    local package="$1"
    local label="$2"
    local with_gpu="$3"
    
    local gpu_label=$([[ "$with_gpu" == "true" ]] && echo "gpu" || echo "no-gpu")
    local result_file="${RESULTS_DIR}/${label}-${gpu_label}.json"
    
    log ""
    log ">>> Testing: ${label} (GPU: ${with_gpu})"
    
    if [[ "$with_gpu" == "true" ]]; then
        "${SCRIPT_DIR}/gpu-control.sh" --attach
    else
        "${SCRIPT_DIR}/gpu-control.sh" --detach
    fi
    
    sleep 5
    
    "${SCRIPT_DIR}/deploy.sh" --package "$package"
    "${SCRIPT_DIR}/test.sh" --test "$TEST_ONLY" --label "${label}-${gpu_label}" --output "$result_file" || true
    
    log ">>> Results: ${result_file}"
}

if [[ "$SKIP_BUILD" != "true" ]]; then
    log ""
    log "=== Building Old Version ==="
    "${SCRIPT_DIR}/build.sh" --clean --ref "$OLD_REF"
    OLD_PKG=$(find "${REPO_PATH}/dist/" -name "rocketchat-*.rpm" | head -1)
    cp "$OLD_PKG" "${RESULTS_DIR}/old-version.rpm"
    
    log ""
    log "=== Building New Version ==="
    "${SCRIPT_DIR}/build.sh" --clean --ref "$NEW_REF"
    NEW_PKG=$(find "${REPO_PATH}/dist/" -name "rocketchat-*.rpm" | head -1)
    cp "$NEW_PKG" "${RESULTS_DIR}/new-version.rpm"
else
    OLD_PKG="${RESULTS_DIR}/old-version.rpm"
    NEW_PKG="${RESULTS_DIR}/new-version.rpm"
    
    if [[ ! -f "$OLD_PKG" ]] || [[ ! -f "$NEW_PKG" ]]; then
        log_error "Packages not found. Run without --skip-build first."
        exit 1
    fi
fi

log ""
log "=== Running Test Matrix ==="

run_test_phase "${RESULTS_DIR}/old-version.rpm" "old-version" "false"

if [[ "$SKIP_GPU" != "true" ]]; then
    run_test_phase "${RESULTS_DIR}/old-version.rpm" "old-version" "true"
fi

run_test_phase "${RESULTS_DIR}/new-version.rpm" "new-version" "false"

if [[ "$SKIP_GPU" != "true" ]]; then
    run_test_phase "${RESULTS_DIR}/new-version.rpm" "new-version" "true"
fi

log ""
log "=== Generating Report ==="

cat > "${RESULTS_DIR}/RESULTS.md" << EOF
# Fedora 42 Test Results

**Date**: $(date '+%Y-%m-%d %H:%M:%S')
**Old Version**: ${OLD_REF}
**New Version**: ${NEW_REF}

## Summary

| Test | Old (no GPU) | Old (GPU) | New (no GPU) | New (GPU) |
|------|--------------|-----------|--------------|-----------|
EOF

for test in wayland-real wayland-fake wayland-nodisp x11; do
    printf "| %s " "$test" >> "${RESULTS_DIR}/RESULTS.md"
    
    for file in old-version-no-gpu old-version-gpu new-version-no-gpu new-version-gpu; do
        json="${RESULTS_DIR}/${file}.json"
        if [[ -f "$json" ]]; then
            result=$(jq -r --arg t "$test" '.tests[] | select(.name == $t) | .result' "$json" 2>/dev/null || echo "N/A")
            printf "| %s " "$result" >> "${RESULTS_DIR}/RESULTS.md"
        else
            printf "| N/A " >> "${RESULTS_DIR}/RESULTS.md"
        fi
    done
    echo "|" >> "${RESULTS_DIR}/RESULTS.md"
done

log_success "Test suite complete!"
log ""
log "Results: ${RESULTS_DIR}"
log "Report:  ${RESULTS_DIR}/RESULTS.md"
log ""

cat "${RESULTS_DIR}/RESULTS.md"
