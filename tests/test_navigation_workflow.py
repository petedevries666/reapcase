import ast
import json
from pathlib import Path
from types import SimpleNamespace

from stadium_reaper_bridge.editor.app import MARKER_MANAGER_COLUMNS, ReapcaseEditor
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.navigation import (
    adjacent_structure_region_index, marker_region_rows, structure_region_indices)
from stadium_reaper_bridge.editor.preferences import RecentFiles
from stadium_reaper_bridge.editor.structure import is_pause_marker
from stadium_reaper_bridge.editor.style import LANE_PALETTE, lane_colors


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


def test_named_end_is_not_a_navigable_structure_region(tmp_path):
    song = tmp_path / "named-end.json"
    song.write_text(json.dumps({
        "name": "Named End", "ppqn": 240, "params": None, "tracks": [],
        "flags": [
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "005-01.001|MARKER;VERSE;7;Off;Off;Off;false;A;B;C",
            "009-01.001|MARKER;END;7;Off;Off;Off;false;A;B;C",
            "013-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ]}))
    model = EditorModel.open(song)
    verse, named_end = 1, 2

    assert structure_region_indices(model) == (verse,)
    assert adjacent_structure_region_index(
        model, model._units(model.timeline.events[verse].position), 1) == verse
    assert adjacent_structure_region_index(
        model, model._units(model.timeline.events[named_end].position), -1) == verse


def test_marker_manager_projects_structure_and_canonical_looper_regions(tmp_path):
    song = tmp_path / "semantic-regions.json"
    song.write_text(json.dumps({
        "name": "Semantic Regions", "ppqn": 240, "params": None, "tracks": [],
        "flags": [
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "001-01.001|MARKER;INTRO;7;Off;Off;Off;false;A;B;C",
            "003-01.001|LOOPER;RECORD;1;Record",
            "005-01.001|LOOPER;STOP;1;Stop",
            "007-01.001|MIDI_CC;BASS PLAY;4;CC;3;61;127",
            "009-01.001|MIDI_CC;BASS STOP;4;CC;3;61;0",
            "013-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ]}))
    rows = marker_region_rows(EditorModel.open(song))
    regions = {(row.kind, row.name): row for row in rows}

    assert any(row.lane == "STRUCTURE" for row in rows)
    assert regions[("LOOPER REGION", "RECORD")].lane == "STADIUM"
    assert regions[("LOOPER REGION", "PLAY")].lane == "SECOND HELIX"
    for lane in ("STRUCTURE", "STADIUM", "SECOND HELIX"):
        assert lane_colors(lane) is LANE_PALETTE[lane]


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


def test_timeline_arrow_shortcuts_delegate_only_from_canvas():
    """The routing layer invokes the supplied canonical command, not its own algorithm."""
    calls = []
    canvas = ShortcutWidget("Canvas", None)
    editor = SimpleNamespace(canvas=canvas)
    event = SimpleNamespace(widget=canvas)
    assert ReapcaseEditor._editor_shortcut(
        editor, event, lambda direction: calls.append(direction), 1) == "break"
    for widget_class in ("Entry", "Text", "TSpinbox", "TCombobox", "Treeview"):
        event = SimpleNamespace(widget=ShortcutWidget(widget_class, editor))
        assert ReapcaseEditor._editor_shortcut(
            editor, event, lambda direction: calls.append(direction), -1) is None
    dialog_event = SimpleNamespace(widget=ShortcutWidget("Canvas", object()))
    assert ReapcaseEditor._editor_shortcut(
        editor, dialog_event, lambda direction: calls.append(direction), -1) is None
    assert calls == [1]


def test_arrow_and_file_bindings_use_existing_editor_commands():
    source = Path("src/stadium_reaper_bridge/editor/app.py").read_text(encoding="utf-8")
    expected_bindings = {
        'self.bind_all("<Left>", lambda e: self._editor_shortcut(e, self.navigate_region, -1))',
        'self.bind_all("<Right>", lambda e: self._editor_shortcut(e, self.navigate_region, 1))',
        'self.bind_all("<Up>", lambda e: self._editor_shortcut(e, self.zoom_step, 1.25))',
        'self.bind_all("<Down>", lambda e: self._editor_shortcut(e, self.zoom_step, 1 / 1.25))',
        'self.bind_all("<Control-o>", lambda e: self._global_editor_shortcut(e, self.open_json))',
        'self.bind_all("<Control-s>", lambda e: self._global_editor_shortcut(e, self.save))',
    }
    assert expected_bindings <= {line.strip() for line in source.splitlines()}
    assert 'self.bind_all("<Control-Shift-Key-S>",' in source

    # Guard against an arrow callback growing a second navigation/zoom path.
    tree = ast.parse(source)
    editor = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "ReapcaseEditor")
    build_menu = next(node for node in editor.body
                      if isinstance(node, ast.FunctionDef) and node.name == "_build_menu")
    arrow_bindings = [node for node in ast.walk(build_menu) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Attribute)
                      and node.func.attr == "bind_all" and node.args
                      and isinstance(node.args[0], ast.Constant)
                      and node.args[0].value in {"<Left>", "<Right>", "<Up>", "<Down>"}]
    assert len(arrow_bindings) == 4
    assert all(isinstance(binding.args[1], ast.Lambda) for binding in arrow_bindings)


def test_file_menu_displays_standard_accelerators():
    source = Path("src/stadium_reaper_bridge/editor/app.py").read_text(encoding="utf-8")
    assert 'label="Open...", command=self.open_json, accelerator="Ctrl+O"' in source
    assert 'label="Save", command=self.save, accelerator="Ctrl+S"' in source
    assert 'accelerator="Ctrl+Shift+S"' in source


def test_marker_manager_toggle_reuses_one_docked_panel():
    class Variable:
        value = False
        def get(self): return self.value
        def set(self, value): self.value = value

    calls = []
    editor = SimpleNamespace(marker_manager_visible=Variable(),
                             apply_sidebar_visibility=lambda: calls.append("layout"))
    ReapcaseEditor.toggle_marker_manager(editor)
    assert editor.marker_manager_visible.get() is True
    ReapcaseEditor.toggle_marker_manager(editor)
    assert editor.marker_manager_visible.get() is False
    assert calls == ["layout", "layout"]
    assert not hasattr(editor, "_manager_windows")  # no popup or duplicate is created


def test_marker_manager_columns_are_compact_and_semantic_colors_are_centralized():
    assert MARKER_MANAGER_COLUMNS == ("kind", "name")
    assert "position" not in MARKER_MANAGER_COLUMNS
    assert "end" not in MARKER_MANAGER_COLUMNS
    for lane in LANE_PALETTE:
        palette = lane_colors(lane)
        assert palette.background_highlight.startswith("#")
        assert palette.text.startswith("#")


def test_sidebar_visibility_states_and_stacking():
    class Variable:
        def __init__(self, value): self.value = value
        def get(self): return self.value

    class Widget:
        def __init__(self): self.visible = False; self.rows = {}
        def grid(self, **_kwargs): self.visible = True
        def grid_forget(self): self.visible = False
        def rowconfigure(self, row, **kwargs): self.rows[row] = kwargs
        def columnconfigure(self, *_args, **_kwargs): pass

    for inspector, manager, expected_sidebar in (
            (True, False, True), (False, True, True), (True, True, True),
            (False, False, False)):
        sidebar, inspector_panel, manager_panel = Widget(), Widget(), Widget()
        editor = SimpleNamespace(
            inspector_visible=Variable(inspector), marker_manager_visible=Variable(manager),
            right_sidebar=sidebar, main_content=Widget(), inspector=inspector_panel,
            marker_manager=manager_panel, _refresh_inspector=lambda: None,
            _refresh_marker_manager=lambda: None)
        ReapcaseEditor.apply_sidebar_visibility(editor)
        assert sidebar.visible is expected_sidebar
        assert inspector_panel.visible is inspector
        assert manager_panel.visible is manager
        if inspector and manager:
            assert sidebar.rows[0]["weight"] == 35
            assert sidebar.rows[1]["weight"] == 65


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
