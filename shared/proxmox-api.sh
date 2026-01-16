#!/bin/bash
# Proxmox API Helper Functions
# Source this file after config.sh

PROXMOX_TICKET=""
PROXMOX_CSRF=""

proxmox_auth() {
    local auth_response
    auth_response=$(curl -k -s -d "username=${PROXMOX_USER}&password=${PROXMOX_PASSWORD}" \
        "https://${PROXMOX_HOST}:${PROXMOX_PORT}/api2/json/access/ticket")
    
    PROXMOX_TICKET=$(echo "$auth_response" | jq -r '.data.ticket')
    PROXMOX_CSRF=$(echo "$auth_response" | jq -r '.data.CSRFPreventionToken')
    
    if [[ "$PROXMOX_TICKET" == "null" ]] || [[ -z "$PROXMOX_TICKET" ]]; then
        echo "ERROR: Failed to authenticate with Proxmox" >&2
        return 1
    fi
}

proxmox_get() {
    local endpoint="$1"
    [[ -z "$PROXMOX_TICKET" ]] && proxmox_auth
    curl -k -s -b "PVEAuthCookie=$PROXMOX_TICKET" \
        "https://${PROXMOX_HOST}:${PROXMOX_PORT}${endpoint}"
}

proxmox_post() {
    local endpoint="$1"
    local data="${2:-}"
    [[ -z "$PROXMOX_TICKET" ]] && proxmox_auth
    curl -k -s -b "PVEAuthCookie=$PROXMOX_TICKET" \
        -H "CSRFPreventionToken: $PROXMOX_CSRF" \
        -X POST ${data:+-d "$data"} \
        "https://${PROXMOX_HOST}:${PROXMOX_PORT}${endpoint}"
}

proxmox_put() {
    local endpoint="$1"
    local data="${2:-}"
    [[ -z "$PROXMOX_TICKET" ]] && proxmox_auth
    curl -k -s -b "PVEAuthCookie=$PROXMOX_TICKET" \
        -H "CSRFPreventionToken: $PROXMOX_CSRF" \
        -X PUT ${data:+-d "$data"} \
        "https://${PROXMOX_HOST}:${PROXMOX_PORT}${endpoint}"
}

vm_status() {
    local vmid="$1"
    proxmox_get "/api2/json/nodes/${PROXMOX_NODE}/qemu/${vmid}/status/current" | jq -r '.data.status'
}

vm_start() {
    local vmid="$1"
    proxmox_post "/api2/json/nodes/${PROXMOX_NODE}/qemu/${vmid}/status/start"
}

vm_stop() {
    local vmid="$1"
    proxmox_post "/api2/json/nodes/${PROXMOX_NODE}/qemu/${vmid}/status/stop"
}

vm_get_ip() {
    local vmid="$1"
    proxmox_get "/api2/json/nodes/${PROXMOX_NODE}/qemu/${vmid}/agent/network-get-interfaces" | \
        jq -r '.data.result[] | select(.name != "lo") | .["ip-addresses"][]? | select(.["ip-address-type"] == "ipv4") | .["ip-address"]' | head -1
}

vm_wait_for_ip() {
    local vmid="$1"
    local timeout="${2:-$VM_BOOT_TIMEOUT}"
    local elapsed=0
    
    while [[ $elapsed -lt $timeout ]]; do
        local ip=$(vm_get_ip "$vmid" 2>/dev/null)
        if [[ -n "$ip" ]]; then
            echo "$ip"
            return 0
        fi
        sleep 3
        elapsed=$((elapsed + 3))
    done
    
    echo "ERROR: Timeout waiting for VM IP" >&2
    return 1
}

vm_get_config() {
    local vmid="$1"
    proxmox_get "/api2/json/nodes/${PROXMOX_NODE}/qemu/${vmid}/config" | jq '.data'
}

vm_set_config() {
    local vmid="$1"
    local config="$2"
    proxmox_put "/api2/json/nodes/${PROXMOX_NODE}/qemu/${vmid}/config" "$config"
}

vm_has_gpu() {
    local vmid="$1"
    local config=$(vm_get_config "$vmid")
    echo "$config" | jq -e 'to_entries[] | select(.key | startswith("hostpci"))' > /dev/null 2>&1
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

log_success() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] SUCCESS: $*"
}

timestamp() {
    date '+%Y%m%d_%H%M%S'
}
