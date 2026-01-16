#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

print_usage() {
    cat << 'EOF'
Control GPU passthrough for Fedora 42 VM

Usage: ./gpu-control.sh ACTION

Actions:
  --attach   Attach GPU to VM (requires VM restart)
  --detach   Detach GPU from VM (requires VM restart)
  --status   Show current GPU status

Examples:
  ./gpu-control.sh --status
  ./gpu-control.sh --attach
  ./gpu-control.sh --detach
EOF
}

ACTION=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --attach) ACTION="attach"; shift ;;
        --detach) ACTION="detach"; shift ;;
        --status) ACTION="status"; shift ;;
        -h|--help) print_usage; exit 0 ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
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
        log "VM stopped"
    fi
    
    "$@"
    
    if [[ "$was_running" == "true" ]]; then
        log "Starting VM..."
        vm_start "$VMID" > /dev/null
        
        log "Waiting for VM to boot..."
        VM_IP=$(vm_wait_for_ip "$VMID")
        log "VM ready: $VM_IP"
    fi
}

do_attach() {
    if gpu_attached; then
        log "GPU already attached"
        return 0
    fi
    
    log "Attaching GPU (${GPU_PCI_ADDRESS})..."
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
    log "GPU Status for Fedora 42 VM (${VMID})"
    log "========================================"
    
    local status=$(vm_status "$VMID")
    log "VM Status: $status"
    
    if gpu_attached; then
        log "GPU: ATTACHED"
        local config=$(vm_get_config "$VMID")
        echo "$config" | jq -r 'to_entries[] | select(.key | startswith("hostpci")) | "  \(.key)=\(.value)"'
    else
        log "GPU: NOT ATTACHED"
    fi
    
    if [[ "$status" == "running" ]]; then
        local ip=$(vm_get_ip "$VMID" 2>/dev/null || echo "unknown")
        log "VM IP: $ip"
        
        if gpu_attached && [[ "$ip" != "unknown" ]]; then
            log ""
            log "GPU visible in VM:"
            docker run --rm --network host alpine sh -c "
                apk add --no-cache openssh-client sshpass >/dev/null 2>&1
                sshpass -p '${VM_PASSWORD}' ssh -o StrictHostKeyChecking=no ${VM_USER}@${ip} \
                    'lspci | grep -i nvidia || echo \"No NVIDIA GPU visible\"'
            " 2>/dev/null
        fi
    fi
    
    log "========================================"
    
    echo ""
    echo "GPU_ATTACHED=$(gpu_attached && echo true || echo false)"
    echo "VM_STATUS=$status"
}

case "$ACTION" in
    attach) restart_vm do_attach ;;
    detach) restart_vm do_detach ;;
    status) do_status ;;
esac
