"""CLI client for the live dashboard authoring API."""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlencode
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


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mosdat author", description="Agent client for the live authoring API")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Live dashboard URL")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("vms", help="List configured VMs and Proxmox power state")

    start = sub.add_parser("start", help="Create an authoring session")
    start.add_argument("--vm", required=True)
    start.add_argument("--model")
    start.add_argument("--verify-model")

    session = sub.add_parser("session", help="Show authoring session state")
    session.add_argument("--session", required=True)

    capture = sub.add_parser("capture", help="Capture the current VNC screen")
    capture.add_argument("--session", required=True)

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

    validate = sub.add_parser("validate", help="Validate current draft scenario")
    validate.add_argument("--session", required=True)
    validate.add_argument("--name", default="authored-scenario")

    export = sub.add_parser("export", help="Export current draft scenario YAML")
    export.add_argument("--session", required=True)
    export.add_argument("--name", default="authored-scenario")

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
    if args.command == "start":
        payload = {"vm": args.vm, "model": args.model, "verify_model": args.verify_model}
        return _print(_request_json(base_url, "POST", "/api/author/session", {k: v for k, v in payload.items() if v}))
    if args.command == "session":
        return _print(_request_json(base_url, "GET", "/api/author/session", query={"session": args.session}))
    if args.command == "capture":
        return _print(_request_json(base_url, "POST", "/api/author/capture", {"session_id": args.session}))
    if args.command == "localize":
        return _print(_request_json(base_url, "POST", "/api/author/vlm/localize", {"session_id": args.session, "prompt": args.prompt}))
    if args.command == "verify":
        return _print(_request_json(base_url, "POST", "/api/author/vlm/verify", {"session_id": args.session, "question": args.question}))
    if args.command == "action":
        payload = dict(args.json)
        payload.update({"session_id": args.session, "action": args.kind, "confirm": True})
        return _print(_request_json(base_url, "POST", "/api/author/action", payload))
    if args.command == "validate":
        return _print(_request_json(base_url, "POST", "/api/author/validate", {"session_id": args.session, "name": args.name}))
    if args.command == "export":
        return _print(_request_text(base_url, "/api/author/export", query={"session": args.session, "name": args.name}))
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
