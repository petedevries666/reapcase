import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from stadium_reaper_bridge.editor.app import (GLOBAL_MANAGER_SHORTCUTS,
                                               MARKER_MANAGER_COLUMNS, ReapcaseEditor)
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.navigation import (
    adjacent_structure_region_index, marker_flag_manager_rows, marker_region_rows,
    structure_manager_rows, structure_region_indices)
from stadium_reaper_bridge.editor.stadium_workspace import (
    MANIFEST_NAME, discover_workspace_songs)
from stadium_reaper_bridge.editor.song_browser import (
    SongDirectory, SongMetadataCache, initial_song_folder, workspace_for_song)
from stadium_reaper_bridge.editor.structure import is_pause_marker
from stadium_reaper_bridge.editor.style import (LANE_PALETTE, REAPCASE_COMBOBOX_STYLE,
                                                 REAPCASE_ENTRY_STYLE,
                                                 REAPCASE_TREEVIEW_STYLE, lane_colors)


def make_workspace(root, songs):
    root.mkdir()
    (root / MANIFEST_NAME).write_text(json.dumps({"workspace_type": "stadium_backup"}))
    directory = root / "showcase/songs/workspace"
    directory.mkdir(parents=True)
    for filename, document in songs.items():
        (directory / filename).write_text(
            document if isinstance(document, str) else json.dumps(document))
    return root


def test_song_browser_uses_explicit_dark_widget_styles():
    source = Path("src/stadium_reaper_bridge/editor/song_browser.py").read_text(encoding="utf-8")
    assert "apply_ttk_theme(self)" in source
    assert "style=REAPCASE_TREEVIEW_STYLE" in source
    assert source.count("style=REAPCASE_ENTRY_STYLE") == 2
    assert "style=REAPCASE_COMBOBOX_STYLE" in source
    assert 'tags=("folder",)' in source
    assert 'tags=("song",)' in source


def test_workspace_inventory_uses_metadata_natural_order_and_skips_bad_json(tmp_path):
    workspace = make_workspace(tmp_path / "one", {
        "101.json": {"name": "FROM METADATA", "flags": []},
        "10.json": {"name": "TEN", "flags": []},
        "9.json": {"name": "NINE", "flags": []},
        "broken.json": "{nope",
        "settings.json": {"name": "NOT A SONG"},
    })
    songs = discover_workspace_songs(workspace)
    assert [song.path.name for song in songs] == ["9.json", "10.json", "101.json"]
    assert [song.title for song in songs] == ["NINE", "TEN", "FROM METADATA"]
    assert songs[-1].label == "101   FROM METADATA"


def test_changing_workspace_replaces_cached_inventory_and_invalidation_finds_addition(tmp_path):
    one = make_workspace(tmp_path / "one", {"1.json": {"name": "ONE", "flags": []}})
    two = make_workspace(tmp_path / "two", {"2.json": {"name": "TWO", "flags": []}})
    editor = SimpleNamespace(stadium_workspace=None, _workspace_song_inventory=None)
    editor.invalidate_workspace_song_inventory = lambda: ReapcaseEditor.invalidate_workspace_song_inventory(editor)
    editor.workspace_songs = lambda: ReapcaseEditor.workspace_songs(editor)
    assert [song.title for song in ReapcaseEditor.set_stadium_workspace(editor, one)] == ["ONE"]
    assert [song.title for song in ReapcaseEditor.set_stadium_workspace(editor, two)] == ["TWO"]
    directory = two / "showcase/songs/workspace"
    (directory / "3.json").write_text(json.dumps({"name": "THREE", "flags": []}))
    assert [song.title for song in editor.workspace_songs()] == ["TWO"]
    editor.invalidate_workspace_song_inventory()
    assert [song.title for song in editor.workspace_songs()] == ["TWO", "THREE"]


