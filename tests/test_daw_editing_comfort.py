from pathlib import Path

from stadium_reaper_bridge.editor.inspector import inspector_projection, semantic_tooltip
from stadium_reaper_bridge.editor.layout import fit_range_scale, zoom_about_cursor
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.navigation import (
    adjacent_event_index, adjacent_marker_index, focused_lane_visibility,
)


FIXTURE = Path(__file__).parent / "fixtures" / "perfect_picture_336.json"


def model():
    return EditorModel.open(FIXTURE)


def test_inspector_uses_semantic_edit_descriptor_and_summarizes_multi_selection():
    editor = model()
    stadium = next(i for i, event in enumerate(editor.timeline.events)
                   if event.source.type == "PRESETSNAP")
    single = inspector_projection(editor, {stadium})
    assert single.heading == "STADIUM SNAPSHOT"
    assert any(label == "Snapshot" for label, _ in single.fields)
    assert "PRESETSNAP" not in semantic_tooltip(editor, stadium)

    other = next(i for i, event in enumerate(editor.timeline.events)
                 if editor.lane(event) == "SECOND HELIX")
    multiple = inspector_projection(editor, {stadium, other})
    assert multiple.count == 2
    assert multiple.heading == "2 events selected"
    assert "STADIUM" in dict(multiple.fields)["Lanes"]


def test_central_duplicate_repeats_offset_and_undoes_each_transaction():
    editor = model()
    candidates = [i for i, event in enumerate(editor.timeline.events)
                  if event.source.type == "PRESETSNAP"][:3]
    editor.selected = set(candidates)
    original = [editor._units(editor.timeline.events[i].position) for i in candidates]
    offset = editor.song.ppqn * 4 * 8
    assert editor.duplicate_selected(offset) == 3
    first = [editor._units(editor.timeline.events[i].position) for i in sorted(editor.selected)]
    assert first == [value + offset for value in original]
    assert editor.duplicate_selected() == 3
    second = [editor._units(editor.timeline.events[i].position) for i in sorted(editor.selected)]
    assert second == [value + offset for value in first]
    assert editor.undo() and editor.undo()


def test_region_duplicate_excludes_structure_and_preserves_internal_units():
    editor = model()
    eligible = next((i for i, event in enumerate(editor.timeline.events)
                     if editor.lane(event) != "STRUCTURE"), None)
    source = editor.timeline.events[eligible]
    start = editor._units(source.position)
    count = editor.duplicate_region(start, start + 1, start + editor.song.ppqn * 8)
    assert count == 1
    duplicate = editor.timeline.events[next(iter(editor.selected))]
    assert editor.lane(duplicate) == editor.lane(source)
    assert editor._units(duplicate.position) == start + editor.song.ppqn * 8


def test_focus_navigation_and_zoom_are_presentation_only():
    normal = {"STRUCTURE": True, "STADIUM": True, "LIGHTS": True, "VIDEO": True}
    assert focused_lane_visibility(normal, "LIGHTS") == {
        "STRUCTURE": True, "STADIUM": False, "LIGHTS": True, "VIDEO": False}
    assert normal["VIDEO"]  # input preference was not mutated

    editor = model()
    assert adjacent_event_index(editor, -1, 1) is not None
    assert adjacent_marker_index(editor, -1, 1) is not None
    zoom = zoom_about_cursor(90, 180, 300, 400)
    old_beat = (300 + 400 - 140) / 90
    new_beat = (zoom.scroll_x + 400 - 140) / zoom.pixels_per_beat
    assert abs(old_beat - new_beat) < 1e-9
    assert 1 <= fit_range_scale(100, 100, editor.song.ppqn, 1000) <= 360
