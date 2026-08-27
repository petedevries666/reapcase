import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from stadium_reaper_bridge.editor.composite import lane_height
from stadium_reaper_bridge.editor.layout import HEADER_WIDTH, RULER_HEIGHT
from stadium_reaper_bridge.editor.model import EditorModel, LANES
from stadium_reaper_bridge.editor.navigation import (
    ViewState, default_lane_visibility, event_list_rows, jump_viewport_left, marker_region_rows,
    move_visible_lane, normalized_lane_order, visible_lane_layout,
)
from stadium_reaper_bridge.editor.style import REAPCASE_TREEVIEW_STYLE
from stadium_reaper_bridge.editor.app import ReapcaseEditor
from stadium_reaper_bridge.editor import app as editor_app
from stadium_reaper_bridge.editor.audio_engine import PlaybackState


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


class Variable:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class GhostCanvas:
    def __init__(self): self.deleted = []; self.images = []
    def delete(self, tag): self.deleted.append(tag)
    def canvasx(self, _x): return 0
    def winfo_width(self): return 100
    def create_image(self, *args, **kwargs): self.images.append((args, kwargs))


def ghost_editor(visible=False):
    return SimpleNamespace(
        full_song_ghost_visible=Variable(visible), canvas=GhostCanvas(),
        _ghost_raster_cache={}, _ghost_raster_coverage=None,
        _ghost_refresh_pending=False, _ghost_waveform_image=None,
        _waveform_images=[])


def bind_ghost_methods(editor):
    editor._clear_ghost_waveform = lambda: ReapcaseEditor._clear_ghost_waveform(editor)


def test_full_song_ghost_defaults_on_and_reset_preserves_preference():
    source = Path("src/stadium_reaper_bridge/editor/app.py").read_text(encoding="utf-8")
    assert 'preferences.get("full_song_ghost_visible", True)' in source
    editor = ghost_editor(False)
    bind_ghost_methods(editor)
    editor._ghost_raster_cache["old"] = object()
    ReapcaseEditor._reset_full_song_ghost(editor)
    assert editor.full_song_ghost_visible.get() is False
    assert editor._ghost_raster_cache == {}


def test_disabled_full_song_ghost_does_no_raster_or_cache_work(monkeypatch):
    editor = ghost_editor(False)
    bind_ghost_methods(editor)
    editor.model = SimpleNamespace(audio_tracks=())
    monkeypatch.setattr(editor_app, "full_song_track",
                        lambda *_args: pytest.fail("ghost track must not be inspected"))
    monkeypatch.setattr(editor_app, "cached_ghost_raster",
                        lambda *_args: pytest.fail("ghost raster must not be cached"))
    ReapcaseEditor._draw_ghost_waveform(editor, object(), ())
    assert editor.canvas.images == []


def test_view_toggle_enables_rendering_and_disabling_clears_artifacts(monkeypatch):
    editor = ghost_editor(True)
    bind_ghost_methods(editor)
    summary = object()
    editor.model = SimpleNamespace(
        audio_tracks=(object(),), tempo_map=object(), song=SimpleNamespace(ppqn=960))
    editor.waveforms = {"full-song": summary}
    editor.pixels_per_beat = 48.0
    monkeypatch.setattr(editor_app, "full_song_track", lambda _tracks: object())
    monkeypatch.setattr(editor_app, "waveform_cache_key", lambda _track: "full-song")
    monkeypatch.setattr(editor_app, "ghost_waveform_lane_bounds", lambda _layout: (10, 50))
    monkeypatch.setattr(editor_app, "buffered_viewport", lambda _left, _width: (0, 300))
    monkeypatch.setattr(editor_app, "ghost_raster_cache_key", lambda *_args, **_kwargs: ("key",))
    image = SimpleNamespace(width=lambda: 300)
    raster_calls = []
    monkeypatch.setattr(editor_app, "cached_ghost_raster",
                        lambda cache, key, render: (raster_calls.append(key) or (0, image)))

    ReapcaseEditor._draw_ghost_waveform(editor, object(), ("STRUCTURE",))
    assert raster_calls == [("key",)]
    assert editor.canvas.images

    editor._ghost_raster_cache["old"] = image
    editor.full_song_ghost_visible.set(False)
    redraws = []
    editor.redraw = lambda: redraws.append(True)
    persisted = []
    monkeypatch.setattr(editor_app, "update_preferences",
                        lambda update: (update(data := {}), persisted.append(data)))
    ReapcaseEditor.toggle_full_song_ghost(editor)
    assert editor.canvas.deleted[-1] == "ghost-waveform"
    assert editor._ghost_raster_cache == {}
    assert editor._ghost_waveform_image is None
    assert redraws == [True]
    assert persisted == [{"full_song_ghost_visible": False}]


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
    assert any(row.lane == "SECOND HELIX" and row.name ==
               model.timeline.events[row.index].data["label"] for row in rows)
    assert all(";" not in row.name for row in rows)


def test_marker_manager_only_extracts_marker_and_canonical_region_rows():
    rows = marker_region_rows(perfect_picture())
    assert rows
    assert {row.kind for row in rows} <= {
        "START", "END", "MARKER", "PAUSE", "CYCLE_START", "CYCLE_END", "REGION"}
    assert {row.lane for row in rows} == {"STRUCTURE"}
    assert not any(row.kind in {"PRESETSNAP", "MIDI_CC", "VIDEO"} for row in rows)
    assert all(row.position.count("-") == 1 for row in rows)


