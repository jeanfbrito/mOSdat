#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../shared/config.sh"
source "${SCRIPT_DIR}/../../shared/proxmox-api.sh"

export VMID="100"
export VM_NAME="fedora42-gpu-test"
export VM_IP="192.168.13.80"
export VM_USER="jean"
export PACKAGE_FORMAT="rpm"
export PACKAGE_MANAGER="dnf"
