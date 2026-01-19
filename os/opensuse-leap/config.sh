#!/bin/bash
OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/../../shared/config.sh"
source "${OS_SCRIPT_DIR}/../../shared/proxmox-api.sh"

export VMID="106"
export VM_NAME="opensuse-leap"
export VM_IP="192.168.13.84"  # To be set after VM installation
export VM_USER="jean"
export PACKAGE_FORMAT="rpm"
export PACKAGE_MANAGER="zypper"
