#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../shared/config.sh"
source "${SCRIPT_DIR}/../../shared/proxmox-api.sh"

export VMID="105"
export VM_NAME="windows-11"
export VM_USER="jean"
export VM_PASSWORD="cb6wist3"
export PACKAGE_FORMAT="nsis"
