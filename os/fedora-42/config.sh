#!/bin/bash
# Fedora 42 Specific Configuration

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../shared/config.sh"
source "${SCRIPT_DIR}/../../shared/proxmox-api.sh"

export VMID="100"
export VM_NAME="fedora42-gpu-test"
export VM_USER="jean"
export VM_PASSWORD="cb6wist3"
export PACKAGE_FORMAT="rpm"
export PACKAGE_MANAGER="dnf"