def test_structure_manager_replaces_region_marker_but_keeps_pause_and_end_marker():
    model = perfect_picture()
    rows = marker_region_rows(model)
    region_indices = {row.indices[0] for row in rows if row.kind == "REGION"}

    assert region_indices
    assert not any(row.kind == "MARKER" and row.indices[0] in region_indices
                   for row in rows)
    pause_indices = {index for index, event in enumerate(model.timeline.events)
                     if event.source.type == "MARKER"
                     and str(event.data.get("pause_at_marker", "")).casefold() == "on"}
    assert pause_indices <= {row.indices[0] for row in rows if row.kind == "PAUSE"}


def test_repeated_redraw_requests_coalesce_at_idle():
    callbacks = []
    redraws = []
    editor = SimpleNamespace(
        _redraw_idle_id=None,
        after_idle=lambda callback: callbacks.append(callback) or "idle-1",
        winfo_exists=lambda: True,
        redraw=lambda reason=None: redraws.append(reason))

    ReapcaseEditor.request_redraw(editor, "first change")
    ReapcaseEditor.request_redraw(editor, "second change")
    assert len(callbacks) == 1
    callbacks.pop()()
    assert redraws == ["first change"]
    assert editor._redraw_idle_id is None


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


class FollowCanvas:
    def __init__(self):
        self.left = 0.0
        self.moves = []
        self.playhead = [0.0, 0.0, 0.0, 200.0]

    def find_withtag(self, tag): return (1,) if tag == "playhead" else ()
    def coords(self, _item, *values):
        if values: self.playhead = list(values)
        return self.playhead
    def canvasx(self, _x): return self.left
    def winfo_width(self): return 100
    def cget(self, _name): return "0 0 1000 300"
    def xview_moveto(self, fraction): self.left = fraction * 1000
    def move(self, tag, dx, dy): self.moves.append((tag, dx, dy))


def test_playback_follow_scroll_moves_view_without_redraw_or_rasterization():
    canvas = FollowCanvas()
    redraws = []
    clock_reads = []
    live_polls = []

    def units_for(seconds):
        clock_reads.append(seconds)
        return 2000
    editor = SimpleNamespace(
        model=SimpleNamespace(
            tempo_map=SimpleNamespace(
                seconds_to_musical_position=lambda _seconds: SimpleNamespace(render=lambda: "001-01.001"),
                seconds_to_units=units_for),
            song=SimpleNamespace(ppqn=1000)),
        audio_engine=SimpleNamespace(current_time=1.0, audible_time=.96,
                                     state=PlaybackState.PLAYING),
        live_midi=SimpleNamespace(playing=True,
                                  poll=lambda units: live_polls.append(units)),
        transport_position=SimpleNamespace(set=lambda _value: None),
        pixels_per_beat=100.0,
        canvas=canvas,
        full_song_ghost_visible=Variable(True),
        _follow_suspended_until=0.0,
        _ghost_refresh_pending=False,
        _ghost_raster_coverage=(0.0, 1000.0),
        redraw=lambda: redraws.append(True),
        after_idle=lambda _callback: pytest.fail("buffered raster should not refresh"),
        after=lambda *_args: None)
    editor._update_fixed_headers_for_scroll = (
        lambda previous: ReapcaseEditor._update_fixed_headers_for_scroll(editor, previous))
    editor._transport_tick = lambda: ReapcaseEditor._transport_tick(editor)
    editor._refresh_ghost_waveform = lambda: None

    for _ in range(3):
        ReapcaseEditor._transport_tick(editor)
    assert clock_reads == [.96, .96, .96]
    assert live_polls == [2000, 2000, 2000]

    expected_left = HEADER_WIDTH + 200.0 - 30.0
    assert canvas.left == pytest.approx(expected_left)
    assert redraws == []
    assert canvas.moves == [("fixed-header", expected_left, 0)]
    assert canvas.playhead[0] == canvas.playhead[2] == pytest.approx(HEADER_WIDTH + 200.0)

    # Once the viewport leaves coverage, transport schedules exactly one
    # ghost-only refresh rather than rasterizing synchronously on every tick.
    callbacks = []
    canvas.left = 0.0
    canvas.moves.clear()
    editor._ghost_raster_coverage = (0.0, expected_left + 99.0)
    editor.after_idle = callbacks.append
    for _ in range(3):
        ReapcaseEditor._transport_tick(editor)
    assert callbacks == [editor._refresh_ghost_waveform]
    assert redraws == []

    # The same follow-scroll path never schedules ghost work while the View
    # preference is disabled, even with no buffered coverage.
    callbacks.clear()
    canvas.left = 0.0
    editor._ghost_refresh_pending = False
    editor._ghost_raster_coverage = None
    editor.full_song_ghost_visible.set(False)
    for _ in range(3):
        ReapcaseEditor._transport_tick(editor)
    assert callbacks == []


def test_menu_order_and_shared_manager_treeview_style_are_wired_in_app():
    source = Path("src/stadium_reaper_bridge/editor/app.py").read_text(encoding="utf-8")
    cascade_positions = [source.index(f'bar.add_cascade(label="{label}"')
                         for label in ("File", "Edit", "Select", "View", "Show")]
    assert cascade_positions == sorted(cascade_positions)
    assert source.count("style=REAPCASE_TREEVIEW_STYLE") == 3
    assert REAPCASE_TREEVIEW_STYLE == "Reapcase.Treeview"
    for handler in ("self.select_all", "self.select_after", "self.select_lane", "self.shift_dialog",
                    "self.new_show", "self.open_show", "self.save_show", "self.add_show_song",
                    "self.remove_show_song", "self.relocate_show_song", "self.preflight_show",
                    "self.refresh_show", "self.midi_settings"):
        assert handler in source
