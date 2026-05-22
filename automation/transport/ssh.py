import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SSHResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


class SSHClient:
    def __init__(
        self,
        host: str,
        user: str,
        connect_timeout: int = 10,
        *,
        persistent: bool = False,
        control_persist: int = 60,
    ) -> None:
        self.host = host
        self.user = user
        self.connect_timeout = connect_timeout
        self._persistent = persistent
        self._control_persist = control_persist
        self._base_opts = [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", f"ConnectTimeout={connect_timeout}",
        ]
        # Stage 3c: ControlMaster socket for the AT-SPI hot path.
        # Hash the host/user so the socket path stays well below Linux's
        # 108-byte sun_path limit, regardless of $HOME length.
        if persistent:
            cache = Path.home() / ".cache" / "mosdat" / "ssh-cm"
            cache.mkdir(parents=True, exist_ok=True)
            key = hashlib.blake2b(
                f"{user}@{host}".encode(), digest_size=8
            ).hexdigest()
            self._control_path: Optional[str] = str(cache / key)
        else:
            self._control_path = None

    # ------------------------------------------------------------------ argv
    def _control_opts(self) -> list[str]:
        if not self._control_path:
            return []
        return [
            "-o", f"ControlPath={self._control_path}",
            "-o", "ControlMaster=auto",
            "-o", f"ControlPersist={self._control_persist}",
        ]

    def _ssh_args(self) -> list[str]:
        """Build the ssh argv prefix (no remote command). Used by tests."""
        return (
            ["ssh"]
            + self._base_opts
            + self._control_opts()
            + [f"{self.user}@{self.host}"]
        )

    def _scp_args(self, *, recursive: bool = False) -> list[str]:
        """Build the scp argv prefix (no source/dest). Used by tests."""
        args = ["scp"]
        if recursive:
            args.append("-r")
        return args + self._base_opts + self._control_opts()

    # ------------------------------------------------------------------- ops
    def run(
        self,
        command: str,
        timeout: Optional[int] = None,
        capture_output: bool = True,
        stream_output: bool = False,
    ) -> SSHResult:
        ssh_cmd = self._ssh_args() + [command]

        if stream_output:
            process = subprocess.Popen(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            output_lines: list[str] = []
            if process.stdout:
                for line in process.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    output_lines.append(line)
            process.wait()
            return SSHResult(
                returncode=process.returncode or 0,
                stdout="".join(output_lines),
                stderr="",
            )

        for attempt in range(2):
            try:
                result = subprocess.run(
                    ssh_cmd,
                    capture_output=capture_output,
                    text=True,
                    timeout=timeout,
                )
                if result.returncode == 255 and attempt == 0:
                    # rc 255 = SSH connection failure (not a remote command error).
                    # Retry once after a short delay.
                    import time as _time
                    _time.sleep(5)
                    continue
                return SSHResult(
                    returncode=result.returncode,
                    stdout=result.stdout if capture_output else "",
                    stderr=result.stderr if capture_output else "",
                )
            except subprocess.TimeoutExpired:
                return SSHResult(returncode=124, stdout="", stderr="SSH command timed out")
        # Should not be reached, but satisfies the type checker.
        return SSHResult(returncode=255, stdout="", stderr="SSH connection failed after retry")

    def is_reachable(self) -> bool:
        result = self.run("echo OK", timeout=5)
        return result.success and "OK" in result.stdout

    def scp_to(self, local_path: Path, remote_path: str) -> SSHResult:
        scp_cmd = self._scp_args() + [
            str(local_path),
            f"{self.user}@{self.host}:{remote_path}",
        ]
        try:
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=300)
            return SSHResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired:
            return SSHResult(returncode=124, stdout="", stderr="SCP timed out")

    def scp_from(self, remote_path: str, local_path: Path) -> SSHResult:
        scp_cmd = self._scp_args() + [
            f"{self.user}@{self.host}:{remote_path}",
            str(local_path),
        ]
        try:
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            return SSHResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired:
            return SSHResult(returncode=124, stdout="", stderr="SCP timed out")

    def scp_dir_to(self, local_path: Path, remote_path: str) -> SSHResult:
        scp_cmd = self._scp_args(recursive=True) + [
            str(local_path),
            f"{self.user}@{self.host}:{remote_path}",
        ]
        try:
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=300)
            return SSHResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired:
            return SSHResult(returncode=124, stdout="", stderr="SCP timed out")

    # -------------------------------------------------------------- teardown
    def close_persistent(self) -> None:
        """Tell the ControlMaster process to exit.

        No-op if this client is not persistent or if no master is running.
        Safe to call multiple times.
        """
        if not self._control_path:
            return
        try:
            subprocess.run(
                [
                    "ssh",
                    "-O", "exit",
                    "-S", self._control_path,
                    f"{self.user}@{self.host}",
                ],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            # Master may already be gone (ControlPersist expired) — fine.
            pass

    # --------------------------------------------------------- context mgmt
    def __enter__(self) -> "SSHClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close_persistent()


def wait_for_ssh(host: str, user: str, timeout: int = 240, interval: int = 5) -> bool:
    import time
    client = SSHClient(host, user)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.is_reachable():
            return True
        time.sleep(interval)
    return False
