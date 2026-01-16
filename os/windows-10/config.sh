#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../shared/config.sh"
source "${SCRIPT_DIR}/../../shared/proxmox-api.sh"

export VMID="104"
export VM_NAME="windows-10"
export VM_USER="jean"
export VM_PASSWORD="cb6wist3"
export PACKAGE_FORMAT="nsis"
