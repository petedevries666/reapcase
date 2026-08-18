import ast
from pathlib import Path

import pytest

from stadium_reaper_bridge.editor.composite import lane_height
from stadium_reaper_bridge.editor.layout import HEADER_WIDTH, RULER_HEIGHT
from stadium_reaper_bridge.editor.model import EditorModel, LANES
from stadium_reaper_bridge.editor.navigation import (
    ViewState, event_list_rows, jump_viewport_left, marker_region_rows,
    visible_lane_layout,
)


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
