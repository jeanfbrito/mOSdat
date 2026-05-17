"""cmd_record — extracted from automation/main.py."""
import sys
from pathlib import Path


def cmd_record(args) -> int:
    """C3: Interactive scenario authoring — open VNC viewer, capture clicks, generate YAML."""
    from automation.config import load_config
    config = load_config(args.config)

    vm_names = [v.strip() for v in args.vms.split(",")]
    if len(vm_names) != 1:
        print("[mOSdat] ERROR: --record requires exactly one VM (--vms <single-vm>)")
        return 1
    vm_name = vm_names[0]
    if vm_name not in config.vm_by_name:
        print(f"[mOSdat] ERROR: Unknown VM '{vm_name}'. Available: {', '.join(config.vm_by_name.keys())}")
        return 1
    vm = config.vm_by_name[vm_name]

    if not config.proxmox.password:
        print("[mOSdat] ERROR: Proxmox password required for VNC. Set MOSDAT_PROXMOX_PASSWORD or [proxmox].password.")
        return 1

    from automation.vlm.client import VLMClient
    from automation.transport.vnc import VncClient
    from automation.proxmox.api import ProxmoxAPI

    vlm = VLMClient(
        base_url=config.vlm.base_url,
        model=args.model or config.vlm.model,
        verify_model=args.verify_model or config.vlm.verify_model or None,
        api_key=config.vlm.api_key,
        max_tokens_floor=config.vlm.max_tokens_floor,
    )
    proxmox = ProxmoxAPI(config.proxmox)

    output_path = Path(args.output) if args.output else None

    # QApplication must be created here, not at import time (keeps headless imports safe).
    from PyQt6.QtWidgets import QApplication
    from automation.record import RecorderWindow

    app = QApplication(sys.argv)

    with VncClient(proxmox, vmid=vm.vmid) as vnc:
        window = RecorderWindow(vnc, vlm, output_path=output_path)
        window.resize(1200, 700)
        window.show()
        return app.exec()
