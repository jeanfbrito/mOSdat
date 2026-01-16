#!/bin/bash
OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/../../shared/config.sh"
source "${OS_SCRIPT_DIR}/../../shared/proxmox-api.sh"

export VMID="101"
export VM_NAME="ubuntu-22.04"
export VM_IP="192.168.13.81"
export VM_USER="jean"
export PACKAGE_FORMAT="deb"
export PACKAGE_MANAGER="apt"
