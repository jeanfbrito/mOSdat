import time
from typing import Optional

from .config import ProxmoxConfig, VMConfig
from .proxmox import ProxmoxAPI
from .ssh import SSHClient, wait_for_ssh


class GPUError(Exception):
    pass


class GPUManager:
    def __init__(self, api: ProxmoxAPI, config: ProxmoxConfig, vms: list[VMConfig] | None = None):
        self.api = api
        self.config = config
        self.vms = vms or []

    def find_current_owner(self) -> Optional[int]:
        for vm in self.vms:
            if self.api.has_gpu_attached(vm.vmid):
                return vm.vmid
        return None

    def detach_from_current(self, log_fn=print) -> Optional[int]:
        current_owner = self.find_current_owner()
        if current_owner is None:
            log_fn("GPU not attached to any VM")
            return None

        log_fn(f"Detaching GPU from VM {current_owner}")
        
        status = self.api.get_vm_status(current_owner)
        if status == "running":
            log_fn(f"  Stopping VM {current_owner}...")
            self.api.stop_vm(current_owner)
            if not self.api.wait_for_status(current_owner, "stopped", timeout=60):
                raise GPUError(f"VM {current_owner} did not stop in time")
        
        log_fn(f"  Removing GPU config from VM {current_owner}")
        self.api.detach_gpu(current_owner)
        
        log_fn(f"  Starting VM {current_owner} without GPU...")
        self.api.start_vm(current_owner)
        
        return current_owner

    def attach_to_vm(self, vmid: int, log_fn=print) -> str:
        log_fn(f"Attaching GPU to VM {vmid}")
        
        if self.api.has_gpu_attached(vmid):
            log_fn(f"  GPU already attached to VM {vmid}")
            status = self.api.get_vm_status(vmid)
            if status != "running":
                log_fn(f"  Starting VM {vmid}...")
                self.api.start_vm(vmid)
            ip = self.api.wait_for_ip(vmid, timeout=120)
            if not ip:
                raise GPUError(f"Could not get IP for VM {vmid}")
            return ip
        
        current_owner = self.find_current_owner()
        if current_owner is not None:
            self.detach_from_current(log_fn)
            time.sleep(2)
        
        status = self.api.get_vm_status(vmid)
        if status == "running":
            log_fn(f"  Stopping VM {vmid} to attach GPU...")
            self.api.stop_vm(vmid)
            if not self.api.wait_for_status(vmid, "stopped", timeout=60):
                raise GPUError(f"VM {vmid} did not stop in time")
        
        log_fn(f"  Adding GPU config to VM {vmid}")
        self.api.attach_gpu(vmid, self.config.gpu_pci_address)
        
        log_fn(f"  Starting VM {vmid} with GPU...")
        self.api.start_vm(vmid)
        
        log_fn(f"  Waiting for VM {vmid} to get IP...")
        ip = self.api.wait_for_ip(vmid, timeout=120)
        if not ip:
            raise GPUError(f"Could not get IP for VM {vmid}")
        
        log_fn(f"  VM {vmid} IP: {ip}")
        return ip

    def verify_gpu_visible(self, vm: VMConfig, log_fn=print) -> bool:
        log_fn(f"  Verifying GPU visible in VM {vm.name}...")
        
        if not wait_for_ssh(vm.ip, vm.user, timeout=60):
            log_fn(f"  ERROR: Cannot SSH to VM {vm.name}")
            return False
        
        ssh = SSHClient(vm.ip, vm.user)
        result = ssh.run("lspci | grep -i nvidia", timeout=30)
        
        if result.success and "NVIDIA" in result.stdout:
            log_fn(f"  GPU visible: {result.stdout.strip()}")
            return True
        
        log_fn(f"  WARNING: GPU not visible in VM {vm.name}")
        return False
