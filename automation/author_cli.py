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
    return _print(_request_action(base_url, session_id, action, **fields))


def _request_action(base_url: str, session_id: str, action: str, **fields: object) -> dict:
    return _request_json(base_url, "POST", "/api/author/action", _action_payload(session_id, action, **fields))

def _prompt_action(base_url: str, session_id: str, prompt: str, action: str, **fields: object) -> int:
    return _print(_prompt_action_result(base_url, session_id, prompt, action, **fields))

def _prompt_action_result(base_url: str, session_id: str, prompt: str, action: str, **fields: object) -> dict:
    target = _request_json(base_url, "POST", "/api/author/vlm/localize", {"session_id": session_id, "prompt": prompt})
    if target.get("error") or target.get("ok") is False:
        return target
    if not all(key in target for key in ("x", "y")):
        return {"ok": False, "error": "localize response did not include x/y", "localize": target}
    return _request_action(base_url, session_id, action, x=int(target["x"]), y=int(target["y"]), prompt=prompt, **fields)

def _export_with_base(
    base_url: str,
    session_id: str,
    session_name: str,
    base_path: Path,
    insert_at_step: int | None,
    new_name: str | None,
    dry_run: bool,
    output: str | None,
) -> int:
    """I12: export recorded steps as a unified diff against an existing base scenario.

    Parameters
    ----------
    base_url:        live dashboard URL.
    session_id:      author session id.
    session_name:    scenario name for the session export YAML.
    base_path:       path to the existing base scenario YAML on disk.
    insert_at_step:  1-indexed step before which to insert; None = append at end.
    new_name:        if given, write a complete new scenario file instead of diff.
    dry_run:         validate but don't write / emit.
    output:          write diff/file to this path instead of stdout.
    """
    import copy
    import difflib
    import sys

    import yaml

    # --- 1. Fetch recorded steps from the live session ----------------------
    session_data = _request_text(base_url, "/api/author/export", query={"session": session_id, "name": session_name})
    if session_data.get("error"):
        return _print(session_data)
    session_yaml_text: str = str(session_data.get("yaml", ""))
    if not session_yaml_text.strip():
        return _print({"ok": False, "error": "author session returned empty YAML"})
    session_doc: dict = yaml.safe_load(session_yaml_text)
    recorded_steps: list[dict] = list(session_doc.get("steps", []))
    if not recorded_steps:
        return _print({"ok": False, "error": "author session has no recorded steps"})

    # --- 2. Load base scenario from disk ------------------------------------
    if not base_path.exists():
        return _print({"ok": False, "error": f"base scenario not found: {base_path}"})
    with open(base_path, encoding="utf-8") as fh:
        base_text = fh.read()
    base_doc: dict = yaml.safe_load(base_text)
    base_steps: list[dict] = list(base_doc.get("steps", []))

    # --- 3. Build modified scenario in memory (deep-copy base + insertions) -
    modified: dict = copy.deepcopy(base_doc)
    if insert_at_step is None:
        # append at end
        modified["steps"] = base_steps + recorded_steps
    else:
        # insert_at_step is 1-indexed: insert BEFORE that step
        idx = insert_at_step - 1
        if idx < 0 or idx > len(base_steps):
            return _print({
                "ok": False,
                "error": (
                    f"--insert-at-step {insert_at_step} out of range "
                    f"(base has {len(base_steps)} step(s); valid range 1-{len(base_steps) + 1})"
                ),
            })
        modified["steps"] = base_steps[:idx] + recorded_steps + base_steps[idx:]

    if new_name:
        modified["name"] = new_name

    # --- 4. Validate via ScenarioModel before emitting ----------------------
    try:
        from automation.scenario import ScenarioModel
        ScenarioModel.model_validate(modified)
    except Exception as exc:  # noqa: BLE001
        return _print({"ok": False, "error": f"modified scenario failed validation: {exc}"})

    # --- 5. dry-run: stop here ---------------------------------------------
    if dry_run:
        step_count = len(modified.get("steps", []))
        return _print({"ok": True, "dry_run": True, "step_count": step_count, "base": str(base_path)})

    # --- 6a. --name mode: write complete new scenario file -----------------
    if new_name:
        dest = Path("shared/scenarios/functional") / f"{new_name}.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        yaml_out = yaml.safe_dump(modified, sort_keys=False, allow_unicode=True)
        if output:
            dest = Path(output)
            dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(yaml_out, encoding="utf-8")
        return _print({"ok": True, "output": str(dest), "step_count": len(modified["steps"])})

    # --- 6b. diff mode: emit unified diff to stdout or file ----------------
    modified_text = yaml.safe_dump(modified, sort_keys=False, allow_unicode=True)
    base_lines = base_text.splitlines(keepends=True)
    modified_lines = modified_text.splitlines(keepends=True)
    # Use "a/<base>" / "b/<base>" as conventional patch -p1 headers
    base_label = f"a/{base_path}"
    modified_label = f"b/{base_path}"
    diff_lines = list(difflib.unified_diff(base_lines, modified_lines, fromfile=base_label, tofile=modified_label))
    diff_text = "".join(diff_lines)
    if not diff_text:
        sys.stderr.write("[author export] No differences between base and modified scenario.\n")
        return 0
    if output:
        Path(output).write_text(diff_text, encoding="utf-8")
        return _print({"ok": True, "output": output, "hunks": diff_text.count("\n@@")})
    sys.stdout.write(diff_text)
    return 0


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
    vlm = vms_payload.get("vlm") if isinstance(vms_payload.get("vlm"), dict) else {}
    verify_configured = bool(vlm.get("verify_model_configured"))
    checks.append({
        "name": "verify_model_configured",
        "ok": verify_configured,
        "required": False,
        "detail": None if verify_configured else "set VLM_VERIFY_MODEL or [vlm].verify_model for yes/no checks",
    })
    ok = all(check["ok"] for check in checks if check.get("required", True))
    return _print({"ok": ok, "checks": checks})


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

    describe = sub.add_parser("describe", help="Describe a clicked screenshot point as a localize prompt")
    describe.add_argument("--session", required=True)
    describe.add_argument("--x", required=True, type=int)
    describe.add_argument("--y", required=True, type=int)

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

    prompt_hover = sub.add_parser("prompt-hover", help="Localize a prompt and hover its center")
    prompt_hover.add_argument("--session", required=True)
    prompt_hover.add_argument("--prompt", required=True)

    prompt_type = sub.add_parser("prompt-type", help="Localize a prompt, click it, then type text")
    prompt_type.add_argument("--session", required=True)
    prompt_type.add_argument("--prompt", required=True)
    prompt_type.add_argument("--text", required=True)
    prompt_type.add_argument("--button", choices=["left", "right"], default="left")
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
    export.add_argument("--output", help="Write YAML/diff to path instead of stdout")
    # I12: diff-against-base flags
    export.add_argument("--base", metavar="SCENARIO_YAML", help="I12: base scenario to diff against")
    export.add_argument(
        "--insert-at-step",
        type=int,
        default=None,
        metavar="N",
        help="I12: insert recorded steps BEFORE step N (1-indexed); default: append at end",
    )
    export.add_argument(
        "--dry-run",
        action="store_true",
        help="I12: validate the modified scenario but don't write or emit",
    )

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
    if args.command == "describe":
        return _print(_request_json(base_url, "POST", "/api/author/vlm/describe", {"session_id": args.session, "x": args.x, "y": args.y}))
    if args.command == "verify":
        return _print(_request_json(base_url, "POST", "/api/author/vlm/verify", {"session_id": args.session, "question": args.question}))
    if args.command == "action":
        payload = dict(args.json)
        payload.update({"session_id": args.session, "action": args.kind, "confirm": True})
        return _print(_request_json(base_url, "POST", "/api/author/action", payload))
    if args.command == "click":
        return _post_action(base_url, args.session, "click", x=args.x, y=args.y, button=args.button, prompt=args.prompt)
    if args.command == "prompt-click":
        return _prompt_action(base_url, args.session, args.prompt, "click", button=args.button)
    if args.command == "prompt-hover":
        return _prompt_action(base_url, args.session, args.prompt, "hover")
    if args.command == "prompt-type":
        click_result = _prompt_action_result(base_url, args.session, args.prompt, "click", button=args.button)
        if click_result.get("error") or click_result.get("ok") is False:
            return _print(click_result)
        return _post_action(base_url, args.session, "type", text=args.text)
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
        if args.base:
            return _export_with_base(
                base_url=base_url,
                session_id=args.session,
                session_name=args.name,
                base_path=Path(args.base),
                insert_at_step=args.insert_at_step,
                new_name=args.name if args.name != "authored-scenario" else None,
                dry_run=args.dry_run,
                output=args.output,
            )
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
