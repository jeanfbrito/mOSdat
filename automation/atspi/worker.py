#!/usr/bin/env python3
"""AT-SPI worker — runs ON the target VM, NOT on the host.

Invocation (one-shot, JSON-RPC-over-stdio style but argv-based because the
host SSH client is one-shot — see `automation/transport/ssh.py`):

    python3 worker.py '<json-op-batch>'
    # or, if no arg, reads the batch from stdin

Op-batch shape (input):
    {
      "ops": [
        {"id": "1", "op": "find",       "role": "push button",
         "name": "Connect", "name_substr": false, "app_filter": "rocket"},
        {"id": "2", "op": "do_action",  "from_id": "1", "action_idx": 0},
        {"id": "3", "op": "verify",     "role": "frame", "name_substr": "rocket.chat"},
        {"id": "4", "op": "tree_dump",  "max_depth": 30, "app_filter": "rocket"},
        {"id": "5", "op": "wait_for",
         "any": [{"role": "push button", "name": "Connect"}],
         "timeout_ms": 15000, "interval_ms": 500}
      ],
      "init": {"force_atspi_init": true, "settle_ms": 0}
    }

Result shape (stdout, pretty-printed JSON):
    {
      "ok": true,                 # false iff any op failed
      "results": {"1": {...}, ...},
      "errors": []                # batch-level errors (e.g. Atspi.init failure)
    }

Exit code: 0 iff every op `ok`. 1 if any op failed or a batch-level error fired.

Single-shot, stateless: every invocation re-walks the tree from the desktop
root. No persistent daemon — the SSH transport tears down each call.

Live-verified fixes from POC (see findings.md "AT-SPI POC LIVE RESULT"):
  * `Atspi.init()` called BEFORE `Atspi.get_desktop(0)`.
  * Role read via `node.get_role_name()` (not `Atspi.role_get_name(role)`).
  * App-name filter is case-insensitive.
  * Frame hashing uses stdlib `hashlib.blake2b(digest_size=8)` (no xxhash).
  * `gi.require_version("Atspi", "2.0")` before the import to silence warning.

Dependencies: stdlib + `gi.repository.Atspi` + PIL. No third-party deps.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Optional


# --------------------------- AT-SPI lazy bootstrap ---------------------------

_ATSPI_READY = False
_DESKTOP = None  # cached after first init


def _ensure_atspi(force_init: bool = True) -> tuple[Any, Optional[str]]:
    """Import gi, init AT-SPI, return (desktop, error_or_None).

    Idempotent within a single worker process: re-uses the cached desktop
    after first successful init. The host invokes the worker fresh each
    SSH call, so cross-call caching is not the goal here — the cache only
    spans the multiple ops within one batch.
    """
    global _ATSPI_READY, _DESKTOP
    if _ATSPI_READY and _DESKTOP is not None and not force_init:
        return _DESKTOP, None
    try:
        import gi  # type: ignore

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # type: ignore
    except Exception as e:
        return None, f"gi/Atspi import failed: {e!r}"

    try:
        Atspi.init()
    except Exception as e:
        # Atspi.init may legitimately return non-zero on already-initialized;
        # treat as soft warning, continue to get_desktop.
        # If get_desktop also fails, that real error wins.
        _last_init_err = repr(e)
    else:
        _last_init_err = None

    try:
        desktop = Atspi.get_desktop(0)
    except Exception as e:
        return None, f"Atspi.get_desktop(0) failed: {e!r} (init_err={_last_init_err})"

    _DESKTOP = desktop
    _ATSPI_READY = True
    return desktop, None


# --------------------------- tree walking ------------------------------------


def _find_app(desktop: Any, app_filter: str) -> Optional[Any]:
    """Case-insensitive substring match on top-level app names."""
    needle = (app_filter or "").lower()
    try:
        n = desktop.get_child_count()
    except Exception:
        return None
    for i in range(n):
        try:
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            name = app.get_name() or ""
            if not needle or needle in name.lower():
                return app
        except Exception:
            continue
    return None


def _node_meta(node: Any) -> dict[str, Any]:
    """Extract role/name/action metadata for one node. Never raises."""
    try:
        role = node.get_role_name()
    except Exception:
        role = "<unknown>"
    try:
        name = node.get_name() or ""
    except Exception:
        name = ""
    n_actions = 0
    action_names: list[str] = []
    try:
        action_iface = node.get_action_iface()
        if action_iface is not None:
            n_actions = node.get_n_actions()
            for ai in range(n_actions):
                try:
                    action_names.append(node.get_action_name(ai))
                except Exception:
                    action_names.append("<?>")
    except Exception:
        pass
    try:
        child_count = node.get_child_count()
    except Exception:
        child_count = 0
    return {
        "role": role,
        "name": name,
        "n_actions": n_actions,
        "action_names": action_names,
        "child_count": child_count,
    }


def _walk(node: Any, path: str, depth: int, max_depth: int,
          out: list[dict]) -> None:
    if depth > max_depth:
        return
    meta = _node_meta(node)
    meta["path"] = path
    meta["depth"] = depth
    out.append(meta)
    for i in range(meta["child_count"]):
        try:
            child = node.get_child_at_index(i)
        except Exception:
            continue
        if child is None:
            continue
        _walk(child, f"{path}/{i}", depth + 1, max_depth, out)


def _get_node_by_path(root: Any, path: str) -> Optional[Any]:
    """Resolve a /a/b/c path (relative to `root`) to an AT-SPI node."""
    if path in ("", "/"):
        return root
    parts = [p for p in path.strip("/").split("/") if p != ""]
    node = root
    for raw in parts:
        try:
            idx = int(raw)
        except ValueError:
            return None
        try:
            node = node.get_child_at_index(idx)
        except Exception:
            return None
        if node is None:
            return None
    return node


def _match(meta: dict, role: Optional[str], name: Optional[str],
           name_substr: bool) -> bool:
    if role is not None and meta.get("role") != role:
        return False
    if name is None:
        return True
    n = meta.get("name", "") or ""
    if name_substr:
        return name in n
    return n == name


def _find_first(desktop: Any, role: Optional[str], name: Optional[str],
                name_substr: bool, app_filter: str,
                max_depth: int = 50) -> Optional[dict]:
    """Walk the matching app's tree, return first metadata dict that matches."""
    app = _find_app(desktop, app_filter)
    if app is None:
        return None
    nodes: list[dict] = []
    _walk(app, "", 0, max_depth, nodes)
    for meta in nodes:
        if _match(meta, role, name, name_substr):
            return meta
    return None


