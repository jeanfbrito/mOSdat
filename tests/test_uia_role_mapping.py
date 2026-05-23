"""Cross-platform AT-SPI <-> UIA role normalization tests for the UIA worker.

Scenarios are authored in AT-SPI vocabulary (``frame``, ``push button``,
``entry``, ...) because Linux is the primary authoring platform. Windows UIA
exposes the equivalent widgets under different names (``Window``, ``Button``,
``Edit``, ...). The worker translates between the two so a single scenario
file matches widgets across OS backends.

These tests run on Linux (CI host); they exercise the pure-Python translation
helpers and the `_match`/`_find_first`/`_node_meta` paths via mocked
pywinauto-shaped element stubs. No real pywinauto / Windows VM required.
"""

from __future__ import annotations

from typing import Optional

import pytest

from automation.uia import worker as uia_worker


# --------------------------- element stub ------------------------------------


class _StubElem:
    """Minimal pywinauto-element-shaped stub.

    Only the attrs `_node_meta`, `_walk`, `_find_first` actually touch are
    implemented (`friendly_class_name`, `window_text`, `children`, `rectangle`,
    `element_info.element.GetCurrentPattern`).
    """

    def __init__(self, *, uia_role: str, name: str = "",
                 children: Optional[list["_StubElem"]] = None,
                 rect: Optional[tuple[int, int, int, int]] = None) -> None:
        self._uia_role = uia_role
        self._name = name
        self._children = list(children or [])
        self._rect = rect

    def friendly_class_name(self) -> str:
        return self._uia_role

    def window_text(self) -> str:
        return self._name

    def children(self) -> list["_StubElem"]:
        return self._children

    def rectangle(self):  # noqa: ANN201 - mimic pywinauto duck-typed shape
        if self._rect is None:
            return None
        l, t, r, b = self._rect

        class _R:
            left, top, right, bottom = l, t, r, b

        return _R

    # element_info.element.GetCurrentPattern is exercised by _node_meta; it
    # must never raise. Returning None for every pattern code => n_actions == 0.
    class _EI:
        class _Inner:
            def GetCurrentPattern(self, code: int):  # noqa: N802 - COM name
                return None

        element = _Inner()

    element_info = _EI()


# --------------------------- forward map -------------------------------------


class TestRoleMapForward:
    """`_uia_candidates_for_role`: AT-SPI -> UIA candidate list."""

    @pytest.mark.parametrize("atspi,expected", [
        ("frame", ["Window", "Pane"]),
        ("push button", ["Button"]),
        ("toggle button", ["Button"]),
        ("check box", ["CheckBox"]),
        ("radio button", ["RadioButton"]),
        ("entry", ["Edit"]),
        ("password text", ["Edit"]),
        ("combo box", ["ComboBox"]),
        ("list box", ["List"]),
        ("list item", ["ListItem"]),
        ("menu", ["Menu"]),
        ("menu bar", ["MenuBar"]),
        ("menu item", ["MenuItem"]),
        ("dialog", ["Window"]),
        ("tool bar", ["ToolBar"]),
        ("tab list", ["Tab"]),
        ("page tab", ["TabItem"]),
        ("label", ["Text"]),
        ("static", ["Text"]),
        ("section", ["Group", "Pane"]),
        ("panel", ["Pane", "Group"]),
        ("document web", ["Document", "Pane"]),
        ("image", ["Image"]),
    ])
    def test_atspi_role_maps_to_expected_uia_candidates(
        self, atspi: str, expected: list[str],
    ) -> None:
        assert uia_worker._uia_candidates_for_role(atspi) == expected

    def test_native_uia_role_passes_through(self) -> None:
        # Roles not in the AT-SPI map (custom or UIA-native) come back as a
        # single-element list with the original capitalisation preserved.
        assert uia_worker._uia_candidates_for_role("CustomBlob") == ["CustomBlob"]
        assert uia_worker._uia_candidates_for_role("DataItem") == ["DataItem"]

    def test_role_match_is_case_insensitive_on_key(self) -> None:
        # `FRAME` and `Frame` should map the same as `frame`.
        assert uia_worker._uia_candidates_for_role("FRAME") == ["Window", "Pane"]
        assert uia_worker._uia_candidates_for_role("Push Button") == ["Button"]

    def test_none_role_returns_none(self) -> None:
        # `None` means "do not filter by role"; the caller must distinguish.
        assert uia_worker._uia_candidates_for_role(None) is None

    def test_empty_string_role_returns_pass_through(self) -> None:
        # Empty string is a degenerate but valid pass-through.
        assert uia_worker._uia_candidates_for_role("") == [""]


