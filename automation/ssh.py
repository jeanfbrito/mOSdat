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
    def __init__(self, host: str, user: str, connect_timeout: int = 10):
        self.host = host
        self.user = user
        self.connect_timeout = connect_timeout
        self._base_opts = [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", f"ConnectTimeout={connect_timeout}",
        ]

    def run(
        self,
        command: str,
        timeout: Optional[int] = None,
        capture_output: bool = True,
        stream_output: bool = False,
    ) -> SSHResult:
        ssh_cmd = ["ssh"] + self._base_opts + [f"{self.user}@{self.host}", command]
        
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
        
        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
            )
            return SSHResult(
                returncode=result.returncode,
                stdout=result.stdout if capture_output else "",
                stderr=result.stderr if capture_output else "",
            )
        except subprocess.TimeoutExpired:
            return SSHResult(returncode=124, stdout="", stderr="SSH command timed out")

    def is_reachable(self) -> bool:
        result = self.run("echo OK", timeout=5)
        return result.success and "OK" in result.stdout

    def scp_to(self, local_path: Path, remote_path: str) -> SSHResult:
        scp_cmd = ["scp"] + self._base_opts + [str(local_path), f"{self.user}@{self.host}:{remote_path}"]
        try:
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=300)
            return SSHResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired:
            return SSHResult(returncode=124, stdout="", stderr="SCP timed out")

    def scp_from(self, remote_path: str, local_path: Path) -> SSHResult:
        scp_cmd = ["scp"] + self._base_opts + [f"{self.user}@{self.host}:{remote_path}", str(local_path)]
        try:
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            return SSHResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired:
            return SSHResult(returncode=124, stdout="", stderr="SCP timed out")

    def scp_dir_to(self, local_path: Path, remote_path: str) -> SSHResult:
        scp_cmd = ["scp", "-r"] + self._base_opts + [str(local_path), f"{self.user}@{self.host}:{remote_path}"]
        try:
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=300)
            return SSHResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired:
            return SSHResult(returncode=124, stdout="", stderr="SCP timed out")


def wait_for_ssh(host: str, user: str, timeout: int = 120, interval: int = 5) -> bool:
    import time
    client = SSHClient(host, user)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.is_reachable():
            return True
        time.sleep(interval)
    return False