# --------------------------- op handlers -------------------------------------


def _op_find(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    role = op.get("role")
    name = op.get("name")
    name_substr = bool(op.get("name_substr", False))
    app_filter = op.get("app_filter", "rocket")
    max_depth = int(op.get("max_depth", 50))
    hit = _find_first(desktop, role, name, name_substr, app_filter, max_depth)
    if hit is None:
        return {"ok": False, "error": "no_match",
                "role": role, "name": name, "name_substr": name_substr,
                "app_filter": app_filter}
    return {"ok": True, **hit}


def _op_verify(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    # Same semantics as find, but framed as an assertion.
    res = _op_find(desktop, op, results)
    if res.get("ok"):
        return {"ok": True, "path": res.get("path"), "role": res.get("role"),
                "name": res.get("name")}
    return {"ok": False, "error": "verify_failed",
            "role": op.get("role"), "name": op.get("name"),
            "name_substr": bool(op.get("name_substr", False))}


def _op_do_action(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    action_idx = int(op.get("action_idx", 0))
    app_filter = op.get("app_filter", "rocket")
    path = op.get("path")
    if path is None:
        from_id = op.get("from_id")
        if from_id is None:
            return {"ok": False, "error": "do_action_needs_path_or_from_id"}
        prior = results.get(str(from_id))
        if not prior or not prior.get("ok"):
            return {"ok": False, "error": "from_id_not_ok",
                    "from_id": from_id}
        path = prior.get("path")
        if path is None:
            return {"ok": False, "error": "from_id_has_no_path",
                    "from_id": from_id}

    app = _find_app(desktop, app_filter)
    if app is None:
        return {"ok": False, "error": "app_not_found",
                "app_filter": app_filter}
    node = _get_node_by_path(app, path)
    if node is None:
        return {"ok": False, "error": "node_path_unresolved", "path": path}

    # Action name (best-effort, pre-invocation).
    action_name: Optional[str] = None
    try:
        action_name = node.get_action_name(action_idx)
    except Exception:
        pass

    t0 = time.monotonic()
    try:
        # TODO: deprecation — `accessible.do_action(idx)` is the
        # python-binding-friendly call site. `Atspi.Action.do_action(node, idx)`
        # is the non-deprecated equivalent but is not reliably exposed in all
        # PyGObject + Atspi version combos. Stick with the instance method
        # until we hit a version where it raises.
        node.do_action(action_idx)
    except Exception as e:
        return {"ok": False, "error": "do_action_raised",
                "exc": repr(e), "path": path, "action_idx": action_idx}
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return {"ok": True, "path": path, "action_idx": action_idx,
            "action_name": action_name, "elapsed_ms": elapsed_ms}


def _op_tree_dump(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    app_filter = op.get("app_filter", "rocket")
    max_depth = int(op.get("max_depth", 30))
    app = _find_app(desktop, app_filter)
    if app is None:
        return {"ok": False, "error": "app_not_found",
                "app_filter": app_filter, "nodes": []}
    nodes: list[dict] = []
    _walk(app, "", 0, max_depth, nodes)
    return {"ok": True, "node_count": len(nodes), "nodes": nodes,
            "app_name": app.get_name() if app is not None else None}


def _op_wait_for(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    any_conds: list[dict] = op.get("any") or []
    all_conds: list[dict] = op.get("all") or []
    if not any_conds and not all_conds:
        return {"ok": False, "error": "wait_for_needs_any_or_all"}
    timeout_ms = int(op.get("timeout_ms", 15000))
    interval_ms = int(op.get("interval_ms", 500))
    app_filter = op.get("app_filter", "rocket")
    max_depth = int(op.get("max_depth", 50))

    deadline = time.monotonic() + (timeout_ms / 1000.0)
    polls = 0
    last_seen: list[str] = []
    while True:
        polls += 1
        if any_conds:
            for cond in any_conds:
                hit = _find_first(
                    desktop,
                    cond.get("role"), cond.get("name"),
                    bool(cond.get("name_substr", False)),
                    cond.get("app_filter", app_filter), max_depth,
                )
                if hit is not None:
                    return {"ok": True, "matched": "any", "cond": cond,
                            "path": hit.get("path"),
                            "polls": polls}
        if all_conds:
            matches: list[dict] = []
            for cond in all_conds:
                hit = _find_first(
                    desktop,
                    cond.get("role"), cond.get("name"),
                    bool(cond.get("name_substr", False)),
                    cond.get("app_filter", app_filter), max_depth,
                )
                if hit is None:
                    matches = []
                    break
                matches.append({"cond": cond, "path": hit.get("path")})
            if matches and len(matches) == len(all_conds):
                return {"ok": True, "matched": "all", "matches": matches,
                        "polls": polls}
        if time.monotonic() >= deadline:
            return {"ok": False, "error": "wait_for_timeout",
                    "timeout_ms": timeout_ms, "polls": polls,
                    "last_seen": last_seen}
        time.sleep(interval_ms / 1000.0)


_OP_HANDLERS = {
    "find": _op_find,
    "verify": _op_verify,
    "do_action": _op_do_action,
    "tree_dump": _op_tree_dump,
    "wait_for": _op_wait_for,
}


# --------------------------- batch driver ------------------------------------


def run_batch(batch: dict) -> dict:
    ops: list[dict] = batch.get("ops") or []
    init: dict = batch.get("init") or {}
    force_init = bool(init.get("force_atspi_init", True))
    settle_ms = int(init.get("settle_ms", 0))

    out: dict[str, Any] = {"ok": True, "results": {}, "errors": []}

    if not ops:
        # Empty batch is a valid no-op (useful for smoke tests without an
        # AT-SPI bus available, e.g. on the developer host).
        return out

    desktop, err = _ensure_atspi(force_init=force_init)
    if err is not None or desktop is None:
        out["ok"] = False
        out["errors"].append(err or "desktop is None")
        # Mark every op as not-run so callers see structured failures.
        for op in ops:
            op_id = str(op.get("id", ""))
            out["results"][op_id] = {"ok": False, "error": "atspi_not_ready",
                                     "detail": err}
        return out

    if settle_ms > 0:
        time.sleep(settle_ms / 1000.0)

    for op in ops:
        op_id = str(op.get("id", ""))
        op_name = op.get("op")
        handler = _OP_HANDLERS.get(op_name or "")
        if handler is None:
            out["results"][op_id] = {"ok": False,
                                     "error": "unknown_op", "op": op_name}
            out["ok"] = False
            continue
        try:
            res = handler(desktop, op, out["results"])
        except Exception as e:
            res = {"ok": False, "error": "op_raised", "exc": repr(e)}
        out["results"][op_id] = res
        if not res.get("ok"):
            out["ok"] = False
    return out


def _read_batch_from_argv_or_stdin() -> dict:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        raw = sys.argv[1]
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        return {"ops": []}
    return json.loads(raw)


def main() -> int:
    try:
        batch = _read_batch_from_argv_or_stdin()
    except Exception as e:
        result = {"ok": False, "results": {},
                  "errors": [f"batch_parse_failed: {e!r}"]}
        print(json.dumps(result, indent=2))
        return 1
    result = run_batch(batch)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