# --------------------------- reverse map -------------------------------------


class TestRoleMapReverse:
    """`_atspi_role_for_uia`: UIA -> AT-SPI synonym for tree_dump output."""

    @pytest.mark.parametrize("uia,expected_atspi", [
        ("Window", "frame"),    # `frame` wins over `dialog` (registered first)
        ("Pane", "frame"),      # `frame` wins over `panel`/`section`/`document web`
        ("Button", "push button"),
        ("Edit", "entry"),
        ("CheckBox", "check box"),
        ("ComboBox", "combo box"),
        ("Text", "label"),      # `label` wins over `static`
        ("Image", "image"),
        ("MenuItem", "menu item"),
    ])
    def test_uia_role_translates_to_atspi(
        self, uia: str, expected_atspi: str,
    ) -> None:
        assert uia_worker._atspi_role_for_uia(uia) == expected_atspi

    def test_unknown_uia_role_passes_through(self) -> None:
        # No mapping -> raw UIA string returned (`Custom` widgets survive).
        assert uia_worker._atspi_role_for_uia("DataItem") == "DataItem"

    def test_empty_uia_role_returns_empty(self) -> None:
        assert uia_worker._atspi_role_for_uia("") == ""


# --------------------------- _node_meta emits both --------------------------


class TestNodeMetaEmitsBothRoles:
    def test_tree_dump_emits_both_roles_for_window(self) -> None:
        # UIA `Window` -> scenario-friendly `frame` + raw `Window`.
        elem = _StubElem(uia_role="Window", name="Rocket.Chat")
        meta = uia_worker._node_meta(elem)
        assert meta["role"] == "frame"
        assert meta["uia_control_type"] == "Window"
        assert meta["name"] == "Rocket.Chat"

    def test_tree_dump_emits_both_roles_for_button(self) -> None:
        elem = _StubElem(uia_role="Button", name="Sign In")
        meta = uia_worker._node_meta(elem)
        assert meta["role"] == "push button"
        assert meta["uia_control_type"] == "Button"

    def test_tree_dump_preserves_unknown_role(self) -> None:
        # Custom widget classes have no AT-SPI synonym; `role` falls back to
        # the raw UIA string so authors still see SOMETHING to grep on.
        elem = _StubElem(uia_role="DataItem", name="row-1")
        meta = uia_worker._node_meta(elem)
        assert meta["role"] == "DataItem"
        assert meta["uia_control_type"] == "DataItem"


# --------------------------- _match cross-vocab ------------------------------


def _meta(uia_role: str, name: str = "") -> dict:
    """Shortcut: build the meta dict `_match` consumes."""
    return uia_worker._node_meta(_StubElem(uia_role=uia_role, name=name))


class TestMatchCrossVocabulary:
    def test_atspi_frame_matches_uia_window(self) -> None:
        # The Step 7 regression case: scenario asks `role: frame`, RC main
        # window is reported as `Window` on Windows.
        assert uia_worker._match(_meta("Window"), role="frame",
                                 name=None, name_substr=False) is True

    def test_atspi_frame_matches_uia_pane(self) -> None:
        # Fallback candidate: some Chromium frames report as `Pane`.
        assert uia_worker._match(_meta("Pane"), role="frame",
                                 name=None, name_substr=False) is True

    def test_atspi_frame_does_not_match_button(self) -> None:
        # Negative case: `Button` is NOT in `frame`'s candidate list.
        assert uia_worker._match(_meta("Button"), role="frame",
                                 name=None, name_substr=False) is False

    def test_atspi_push_button_matches_uia_button(self) -> None:
        assert uia_worker._match(_meta("Button"), role="push button",
                                 name=None, name_substr=False) is True

    def test_atspi_entry_matches_uia_edit(self) -> None:
        assert uia_worker._match(_meta("Edit"), role="entry",
                                 name=None, name_substr=False) is True

    def test_atspi_check_box_matches_uia_checkbox(self) -> None:
        assert uia_worker._match(_meta("CheckBox"), role="check box",
                                 name=None, name_substr=False) is True

    def test_native_uia_role_passes_through_to_match(self) -> None:
        # A scenario explicitly targeting `Button` (UIA-native) still works.
        assert uia_worker._match(_meta("Button"), role="Button",
                                 name=None, name_substr=False) is True
        # And does NOT accidentally match other UIA widgets.
        assert uia_worker._match(_meta("Edit"), role="Button",
                                 name=None, name_substr=False) is False

    def test_role_match_is_case_insensitive(self) -> None:
        # Authors that type `FRAME` or `Push Button` still match.
        assert uia_worker._match(_meta("Window"), role="FRAME",
                                 name=None, name_substr=False) is True
        assert uia_worker._match(_meta("Button"), role="Push Button",
                                 name=None, name_substr=False) is True

    def test_name_filter_still_applies_with_translated_role(self) -> None:
        # Role passes (frame -> Window) AND name exact-matches.
        assert uia_worker._match(_meta("Window", "Rocket.Chat"),
                                 role="frame", name="Rocket.Chat",
                                 name_substr=False) is True
        # Role passes but name mismatches -> reject.
        assert uia_worker._match(_meta("Window", "Other"),
                                 role="frame", name="Rocket.Chat",
                                 name_substr=False) is False

    def test_name_substr_works_with_translated_role(self) -> None:
        assert uia_worker._match(_meta("Window", "Rocket.Chat Login"),
                                 role="frame", name="Rocket.Chat",
                                 name_substr=True) is True

    def test_no_role_filter_accepts_any_widget(self) -> None:
        # `role=None` -> role check skipped entirely.
        assert uia_worker._match(_meta("Window"), role=None,
                                 name=None, name_substr=False) is True