def test_standalone_song_is_not_an_implicit_workspace_and_menu_is_disabled():
    configured = []
    editor = SimpleNamespace(stadium_workspace=None, model=SimpleNamespace(path=Path("standalone.json")),
                             file_menu=SimpleNamespace(entryconfigure=lambda *a, **k: configured.append((a, k))),
                             _workspace_song_cascade=1)
    assert ReapcaseEditor.workspace_songs(editor) == ()
    ReapcaseEditor._refresh_file_menu_state(editor)
    assert configured == [((1,), {"state": "disabled"})]


def test_workspace_selection_uses_normal_opening_pipeline(tmp_path):
    song = SimpleNamespace(path=(tmp_path / "4.json").resolve())
    calls = []
    editor = SimpleNamespace(workspace_songs=lambda: (song,),
                             _begin_song_open=lambda path: calls.append(path) or True)
    assert ReapcaseEditor.open_workspace_song(editor, song.path)
    assert calls == [song.path]


def test_dirty_song_can_cancel_normal_opening_pipeline(monkeypatch, tmp_path):
    editor = SimpleNamespace(loading=False, model=SimpleNamespace(modified=True))
    monkeypatch.setattr("stadium_reaper_bridge.editor.app.messagebox.askyesnocancel",
                        lambda *args, **kwargs: None)
    assert ReapcaseEditor._begin_song_open(editor, tmp_path / "next.json") is False


def test_song_browser_reads_headers_sorts_naturally_and_ignores_non_songs(tmp_path):
    for filename, document in {
        "101.json": {"name": "ONE OH ONE", "flags": []},
        "10.json": {"name": "TEN", "flags": []},
        "9.json": {"name": "NINE", "flags": []},
        "431.json": {"name": "LATE NIGHT PARTY", "flags": []},
        "broken.json": "{bad",
        "settings.json": {"name": "Not a Song"},
    }.items():
        (tmp_path / filename).write_text(
            document if isinstance(document, str) else json.dumps(document))

    directory = SongDirectory(tmp_path).scan()

    assert [(song.file_id, song.title) for song in directory.songs] == [
        ("9", "NINE"), ("10", "TEN"), ("101", "ONE OH ONE"),
        ("431", "LATE NIGHT PARTY")]
    assert not {"broken", "settings"} & {song.file_id for song in directory.songs}


def test_song_browser_searches_title_and_file_number(tmp_path):
    (tmp_path / "453.json").write_text(json.dumps({"name": "CLOCKSICK", "flags": []}))
    (tmp_path / "431.json").write_text(json.dumps({"name": "LATE NIGHT PARTY", "flags": []}))
    directory = SongDirectory(tmp_path).scan()
    assert [song.file_id for song in directory.filtered_songs("clocks")] == ["453"]
    assert [song.title for song in directory.filtered_songs("431")] == ["LATE NIGHT PARTY"]


def test_song_browser_keeps_folders_navigable(tmp_path):
    (tmp_path / "Songs 10").mkdir(); (tmp_path / "Songs 9").mkdir()
    directory = SongDirectory(tmp_path).scan()
    assert [folder.name for folder in directory.folders] == ["Songs 9", "Songs 10"]


def test_song_browser_metadata_cache_avoids_reparsing_unchanged_song(monkeypatch, tmp_path):
    song = tmp_path / "431.json"
    song.write_text(json.dumps({"name": "LATE NIGHT PARTY", "flags": []}))
    cache = SongMetadataCache()
    assert cache.inspect(song).title == "LATE NIGHT PARTY"
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("unchanged metadata was reparsed")))
    assert cache.inspect(song).title == "LATE NIGHT PARTY"


def test_metadata_browser_never_constructs_editor_model_or_resolves_audio(monkeypatch, tmp_path):
    (tmp_path / "1.json").write_text(json.dumps({"name": "LIGHTWEIGHT", "flags": []}))
    monkeypatch.setattr(EditorModel, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("heavy EditorModel constructed")))
    monkeypatch.setattr(EditorModel, "open_phased", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("audio-capable loading pipeline invoked")))
    assert SongDirectory(tmp_path).scan().songs[0].title == "LIGHTWEIGHT"


