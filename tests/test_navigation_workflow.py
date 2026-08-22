import json
from pathlib import Path
from types import SimpleNamespace

from stadium_reaper_bridge.editor.app import ReapcaseEditor
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.navigation import adjacent_structure_region_index
from stadium_reaper_bridge.editor.preferences import RecentFiles
from stadium_reaper_bridge.editor.structure import is_pause_marker


def test_recent_files_are_bounded_deduplicated_named_and_independent(tmp_path):
    preferences = tmp_path / "config" / "ui.json"
    recent = RecentFiles(preferences)
    paths = []
    for number in range(12):
        path = tmp_path / f"{number}.json"
        path.write_text(json.dumps({"name": f"SONG {number}", "flags": []}))
        paths.append(path)
        recent.add(path, f"SONG {number}")
    assert len(recent.entries) == 10
    assert recent.entries[0].display == "SONG 11 — 11.json"
    recent.add(paths[5], "SONG 5")
    assert len(recent.entries) == 10
    assert recent.entries[0].path == str(paths[5].resolve())
    assert len([entry for entry in recent.entries if entry.path == str(paths[5].resolve())]) == 1
    assert RecentFiles(preferences).entries == recent.entries
    assert "recent_files" not in json.loads(paths[5].read_text())


def test_recent_missing_file_is_removed_without_starting_loader(tmp_path):
    path = tmp_path / "gone.json"
    recent = RecentFiles(tmp_path / "ui.json")
    path.write_text("{}")
    recent.add(path, "GONE")
    path.unlink()
    messages = []
    editor = SimpleNamespace(recent_files=recent, loading=False)
    import stadium_reaper_bridge.editor.app as app
    old = app.messagebox.showerror
    app.messagebox.showerror = lambda *args, **kwargs: messages.append(args)
    try:
        assert ReapcaseEditor.open_recent(editor, path) is False
    finally:
        app.messagebox.showerror = old
    assert messages and recent.entries == []


def test_structure_region_navigation_excludes_pauses_and_cycles_and_clamps():
    model = EditorModel.open(Path("tests/fixtures/clocksick_453.json"))
    ordinary = [(model._units(event.position), index) for index, event in
                enumerate(model.timeline.events)
                if event.source.type == "MARKER" and not is_pause_marker(event)]
    ordinary.sort()
    first_units, first = ordinary[0]
    last_units, last = ordinary[-1]
    assert adjacent_structure_region_index(model, first_units, -1) == first
    assert adjacent_structure_region_index(model, last_units, 1) == last
    assert adjacent_structure_region_index(model, first_units, 1) == ordinary[1][1]
    types = {model.timeline.events[index].source.type for _, index in ordinary}
    assert types == {"MARKER"}


def test_shared_jump_updates_cursor_seeks_selects_and_can_defer_reveal(monkeypatch):
    model = EditorModel.open(Path("tests/fixtures/perfect_picture_336.json"))
    seeks, redraws = [], []
    editor = SimpleNamespace(
        model=model, audio_engine=SimpleNamespace(seek=seeks.append),
        transport_position=SimpleNamespace(set=lambda _value: None),
        _follow_suspended_until=0, redraw=lambda: redraws.append(True))
    editor.seek_units = lambda units: ReapcaseEditor.seek_units(editor, units)
    ReapcaseEditor.jump_to_units(editor, 960, select_index=1, reveal=False)
    assert model._units(model.cursor) == 960
    assert model.selected == {1}
    assert seeks == [model.tempo_map.units_to_seconds(960)]
    assert redraws == [True]


class ShortcutWidget:
    def __init__(self, widget_class, toplevel):
        self.widget_class, self.toplevel = widget_class, toplevel

    def winfo_class(self):
        return self.widget_class

    def winfo_toplevel(self):
        return self.toplevel


def test_global_workflow_shortcuts_work_outside_canvas_and_in_event_list():
    calls = []
    editor = SimpleNamespace()
    editor._global_editor_shortcut = lambda event, command, *args, **kwargs: (
        ReapcaseEditor._global_editor_shortcut(editor, event, command, *args, **kwargs))
    for widget_class in ("TFrame", "TButton", "Treeview"):
        event = SimpleNamespace(widget=ShortcutWidget(widget_class, editor))
        assert editor._global_editor_shortcut(event, lambda: calls.append(widget_class)) == "break"
    assert calls == ["TFrame", "TButton", "Treeview"]


def test_global_workflow_shortcuts_ignore_text_inputs_and_dialogs():
    calls = []
    editor = SimpleNamespace()
    for widget_class in ("Entry", "Text", "TSpinbox", "TCombobox"):
        event = SimpleNamespace(widget=ShortcutWidget(widget_class, editor))
        assert ReapcaseEditor._global_editor_shortcut(
            editor, event, lambda: calls.append(widget_class)) is None
    dialog_event = SimpleNamespace(widget=ShortcutWidget("TButton", object()))
    assert ReapcaseEditor._global_editor_shortcut(
        editor, dialog_event, lambda: calls.append("dialog")) is None
    assert calls == []


def test_event_list_navigation_preserves_complete_multi_selection():
    class Tree:
        def selection(self): return ("2", "5")

    navigations = []
    editor = SimpleNamespace(
        model=SimpleNamespace(selected=set()), event_tree=Tree(),
        _event_rows={"2": SimpleNamespace(units=240), "5": SimpleNamespace(units=960)},
        jump_to_units=lambda units, **kwargs: navigations.append((units, kwargs)),
        _refresh_inspector=lambda: None)
    ReapcaseEditor._event_list_selected(editor)
    assert editor.model.selected == {2, 5}
    assert navigations == [(240, {"reveal": False})]