# --------------------------- _find_first end-to-end --------------------------


class TestFindFirstWithMappedRoles:
    """End-to-end: walk a mocked tree, find a node by AT-SPI role."""

    def _build_tree(self) -> _StubElem:
        # Mimic the RC main-window shape seen on Windows: top-level Window,
        # then a Pane sub-tree containing buttons / edits.
        login_btn = _StubElem(uia_role="Button", name="Sign In",
                              rect=(10, 10, 110, 40))
        email = _StubElem(uia_role="Edit", name="Email",
                          rect=(10, 50, 210, 70))
        pane = _StubElem(uia_role="Pane", name="Login form",
                         children=[email, login_btn],
                         rect=(0, 0, 400, 600))
        root = _StubElem(uia_role="Window", name="Rocket.Chat",
                         children=[pane],
                         rect=(0, 0, 800, 600))
        return root

    def _patched_find(self, monkeypatch, root: _StubElem):
        # Bypass the real desktop / kick-tree machinery; just walk `root`.
        monkeypatch.setattr(uia_worker, "_kick_uia_tree",
                            lambda *a, **kw: None)
        monkeypatch.setattr(uia_worker, "_find_app", lambda d, f: root)

    def test_find_frame_resolves_to_top_level_window(self, monkeypatch) -> None:
        root = self._build_tree()
        self._patched_find(monkeypatch, root)
        hit = uia_worker._find_first(
            desktop=None, role="frame", name="Rocket.Chat",
            name_substr=False, app_filter="rocket",
        )
        assert hit is not None
        assert hit["role"] == "frame"
        assert hit["uia_control_type"] == "Window"
        assert hit["name"] == "Rocket.Chat"

    def test_find_push_button_resolves_under_pane(self, monkeypatch) -> None:
        root = self._build_tree()
        self._patched_find(monkeypatch, root)
        hit = uia_worker._find_first(
            desktop=None, role="push button", name="Sign In",
            name_substr=False, app_filter="rocket",
        )
        assert hit is not None
        assert hit["role"] == "push button"
        assert hit["uia_control_type"] == "Button"

    def test_find_entry_resolves_to_edit(self, monkeypatch) -> None:
        root = self._build_tree()
        self._patched_find(monkeypatch, root)
        hit = uia_worker._find_first(
            desktop=None, role="entry", name="Email",
            name_substr=False, app_filter="rocket",
        )
        assert hit is not None
        assert hit["uia_control_type"] == "Edit"

    def test_find_native_uia_role_still_works(self, monkeypatch) -> None:
        # Pre-existing scenarios that pass `role: Button` directly continue
        # to work unchanged.
        root = self._build_tree()
        self._patched_find(monkeypatch, root)
        hit = uia_worker._find_first(
            desktop=None, role="Button", name="Sign In",
            name_substr=False, app_filter="rocket",
        )
        assert hit is not None
        assert hit["uia_control_type"] == "Button"

    def test_find_unknown_role_returns_none(self, monkeypatch) -> None:
        root = self._build_tree()
        self._patched_find(monkeypatch, root)
        hit = uia_worker._find_first(
            desktop=None, role="frame", name="Definitely Not Here",
            name_substr=False, app_filter="rocket",
        )
        assert hit is None
