"""Tests for ADRE UI automation scaffolding (no ADRE install required)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rotordyn.adre_automate import (
    AdreNotInstalled,
    RecipeIncomplete,
    _manager_row_score,
    _tv_children,
    find_adre_executable,
    load_recipe,
    run_recipe,
    smoke_check,
)

ROOT = Path(__file__).resolve().parents[1]


def test_find_adre_raises_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("ADRE_SXP_EXE", raising=False)
    monkeypatch.delenv("ADRE_EXE", raising=False)
    monkeypatch.setattr("rotordyn.adre_automate._ADRE_DIR_CANDIDATES", (str(tmp_path / "none"),))
    with pytest.raises(AdreNotInstalled):
        find_adre_executable()


def test_find_adre_respects_env(monkeypatch, tmp_path):
    fake = tmp_path / "ADRE Sxp.exe"
    fake.write_bytes(b"mz")
    monkeypatch.setenv("ADRE_SXP_EXE", str(fake))
    assert find_adre_executable() == fake


def test_example_recipe_loads():
    recipe = load_recipe(ROOT / "adre_export_recipe.example.json")
    assert recipe["channels"] == ["BRG1X", "BRG1Y", "Kph 1"]
    assert any(step.get("action") == "placeholder" for step in recipe["steps"])


def test_filled_recipe_has_no_placeholders():
    recipe = load_recipe(ROOT / "adre_export_recipe.json")
    placeholders = [
        step for step in recipe["steps"] if step.get("action") in {"placeholder", "todo"}
    ]
    assert placeholders == []
    actions = [step["action"] for step in recipe["steps"]]
    assert "select_sampling_cards" in actions
    assert "open_database" in actions
    assert "context_plots" in actions
    assert "tree_click" in actions
    assert "export_plots" in actions
    assert "configure_plots" in actions
    assert "open_file_dialog" in actions
    assert "split_static" in actions
    assert actions.index("split_static") < actions.index("configure_plots")
    assert actions.count("export_plots") == 3
    assert actions.count("context_plots") == 2
    assert actions[-1] == "wait_for_csvs"
    assert recipe["steps"][-1]["min_files"] == 3


def test_placeholder_recipe_is_rejected(tmp_path):
    path = tmp_path / "recipe.json"
    path.write_text(
        json.dumps({"steps": [{"action": "placeholder", "message": "fill me"}]}),
        encoding="utf-8",
    )
    with pytest.raises(RecipeIncomplete, match="fill me"):
        run_recipe(app=None, job=None, recipe=load_recipe(path))  # type: ignore[arg-type]


def test_smoke_check_reports_status():
    info = smoke_check()
    assert "status" in info
    assert "recipe" in info


def test_tv_children_walks_when_winforms_hides_kids(monkeypatch):
    """WinForms sets cChildren = I_CHILDRENCALLBACK; pywinauto then returns []."""
    pywinauto = pytest.importorskip("pywinauto")

    class Node:
        def __init__(self, name, nxt=None):
            self.name = name
            self._nxt = nxt

        def next_item(self):
            return self._nxt

        def text(self):
            return self.name

    class Ctrl:
        def send_message(self, msg, wparam, lparam):
            return 1

    class Item:
        tree_ctrl = Ctrl()
        elem = 99

        def expand(self):
            pass

        def children(self):
            return []

    first = Node("3_GE_7HA", Node("1_GE 7FA"))
    monkeypatch.setattr(
        "pywinauto.controls.common_controls._treeview_element",
        lambda handle, ctrl: first,
    )
    kids = _tv_children(Item())
    assert [k.name for k in kids] == ["3_GE_7HA", "1_GE 7FA"]
    assert pywinauto is not None


def test_popup_search_includes_adre_titled_win32_menu():
    from rotordyn.adre_automate import _is_popup_search_window

    assert _is_popup_search_window("ADRE® Sxp", class_name="#32768") is True
    assert _is_popup_search_window("", class_name="") is True
    assert _is_popup_search_window("Context", friendly="menu") is True
    assert _is_popup_search_window("ADRE® Sxp", class_name="WindowsForms10.Window.8.app") is False
    assert _is_popup_search_window("HV - Plot Session", auto_id="PlotSessionWindow") is False
    assert _is_popup_search_window("adre_export_recipe.json - RotorDyn - Cursor") is False


def test_manager_row_score_prefers_name_and_path():
    assert _manager_row_score("3_GE_7HA", "3_GE_7HA") > _manager_row_score(
        "1_GE 7FA", "3_GE_7HA"
    )
    vault = r"D:\Downloads\RotorDyn\imports\imp_x\working\3_GE_7HA"
    assert _manager_row_score(vault, "3_GE_7HA", vault) > _manager_row_score(
        "3_GE_7HA", "3_GE_7HA"
    )
    assert _manager_row_score("Open", "3_GE_7HA") == 0


def test_flexgrid_cell_title_parse():
    from rotordyn.adre_automate import _FLEX_CELL

    match = _FLEX_CELL.fullmatch("Row 4 Column 3")
    assert match is not None
    assert match.groups() == ("4", "3")
    assert _FLEX_CELL.fullmatch("Row 0") is None
    from rotordyn.adre_automate import _flex_cells_with_value, _is_sync_waveform_label

    cells = {
        (0, 3): ("Variable", None),
        (1, 3): ("Sync Waveform", object()),
        (2, 3): ("Sync Waveform", object()),
        (3, 3): ("Async Waveform", object()),
        (1, 8): ("Direct", object()),
    }
    assert _flex_cells_with_value(cells, "Sync Waveform") == [(1, 3), (2, 3)]
    assert _flex_cells_with_value(cells, "Async Waveform") == [(3, 3)]
    assert _is_sync_waveform_label("Sync Waveform")
    assert _is_sync_waveform_label("#2 Sync Waveform")
    assert not _is_sync_waveform_label("Async Waveform")
    from rotordyn.adre_automate import _combo_item_is_choice

    assert _combo_item_is_choice("Async Waveform", "Async Waveform")
    assert not _combo_item_is_choice("#2 Async Waveform", "Async Waveform")
    assert not _combo_item_is_choice("#3 Async Waveform", "Async Waveform")
    assert not _combo_item_is_choice("Sync Waveform", "Async Waveform")
    from rotordyn.adre_automate import _variable_col_span

    class SpanRect:
        def __init__(self, left, right):
            self.left, self.top, self.right, self.bottom = left, 0, right, 20

    class SpanCtrl:
        def __init__(self, left, right):
            self._rect = SpanRect(left, right)

        def rectangle(self):
            return self._rect

    span = _variable_col_span(
        {
            (1, 3): ("Sync Waveform", SpanCtrl(10, 900)),
            (2, 3): ("Sync Waveform", SpanCtrl(240, 380)),
        },
        3,
    )
    assert span == (240, 380)
    from rotordyn.adre_automate import _text_is_loading_plot_groups

    assert _text_is_loading_plot_groups("Loading plot groups...")
    assert _text_is_loading_plot_groups("Loading Plot Groups")
    assert not _text_is_loading_plot_groups("Timebase Plot Group Configuration")
    from rotordyn.adre_automate import _config_ok_coords

    class Rect:
        right, bottom = 1000, 800

    x, y = _config_ok_coords(Rect())
    assert x < 1000 - 200
    assert y == 782
    from rotordyn.adre_automate import _config_splitter_target_y, _splitter_score

    assert 990 < _config_splitter_target_y(100, 1100) < 1060

    class SplitRect:
        def __init__(self, left, top, right, bottom):
            self.left, self.top, self.right, self.bottom = left, top, right, bottom

    class SplitCtrl:
        def __init__(self, left, top, right, bottom, auto_id="", kind="Pane"):
            self._rect = SplitRect(left, top, right, bottom)
            self.element_info = type("E", (), {"automation_id": auto_id})()
            self._kind = kind

        def rectangle(self):
            return self._rect

        def friendly_class_name(self):
            return self._kind

        def window_text(self):
            return ""

    host = SplitRect(0, 0, 1000, 800)
    bar = SplitCtrl(10, 360, 990, 368, auto_id="splitContainer1", kind="Thumb")
    title = SplitCtrl(0, 0, 1000, 30, auto_id="", kind="TitleBar")
    assert _splitter_score(bar, host) > _splitter_score(title, host)
    assert _splitter_score(bar, host) > 2.5
    from rotordyn.adre_automate import _config_splitter_y, _union_rect

    class GridRect:
        def __init__(self, left, top, right, bottom):
            self.left, self.top, self.right, self.bottom = left, top, right, bottom

    box = _union_rect([GridRect(10, 40, 900, 420), GridRect(10, 80, 880, 400)])
    assert box.top == 40 and box.bottom == 420
    host = GridRect(0, 0, 1000, 800)

    class FakeDlg:
        def descendants(self):
            return []

    # No grid/options → seam unknown
    assert _config_splitter_y(FakeDlg(), host) is None
    from rotordyn.adre_automate import _channel_grid_cells

    mixed = {
        (0, 0): ("Name", None),
        (0, 3): ("Variable", None),
        (1, 0): ("39V-1A", None),
        (1, 3): ("Sync Waveform", None),
        (2, 0): ("Scaling", None),
        (2, 1): ("Auto", None),
        (8, 12): ("Direct", None),
    }
    channel_only = _channel_grid_cells(mixed)
    assert (0, 3) in channel_only and (8, 12) in channel_only
    assert (2, 1) not in channel_only
    assert (0, 0) not in channel_only
    from rotordyn.adre_automate import _rect_in_box

    class Box:
        def __init__(self, top, bottom):
            self.top, self.bottom = top, bottom

    assert _rect_in_box(Box(40, 58), Box(30, 200))
    assert not _rect_in_box(Box(190, 210), Box(30, 200))
    assert not _rect_in_box(Box(188, 198), Box(30, 200))
    from rotordyn.adre_automate import _plot_variable_column, _variable_column_cells

    assert _plot_variable_column(cells) == 3
    rows = _variable_column_cells(
        {
            (0, 3): ("Variable", None),
            (5, 3): ("Sync Waveform", object()),
            (6, 3): ("#2 Sync Waveform", object()),
            (7, 3): ("Async Waveform", object()),
            (5, 8): ("Direct", object()),
        },
        3,
    )
    assert [r[0] for r in rows] == [5, 6]
    assert [r[2] for r in rows] == ["Sync Waveform", "#2 Sync Waveform"]

    assert _plot_variable_column(cells) == 3
    name = "3_GE_7HA"
    path = r"D:\Downloads\RotorDyn\imports\imp_old\working\3_GE_7HA\\"
    assert _manager_row_score(name, "3_GE_7HA", path) >= 20
    assert _manager_row_score(path, "3_GE_7HA", path) >= 15


def test_set_clipboard_roundtrip(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("rotordyn.adre_automate.subprocess.run", fake_run)
    from rotordyn.adre_automate import _set_clipboard

    path = r"D:\Downloads\RotorDyn\imports\imp_20260909_160930_bb59d499\exports\export_static.csv"
    _set_clipboard(path)
    assert path in captured["args"][-1]


def test_sampling_card_nodes_collapses_cards(monkeypatch):
    from rotordyn.adre_automate import _sampling_card_nodes, _tv_text

    collapsed = []

    class Node:
        def __init__(self, name, kids=None):
            self._name = name
            self._kids = kids or []

        def text(self):
            return self._name

    card1 = Node("Dynamic Sampling Card", [Node("39V-4A")])
    card2 = Node("Dynamic Sampling Card", [Node("39V-5A")])
    inst = Node("ADRE 408", [card1, card2, Node("Keyphasor Card")])

    def fake_children(item):
        if hasattr(item, "_kids"):
            return item._kids
        return []

    monkeypatch.setattr("rotordyn.adre_automate._tv_children", fake_children)
    monkeypatch.setattr(
        "rotordyn.adre_automate._tv_collapse", lambda item: collapsed.append(item)
    )
    cards = _sampling_card_nodes(inst, "Dynamic Sampling Card")
    assert len(cards) == 2
    assert [_tv_text(c) for c in cards] == ["Dynamic Sampling Card"] * 2
    assert collapsed == cards


def test_select_collapsed_cards_clicks_first_then_shift_last(monkeypatch):
    from rotordyn.adre_automate import _select_collapsed_cards

    clicks = []

    class Item:
        def __init__(self, i):
            self.i = i
            self.selected = False

        def is_selected(self):
            return self.selected

    def fake_click(item, pressed="", button="left"):
        clicks.append((item.i, pressed, button))
        item.selected = True

    monkeypatch.setattr("rotordyn.adre_automate._tv_collapse", lambda _item: None)
    monkeypatch.setattr("rotordyn.adre_automate._tv_click_label", fake_click)
    monkeypatch.setattr("rotordyn.adre_automate._key_down", lambda _vk: None)
    monkeypatch.setattr("rotordyn.adre_automate._key_up", lambda _vk: None)
    a, b, c = Item(0), Item(1), Item(2)
    # After first+shift, pretend only last is selected so Control-click fills in.
    def fake_click_partial(item, pressed="", button="left"):
        clicks.append((item.i, pressed, button))
        if not clicks or item.i == 2 or len(clicks) > 2:
            item.selected = True

    monkeypatch.setattr("rotordyn.adre_automate._tv_click_label", fake_click_partial)
    a.selected = False
    b.selected = False
    c.selected = False
    _select_collapsed_cards([a, b, c])
    assert clicks[0][0] == 0
    assert any(i == 2 for i, _p, _b in clicks)


def test_tv_icon_coords_are_left_of_the_name():
    from rotordyn.adre_automate import _tv_icon_coords

    class Rect:
        def __init__(self, left, top, right, bottom):
            self.left, self.top, self.right, self.bottom = left, top, right, bottom

    class Item:
        def client_rect(self, text_area_rect=True):
            if text_area_rect:
                return Rect(56, 10, 180, 26)
            return Rect(0, 10, 180, 26)

    x, y = _tv_icon_coords(Item())
    assert 16 <= x < 56
    assert y == 18


def test_window_spec_keeps_objects_that_have_child_window():
    from rotordyn.adre_automate import _window_spec

    class Spec:
        def child_window(self, **_kwargs):
            return self

    spec = Spec()
    assert _window_spec(spec) is spec
    assert _window_spec(None) is None


def test_by_auto_id_uses_child_window():
    from rotordyn.adre_automate import _by_auto_id

    class Spec:
        def __init__(self):
            self.kwargs = None

        def children(self):
            return []

        def descendants(self):
            return []

        def child_window(self, **kwargs):
            self.kwargs = kwargs
            return self

        def exists(self, timeout=0):
            return True

    spec = Spec()
    assert _by_auto_id(spec, "m_c1PlotGroupToolBar") is spec
    assert spec.kwargs == {"auto_id": "m_c1PlotGroupToolBar"}


def test_titlebar_button_finds_close():
    from rotordyn.adre_automate import _titlebar_button

    class Ctrl:
        def __init__(self, title="", kind="Pane", kids=None):
            self._title = title
            self._kind = kind
            self._kids = kids or []

        def window_text(self):
            return self._title

        def friendly_class_name(self):
            return self._kind

        def children(self):
            return self._kids

    close = Ctrl("Close", "Button")
    bar = Ctrl("", "TitleBar", [close])
    win = Ctrl("HV - Plot Session", "Dialog", [bar])
    assert _titlebar_button(win, "Close") is close


def test_is_save_dialog_matches_export_plots_title():
    from rotordyn.adre_automate import _is_save_dialog

    class Win:
        def __init__(self, title):
            self._title = title

        def window_text(self):
            return self._title

    assert _is_save_dialog(Win("Export Plots"))
    assert _is_save_dialog(Win("Save As"))
    assert not _is_save_dialog(Win("ADRE® Sxp"))
    assert not _is_save_dialog(Win("HV - Plot Session"))


def test_plot_config_dialog_title_match():
    from rotordyn.adre_automate import (
        _config_dialog_score,
        _is_plot_config_dialog_title,
        _item_text_is_configure,
    )

    assert _is_plot_config_dialog_title("Timebase Plot Group Configuration")
    assert _is_plot_config_dialog_title("Tabular List Plot Group Configuration")
    assert not _is_plot_config_dialog_title("HV - Plot Session")
    assert not _is_plot_config_dialog_title("Export Plots")
    assert _config_dialog_score("Timebase Plot Group Configuration", "Timebase") > (
        _config_dialog_score("Tabular List Plot Group Configuration")
    )
    assert _config_dialog_score("Tabular List Plot Group Configuration", "Timebase") == 0
    assert _item_text_is_configure("Configure")
    assert _item_text_is_configure("&Configure")
    assert _item_text_is_configure("Configure...")
    assert not _item_text_is_configure("Configuration Hierarchy")


def test_plot_click_points_stay_inside_waveform():
    from rotordyn.adre_automate import _plot_click_points

    class Rect:
        left, top, right, bottom = 100, 80, 900, 680

    class Group:
        def rectangle(self):
            return Rect()

    points = _plot_click_points(Group())
    assert len(points) >= 3
    for x, y in points:
        assert 140 <= x <= 860
        assert 130 <= y <= 630


def test_in_hv_top_toolbar_skips_replay_row():
    from rotordyn.adre_automate import _in_hv_top_toolbar

    class Ctrl:
        def __init__(self, title="", kids=None):
            self._title = title
            self._kids = kids or []
            self._parent = None
            for child in self._kids:
                child._parent = self

        def window_text(self):
            return self._title

        def children(self):
            return self._kids

        def parent(self):
            if self._parent is None:
                raise RuntimeError("top")
            return self._parent

    top_export = Ctrl("Export Plots")
    Ctrl("", [Ctrl("Print"), top_export, Ctrl("Create Replay Database")])
    assert _in_hv_top_toolbar(top_export)

    plot_export = Ctrl("Export Plots")
    Ctrl("", [plot_export, Ctrl("Page 1")])
    assert not _in_hv_top_toolbar(plot_export)