def test_workspace_song_detection_and_initial_folder_preference(tmp_path):
    workspace = make_workspace(tmp_path / "imported", {
        "453.json": {"name": "CLOCKSICK", "flags": []}})
    songs = workspace / "showcase/songs/workspace"
    remembered = tmp_path / "remembered"; remembered.mkdir()
    assert workspace_for_song(songs / "453.json") == workspace.resolve()
    assert initial_song_folder(workspace, remembered) == songs.resolve()
    assert initial_song_folder(None, remembered) == remembered.resolve()
    assert workspace_for_song(remembered / "standalone.json") is None


def test_normal_open_pipeline_activates_workspace_and_standalone_clears_it(tmp_path):
    workspace = make_workspace(tmp_path / "imported", {
        "4.json": {"name": "FOUR", "flags": []}})
    song = workspace / "showcase/songs/workspace/4.json"
    calls = []
    editor = SimpleNamespace(
        stadium_workspace=None,
        set_stadium_workspace=lambda path: calls.append(Path(path)),
        invalidate_workspace_song_inventory=lambda: calls.append("invalidate"))
    ReapcaseEditor._activate_workspace_for_song(editor, song)
    assert calls == [workspace.resolve()]

    editor.stadium_workspace = workspace
    ReapcaseEditor._activate_workspace_for_song(editor, tmp_path / "standalone.json")
    assert editor.stadium_workspace is None
    assert calls[-1] == "invalidate"


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


def test_manager_projections_separate_structure_from_editable_flags(tmp_path):
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
    model = EditorModel.open(song)
    rows = structure_manager_rows(model)
    flags = marker_flag_manager_rows(model)

    assert rows and {row.lane for row in rows} == {"STRUCTURE"}
    assert "REGION" in {row.kind for row in rows}
    assert {row.lane for row in flags} == {"STADIUM", "SECOND HELIX"}
    assert {row.name for row in flags} >= {"LOOPER RECORD", "BASS PLAY"}
    assert not any("REGION" in row.kind for row in flags)
    for lane in ("STRUCTURE", "STADIUM", "SECOND HELIX"):
        assert lane_colors(lane) is LANE_PALETTE[lane]


def test_marker_flag_filters_use_canonical_lanes_without_mutating_model():
    model = EditorModel.open(Path("tests/fixtures/wanna_be_429.json"))
    before = tuple((event.source.payload, event.position) for event in model.timeline.events)
    all_rows = marker_flag_manager_rows(model)
    stadium = marker_flag_manager_rows(model, {"STADIUM"})
    midi = marker_flag_manager_rows(model, {"MIDI / OTHER"})

    assert all_rows and stadium
    assert {row.lane for row in stadium} == {"STADIUM"}
    assert midi == ()  # displayed labels cannot leak another lane into this filter
    assert len(stadium) < len(all_rows)
    assert tuple((event.source.payload, event.position) for event in model.timeline.events) == before


