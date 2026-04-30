import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests
import urllib3

from .config import ProxmoxConfig

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Retry policy constants
_RETRY_TOTAL = 3          # 1 initial + 2 retries
_RETRY_BACKOFFS = [1, 2]  # seconds between attempts
_RETRY_MAX_TOTAL_S = 10   # cap so polling budget stays intact
_RETRYABLE_STATUS = {500, 502, 503, 504, 596}

# 4xx codes — non-recoverable, never retry
# SSLError — config issue, not transient, never retry


def _log_path(run_id: Optional[str]) -> str:
    """Resolve the JSONL log path from the current run-id, or fall back to _orphan."""
    base = "results"
    folder = run_id if run_id else "_orphan"
    path = os.path.join(base, folder, "proxmox-api.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _append_log(run_id: Optional[str], entry: dict) -> None:
    """Append one JSON line to the run's proxmox-api.log. Never raises."""
    try:
        path = _log_path(run_id)
        with open(path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("proxmox log write failed: %s", exc)


class ProxmoxAPIError(Exception):
    pass


class ProxmoxAPI:
    def __init__(self, config: ProxmoxConfig, state=None):
        """
        Parameters
        ----------
        config:
            Proxmox connection settings.
        state:
            Optional State object. If provided and has a ``run_id`` attribute,
            API call logs are written to ``results/<run_id>/proxmox-api.log``.
            Otherwise logs go to ``results/_orphan/proxmox-api.log``.
        """
        self.config = config
        self._state = state
        self._ticket: Optional[str] = None
        self._csrf_token: Optional[str] = None
        self._session = requests.Session()
        self._session.verify = False

    def _run_id(self) -> Optional[str]:
        try:
            return self._state.run_id if self._state is not None else None
        except AttributeError:
            return None

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

    def _request(self, method: str, endpoint: str, idempotent: bool = False, **kwargs) -> dict:
        """Send an authenticated request to the Proxmox API.

        Parameters
        ----------
        method:
            HTTP verb (GET, POST, PUT, DELETE).
        endpoint:
            Path relative to ``config.base_url``.
        idempotent:
            When True, transient errors (5xx, ConnectionError, Timeout) trigger
            up to 2 retries with exponential back-off (1 s, 2 s).  Total time
            capped at ``_RETRY_MAX_TOTAL_S`` seconds so polling loops stay on
            budget.  4xx and SSLError are never retried regardless of this flag.
        """
        self._ensure_auth()

        url = f"{self.config.base_url}{endpoint}"
        headers = kwargs.pop("headers", {})
        headers["CSRFPreventionToken"] = self._csrf_token or ""
        cookies = {"PVEAuthCookie": self._ticket or ""}

        run_id = self._run_id()
        overall_start = time.monotonic()
        last_exc: Optional[Exception] = None
        last_status: Optional[int] = None

        max_attempts = _RETRY_TOTAL if idempotent else 1

        for attempt in range(1, max_attempts + 1):
            t0 = time.monotonic()
            error_msg: Optional[str] = None
            status: Optional[int] = None

            try:
                response = self._session.request(
                    method, url, headers=headers, cookies=cookies, **kwargs
                )
                status = response.status_code
                last_status = status

                entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "method": method,
                    "endpoint": endpoint,
                    "status": status,
                    "duration_ms": round((time.monotonic() - t0) * 1000),
                    "attempt": attempt,
                }

                if status >= 400:
                    error_msg = f"API error {status}: {response.text}"
                    entry["error"] = error_msg
                    _append_log(run_id, entry)

                    # 4xx — non-recoverable, never retry
                    if status < 500:
                        raise ProxmoxAPIError(error_msg)

                    # 5xx or Proxmox-specific 596
                    last_exc = ProxmoxAPIError(error_msg)
                    if not idempotent or attempt >= max_attempts:
                        raise last_exc
                    # check total time budget before sleeping
                    elapsed = time.monotonic() - overall_start
                    backoff = _RETRY_BACKOFFS[attempt - 1]
                    if elapsed + backoff >= _RETRY_MAX_TOTAL_S:
                        raise last_exc
                    time.sleep(backoff)
                    continue

                _append_log(run_id, entry)
                return response.json()

            except (requests.exceptions.SSLError,) as exc:
                # Config issue — never retry
                error_msg = str(exc)
                _append_log(run_id, {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "method": method,
                    "endpoint": endpoint,
                    "status": None,
                    "duration_ms": round((time.monotonic() - t0) * 1000),
                    "attempt": attempt,
                    "error": error_msg,
                })
                raise

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                error_msg = str(exc)
                _append_log(run_id, {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "method": method,
                    "endpoint": endpoint,
                    "status": None,
                    "duration_ms": round((time.monotonic() - t0) * 1000),
                    "attempt": attempt,
                    "error": error_msg,
                })
                last_exc = exc
                if not idempotent or attempt >= max_attempts:
                    raise
                elapsed = time.monotonic() - overall_start
                backoff = _RETRY_BACKOFFS[attempt - 1]
                if elapsed + backoff >= _RETRY_MAX_TOTAL_S:
                    raise
                time.sleep(backoff)

            except ProxmoxAPIError:
                # Already logged and raised above
                raise

        # Exhausted retries
        if last_exc is not None:
            raise last_exc
        raise ProxmoxAPIError("_request exhausted retries with no result")

    def get(self, endpoint: str) -> dict:
        return self._request("GET", endpoint, idempotent=True)

    def post(self, endpoint: str, data: Optional[dict] = None) -> dict:
        return self._request("POST", endpoint, idempotent=False, data=data or {})

    def put(self, endpoint: str, data: Optional[dict] = None) -> dict:
        return self._request("PUT", endpoint, idempotent=False, data=data or {})

    def delete(self, endpoint: str) -> dict:
        return self._request("DELETE", endpoint, idempotent=False)

    def get_vm_status(self, vmid: int) -> str:
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/status/current"
        data = self.get(endpoint).get("data", {})
        return data.get("status", "unknown")

    def start_vm(self, vmid: int) -> None:
        # State-transition POST — NOT idempotent; retrying could double-start
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/status/start"
        self.post(endpoint)

    def stop_vm(self, vmid: int) -> None:
        # State-transition POST — NOT idempotent
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/status/stop"
        self.post(endpoint)

    def shutdown_vm(self, vmid: int) -> None:
        # State-transition POST — NOT idempotent
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/status/shutdown"
        self.post(endpoint)

    def get_vm_config(self, vmid: int) -> dict:
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/config"
        return self.get(endpoint).get("data", {})

    def set_vm_config(self, vmid: int, **config) -> None:
        # PUT with side-effects — NOT idempotent (e.g. delete= operations)
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

    def vncproxy(self, vmid: int) -> dict:
        """Open a WebSocket VNC proxy for a QEMU VM.

        Calls POST /nodes/{node}/qemu/{vmid}/vncproxy?websocket=1 and returns
        the response data: {"ticket": str, "port": int, "user": str, "upid": str}.

        The returned ticket is the vncticket (used as the VNC password during
        RFB authentication *and* as a query parameter on the vncwebsocket URL).

        Note: This is a non-idempotent POST — no retry on failure.
        """
        self._ensure_auth()
        run_id = self._run_id()
        endpoint = f"/nodes/{self.config.node}/qemu/{vmid}/vncproxy"
        url = f"{self.config.base_url}{endpoint}"

        t0 = time.monotonic()
        error_msg: Optional[str] = None
        status: Optional[int] = None

        try:
            response = self._session.post(
                url,
                params={"websocket": 1},
                headers={"CSRFPreventionToken": self._csrf_token or ""},
                cookies={"PVEAuthCookie": self._ticket or ""},
            )
            status = response.status_code
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": "POST",
                "endpoint": endpoint,
                "status": status,
                "duration_ms": round((time.monotonic() - t0) * 1000),
                "attempt": 1,
            }
            if status >= 400:
                error_msg = f"vncproxy API error {status}: {response.text[:200]}"
                entry["error"] = error_msg
                _append_log(run_id, entry)
                raise ProxmoxAPIError(error_msg)
            _append_log(run_id, entry)
            return response.json().get("data", {})

        except ProxmoxAPIError:
            raise
        except Exception as exc:
            error_msg = str(exc)
            _append_log(run_id, {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": "POST",
                "endpoint": endpoint,
                "status": None,
                "duration_ms": round((time.monotonic() - t0) * 1000),
                "attempt": 1,
                "error": error_msg,
            })
            raise

    @property
    def auth_ticket(self) -> str:
        """The PVEAuthCookie value — required for the vncwebsocket handshake."""
        self._ensure_auth()
        return self._ticket or ""
