from pathlib import Path
import struct

from stadium_reaper_bridge.editor.audio import (
    AudioTrackView, full_song_track, waveform_cache_key,
)
from stadium_reaper_bridge.editor.composite import lane_height
from stadium_reaper_bridge.editor.layout import RULER_HEIGHT
from stadium_reaper_bridge.editor.navigation import (
    focused_lane_visibility, ghost_waveform_lane_bounds, visible_lane_layout,
)
from stadium_reaper_bridge.stadium import MusicalPosition
from stadium_reaper_bridge.timing import TimingMap
from stadium_reaper_bridge.editor.waveform import raster_transparent_png


P = MusicalPosition


def timing(*changes):
    return TimingMap(240, [(P(bar, 1, 1), 120, numerator, 4)
                           for bar, numerator in changes])


def test_quarter_note_grid_positions_and_bar_classification_in_four_four():
    points = list(timing((1, 4)).iter_beats(0, 2 * 4 * 240 - 1))
    assert [point.units for point in points] == list(range(0, 8 * 240, 240))
    assert [point.is_bar for point in points] == [True, False, False, False] * 2


def test_quarter_note_grid_positions_in_three_four():
    points = list(timing((1, 3)).iter_beats(0, 2 * 3 * 240 - 1))
    assert [(point.position.bar, point.position.beat, point.units) for point in points] == [
        (1, 1, 0), (1, 2, 240), (1, 3, 480),
        (2, 1, 720), (2, 2, 960), (2, 3, 1200),
    ]


def test_quarter_note_grid_follows_time_signature_changes():
    points = list(timing((1, 4), (3, 3), (5, 5)).iter_beats(0, 12 * 240 - 1))
    assert [(point.position.bar, point.position.beat) for point in points] == [
        (1, 1), (1, 2), (1, 3), (1, 4),
        (2, 1), (2, 2), (2, 3), (2, 4),
        (3, 1), (3, 2), (3, 3),
        (4, 1),
    ]
    assert [point.units for point in points] == list(range(0, 12 * 240, 240))


def track(name, path=None):
    return AudioTrackView(1, {"name": name, "filename": "mix.wav"}, path)


def test_full_song_detection_is_case_insensitive_and_requires_resolution():
    resolved = track("  full-SoNg ", Path("/audio/mix.wav"))
    assert full_song_track((track("Click", Path("/audio/click.wav")), resolved)) is resolved
    assert full_song_track((track("FULL-SONG"),)) is None
    assert full_song_track((track("FULL-SONG"),), resolved=False) is not None
    assert full_song_track((track("Full Song", Path("/audio/mix.wav")),)) is None


def test_full_song_normal_and_ghost_renderers_share_the_path_cache_key():
    source = track("FULL-SONG", Path("/audio/mix.wav"))
    cache = {waveform_cache_key(source): object()}
    assert cache[waveform_cache_key(source)] is cache[str(source.resolved_path)]


def test_ghost_waveform_is_one_viewport_raster_not_per_column_primitives():
    columns = [(-0.5, 0.75)] * 5000
    png = raster_transparent_png(columns, 180, (41, 71, 94), stride=2,
                                 vertical_padding=10)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # One encoded image has viewport dimensions independent of the number of
    # envelope columns; app.py adds it with exactly one create_image call.
    assert struct.unpack(">II", png[16:24]) == (5000, 180)


def test_ghost_bounds_use_first_three_visible_reordered_semantic_lanes():
    order = ("VIDEO", "STRUCTURE", "LIGHTS", "STADIUM")
    layout = visible_lane_layout(order, {lane: True for lane in order})
    assert ghost_waveform_lane_bounds(layout) == (
        RULER_HEIGHT, RULER_HEIGHT + sum(lane_height(lane) for lane in order[:3]))


def test_ghost_bounds_follow_focus_lane_and_fewer_than_three_lanes():
    order = ("STRUCTURE", "STADIUM", "SECOND HELIX")
    focused = focused_lane_visibility({lane: True for lane in order}, "STADIUM")
    one = visible_lane_layout(order, focused)
    assert one.lanes == ("STRUCTURE", "STADIUM")
    assert ghost_waveform_lane_bounds(one) == (
        RULER_HEIGHT,
        RULER_HEIGHT + lane_height("STRUCTURE") + lane_height("STADIUM"))

    only_one = visible_lane_layout(order, {"STRUCTURE": False, "STADIUM": True,
                                           "SECOND HELIX": False})
    assert ghost_waveform_lane_bounds(only_one) == (
        RULER_HEIGHT, RULER_HEIGHT + lane_height("STADIUM"))

    two = visible_lane_layout(order, {"STRUCTURE": False, "STADIUM": True,
                                      "SECOND HELIX": True})
    assert ghost_waveform_lane_bounds(two) == (
        RULER_HEIGHT,
        RULER_HEIGHT + lane_height("STADIUM") + lane_height("SECOND HELIX"))
    assert ghost_waveform_lane_bounds(visible_lane_layout(order, {
        lane: False for lane in order})) is None
