"""Pure derivation and geometry for the composite Structure lane."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import re
from typing import Iterable, Optional

from ..timeline import TimelineEvent
from ..stadium import StadiumFlag
from ..timing import TimingMap

MARKERS_HEIGHT = 30
PAUSES_HEIGHT = 26
CYCLES_HEIGHT = 30
STRUCTURE_HEIGHT = MARKERS_HEIGHT + PAUSES_HEIGHT + CYCLES_HEIGHT
MANAGED_MEASURE_SUFFIX = re.compile(r" \([0-9]+m\)$")


@dataclass(frozen=True)
class StructureRegion:
    kind: str
    start_units: int
    end_units: int
    label: str
    source_event_indices: tuple[int, ...]


@dataclass(frozen=True)
class StructureLayout:
    regions: tuple[StructureRegion, ...]
    pause_indices: tuple[int, ...]
    unmatched_cycle_indices: tuple[int, ...]


def is_pause_marker(event: TimelineEvent) -> bool:
    """Interpret Stadium's semantic On/Off marker option, never its label."""
    value = event.data.get("pause_at_marker", "Off")
    return value is True or str(value).strip().lower() in {"on", "true", "1", "yes"}


def structure_sublane(event: TimelineEvent) -> str:
    if event.source.type == "MARKER":
        return "pauses" if is_pause_marker(event) else "markers"
    if event.source.type in {"CYCLE_START", "CYCLE_END"}:
        return "cycles"
    return "markers"


def derive_structure_layout(events: Iterable[TimelineEvent], units_for,
                            song_end_units: int) -> StructureLayout:
    """Derive marker spans and conservative chronological cycle pairs.

    Stadium fixtures expose no cycle IDs.  Consequently only a non-nested
    start followed by the next end is paired; nested starts make the open range
    ambiguous and all involved endpoints remain unmatched.
    """
    indexed = list(enumerate(events))
    ordinary = [(i, e) for i, e in indexed
                if e.source.type == "MARKER" and not is_pause_marker(e)]
    ordinary.sort(key=lambda pair: (units_for(pair[1].position), pair[0]))
    regions = []
    for offset, (index, event) in enumerate(ordinary):
        start = units_for(event.position)
        end = (units_for(ordinary[offset + 1][1].position)
               if offset + 1 < len(ordinary) else song_end_units)
        regions.append(StructureRegion("marker", start, max(start, end),
                                       str(event.data.get("name") or "MARKER"), (index,)))

    boundaries = [(i, e) for i, e in indexed
                  if e.source.type in {"CYCLE_START", "CYCLE_END"}]
    boundaries.sort(key=lambda pair: (units_for(pair[1].position), pair[0]))
    open_start = None
    ambiguous: set[int] = set()
    for index, event in boundaries:
        if event.source.type == "CYCLE_START":
            if open_start is not None:
                ambiguous.update((open_start[0], index))
                open_start = None
            else:
                open_start = (index, event)
        elif open_start is None:
            ambiguous.add(index)
        else:
            start_index, start_event = open_start
            regions.append(StructureRegion("cycle", units_for(start_event.position),
                                           units_for(event.position), "CYCLE",
                                           (start_index, index)))
            open_start = None
    if open_start is not None:
        ambiguous.add(open_start[0])
    pauses = tuple(i for i, e in indexed if e.source.type == "MARKER" and is_pause_marker(e))
    return StructureLayout(tuple(regions), pauses, tuple(sorted(ambiguous)))


def normalize_structure_measure_labels(events: list[TimelineEvent], timing_map: TimingMap,
                                       song_end_units: int) -> bool:
    """Write canonical measure counts into ordinary Stadium marker payloads.

    The geometry deliberately comes from :func:`derive_structure_layout`, so
    pause markers and cycle boundaries can never acquire a managed suffix.
    Replacing whole events (rather than mutating their dictionaries) also
    keeps model undo snapshots useful.
    """
    layout = derive_structure_layout(events, timing_map.position_to_units, song_end_units)
    changed = False
    for region in layout.regions:
        if region.kind != "marker":
            continue
        index = region.source_event_indices[0]
        event = events[index]
        if not isinstance(event.source, StadiumFlag):
            continue
        # Integrate fractions of each musical bar. This is signature-aware and
        # gives an integer for the bar-aligned STRUCTURE boundaries Stadium uses.
        measures = 0.0
        cursor = region.start_units
        while cursor < region.end_units:
            position = timing_map.units_to_position(cursor)
            bar_start = timing_map.bar_start_units(position.bar)
            bar_end = timing_map.bar_end_units(position.bar)
            stop = min(region.end_units, bar_end)
            measures += (stop - cursor) / (bar_end - bar_start)
            cursor = stop
        count = int(measures + 0.5)
        old_name = str(event.data.get("name") or "MARKER")
        base = MANAGED_MEASURE_SUFFIX.sub("", old_name)
        name = f"{base} ({count}m)"
        if name == old_name:
            continue
        fields = list(event.source.fields)
        if len(fields) < 2:
            continue
        fields[1] = name
        source = replace(event.source, payload=";".join(fields), original=None)
        events[index] = replace(event, source=source, data=source.semantic_data())
        changed = True
    return changed


def sticky_label_x(region_start: float, region_end: float, viewport_left: float,
                   label_width: float, padding: float = 6) -> Optional[float]:
    """Clamp one label to the visible edge while keeping it inside its region."""
    available = region_end - region_start - 2 * padding
    if available < label_width or available <= 0:
        return None
    return min(max(viewport_left + padding, region_start + padding),
               region_end - label_width - padding)
