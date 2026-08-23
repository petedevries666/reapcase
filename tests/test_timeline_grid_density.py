from stadium_reaper_bridge.editor.layout import (
    is_major_display_bar,
    snap_drag_delta,
    timeline_grid_density,
    timeline_x,
)
from stadium_reaper_bridge.stadium import MusicalPosition
from stadium_reaper_bridge.timing import TimingMap


P = MusicalPosition


def timing(*changes):
    return TimingMap(240, [(P(bar, 1, 1), tempo, numerator, 4)
                           for bar, tempo, numerator in changes])


def displayed_bars(timing_map, density, end_bar):
    end = timing_map.bar_start_units(end_bar)
    return [point.position.bar for point in timing_map.iter_bars(0, end)
            if is_major_display_bar(point.position.bar, density)]


def test_density_progressively_removes_beats_then_groups_measures():
    densities = [timeline_grid_density(scale, beats) for scale, beats in
                 ((90, 4), (20, 4), (10, 4), (5, 4), (2, 4), (1, 3))]
    assert [(item.show_beats, item.bar_stride) for item in densities] == [
        (True, 1), (False, 1), (False, 2), (False, 4), (False, 8), (False, 16),
    ]


def test_measure_labels_share_stride_and_stay_anchored_to_measure_one():
    density = timeline_grid_density(5, 4)
    assert displayed_bars(timing((1, 120, 4)), density, 17) == [1, 5, 9, 13, 17]


def test_shortest_visible_signature_controls_density_across_changes():
    mixed = timing((1, 120, 5), (3, 90, 3), (5, 140, 4))
    end = mixed.bar_start_units(7)
    assert mixed.minimum_beats_per_bar(0, end) == 3
    density = timeline_grid_density(10, mixed.minimum_beats_per_bar(0, end))
    assert (density.show_beats, density.bar_stride) == (False, 2)
    assert displayed_bars(mixed, density, 7) == [1, 3, 5, 7]
    # Tempo and signature changes alter neither the numbered-bar anchor nor the
    # exact TimingMap-derived position of a displayed line.
    assert mixed.bar_start_units(5) == (5 + 5 + 3 + 3) * 240


def test_visual_density_does_not_change_snap_or_semantic_coordinates():
    mixed = timing((1, 120, 4), (3, 120, 5))
    event_units = mixed.position_to_units(P(3, 4, 1))
    x_before = timeline_x(event_units, mixed.ppqn, 5)
    density = timeline_grid_density(5, mixed.minimum_beats_per_bar(0, event_units))
    assert density.bar_stride == 4
    assert snap_drag_delta(0, 271, "1 beat", 240, 4, mixed) == 240
    assert timeline_x(event_units, mixed.ppqn, 5) == x_before
