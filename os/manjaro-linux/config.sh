#!/bin/bash
OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/../../shared/config.sh"
source "${OS_SCRIPT_DIR}/../../shared/proxmox-api.sh"

export VMID="103"
export VM_NAME="manjaro-linux"
export VM_IP=""
export VM_USER="jean"
export PACKAGE_FORMAT="AppImage"
export PACKAGE_MANAGER="pacman"
