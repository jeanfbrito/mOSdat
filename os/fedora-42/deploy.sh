#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

print_usage() {
    cat << 'EOF'
Deploy Rocket.Chat Electron to Fedora 42 VM

Usage: ./deploy.sh [OPTIONS]

Options:
  --package PATH  Path to RPM file (default: auto-detect from dist/)
  --skip-install  Only transfer, don't install
  -h, --help      Show this help

Examples:
  ./deploy.sh                                    # Deploy latest build
  ./deploy.sh --package /path/to/rocketchat.rpm  # Deploy specific package
  ./deploy.sh --skip-install                     # Transfer only
EOF
}

PACKAGE_PATH=""
SKIP_INSTALL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --package) PACKAGE_PATH="$2"; shift 2 ;;
        --skip-install) SKIP_INSTALL=true; shift ;;
        -h|--help) print_usage; exit 0 ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$PACKAGE_PATH" ]]; then
    PACKAGE_PATH=$(find "${REPO_PATH}/dist/" -maxdepth 1 -name "rocketchat-*.rpm" 2>/dev/null | head -1)
fi

if [[ -z "$PACKAGE_PATH" ]] || [[ ! -f "$PACKAGE_PATH" ]]; then
    log_error "Package not found. Run build.sh first or specify --package"
    exit 1
fi

PACKAGE_NAME=$(basename "$PACKAGE_PATH")

log "========================================"
log "Deploying to Fedora 42 VM"
log "========================================"
log "VM ID:   ${VMID}"
log "Package: ${PACKAGE_NAME}"
log "Size:    $(du -h "$PACKAGE_PATH" | cut -f1)"
log "========================================"

log "Getting VM IP..."
VM_IP=$(vm_get_ip "$VMID")

if [[ -z "$VM_IP" ]]; then
    log_error "Could not get VM IP. Is VM running with guest agent?"
    exit 1
fi

log "VM IP: $VM_IP"

log "Transferring package..."
docker run --rm --network host \
    -v "$(dirname "$PACKAGE_PATH"):/transfer:ro" \
    alpine sh -c "
        apk add --no-cache openssh-client sshpass >/dev/null 2>&1
        sshpass -p '${VM_PASSWORD}' scp -o StrictHostKeyChecking=no \
            /transfer/${PACKAGE_NAME} ${VM_USER}@${VM_IP}:/tmp/
    " 2>/dev/null

log "Transfer complete"

if [[ "$SKIP_INSTALL" == "true" ]]; then
    log "Skipping installation (--skip-install)"
    exit 0
fi

log "Installing package..."
docker run --rm --network host alpine sh -c "
    apk add --no-cache openssh-client sshpass >/dev/null 2>&1
    sshpass -p '${VM_PASSWORD}' ssh -o StrictHostKeyChecking=no ${VM_USER}@${VM_IP} \
        'echo ${VM_PASSWORD} | sudo -S dnf install -y /tmp/${PACKAGE_NAME} 2>&1'
" 2>/dev/null

log_success "Package installed"

log "Verifying installation..."
docker run --rm --network host alpine sh -c "
    apk add --no-cache openssh-client sshpass >/dev/null 2>&1
    sshpass -p '${VM_PASSWORD}' ssh -o StrictHostKeyChecking=no ${VM_USER}@${VM_IP} \
        'ls -la /opt/Rocket.Chat/rocketchat-desktop* && file /opt/Rocket.Chat/rocketchat-desktop'
" 2>/dev/null

echo ""
echo "VM_IP=$VM_IP"
echo "PACKAGE_INSTALLED=$PACKAGE_NAME"
