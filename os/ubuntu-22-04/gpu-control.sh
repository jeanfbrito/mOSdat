#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

print_usage() {
    cat << 'EOF'
Control GPU passthrough for Ubuntu 22.04 VM

Usage: ./gpu-control.sh ACTION

Actions:
  --attach   Attach GPU to VM
  --detach   Detach GPU from VM
  --status   Show current GPU status
EOF
}

ACTION=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --attach) ACTION="attach"; shift ;;
        --detach) ACTION="detach"; shift ;;
        --status) ACTION="status"; shift ;;
        -h|--help) print_usage; exit 0 ;;
        *) log_error "Unknown: $1"; exit 1 ;;
    esac
done

[[ -z "$ACTION" ]] && { print_usage; exit 1; }

gpu_attached() {
    local config=$(vm_get_config "$VMID")
    echo "$config" | jq -e 'to_entries[] | select(.key | startswith("hostpci"))' > /dev/null 2>&1
}

restart_vm() {
    local was_running=false
    [[ "$(vm_status "$VMID")" == "running" ]] && was_running=true
    
    if [[ "$was_running" == "true" ]]; then
        log "Stopping VM..."
        vm_stop "$VMID" > /dev/null
        for i in {1..30}; do
            [[ "$(vm_status "$VMID")" == "stopped" ]] && break
            sleep 2
        done
    fi
    
    "$@"
    
    if [[ "$was_running" == "true" ]]; then
        log "Starting VM..."
        vm_start "$VMID" > /dev/null
        VM_IP=$(vm_wait_for_ip "$VMID")
        log "VM ready: $VM_IP"
    fi
}

do_attach() {
    if gpu_attached; then
        log "GPU already attached"
        return 0
    fi
    log "Attaching GPU..."
    vm_set_config "$VMID" "hostpci0=${GPU_PCI_ADDRESS},pcie=1" > /dev/null
    log_success "GPU attached"
}

do_detach() {
    if ! gpu_attached; then
        log "No GPU attached"
        return 0
    fi
    log "Detaching GPU..."
    local config=$(vm_get_config "$VMID")
    local keys=$(echo "$config" | jq -r 'to_entries[] | select(.key | startswith("hostpci")) | .key')
    for key in $keys; do
        vm_set_config "$VMID" "delete=$key" > /dev/null
    done
    log_success "GPU detached"
}

do_status() {
    log "========================================"
    log "GPU Status: Ubuntu 22.04 (${VMID})"
    log "========================================"
    log "VM Status: $(vm_status "$VMID")"
    if gpu_attached; then
        log "GPU: ATTACHED"
    else
        log "GPU: NOT ATTACHED"
    fi
    log "========================================"
}

case "$ACTION" in
    attach) restart_vm do_attach ;;
    detach) restart_vm do_detach ;;
    status) do_status ;;
esac
