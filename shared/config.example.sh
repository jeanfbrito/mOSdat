#!/bin/bash
#
# Example Configuration - Copy to config.local.sh and fill in your values
#
# cp config.example.sh config.local.sh
# nano config.local.sh

export PROXMOX_HOST="192.168.1.100"
export PROXMOX_PORT="8006"
export PROXMOX_USER="root@pam"
export PROXMOX_PASSWORD="your_secure_password"
export PROXMOX_NODE="pve"

export REPO_PATH="/home/youruser/Rocket.Chat.Electron"

export GPU_PCI_ADDRESS="0000:01:00"

export DEFAULT_VM_USER="testuser"
export DEFAULT_VM_PASSWORD="vm_password"
