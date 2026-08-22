import ast
from pathlib import Path

import pytest

from stadium_reaper_bridge.editor.composite import lane_height
from stadium_reaper_bridge.editor.layout import HEADER_WIDTH, RULER_HEIGHT
from stadium_reaper_bridge.editor.model import EditorModel, LANES
from stadium_reaper_bridge.editor.navigation import (
    ViewState, default_lane_visibility, event_list_rows, jump_viewport_left, marker_region_rows,
    move_visible_lane, normalized_lane_order, visible_lane_layout,
)
from stadium_reaper_bridge.editor.style import REAPCASE_TREEVIEW_STYLE


def perfect_picture():
    return EditorModel.open(Path("tests/fixtures/perfect_picture_336.json"))


def test_view_switch_preserves_independent_editor_state():
    state = ViewState()
    selection, playhead, zoom, scroll = {4, 9}, 1920, 90.0, .42
    state.switch("event_list")
    state.switch("timeline")
    assert state.current_view == "timeline"
    assert (selection, playhead, zoom, scroll) == ({4, 9}, 1920, 90.0, .42)
    with pytest.raises(ValueError):
        state.switch("mixer")


@pytest.mark.parametrize("hidden", [(), ("VIDEO",), ("VIDEO", "LIGHTS", "MIDI / OTHER"), LANES[1:]])
def test_visible_lane_layout_is_contiguous_and_audio_follows(hidden):
    visible = {lane: lane not in hidden for lane in LANES}
    layout = visible_lane_layout(LANES, visible)
    cursor = RULER_HEIGHT
    for lane in layout.lanes:
        assert layout.tops[lane] == cursor
        cursor += lane_height(lane)
    assert layout.event_bottom == cursor
    assert layout.audio_top == cursor


def test_each_song_load_uses_the_default_working_lane_visibility():
    available = LANES + ("AUDIO",)
    expected = {
        "STRUCTURE": True, "STADIUM": True, "SECOND HELIX": True,
        "VIDEO": False, "LIGHTS": False, "MIDI / OTHER": False,
        "SEQCLICK": False, "SEQ INSTRUCTIONS": False, "AUDIO": True,
    }
    song_a = default_lane_visibility(available)
    assert song_a == expected
    song_a["LIGHTS"] = True
    assert default_lane_visibility(available) == expected


def test_lane_reorder_swaps_adjacent_visible_lanes_deterministically():
    order = ["STRUCTURE", "STADIUM", "SECOND HELIX", "VIDEO"]
    visible = {lane: True for lane in order}
    order = move_visible_lane(order, visible, "VIDEO", -1)
    assert order == ["STRUCTURE", "STADIUM", "VIDEO", "SECOND HELIX"]
    order = move_visible_lane(order, visible, "VIDEO", -1)
    assert order == ["STRUCTURE", "VIDEO", "STADIUM", "SECOND HELIX"]


def test_lane_reorder_skips_hidden_lane_without_losing_it():
    order = ["STRUCTURE", "STADIUM", "SECOND HELIX", "VIDEO", "LIGHTS"]
    visible = {lane: lane != "SECOND HELIX" for lane in order}
    moved = move_visible_lane(order, visible, "VIDEO", -1)
    assert moved == ["STRUCTURE", "VIDEO", "SECOND HELIX", "STADIUM", "LIGHTS"]
    assert [lane for lane in moved if not visible[lane]] == ["SECOND HELIX"]


def test_normalized_lane_order_repairs_stale_preferences():
    assert normalized_lane_order(["VIDEO", "VIDEO", "OLD"], LANES) == [
        "VIDEO", *[lane for lane in LANES if lane != "VIDEO"]]


def test_event_list_is_a_semantic_projection_and_does_not_filter_hidden_lanes():
    model = perfect_picture()
    rows = event_list_rows(model)
    assert len(rows) == len(model.timeline.events)
    assert [row.units for row in rows] == sorted(row.units for row in rows)
    assert rows[0].position == "001-01.001"
    assert rows[0].lane == "STRUCTURE"
    assert any(row.kind == "PRESETSNAP" and "SNAP" in row.name for row in rows)
    assert any(row.lane == "SECOND HELIX" and row.name.startswith("BASS") for row in rows)
    assert all(";" not in row.name for row in rows)


def test_marker_manager_only_extracts_structural_navigation_rows():
    rows = marker_region_rows(perfect_picture())
    assert rows
    assert {row.kind for row in rows} <= {"START", "END", "MARKER", "PAUSE", "CYCLE"}
    assert all(row.position.count("-") == 1 for row in rows)


def test_jump_scroll_uses_units_and_first_third_lookahead():
    units, ppqn, scale, viewport = 16_000, 960, 90.0, 1_000
    left = jump_viewport_left(units, ppqn, scale, viewport)
    target_x = HEADER_WIDTH + units / ppqn * scale
    assert target_x - left == pytest.approx(viewport * .28)


def test_visual_redraw_and_transport_do_not_rebuild_navigation_treeviews():
    """Keep the 30 FPS transport path independent from structural projections."""
    source = Path("src/stadium_reaper_bridge/editor/app.py").read_text(encoding="utf-8")
    editor = next(node for node in ast.parse(source).body
                  if isinstance(node, ast.ClassDef) and node.name == "ReapcaseEditor")
    methods = {node.name: node for node in editor.body if isinstance(node, ast.FunctionDef)}
    for method_name in ("redraw", "_transport_tick"):
        called = {node.func.attr for node in ast.walk(methods[method_name])
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        assert "_refresh_navigation" not in called
        assert "_refresh_event_list" not in called


def test_menu_order_and_shared_manager_treeview_style_are_wired_in_app():
    source = Path("src/stadium_reaper_bridge/editor/app.py").read_text(encoding="utf-8")
    cascade_positions = [source.index(f'bar.add_cascade(label="{label}"')
                         for label in ("File", "Edit", "Select", "View", "Show")]
    assert cascade_positions == sorted(cascade_positions)
    assert source.count("style=REAPCASE_TREEVIEW_STYLE") == 2
    assert REAPCASE_TREEVIEW_STYLE == "Reapcase.Treeview"
    for handler in ("self.select_all", "self.select_after", "self.select_lane", "self.shift_dialog",
                    "self.new_show", "self.open_show", "self.save_show", "self.add_show_song",
                    "self.remove_show_song", "self.relocate_show_song", "self.preflight_show",
                    "self.refresh_show", "self.midi_settings"):
        assert handler in source
