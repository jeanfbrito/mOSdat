#!/usr/bin/env python3
"""UIA worker — runs ON the target Windows VM, NOT on the host.

Windows analog of `automation/atspi/worker.py`. Mirrors the JSON op-batch
protocol exactly so the host-side dispatch in the runner can stay
identical across OSes.

Invocation (one-shot, JSON-RPC-over-stdio style but argv-based because the
host SSH client is one-shot — see `automation/transport/ssh.py`):

    python C:\\tmp\\mosdat_uia_worker.py "<json-escaped-op-batch>"
    # or, if no arg, reads the batch from stdin

Op-batch shape (input):
    {
      "ops": [
        {"id": "1", "op": "find",       "role": "Button",
         "name": "Connect", "name_substr": false, "app_filter": "Rocket.Chat"},
        {"id": "2", "op": "do_action",  "from_id": "1", "action_idx": 0},
        {"id": "3", "op": "verify",     "role": "Window", "name_substr": "Rocket.Chat"},
        {"id": "4", "op": "tree_dump",  "max_depth": 30, "app_filter": "Rocket.Chat"},
        {"id": "5", "op": "wait_for",
         "any": [{"role": "Button", "name": "Connect"}],
         "timeout_ms": 15000, "interval_ms": 500},
        {"id": "6", "op": "get_at_point", "x": 100, "y": 200}
      ],
      "init": {"force_uia_init": true, "settle_ms": 0}
    }

Result shape (stdout, pretty-printed JSON):
    {
      "ok": true,                 # false iff any op failed
      "results": {"1": {...}, ...},
      "errors": []                # batch-level errors (e.g. pywinauto missing)
    }

Exit code: 0 iff every op `ok`. 1 if any op failed or a batch-level error fired.

Single-shot, stateless: every invocation re-walks the desktop tree from
the root. No persistent daemon — the SSH transport tears down each call.

Windows-specific notes:
  * **Lazy a11y init**: Chromium on Windows does NOT expose its UIA tree
    until either an assistive-tech client attaches OR a `WM_GETOBJECT`
    message with lParam=`UiaRootObjectId` (1) is sent. Plain `pywinauto`
    enumeration may see only the top-level frame with zero children. The
    worker kicks the tree by sending `WM_GETOBJECT` (0x003D) to every
    top-level window of the target process before walking.
  * **App filter** matches case-insensitively on EITHER the window title
    OR the process name (`Rocket.Chat.exe`). Composing both lets a single
    `app_filter: "rocket"` work for arbitrary Electron builds.
  * **Extents**: `element.rectangle()` returns
    `pywinauto.win32structures.RECT(left, top, right, bottom)`. The
    worker converts to the same `{x, y, width, height}` shape the AT-SPI
    worker emits so downstream consumers stay identical.
  * **Zero-extent walk**: same `_resolve_clickable_extents` ancestor-walk
    semantics as the AT-SPI worker — if the widget itself has a < 8x8
    bbox, walk up to 5 parents looking for a sensible clickable
    container (max 600x200 to avoid landing on the whole frame).

Dependencies (Windows-only): `pywinauto`, `pywin32`, `comtypes`.
Imports are wrapped in try/except so a smoke-run on Linux returns a
clean JSON error instead of an unhandled ImportError.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Optional


# --------------------------- AT-SPI <-> UIA role normalization ---------------
#
# Scenarios are authored in AT-SPI vocabulary (`frame`, `push button`, `entry`,
# ...) because Linux is the primary authoring platform. Windows UIA reports
# the same widgets under different names (`Window`, `Button`, `Edit`, ...).
#
# When a scenario asks for `role: frame` we accept any UIA ControlType in the
# candidate list below; native-UIA role strings (anything not in this map)
# pass through unchanged so scenarios CAN target a UIA-specific class when
# they need to.
#
# `tree_dump` output emits BOTH the AT-SPI-translated `role` (for scenario
# authors) AND the raw UIA name as `uia_control_type` (for debugging).

_ROLE_MAP_ATSPI_TO_UIA: dict[str, list[str]] = {
    "frame": ["Window", "Pane"],
    "push button": ["Button"],
    "toggle button": ["Button"],
    "check box": ["CheckBox"],
    "radio button": ["RadioButton"],
    "entry": ["Edit"],
    "password text": ["Edit"],
    "combo box": ["ComboBox"],
    "list box": ["List"],
    "list item": ["ListItem"],
    "menu": ["Menu"],
    "menu bar": ["MenuBar"],
    "menu item": ["MenuItem"],
    "dialog": ["Window"],
    "tool bar": ["ToolBar"],
    "tab list": ["Tab"],
    "page tab": ["TabItem"],
    "label": ["Text"],
    "static": ["Text"],
    "section": ["Group", "Pane"],
    "panel": ["Pane", "Group"],
    "document web": ["Document", "Pane"],
    "image": ["Image"],
}

# Reverse — first AT-SPI synonym for a given UIA name wins. Used only for
# tree_dump output translation; matching uses the forward map.
_ROLE_MAP_UIA_TO_ATSPI: dict[str, str] = {}
for _atspi, _uias in _ROLE_MAP_ATSPI_TO_UIA.items():
    for _u in _uias:
        _ROLE_MAP_UIA_TO_ATSPI.setdefault(_u, _atspi)


def _uia_candidates_for_role(role: Optional[str]) -> Optional[list[str]]:
    """Return UIA ControlType candidates for an AT-SPI `role` string.

    None  -> caller does not filter by role.
    [r]   -> role is a UIA-native string not in the map; pass through as-is.
    [...] -> AT-SPI role; return the mapped candidate list.

    Match is case-insensitive on the key side; output preserves the canonical
    capitalisation pywinauto emits (e.g. ``"Button"``).
    """
    if role is None:
        return None
    key = role.strip().lower()
    if not key:
        return [role]
    mapped = _ROLE_MAP_ATSPI_TO_UIA.get(key)
    if mapped is not None:
        return list(mapped)
    return [role]


def _atspi_role_for_uia(uia_role: str) -> str:
    """Translate a raw UIA ControlType to its AT-SPI synonym.

    Falls back to the raw UIA string when there is no mapping (custom or
    unknown class). Used by `_node_meta` so `tree_dump` output reads in
    scenario-author vocabulary.
    """
    if not uia_role:
        return uia_role
    return _ROLE_MAP_UIA_TO_ATSPI.get(uia_role, uia_role)


# --------------------------- pywinauto lazy bootstrap ------------------------

_UIA_READY = False
_DESKTOP = None  # cached pywinauto Desktop(backend='uia')
_IUIA = None     # cached IUIA().iuia for ElementFromPoint
_IMPORT_ERR: Optional[str] = None


def _ensure_uia(force_init: bool = True) -> tuple[Any, Optional[str]]:
    """Import pywinauto, build a Desktop(backend='uia'), return (desktop, err).

    Idempotent within a single worker process. Returns the cached Desktop
    on subsequent calls. Linux smoke runs (worker imported without
    pywinauto) get a clean structured error rather than a crash.
    """
    global _UIA_READY, _DESKTOP, _IUIA, _IMPORT_ERR
    if _UIA_READY and _DESKTOP is not None and not force_init:
        return _DESKTOP, None
    try:
        from pywinauto import Desktop  # type: ignore
        from pywinauto.uia_defines import IUIA  # type: ignore
    except Exception as e:
        _IMPORT_ERR = f"pywinauto import failed: {e!r}"
        return None, _IMPORT_ERR

    try:
        desktop = Desktop(backend="uia")
    except Exception as e:
        return None, f"Desktop(backend='uia') failed: {e!r}"

    try:
        _IUIA = IUIA().iuia
    except Exception as e:
        # Not fatal: ElementFromPoint is only used by get_at_point.
        _IUIA = None
        _IMPORT_ERR = f"IUIA() init failed: {e!r}"

    _DESKTOP = desktop
    _UIA_READY = True
    return desktop, None


# --------------------------- lazy-a11y kick ----------------------------------


def _kick_uia_tree(process_name: Optional[str] = None) -> None:
    """Force Chromium/Electron to expose its UIA tree.

    Chromium-based apps only build the renderer-process UIA tree when an
    assistive-tech client attaches OR a top-level window receives a
    `WM_GETOBJECT` message with lParam == `UiaRootObjectId` (1). Without
    this nudge `pywinauto` may see the Rocket.Chat frame with NO children.

    Best-effort: any failure here is swallowed because the subsequent
    walk will surface the real problem with structured diagnostics.
    """
    try:
        import ctypes  # type: ignore
        from ctypes import wintypes  # type: ignore
    except Exception:
        return

    WM_GETOBJECT = 0x003D
    UIA_ROOT_OBJECT_ID = 1
    user32 = ctypes.windll.user32

    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM,
    )
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    IsWindowVisible = user32.IsWindowVisible
    SendMessageW = user32.SendMessageW
    SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    SendMessageW.restype = ctypes.c_long

    target_pids: set[int] = set()
    if process_name:
        try:
            import psutil  # type: ignore

            needle = process_name.lower()
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    nm = (p.info.get("name") or "").lower()
                    if needle in nm:
                        target_pids.add(int(p.info["pid"]))
                except Exception:
                    continue
        except Exception:
            # psutil missing — fall through, kick all visible top-levels.
            target_pids = set()

    def _enum_cb(hwnd: int, lparam: int) -> bool:
        try:
            if not IsWindowVisible(hwnd):
                return True
            if target_pids:
                pid = wintypes.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if int(pid.value) not in target_pids:
                    return True
            SendMessageW(hwnd, WM_GETOBJECT, 0, UIA_ROOT_OBJECT_ID)
        except Exception:
            pass
        return True

    try:
        EnumWindows(EnumWindowsProc(_enum_cb), 0)
    except Exception:
        pass


# --------------------------- tree walking ------------------------------------


def _matches_app(elem: Any, needle: str) -> bool:
    """Case-insensitive substring match on title OR process name."""
    if not needle:
        return True
    needle = needle.lower()
    try:
        title = (elem.window_text() or "").lower()
    except Exception:
        title = ""
    if needle in title:
        return True
    # Process name via psutil if available; otherwise fall back to title only.
    try:
        import psutil  # type: ignore

        pid = elem.process_id()
        if pid:
            proc = psutil.Process(int(pid))
            nm = (proc.name() or "").lower()
            if needle in nm:
                return True
    except Exception:
        pass
    return False


def _find_app(desktop: Any, app_filter: str) -> Optional[Any]:
    """Locate the top-level window matching app_filter."""
    try:
        windows = desktop.windows()
    except Exception:
        return None
    for w in windows:
        try:
            if _matches_app(w, app_filter):
                return w
        except Exception:
            continue
    return None


def _rect_to_extents(r: Any) -> Optional[dict[str, int]]:
    """Convert pywinauto RECT → AT-SPI-shape dict. None on failure."""
    if r is None:
        return None
    try:
        left = int(r.left)
        top = int(r.top)
        right = int(r.right)
        bottom = int(r.bottom)
        return {
            "x": left, "y": top,
            "width": max(0, right - left),
            "height": max(0, bottom - top),
        }
    except Exception:
        return None


def _node_meta(elem: Any) -> dict[str, Any]:
    """Extract role/name/extents/actions for one UIA element. Never raises.

    Emits TWO role strings:
      * ``role`` — AT-SPI vocabulary (`frame`, `push button`, ...) when the
        UIA ControlType has a known mapping; raw UIA string otherwise. This
        is what scenario authors see in ``tree_dump`` output.
      * ``uia_control_type`` — raw pywinauto friendly_class_name (e.g.
        ``"Button"``, ``"Window"``) for debugging cross-platform mismatches.
    """
    try:
        uia_role = elem.friendly_class_name()
    except Exception:
        try:
            uia_role = elem.element_info.control_type or "<unknown>"
        except Exception:
            uia_role = "<unknown>"
    role = _atspi_role_for_uia(uia_role)
    try:
        name = elem.window_text() or ""
    except Exception:
        name = ""

    # UIA "actions" are exposed as control patterns. We treat each
    # supported invocation pattern as one action so the wire shape stays
    # symmetric with AT-SPI. Action 0 is the "primary" invocation.
    action_names: list[str] = []
    try:
        ei = elem.element_info.element
        # Try common patterns in priority order.
        for pat_name, code in (
            ("invoke", 10000),       # UIA_InvokePatternId
            ("toggle", 10007),       # UIA_TogglePatternId
            ("select", 10010),       # UIA_SelectionItemPatternId
            ("expand", 10005),       # UIA_ExpandCollapsePatternId
        ):
            try:
                pat = ei.GetCurrentPattern(code)
                if pat is not None:
                    action_names.append(pat_name)
            except Exception:
                continue
    except Exception:
        pass
    n_actions = len(action_names)

    try:
        child_count = len(elem.children())
    except Exception:
        child_count = 0

    return {
        "role": role,
        "uia_control_type": uia_role,
        "name": name,
        "n_actions": n_actions,
        "action_names": action_names,
        "child_count": child_count,
    }


def _walk(elem: Any, path: str, depth: int, max_depth: int,
          out: list[dict]) -> None:
    if depth > max_depth:
        return
    meta = _node_meta(elem)
    meta["path"] = path
    meta["depth"] = depth
    try:
        rect = elem.rectangle()
        ext = _rect_to_extents(rect)
        if ext is not None:
            meta["extents"] = ext
    except Exception:
        pass
    # UIA `IsOffscreen` covers both clipped-out and explicitly-hidden widgets.
    # We record it on every node so `_find_first` can prefer visible matches
    # over stale duplicates (e.g. RC's hidden popover Settings menu item).
    try:
        meta["is_offscreen"] = bool(elem.is_offscreen())
    except Exception:
        meta["is_offscreen"] = False
    out.append(meta)
    try:
        children = elem.children()
    except Exception:
        children = []
    for i, child in enumerate(children):
        if child is None:
            continue
        _walk(child, f"{path}/{i}", depth + 1, max_depth, out)


def _walk_path_to_app(elem: Any, app_filter: str) -> Optional[str]:
    """Walk parents from `elem` up to the matching top-level window.

    Returns the relative index-path (e.g. "/0/3/1"). Returns None if
    `elem` is not under a matching app, or "" for the app window itself.
    """
    needle = (app_filter or "").lower()
    indices: list[int] = []
    current = elem
    for _ in range(200):
        try:
            parent = current.parent()
        except Exception:
            return None
        if parent is None:
            return None
        # Top-level windows have the desktop as their parent. The desktop's
        # parent is None or itself depending on pywinauto version; we treat
        # "parent has no usable rectangle covering > a screen" as the
        # boundary. Simpler check: try matching `current` against app_filter.
        try:
            if _matches_app(current, needle):
                # current is the app top-level.
                return "/" + "/".join(str(i) for i in reversed(indices)) if indices else ""
        except Exception:
            pass
        try:
            siblings = parent.children()
            idx = -1
            for i, sib in enumerate(siblings):
                if sib is current:
                    idx = i
                    break
            if idx < 0:
                return None
        except Exception:
            return None
        indices.append(int(idx))
        current = parent
    return None


def _get_node_by_path(root: Any, path: str) -> Optional[Any]:
    """Resolve a /a/b/c path (relative to `root`) to a pywinauto element."""
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
            children = node.children()
            if idx >= len(children):
                return None
            node = children[idx]
        except Exception:
            return None
        if node is None:
            return None
    return node


def _match(meta: dict, role: Optional[str], name: Optional[str],
           name_substr: bool) -> bool:
    """Match a tree node against (role, name).

    ``role`` is the scenario-side string (AT-SPI vocabulary preferred). The
    node's UIA ControlType (``meta['uia_control_type']``) is compared against
    the candidate list from `_uia_candidates_for_role` case-insensitively, so
    `frame` matches both `Window` and `Pane`. The translated AT-SPI role
    (``meta['role']``) is also accepted as an exact match, so scenarios that
    pre-translate (or that target a class with no AT-SPI synonym) still work.
    """
    if role is not None:
        candidates = _uia_candidates_for_role(role) or []
        # Compare against UIA ControlType (raw) and the AT-SPI-translated
        # role; both case-insensitive. The translated `role` covers the
        # pass-through case where `candidates == [role]`.
        node_uia = (meta.get("uia_control_type") or "").lower()
        node_atspi = (meta.get("role") or "").lower()
        want = {c.lower() for c in candidates}
        if node_uia not in want and node_atspi not in want:
            return False
    if name is None:
        return True
    n = meta.get("name", "") or ""
    if name_substr:
        return name in n
    return n == name


def _find_all(desktop: Any, role: Optional[str], name: Optional[str],
              name_substr: bool, app_filter: str,
              max_depth: int = 50) -> list[dict]:
    """Walk the matching app's tree, return ALL matching metadata dicts.

    Ordering: visible (is_offscreen=False) candidates first, in tree-walk
    order, then offscreen candidates in tree-walk order. Each returned
    dict carries a ``visible`` key reflecting its ``is_offscreen`` state.

    Used by `_op_find_all` and `_find_first`. Pointer-mode candidate
    iteration consumes the full list so it can reject a stale popover
    whose UIA bbox overlaps an unrelated widget and try the next match.
    """
    _kick_uia_tree(app_filter)
    app = _find_app(desktop, app_filter)
    if app is None:
        return []
    nodes: list[dict] = []
    _walk(app, "", 0, max_depth, nodes)
    matches = [m for m in nodes if _match(m, role, name, name_substr)]
    if not matches:
        return []
    visible = [{**m, "visible": True} for m in matches
               if not m.get("is_offscreen", False)]
    offscreen = [{**m, "visible": False} for m in matches
                 if m.get("is_offscreen", False)]
    return visible + offscreen


def _find_first(desktop: Any, role: Optional[str], name: Optional[str],
                name_substr: bool, app_filter: str,
                max_depth: int = 50) -> Optional[dict]:
    """Walk the matching app's tree, return first metadata dict that matches.

    `role` accepts AT-SPI vocabulary (`frame`, `push button`, ...) which is
    mapped to UIA ControlType candidates before comparison (see
    `_uia_candidates_for_role`). UIA-native strings pass through unchanged.

    Visibility ranking: RC's Chromium tree often has duplicate role+name
    pairs (visible widget + a hidden popover copy). We collect ALL matches,
    prefer the first with `is_offscreen=False`, and fall back to the first
    offscreen hit only when no visible candidate exists.
    """
    all_matches = _find_all(desktop, role, name, name_substr,
                            app_filter, max_depth)
    if not all_matches:
        return None
    return all_matches[0]


# --------------------------- op handlers -------------------------------------


def _resolve_clickable_extents(
    app: Any, path: str, target_extents: Optional[dict],
) -> Optional[dict]:
    """Walk up the parent chain to find a useful clickable bbox.

    Same semantics as the AT-SPI worker:
      * If the target's own extents already cover >= 8x8 px, return them.
      * Otherwise walk up to 5 parents, return the first bbox >= 8x8 and
        at most 600x200 (reject huge containers).
      * If no ancestor qualifies, return None.

    Used for `via: hint` mode and for the Fuselage ToggleSwitch class of
    hidden-input widgets (the visible "track" widget is the parent of the
    zero-extent <input> that owns the action).
    """
    MIN_AREA = 64       # 8 * 8
    MAX_W = 600
    MAX_H = 200
    if target_extents:
        try:
            w = int(target_extents.get("width", 0))
            h = int(target_extents.get("height", 0))
            if w * h >= MIN_AREA:
                return {"x": int(target_extents["x"]),
                        "y": int(target_extents["y"]),
                        "width": w, "height": h}
        except Exception:
            pass

    node = _get_node_by_path(app, path) if path else app
    if node is None:
        return None
    cur = node
    for _ in range(5):
        try:
            cur = cur.parent()
        except Exception:
            return None
        if cur is None:
            return None
        try:
            ext = _rect_to_extents(cur.rectangle())
            if ext is None:
                continue
            w = ext["width"]
            h = ext["height"]
            if w * h >= MIN_AREA and w <= MAX_W and h <= MAX_H:
                return ext
        except Exception:
            continue
    return None


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
    app = _find_app(desktop, app_filter)
    if app is not None:
        clickable = _resolve_clickable_extents(
            app, hit.get("path") or "", hit.get("extents"),
        )
        if clickable is not None:
            hit = {**hit, "clickable_extents": clickable}
    return {"ok": True, **hit}


def _op_find_all(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    """Return all role+name matches under the matching app.

    Wire shape:
        {"id":..,"op":"find_all","role":..,"name":..,"name_substr":..,
         "app_filter":..,"limit": N (optional, default 10),"max_depth":..}

    Result:
        {"ok": true, "candidates": [<node>, ...]}

    Each node has the same shape as `_op_find`'s success payload
    (path, role, name, extents, clickable_extents when resolvable,
    visible, is_offscreen, ...). Visible candidates appear first, offscreen
    last (mirrors `_find_first`'s ranking). Empty list when nothing matches.
    Used by pointer-mode candidate iteration to reject stale popovers.
    """
    role = op.get("role")
    name = op.get("name")
    name_substr = bool(op.get("name_substr", False))
    app_filter = op.get("app_filter", "rocket")
    max_depth = int(op.get("max_depth", 50))
    limit = int(op.get("limit", 10))
    if limit < 1:
        limit = 1
    matches = _find_all(desktop, role, name, name_substr,
                        app_filter, max_depth)
    matches = matches[:limit]
    app = _find_app(desktop, app_filter)
    enriched: list[dict] = []
    for hit in matches:
        if app is not None:
            clickable = _resolve_clickable_extents(
                app, hit.get("path") or "", hit.get("extents"),
            )
            if clickable is not None:
                hit = {**hit, "clickable_extents": clickable}
        enriched.append(hit)
    return {"ok": True, "candidates": enriched,
            "role": role, "name": name, "name_substr": name_substr,
            "app_filter": app_filter}


def _op_verify(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    res = _op_find(desktop, op, results)
    if res.get("ok"):
        return {"ok": True, "path": res.get("path"), "role": res.get("role"),
                "name": res.get("name")}
    return {"ok": False, "error": "verify_failed",
            "role": op.get("role"), "name": op.get("name"),
            "name_substr": bool(op.get("name_substr", False))}


def _invoke_action(elem: Any, action_idx: int) -> tuple[bool, Optional[str], Optional[str]]:
    """Invoke action by index. Returns (ok, action_name, error).

    Action 0 is the primary invocation. We try in priority order and pick
    the (idx+1)th supported pattern. UIA exposes invoke/toggle/select/
    expand as control patterns; mapping them to a flat action index keeps
    the wire shape symmetric with AT-SPI's `n_actions`/`action_names`.
    """
    ei = None
    try:
        ei = elem.element_info.element
    except Exception:
        pass

    # Build the list of supported patterns at call time so the action_idx
    # mapping matches what _node_meta reported.
    supported: list[tuple[str, int]] = []
    for pat_name, code in (
        ("invoke", 10000),
        ("toggle", 10007),
        ("select", 10010),
        ("expand", 10005),
    ):
        if ei is None:
            break
        try:
            pat = ei.GetCurrentPattern(code)
            if pat is not None:
                supported.append((pat_name, code))
        except Exception:
            continue

    if not supported:
        # Fallback: pywinauto convenience methods if available.
        for meth in ("invoke", "toggle", "select", "click_input"):
            fn = getattr(elem, meth, None)
            if callable(fn):
                try:
                    fn()
                    return True, meth, None
                except Exception as e:
                    return False, meth, f"{meth} raised: {e!r}"
        return False, None, "no_supported_action_pattern"

    if action_idx >= len(supported):
        return False, None, f"action_idx {action_idx} out of range (have {len(supported)})"

    pat_name, code = supported[action_idx]
    try:
        pat = ei.GetCurrentPattern(code)
        if pat_name == "invoke":
            pat.Invoke()
        elif pat_name == "toggle":
            pat.Toggle()
        elif pat_name == "select":
            pat.Select()
        elif pat_name == "expand":
            # Expand if collapsed; otherwise collapse — best-effort.
            try:
                pat.Expand()
            except Exception:
                pat.Collapse()
        return True, pat_name, None
    except Exception as e:
        return False, pat_name, f"{pat_name} raised: {e!r}"


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

    t0 = time.monotonic()
    ok, action_name, err = _invoke_action(node, action_idx)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if not ok:
        return {"ok": False, "error": "do_action_raised",
                "exc": err, "path": path, "action_idx": action_idx,
                "action_name": action_name}
    return {"ok": True, "path": path, "action_idx": action_idx,
            "action_name": action_name, "elapsed_ms": elapsed_ms}


def _op_get_at_point(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    """Look up the UIA node at a screen coordinate.

    Uses `IUIAutomation::ElementFromPoint`. Used by pointer-mode click to
    verify the cursor lands on the intended widget (or one of its
    descendants/ancestors) before clicking.
    """
    try:
        x = int(op.get("x"))
        y = int(op.get("y"))
    except Exception:
        return {"ok": False, "error": "get_at_point_needs_int_x_y"}
    app_filter = op.get("app_filter", "rocket")
    if _IUIA is None:
        return {"ok": False, "error": "iuia_unavailable",
                "x": x, "y": y}
    try:
        # IUIAutomation::ElementFromPoint takes a tagPOINT — must use the
        # comtypes-defined struct from the UIA dll, not a hand-rolled one.
        from pywinauto.uia_defines import IUIA  # type: ignore
        pt = IUIA().UIA_dll.tagPOINT(x, y)
        com_elem = _IUIA.ElementFromPoint(pt)
    except Exception as e:
        return {"ok": False, "error": f"get_at_point failed: {e!r}"}
    if com_elem is None:
        return {"ok": False, "error": "no_node_at_point", "x": x, "y": y}

    # Wrap the COM element in a pywinauto UIAWrapper so we can walk parents.
    try:
        from pywinauto.controls.uiawrapper import UIAWrapper  # type: ignore
        from pywinauto.uia_element_info import UIAElementInfo  # type: ignore

        ei = UIAElementInfo(com_elem)
        wrapper = UIAWrapper(ei)
    except Exception as e:
        return {"ok": False, "error": f"wrap_com_element failed: {e!r}",
                "x": x, "y": y}

    try:
        uia_role = wrapper.friendly_class_name()
    except Exception:
        uia_role = "<unknown>"
    role = _atspi_role_for_uia(uia_role)
    try:
        name = wrapper.window_text() or ""
    except Exception:
        name = ""
    path = _walk_path_to_app(wrapper, app_filter)
    return {"ok": True, "role": role, "uia_control_type": uia_role,
            "name": name,
            "path": path if path is not None else "",
            "x": x, "y": y,
            "under_app": path is not None}


def _op_tree_dump(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    app_filter = op.get("app_filter", "rocket")
    max_depth = int(op.get("max_depth", 30))
    _kick_uia_tree(app_filter)
    app = _find_app(desktop, app_filter)
    if app is None:
        return {"ok": False, "error": "app_not_found",
                "app_filter": app_filter, "nodes": []}
    nodes: list[dict] = []
    _walk(app, "", 0, max_depth, nodes)
    try:
        app_name = app.window_text() or None
    except Exception:
        app_name = None
    return {"ok": True, "node_count": len(nodes), "nodes": nodes,
            "app_name": app_name}


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


# --------------------------- pointer ops (Windows-only) ---------------------


def _op_move_cursor(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    """Move the OS cursor to (x, y) with optional duration + dwell.

    Windows-specific op (no AT-SPI analog because Linux moves the cursor
    host-side via VNC). Used by the host-side pointer-mode click which
    builds a single op-batch `find → move_cursor → get_at_point → click`.
    """
    try:
        x = int(op.get("x"))
        y = int(op.get("y"))
    except Exception:
        return {"ok": False, "error": "move_cursor_needs_int_x_y"}
    duration = float(op.get("duration", 0.3))
    dwell_ms = int(op.get("dwell_ms", 0))
    try:
        from pywinauto import mouse  # type: ignore

        mouse.move(coords=(x, y), duration=duration)
    except Exception as e:
        return {"ok": False, "error": f"mouse.move failed: {e!r}",
                "x": x, "y": y}
    if dwell_ms > 0:
        time.sleep(dwell_ms / 1000.0)
    return {"ok": True, "x": x, "y": y,
            "duration": duration, "dwell_ms": dwell_ms}


def _op_click_cursor(desktop: Any, op: dict, results: dict[str, dict]) -> dict:
    """Real mouse-button click at (x, y) — `button=1` is left."""
    try:
        x = int(op.get("x"))
        y = int(op.get("y"))
    except Exception:
        return {"ok": False, "error": "click_cursor_needs_int_x_y"}
    btn_idx = int(op.get("button", 1))
    btn_name = {1: "left", 2: "middle", 3: "right"}.get(btn_idx, "left")
    try:
        from pywinauto import mouse  # type: ignore

        mouse.click(button=btn_name, coords=(x, y))
    except Exception as e:
        return {"ok": False, "error": f"mouse.click failed: {e!r}",
                "x": x, "y": y, "button": btn_name}
    return {"ok": True, "x": x, "y": y, "button": btn_name}


_OP_HANDLERS = {
    "find": _op_find,
    "find_all": _op_find_all,
    "verify": _op_verify,
    "do_action": _op_do_action,
    "tree_dump": _op_tree_dump,
    "wait_for": _op_wait_for,
    "get_at_point": _op_get_at_point,
    # Windows-only pointer ops:
    "move_cursor": _op_move_cursor,
    "click_cursor": _op_click_cursor,
}


# --------------------------- batch driver ------------------------------------


def run_batch(batch: dict) -> dict:
    ops: list[dict] = batch.get("ops") or []
    init: dict = batch.get("init") or {}
    force_init = bool(init.get("force_uia_init", True))
    settle_ms = int(init.get("settle_ms", 0))

    out: dict[str, Any] = {"ok": True, "results": {}, "errors": []}

    if not ops:
        # Empty batch is a valid no-op (useful for smoke tests without
        # pywinauto available, e.g. on the developer Linux host).
        return out

    desktop, err = _ensure_uia(force_init=force_init)
    if err is not None or desktop is None:
        out["ok"] = False
        out["errors"].append(err or "desktop is None")
        for op in ops:
            op_id = str(op.get("id", ""))
            out["results"][op_id] = {"ok": False, "error": "uia_not_ready",
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


def _parse_cli_args(argv: list[str]) -> tuple[str, Optional[str]]:
    """Split worker argv into (input_spec, out_path).

    Returns:
        input_spec:
            * ``"file:<path>"`` — read JSON from <path>
            * ``"<json>"``      — JSON string passed directly as argv
            * ``""``            — read from stdin
        out_path:
            * absolute path to write result JSON to (also still prints to
              stdout for back-compat), or None to skip the file write.

    Recognised flags: ``--out <path>`` / ``--out=<path>``.
    The first non-flag positional argv is the input_spec. Session-1
    invocation passes ``file:C:\\tmp\\uia-req-<uuid>.json`` because stdin
    isn't available across the schtasks boundary.
    """
    out_path: Optional[str] = None
    input_spec = ""
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--out":
            if i + 1 >= len(argv):
                raise ValueError("--out requires a path argument")
            out_path = argv[i + 1]
            i += 2
            continue
        if a.startswith("--out="):
            out_path = a[len("--out="):]
            i += 1
            continue
        if not input_spec and a.strip():
            input_spec = a
        i += 1
    return input_spec, out_path


def _load_batch(input_spec: str) -> dict:
    """Load the op-batch given the resolved input_spec from `_parse_cli_args`."""
    if input_spec.startswith("file:"):
        path = input_spec[len("file:"):]
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    elif input_spec:
        raw = input_spec
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        return {"ops": []}
    return json.loads(raw)


def main() -> int:
    try:
        input_spec, out_path = _parse_cli_args(sys.argv)
        batch = _load_batch(input_spec)
    except Exception as e:
        result = {"ok": False, "results": {},
                  "errors": [f"batch_parse_failed: {e!r}"]}
        rendered = json.dumps(result, indent=2)
        print(rendered)
        # Best-effort: write to --out path too, so a Session-1 caller polling
        # the result file sees the parse failure instead of timing out.
        try:
            _, out_path_local = _parse_cli_args(sys.argv)
            if out_path_local:
                with open(out_path_local, "w", encoding="utf-8") as fh:
                    fh.write(rendered)
        except Exception:
            pass
        return 1
    result = run_batch(batch)
    rendered = json.dumps(result, indent=2, default=str)
    print(rendered)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(rendered)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
