#!/bin/bash
OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/../../shared/config.sh"
source "${OS_SCRIPT_DIR}/../../shared/proxmox-api.sh"

export VMID="104"
export VM_NAME="windows-10"
export VM_IP="192.168.13.87"
export VM_USER="jean"
export VM_PASSWORD="cb6wist3"
export PACKAGE_FORMAT="nsis"
