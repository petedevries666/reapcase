"""Musical start-position transformations for the editor transport."""

from __future__ import annotations

from ..timing import TimingMap


def resolve_play_start(timing_map: TimingMap, target_units: int, *,
                       pre_roll_enabled: bool = False,
                       pre_roll_measures: int = 2) -> int:
    """Return the canonical transport start for a new playback request.

    Pre-roll counts physical bars in the timing map rather than estimating a
    duration.  The offset within the target bar is retained where possible;
    when the destination has fewer beats it is clamped inside that bar.
    """
    target_units = max(0, int(target_units))
    if not pre_roll_enabled:
        return target_units
    measures = max(0, int(pre_roll_measures))
    target = timing_map.units_to_position(target_units)
    destination_bar = target.bar - measures
    if destination_bar < 1:
        return 0
    offset = target_units - timing_map.bar_start_units(target.bar)
    destination_start = timing_map.bar_start_units(destination_bar)
    destination_length = (timing_map.bar_start_units(destination_bar + 1) -
                          destination_start)
    return destination_start + min(offset, destination_length - 1)