def test_marker_flag_manager_excludes_non_editable_technical_event(tmp_path):
    song = tmp_path / "technical-event.json"
    song.write_text(json.dumps({
        "name": "Technical Event", "ppqn": 240, "params": None, "tracks": [],
        "flags": [
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "002-01.001|DIAGNOSTIC;INTERNAL;1;Do not edit",
            "003-01.001|MIDI_CC;USER CONTROL;4;CC;1;20;64",
            "004-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ]}))
    model = EditorModel.open(song)

    assert any(event.source.type == "DIAGNOSTIC" for event in model.timeline.events)
    rows = marker_flag_manager_rows(model)
    assert {model.timeline.events[row.indices[0]].source.type for row in rows} == {"MIDI_CC"}


def test_both_manager_selections_delegate_to_canonical_navigation():
    class Tree:
        def selection(self): return ("row",)

    row = SimpleNamespace(units=960, indices=(7,), lane="STRUCTURE")
    calls = []
    editor = SimpleNamespace(
        marker_tree=Tree(), _marker_rows={"row": row},
        marker_flag_tree=Tree(), _marker_flag_rows={"row": row},
        navigate_to_event=lambda *args, **kwargs: calls.append((args, kwargs)))

    ReapcaseEditor._marker_manager_selected(editor)
    ReapcaseEditor._marker_flag_manager_selected(editor)
    assert calls == [
        ((960,), {"select_index": 7, "vertical": False, "reveal_lane": "STRUCTURE"}),
        ((960,), {"select_index": 7, "vertical": False, "reveal_lane": "STRUCTURE"}),
    ]


@pytest.mark.parametrize("source", ("structure", "marker_flag", "event_list"))
def test_manager_and_event_list_navigation_preserve_vertical_viewport(source):
    model = EditorModel.open(Path("tests/fixtures/perfect_picture_336.json"))
    redraws = []

    class Tree:
        def selection(self): return ("3",)

    class Canvas:
        left = 0.0
        vertical = (0.375, 0.625)

        def cget(self, _name): return "0 0 100000 2000"
        def winfo_width(self): return 1000
        def canvasx(self, _x): return self.left
        def xview_moveto(self, fraction): self.left = fraction * 100000
        def yview(self): return self.vertical
        def yview_moveto(self, fraction): self.vertical = (fraction, fraction + .25)

    row = SimpleNamespace(units=8_000, indices=(3,), index=3, lane="SECOND HELIX")
    editor = SimpleNamespace(
        model=model, canvas=Canvas(), pixels_per_beat=80.0,
        audio_engine=SimpleNamespace(seek=lambda _seconds: None),
        transport_position=SimpleNamespace(set=lambda _value: None),
        _follow_suspended_until=0, _refresh_inspector=lambda: None,
        _update_fixed_headers_for_scroll=lambda _previous: None,
        request_redraw=redraws.append, marker_tree=Tree(), marker_flag_tree=Tree(),
        event_tree=Tree(), _marker_rows={"3": row},
        _marker_flag_rows={"3": row}, _event_rows={"3": row})
    editor.seek_units = lambda units: ReapcaseEditor.seek_units(editor, units)
    editor.navigate_to_event = lambda units, **kwargs: ReapcaseEditor.navigate_to_event(
        editor, units, **kwargs)

    vertical_before = editor.canvas.yview()
    if source == "structure":
        ReapcaseEditor._marker_manager_selected(editor)
    elif source == "marker_flag":
        ReapcaseEditor._marker_flag_manager_selected(editor)
    else:
        ReapcaseEditor._event_list_selected(editor)

    assert editor.canvas.left > 0
    assert editor.canvas.yview() == vertical_before
    assert model.selected == {3}
    assert model._units(model.cursor) == 8_000
    assert redraws == ["event navigation"]


def test_shared_navigation_updates_cursor_seeks_selects_and_can_defer_reveal(monkeypatch):
    model = EditorModel.open(Path("tests/fixtures/perfect_picture_336.json"))
    seeks, redraws = [], []
    editor = SimpleNamespace(
        model=model, audio_engine=SimpleNamespace(seek=seeks.append),
        transport_position=SimpleNamespace(set=lambda _value: None),
        _follow_suspended_until=0, _refresh_inspector=lambda: None,
        request_redraw=lambda reason: redraws.append(reason))
    editor.seek_units = lambda units: ReapcaseEditor.seek_units(editor, units)
    ReapcaseEditor.navigate_to_event(editor, 960, select_index=1, horizontal=False)
    assert model._units(model.cursor) == 960
    assert model.selected == {1}
    assert seeks == [model.tempo_map.units_to_seconds(960)]
    assert redraws == ["event navigation"]


def test_rapid_navigation_mutates_final_viewport_before_one_coalesced_redraw():
    model = EditorModel.open(Path("tests/fixtures/perfect_picture_336.json"))
    order, idle, painted = [], [], []

    class Canvas:
        left = 0.0
        def cget(self, _name): return "0 0 100000 2000"
        def winfo_width(self): return 1000
        def canvasx(self, _x): return self.left
        def xview_moveto(self, fraction):
            self.left = fraction * 100000
            order.append(("viewport", self.left))

    editor = SimpleNamespace(
        model=model, canvas=Canvas(), pixels_per_beat=80.0,
        audio_engine=SimpleNamespace(seek=lambda _seconds: None),
        transport_position=SimpleNamespace(set=lambda _value: None),
        _follow_suspended_until=0, _redraw_idle_id=None,
        after_idle=lambda callback: idle.append(callback) or "navigation-redraw",
        winfo_exists=lambda: True,
        redraw=lambda reason=None: (order.append(("redraw", editor.canvas.left)),
                                    painted.append(reason)),
        _update_fixed_headers_for_scroll=lambda _previous: None,
        _refresh_inspector=lambda: None)
    editor.seek_units = lambda units: ReapcaseEditor.seek_units(editor, units)
    editor.request_redraw = lambda reason: ReapcaseEditor.request_redraw(editor, reason)

    targets = [0, 240, 960, 4_000, 8_000, 16_000, 24_000, 32_000, 40_000,
               model.song_end_units]
    for units in targets:
        ReapcaseEditor.navigate_to_event(editor, units)

    assert model._units(model.cursor) == model.song_end_units
    assert len(idle) == 1
    assert all(item[0] == "viewport" for item in order)
    final_left = editor.canvas.left
    idle.pop()()
    assert painted == ["event navigation"]
    assert order[-1] == ("redraw", final_left)


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


def test_manager_shortcuts_are_centralized_and_work_from_text_and_tree_widgets():
    assert GLOBAL_MANAGER_SHORTCUTS == (
        ("<Control-m>", "open_marker_manager"),
        ("<Control-f>", "open_marker_flag_manager"),
    )
    editor = SimpleNamespace()
    calls = []
    for widget_class in ("Treeview", "Entry", "Text"):
        event = SimpleNamespace(widget=ShortcutWidget(widget_class, editor))
        result = ReapcaseEditor._global_editor_shortcut(
            editor, event, lambda: calls.append(widget_class), allow_text_input=True)
        assert result == "break"
    assert calls == ["Treeview", "Entry", "Text"]


def test_manager_openers_expose_and_focus_their_existing_docked_tree():
    class Variable:
        def __init__(self): self.value = False
        def set(self, value): self.value = value
        def get(self): return self.value
    class Tree:
        def __init__(self): self.focuses = 0
        def focus_set(self): self.focuses += 1
    structure_tree, flag_tree = Tree(), Tree()
    layouts = []
    editor = SimpleNamespace(
        marker_manager_visible=Variable(), marker_flag_manager_visible=Variable(),
        marker_tree=structure_tree, marker_flag_tree=flag_tree,
        marker_manager=object(), marker_flag_manager=object(),
        apply_sidebar_visibility=lambda: layouts.append(True))
    assert ReapcaseEditor.open_marker_manager(editor) is editor.marker_manager
    assert ReapcaseEditor.open_marker_flag_manager(editor) is editor.marker_flag_manager
    assert editor.marker_manager_visible.get() and editor.marker_flag_manager_visible.get()
    assert structure_tree.focuses == flag_tree.focuses == 1
    assert len(layouts) == 2


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
    assert 'label="Open Song...", command=self.open_json, accelerator="Ctrl+O"' in source
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
            assert sidebar.rows[0]["weight"] == 1
            assert sidebar.rows[1]["weight"] == 1


def test_event_list_navigation_makes_activated_event_the_current_selection():
    class Tree:
        def selection(self): return ("2", "5")

    navigations = []
    editor = SimpleNamespace(
        model=SimpleNamespace(selected=set()), event_tree=Tree(),
            _event_rows={"2": SimpleNamespace(index=2, units=240, lane="SECOND HELIX"),
                         "5": SimpleNamespace(index=5, units=960, lane="STADIUM")},
            navigate_to_event=lambda units, **kwargs: navigations.append((units, kwargs)),
        _refresh_inspector=lambda: None)
    ReapcaseEditor._event_list_selected(editor)
    assert navigations == [(240, {"select_index": 2, "vertical": False,
                                  "reveal_lane": "SECOND HELIX"})]
