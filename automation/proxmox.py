import time
from typing import Any, Optional

import requests
import urllib3

from .config import ProxmoxConfig

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ProxmoxAPIError(Exception):
    pass


class ProxmoxAPI:
    def __init__(self, config: ProxmoxConfig):
        self.config = config
        self._ticket: Optional[str] = None
        self._csrf_token: Optional[str] = None
        self._session = requests.Session()
        self._session.verify = False

    def _ensure_auth(self) -> None:
        if self._ticket is not None:
            return
        
        url = f"{self.config.base_url}/access/ticket"
        response = self._session.post(url, data={
            "username": self.config.user,
            "password": self.config.password,
        })
        
        if response.status_code != 200:
            raise ProxmoxAPIError(f"Authentication failed: {response.text}")
        
        data = response.json().get("data", {})
        self._ticket = data.get("ticket")
        self._csrf_token = data.get("CSRFPreventionToken")
        
        if not self._ticket:
            raise ProxmoxAPIError("No ticket in auth response")

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        self._ensure_auth()
        
        url = f"{self.config.base_url}{endpoint}"
        headers = kwargs.pop("headers", {})
        headers["CSRFPreventionToken"] = self._csrf_token or ""
        cookies = {"PVEAuthCookie": self._ticket or ""}
        
        response = self._session.request(
            method, url, headers=headers, cookies=cookies, **kwargs
        )
        
        if response.status_code >= 400:
            raise ProxmoxAPIError(f"API error {response.status_code}: {response.text}")
        
        return response.json()

    def get(self, endpoint: str) -> dict:
        return self._request("GET", endpoint)

    def post(self, endpoint: str, data: Optional[dict] = None) -> dict:
        return self._request("POST", endpoint, data=data or {})

    def put(self, endpoint: str, data: Optional[dict] = None) -> dict:
        return self._request("PUT", endpoint, data=data or {})

    def delete(self, endpoint: str) -> dict:
        return self._request("DELETE", endpoint)

    def get_vm_status(self, vmid: int) -> str:
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/status/current"
        data = self.get(endpoint).get("data", {})
        return data.get("status", "unknown")

    def start_vm(self, vmid: int) -> None:
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/status/start"
        self.post(endpoint)

    def stop_vm(self, vmid: int) -> None:
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/status/stop"
        self.post(endpoint)

    def shutdown_vm(self, vmid: int) -> None:
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/status/shutdown"
        self.post(endpoint)

    def get_vm_config(self, vmid: int) -> dict:
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/config"
        return self.get(endpoint).get("data", {})

    def set_vm_config(self, vmid: int, **config) -> None:
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/config"
        self.put(endpoint, data=config)

    def get_vm_ip(self, vmid: int) -> Optional[str]:
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/agent/network-get-interfaces"
        try:
            data = self.get(endpoint).get("data", {}).get("result", [])
            for iface in data:
                if iface.get("name") == "lo":
                    continue
                for addr in iface.get("ip-addresses", []):
                    if addr.get("ip-address-type") == "ipv4":
                        ip = addr.get("ip-address")
                        if ip and not ip.startswith("127."):
                            return ip
        except ProxmoxAPIError:
            pass
        return None

    def wait_for_status(self, vmid: int, target_status: str, timeout: int = 120) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.get_vm_status(vmid) == target_status:
                return True
            time.sleep(3)
        return False

    def wait_for_ip(self, vmid: int, timeout: int = 120) -> Optional[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ip = self.get_vm_ip(vmid)
            if ip:
                return ip
            time.sleep(5)
        return None

    def has_gpu_attached(self, vmid: int) -> bool:
        config = self.get_vm_config(vmid)
        return "hostpci0" in config

    def attach_gpu(self, vmid: int, pci_address: str) -> None:
        self.set_vm_config(vmid, hostpci0=pci_address)

    def detach_gpu(self, vmid: int) -> None:
        self.set_vm_config(vmid, delete="hostpci0")
