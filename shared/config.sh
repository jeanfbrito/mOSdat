#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRAMEWORK_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

[[ -f "${FRAMEWORK_DIR}/.env" ]] && export $(grep -v '^#' "${FRAMEWORK_DIR}/.env" | xargs)

export PROXMOX_HOST="${PROXMOX_HOST:-192.168.13.85}"
export PROXMOX_PORT="${PROXMOX_PORT:-8006}"
export PROXMOX_USER="${PROXMOX_USER:-root@pam}"
export PROXMOX_PASSWORD="${PROXMOX_PASSWORD}"
export PROXMOX_NODE="${PROXMOX_NODE:-pve}"

export REPO_PATH="${REPO_PATH:-/home/jean/projects/linux-testing/Rocket.Chat.Electron}"
export FRAMEWORK_PATH="${FRAMEWORK_DIR}"
export RESULTS_PATH="${FRAMEWORK_PATH}/results"

export OLD_VERSION_REF="${OLD_VERSION_REF:-22c1646}"
export OLD_VERSION_NAME="${OLD_VERSION_NAME:-4.11.0-pre-fix}"
export NEW_VERSION_REF="${NEW_VERSION_REF:-fix-x11-ubuntu2204}"
export NEW_VERSION_NAME="${NEW_VERSION_NAME:-4.11.1-fixed}"

export GPU_PCI_ADDRESS="${GPU_PCI_ADDRESS:-0000:01:00}"
export TEST_TIMEOUT="${TEST_TIMEOUT:-10}"
export VM_BOOT_TIMEOUT="${VM_BOOT_TIMEOUT:-120}"

export DEFAULT_VM_USER="${DEFAULT_VM_USER:-jean}"
export DEFAULT_VM_PASSWORD="${DEFAULT_VM_PASSWORD:-cb6wist3}"
