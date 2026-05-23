"""UIA worker visibility-filter tests.

When Chromium/Electron exposes duplicate widgets under the same role+name
(common pattern: a visible top-bar item plus a hidden popover/menu copy),
`_find_first` must prefer the visible one. The legacy "first match wins"
behaviour returned the offscreen duplicate and broke clicks (tel-qa-001
step 11: Settings menu item resolved to a hidden popover with stale
coordinates).

These tests exercise the pure-Python helpers via mocked pywinauto-shaped
stubs; no real pywinauto / Windows VM required.
"""

from __future__ import annotations

from typing import Optional

from automation.uia import worker as uia_worker


class _StubElem:
    """Minimal pywinauto-element stub with `is_offscreen` support."""

    def __init__(self, *, uia_role: str, name: str = "",
                 children: Optional[list["_StubElem"]] = None,
                 rect: Optional[tuple[int, int, int, int]] = None,
                 is_offscreen: bool = False) -> None:
        self._uia_role = uia_role
        self._name = name
        self._children = list(children or [])
        self._rect = rect
        self._is_offscreen = is_offscreen

    def friendly_class_name(self) -> str:
        return self._uia_role

    def window_text(self) -> str:
        return self._name

    def children(self) -> list["_StubElem"]:
        return self._children

    def rectangle(self):  # noqa: ANN201
        if self._rect is None:
            return None
        l, t, r, b = self._rect

        class _R:
            left, top, right, bottom = l, t, r, b

        return _R

    def is_offscreen(self) -> bool:  # noqa: D401 - mimic pywinauto API
        return self._is_offscreen

    class _EI:
        class _Inner:
            def GetCurrentPattern(self, code: int):  # noqa: N802
                return None

        element = _Inner()

    element_info = _EI()


def _patch_find_app(monkeypatch, root: _StubElem) -> None:
    """Bypass real desktop / kick-tree; route `_find_app` to our stub root."""
    monkeypatch.setattr(uia_worker, "_kick_uia_tree", lambda *a, **kw: None)
    monkeypatch.setattr(uia_worker, "_find_app", lambda d, f: root)


class TestWalkRecordsOffscreen:
    def test_walk_records_is_offscreen_true(self) -> None:
        elem = _StubElem(uia_role="MenuItem", name="Settings",
                         rect=(0, 0, 100, 20), is_offscreen=True)
        out: list[dict] = []
        uia_worker._walk(elem, "", 0, 5, out)
        assert len(out) == 1
        assert out[0]["is_offscreen"] is True

    def test_walk_records_is_offscreen_false(self) -> None:
        elem = _StubElem(uia_role="MenuItem", name="Settings",
                         rect=(0, 0, 100, 20), is_offscreen=False)
        out: list[dict] = []
        uia_worker._walk(elem, "", 0, 5, out)
        assert len(out) == 1
        assert out[0]["is_offscreen"] is False

    def test_walk_defaults_to_false_when_probe_raises(self) -> None:
        # Elements without an `is_offscreen` method shouldn't break the walk.
        class _NoOffscreenElem(_StubElem):
            def is_offscreen(self) -> bool:
                raise RuntimeError("not supported")

        elem = _NoOffscreenElem(uia_role="MenuItem", name="X",
                                rect=(0, 0, 10, 10))
        out: list[dict] = []
        uia_worker._walk(elem, "", 0, 5, out)
        assert out[0]["is_offscreen"] is False


class TestFindPrefersVisible:
    def test_prefers_visible_over_offscreen_duplicate(self, monkeypatch) -> None:
        # Real-world shape: hidden popover Settings appears FIRST in tree
        # order (its parent is enumerated before the visible top-bar item).
        hidden = _StubElem(uia_role="MenuItem", name="Settings",
                           rect=(0, 0, 100, 20), is_offscreen=True)
        visible = _StubElem(uia_role="MenuItem", name="Settings",
                            rect=(200, 200, 300, 220), is_offscreen=False)
        root = _StubElem(uia_role="Window", name="Rocket.Chat",
                         children=[hidden, visible],
                         rect=(0, 0, 800, 600))
        _patch_find_app(monkeypatch, root)
        hit = uia_worker._find_first(
            desktop=None, role="menu item", name="Settings",
            name_substr=False, app_filter="rocket",
        )
        assert hit is not None
        assert hit["visible"] is True
        # Extents must be the visible one's bbox.
        assert hit["extents"]["x"] == 200

    def test_falls_back_to_offscreen_when_none_visible(self, monkeypatch) -> None:
        h1 = _StubElem(uia_role="MenuItem", name="Settings",
                       rect=(0, 0, 100, 20), is_offscreen=True)
        h2 = _StubElem(uia_role="MenuItem", name="Settings",
                       rect=(300, 300, 400, 320), is_offscreen=True)
        root = _StubElem(uia_role="Window", name="Rocket.Chat",
                         children=[h1, h2],
                         rect=(0, 0, 800, 600))
        _patch_find_app(monkeypatch, root)
        hit = uia_worker._find_first(
            desktop=None, role="menu item", name="Settings",
            name_substr=False, app_filter="rocket",
        )
        assert hit is not None
        assert hit["visible"] is False
        # Fallback returns FIRST offscreen hit (tree order).
        assert hit["extents"]["x"] == 0

    def test_single_visible_match_returns_visible_true(self, monkeypatch) -> None:
        only = _StubElem(uia_role="Button", name="Connect",
                         rect=(10, 10, 110, 40), is_offscreen=False)
        root = _StubElem(uia_role="Window", name="Rocket.Chat",
                         children=[only], rect=(0, 0, 800, 600))
        _patch_find_app(monkeypatch, root)
        hit = uia_worker._find_first(
            desktop=None, role="push button", name="Connect",
            name_substr=False, app_filter="rocket",
        )
        assert hit is not None
        assert hit["visible"] is True
