"""CLI client for the live dashboard authoring API."""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from pathlib import Path
from urllib.request import Request, urlopen

def _json_object_arg(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("expected a JSON object")
    return parsed


def _json_array_arg(value: str) -> list:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, list):
        raise argparse.ArgumentTypeError("expected a JSON array")
    return parsed


def _url(base_url: str, path: str, query: dict | None = None) -> str:
    url = base_url.rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    return url


def _request_json(base_url: str, method: str, path: str, payload: dict | None = None, query: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(_url(base_url, path, query), data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - user-provided local/LAN dashboard URL
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"error": body or exc.reason}
        payload.setdefault("status", exc.code)
        return payload


def _request_text(base_url: str, path: str, query: dict | None = None) -> dict:
    request = Request(_url(base_url, path, query), method="GET")
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - user-provided local/LAN dashboard URL
            return {"yaml": response.read().decode("utf-8")}
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"error": body or exc.reason}
        payload.setdefault("status", exc.code)
        return payload


def _print(data: dict) -> int:
    print(json.dumps(data, separators=(",", ":")))
    return 1 if data.get("error") or data.get("ok") is False else 0


def _action_payload(session_id: str, action: str, **fields: object) -> dict:
    payload = {k: v for k, v in fields.items() if v is not None}
    payload.update({"session_id": session_id, "action": action, "confirm": True})
    return payload


def _post_action(base_url: str, session_id: str, action: str, **fields: object) -> int:
    return _print(_request_json(base_url, "POST", "/api/author/action", _action_payload(session_id, action, **fields)))



def _prompt_click(base_url: str, session_id: str, prompt: str, button: str) -> int:
    target = _request_json(base_url, "POST", "/api/author/vlm/localize", {"session_id": session_id, "prompt": prompt})
    if target.get("error") or target.get("ok") is False:
        return _print(target)
    if not all(key in target for key in ("x", "y")):
        return _print({"ok": False, "error": "localize response did not include x/y", "localize": target})
    return _post_action(base_url, session_id, "click", x=int(target["x"]), y=int(target["y"]), button=button, prompt=prompt)

def _write_yaml_output(data: dict, output: str | None) -> int:
    if data.get("error"):
        return _print(data)
    yaml_text = str(data.get("yaml", ""))
    if output is None or output == "-":
        return _print(data)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_text, encoding="utf-8")
    return _print({"ok": True, "output": str(path), "bytes": len(yaml_text.encode("utf-8"))})


def _write_capture_output(data: dict, output: str | None) -> int:
    if data.get("error") or output is None:
        return _print(data)
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    image_b64 = result.get("image") if isinstance(result, dict) else None
    if not image_b64:
        return _print({"ok": False, "error": "capture response did not include image"})
    import base64

    raw = base64.b64decode(str(image_b64))
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    meta = {k: v for k, v in result.items() if k != "image"}
    return _print({"ok": True, "output": str(path), "bytes": len(raw), **meta})


