from types import SimpleNamespace

from stadium_reaper_bridge.editor.app import ReapcaseEditor
from stadium_reaper_bridge.editor.audio_engine import PlaybackState
from stadium_reaper_bridge.stadium import MusicalPosition
from stadium_reaper_bridge.timing import TimingMap


class Variable:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


def selection_editor():
    return SimpleNamespace(time_selection_start_units=None,
                           time_selection_end_units=None,
                           _time_selection_drag=None,
                           redraw=lambda *_args: None)


def test_time_selection_orders_reverse_drag_and_clears_both_boundaries():
    editor = selection_editor()
    assert ReapcaseEditor.set_time_selection(editor, 900, 300)
    assert ReapcaseEditor.time_selection_range(editor) == (300, 900)
    assert ReapcaseEditor.clear_time_selection(editor)
    assert editor.time_selection_start_units is None
    assert editor.time_selection_end_units is None


def test_extending_regions_is_order_independent():
    first = selection_editor()
    second = selection_editor()
    ReapcaseEditor.set_time_selection(first, 400, 1200)
    ReapcaseEditor.extend_time_selection(first, 100, 400)
    ReapcaseEditor.set_time_selection(second, 100, 400)
    ReapcaseEditor.extend_time_selection(second, 400, 1200)
    assert ReapcaseEditor.time_selection_range(first) == (100, 1200)
    assert ReapcaseEditor.time_selection_range(second) == (100, 1200)


def test_boundary_updates_preserve_opposite_boundary():
    editor = selection_editor()
    ReapcaseEditor.set_time_selection(editor, 100, 900)
    ReapcaseEditor.set_time_selection(editor, 200, editor.time_selection_end_units)
    assert ReapcaseEditor.time_selection_range(editor) == (200, 900)
    ReapcaseEditor.set_time_selection(editor, editor.time_selection_start_units, 800)
    assert ReapcaseEditor.time_selection_range(editor) == (200, 800)


def test_empty_range_does_not_break_active_invariant():
    editor = selection_editor()
    ReapcaseEditor.set_time_selection(editor, 100, 200)
    assert not ReapcaseEditor.set_time_selection(editor, 150, 150)
    assert ReapcaseEditor.time_selection_range(editor) == (100, 200)


def test_space_and_escape_use_canonical_commands():
    source = open("src/stadium_reaper_bridge/editor/app.py", encoding="utf-8").read()
    assert "EditorCommand.PLAY_PAUSE: self.play_pause" in source
    assert "EditorCommand.ESCAPE: self.clear_time_selection" in source


def test_selection_is_editor_state_not_song_serialization():
    source = open("src/stadium_reaper_bridge/editor/app.py", encoding="utf-8").read()
    assert "self.time_selection_start_units" in source
    assert "self.time_selection_end_units" in source
    stadium_source = open("src/stadium_reaper_bridge/stadium.py", encoding="utf-8").read()
    assert "time_selection" not in stadium_source
