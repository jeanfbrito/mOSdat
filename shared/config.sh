#!/bin/bash
#
# Shared Configuration for Rocket.Chat Electron Test Framework
#
# SETUP: Copy config.example.sh to config.local.sh and set your values.
#        Or export environment variables before running scripts.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "${SCRIPT_DIR}/config.local.sh" ]]; then
    source "${SCRIPT_DIR}/config.local.sh"
fi

export PROXMOX_HOST="${PROXMOX_HOST:-192.168.1.100}"
export PROXMOX_PORT="${PROXMOX_PORT:-8006}"
export PROXMOX_USER="${PROXMOX_USER:-root@pam}"
export PROXMOX_PASSWORD="${PROXMOX_PASSWORD:-your_password_here}"
export PROXMOX_NODE="${PROXMOX_NODE:-pve}"

export REPO_PATH="${REPO_PATH:-/path/to/Rocket.Chat.Electron}"

export FRAMEWORK_PATH="${FRAMEWORK_PATH:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
export RESULTS_PATH="${RESULTS_PATH:-${FRAMEWORK_PATH}/results}"

export OLD_VERSION_REF="${OLD_VERSION_REF:-22c1646}"
export OLD_VERSION_NAME="${OLD_VERSION_NAME:-4.11.0-pre-fix}"
export NEW_VERSION_REF="${NEW_VERSION_REF:-fix-x11-ubuntu2204}"
export NEW_VERSION_NAME="${NEW_VERSION_NAME:-4.11.1-fixed}"

export GPU_PCI_ADDRESS="${GPU_PCI_ADDRESS:-0000:01:00}"

export TEST_TIMEOUT="${TEST_TIMEOUT:-10}"
export VM_BOOT_TIMEOUT="${VM_BOOT_TIMEOUT:-120}"

export DEFAULT_VM_USER="${DEFAULT_VM_USER:-testuser}"
export DEFAULT_VM_PASSWORD="${DEFAULT_VM_PASSWORD:-testpassword}"