def _doctor(base_url: str) -> int:
    checks = []
    vms_payload = _request_json(base_url, "GET", "/api/author/vms")
    checks.append({"name": "dashboard_reachable", "ok": "error" not in vms_payload, "detail": vms_payload.get("error")})
    configured = bool(vms_payload.get("configured"))
    checks.append({"name": "authoring_configured", "ok": configured, "detail": None if configured else "start live with --config"})
    vms = vms_payload.get("vms") if isinstance(vms_payload.get("vms"), list) else []
    checks.append({"name": "vms_listed", "ok": bool(vms), "detail": f"{len(vms)} VM(s)"})
    running = [vm.get("name") for vm in vms if vm.get("running")]
    checks.append({"name": "running_vm_available", "ok": bool(running), "detail": running[0] if running else "no running VMs"})
    return _print({"ok": all(check["ok"] for check in checks), "checks": checks})


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mosdat author", description="Agent client for the live authoring API")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Live dashboard URL")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("vms", help="List configured VMs and Proxmox power state")
    sub.add_parser("doctor", help="Check author dashboard readiness")

    start = sub.add_parser("start", help="Create an authoring session")
    start.add_argument("--vm", required=True)
    start.add_argument("--model")
    start.add_argument("--verify-model")

    session = sub.add_parser("session", help="Show authoring session state")
    session.add_argument("--session", required=True)

    capture = sub.add_parser("capture", help="Capture the current VNC screen")
    capture.add_argument("--session", required=True)
    capture.add_argument("--output", help="Write captured BMP image bytes to path")

    localize = sub.add_parser("localize", help="Find a prompt on the current screen")
    localize.add_argument("--session", required=True)
    localize.add_argument("--prompt", required=True)

    verify = sub.add_parser("verify", help="Ask the VLM a yes/no screen question")
    verify.add_argument("--session", required=True)
    verify.add_argument("--question", required=True)

    action = sub.add_parser("action", help="Run a confirmed authoring action")
    action.add_argument("--session", required=True)
    action.add_argument("--kind", required=True, choices=["hover", "click", "type", "key", "shell", "wait", "launch"])
    action.add_argument("--json", default={}, type=_json_object_arg, help="Action payload JSON object")

    click = sub.add_parser("click", help="Run a confirmed click action")
    click.add_argument("--session", required=True)
    click.add_argument("--x", required=True, type=int)
    click.add_argument("--y", required=True, type=int)
    click.add_argument("--button", choices=["left", "right"], default="left")
    click.add_argument("--prompt")


    prompt_click = sub.add_parser("prompt-click", help="Localize a prompt and click its center")
    prompt_click.add_argument("--session", required=True)
    prompt_click.add_argument("--prompt", required=True)
    prompt_click.add_argument("--button", choices=["left", "right"], default="left")
    hover = sub.add_parser("hover", help="Run a confirmed hover action")
    hover.add_argument("--session", required=True)
    hover.add_argument("--x", required=True, type=int)
    hover.add_argument("--y", required=True, type=int)
    hover.add_argument("--prompt")

    type_cmd = sub.add_parser("type", help="Run a confirmed text input action")
    type_cmd.add_argument("--session", required=True)
    type_cmd.add_argument("--text", required=True)

    key = sub.add_parser("key", help="Run a confirmed key press action")
    key.add_argument("--session", required=True)
    key.add_argument("--key", required=True)

    wait = sub.add_parser("wait", help="Append and run a bounded wait action")
    wait.add_argument("--session", required=True)
    wait.add_argument("--seconds", type=int, default=1)

    shell = sub.add_parser("shell", help="Run a confirmed shell action")
    shell.add_argument("--session", required=True)
    shell.add_argument("--cmd", required=True)

    launch = sub.add_parser("launch", help="Run a confirmed launch action")
    launch.add_argument("--session", required=True)
    launch.add_argument("--cmd", required=True)
    launch.add_argument("--wait", type=int, default=0)

    validate = sub.add_parser("validate", help="Validate current draft scenario")
    validate.add_argument("--session", required=True)
    validate.add_argument("--name", default="authored-scenario")

    export = sub.add_parser("export", help="Export current draft scenario YAML")
    export.add_argument("--session", required=True)
    export.add_argument("--name", default="authored-scenario")
    export.add_argument("--output", help="Write YAML to path instead of embedding it in JSON; use - for stdout JSON")

    step = sub.add_parser("step", help="Append or replace draft scenario steps")
    step.add_argument("--session", required=True)
    step_group = step.add_mutually_exclusive_group(required=True)
    step_group.add_argument("--json", dest="step_json", type=_json_object_arg, help="Step JSON object to append")
    step_group.add_argument("--steps-json", type=_json_array_arg, help="Full steps JSON array to replace draft")

    close = sub.add_parser("close", help="Close an authoring session")
    close.add_argument("--session", required=True)

    args = parser.parse_args(argv)
    base_url = args.url

    if args.command == "vms":
        return _print(_request_json(base_url, "GET", "/api/author/vms"))
    if args.command == "doctor":
        return _doctor(base_url)
    if args.command == "start":
        payload = {"vm": args.vm, "model": args.model, "verify_model": args.verify_model}
        return _print(_request_json(base_url, "POST", "/api/author/session", {k: v for k, v in payload.items() if v}))
    if args.command == "session":
        return _print(_request_json(base_url, "GET", "/api/author/session", query={"session": args.session}))
    if args.command == "capture":
        return _write_capture_output(_request_json(base_url, "POST", "/api/author/capture", {"session_id": args.session}), args.output)
    if args.command == "localize":
        return _print(_request_json(base_url, "POST", "/api/author/vlm/localize", {"session_id": args.session, "prompt": args.prompt}))
    if args.command == "verify":
        return _print(_request_json(base_url, "POST", "/api/author/vlm/verify", {"session_id": args.session, "question": args.question}))
    if args.command == "action":
        payload = dict(args.json)
        payload.update({"session_id": args.session, "action": args.kind, "confirm": True})
        return _print(_request_json(base_url, "POST", "/api/author/action", payload))
    if args.command == "click":
        return _post_action(base_url, args.session, "click", x=args.x, y=args.y, button=args.button, prompt=args.prompt)
    if args.command == "prompt-click":
        return _prompt_click(base_url, args.session, args.prompt, args.button)
    if args.command == "hover":
        return _post_action(base_url, args.session, "hover", x=args.x, y=args.y, prompt=args.prompt)
    if args.command == "type":
        return _post_action(base_url, args.session, "type", text=args.text)
    if args.command == "key":
        return _post_action(base_url, args.session, "key", key=args.key)
    if args.command == "wait":
        return _post_action(base_url, args.session, "wait", seconds=args.seconds)
    if args.command == "shell":
        return _post_action(base_url, args.session, "shell", cmd=args.cmd)
    if args.command == "launch":
        return _post_action(base_url, args.session, "launch", cmd=args.cmd, wait=args.wait)
    if args.command == "validate":
        return _print(_request_json(base_url, "POST", "/api/author/validate", {"session_id": args.session, "name": args.name}))
    if args.command == "export":
        return _write_yaml_output(_request_text(base_url, "/api/author/export", query={"session": args.session, "name": args.name}), args.output)
    if args.command == "step":
        payload = {"session_id": args.session}
        if args.steps_json is not None:
            payload["steps"] = args.steps_json
        else:
            payload["step"] = args.step_json
        return _print(_request_json(base_url, "POST", "/api/author/step", payload))
    if args.command == "close":
        return _print(_request_json(base_url, "POST", "/api/author/close", {"session_id": args.session}))

    parser.print_help()
    return 1
