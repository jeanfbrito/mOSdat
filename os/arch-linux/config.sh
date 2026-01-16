#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../shared/config.sh"
source "${SCRIPT_DIR}/../../shared/proxmox-api.sh"

export VMID="103"
export VM_NAME="arch-linux"
export VM_USER="jean"
export VM_PASSWORD="cb6wist3"
export PACKAGE_FORMAT="AppImage"
export PACKAGE_MANAGER="pacman"
