"""View/navigation projections over the canonical editor model.

Nothing in this module owns events: rows and manager entries are rebuilt from
``EditorModel.timeline`` whenever the UI refreshes.
"""

from dataclasses import dataclass

from .composite import lane_height
from .display import badge_text
from .layout import HEADER_WIDTH, RULER_HEIGHT, timeline_x
from .structure import is_pause_marker


def normalized_lane_order(order, available) -> list[str]:
    """Return a complete, duplicate-free presentation order.

    Unknown preference entries are discarded and newly introduced lanes are
    appended, so an old UI preference can never hide application lanes.
    """
    available = tuple(available)
    result = [lane for lane in order if lane in available]
    result = list(dict.fromkeys(result))
    return result + [lane for lane in available if lane not in result]


def move_visible_lane(order, visible, lane: str, direction: int) -> list[str]:
    """Swap *lane* with its adjacent visible neighbour.

    Hidden entries are never selected as neighbours and retain their order
    relative to one another.  The returned list is a new presentation-only
    value; no Song or event state is involved.
    """
    result = list(order)
    shown = [item for item in result if visible.get(item, True)]
    if lane not in shown or direction not in (-1, 1):
        return result
    shown_index = shown.index(lane)
    target_index = shown_index + direction
    if not 0 <= target_index < len(shown):
        return result
    other = shown[target_index]
    first, second = result.index(lane), result.index(other)
    result[first], result[second] = result[second], result[first]
    return result


@dataclass
class ViewState:
    current_view: str = "timeline"

    def switch(self, view: str) -> None:
        if view not in {"timeline", "event_list"}:
            raise ValueError(f"Unknown view: {view}")
        self.current_view = view


@dataclass(frozen=True)
class LaneLayout:
    lanes: tuple[str, ...]
    tops: dict[str, int]
    event_bottom: int
    audio_top: int


def visible_lane_layout(lanes, visible) -> LaneLayout:
    """Return contiguous geometry for the visible event/sequence lanes."""
    shown = tuple(lane for lane in lanes if visible.get(lane, True))
    tops, y = {}, RULER_HEIGHT
    for lane in shown:
        tops[lane] = y
        y += lane_height(lane)
    return LaneLayout(shown, tops, y, y)


@dataclass(frozen=True)
class EventListRow:
    index: int
    units: int
    position: str
    lane: str
    kind: str
    name: str
    details: str


def event_list_rows(model) -> tuple[EventListRow, ...]:
    rows = []
    for index, event in enumerate(model.timeline.events):
        kind = event.source.type
        label = badge_text(event)
        details = ""
        if kind in {"START", "TIME"}:
            details = (f"{event.data.get('tempo', '?')} BPM / "
                       f"{event.data.get('time_signature_numerator', '?')}/"
                       f"{event.data.get('time_signature_denominator', '?')}")
        elif kind == "LOOPER":
            details = str(event.data.get("action", "")).upper()
        rows.append(EventListRow(index, model._units(event.position), event.position.render(),
                                 model.lane(event), kind, label, details))
    return tuple(sorted(rows, key=lambda row: (row.units, row.index)))


@dataclass(frozen=True)
class MarkerRegionRow:
    indices: tuple[int, ...]
    units: int
    position: str
    kind: str
    name: str
    end: str = ""


def marker_region_rows(model) -> tuple[MarkerRegionRow, ...]:
    events = model.timeline.events
    rows, pending = [], None
    for index, event in sorted(enumerate(events), key=lambda pair: model._units(pair[1].position)):
        kind = event.source.type
        if kind not in {"START", "END", "MARKER", "CYCLE_START", "CYCLE_END"}:
            continue
        units, position = model._units(event.position), event.position.render()
        if kind == "CYCLE_START":
            pending = (index, event, units)
            continue
        if kind == "CYCLE_END" and pending:
            start_index, start, start_units = pending
            rows.append(MarkerRegionRow((start_index, index), start_units,
                start.position.render(), "CYCLE", badge_text(start), f"→ {position}"))
            pending = None
            continue
        display_kind = "PAUSE" if kind == "MARKER" and is_pause_marker(event) else kind
        rows.append(MarkerRegionRow((index,), units, position, display_kind, badge_text(event)))
    if pending:
        index, event, units = pending
        rows.append(MarkerRegionRow((index,), units, event.position.render(), "CYCLE", badge_text(event)))
    return tuple(sorted(rows, key=lambda row: row.units))


def jump_viewport_left(units: int, ppqn: int, pixels_per_beat: float,
                       viewport_width: float, target: float = .28) -> float:
    """Place a musical target near the first third of the viewport."""
    x = timeline_x(units, ppqn, pixels_per_beat)
    return max(0.0, x - max(1.0, viewport_width) * target)


def focused_lane_visibility(normal: dict[str, bool], lane: str) -> dict[str, bool]:
    """Apply temporary focus without mutating the preference mapping."""
    return {name: bool(name == lane or (name == "STRUCTURE" and lane != "STRUCTURE"))
            for name in normal}


def adjacent_event_index(model, current_units: int, direction: int,
                         visible_lanes=None) -> int | None:
    """Return the canonical chronological neighbour in visible semantic lanes."""
    visible = set(visible_lanes or ())
    ordered = sorted(((model._units(event.position), i) for i, event in enumerate(model.timeline.events)
                      if not visible or model.lane(event) in visible))
    candidates = [(units, i) for units, i in ordered
                  if units > current_units] if direction > 0 else [
                      (units, i) for units, i in ordered if units < current_units]
    return (candidates[0][1] if direction > 0 else candidates[-1][1]) if candidates else None


def adjacent_marker_index(model, current_units: int, direction: int) -> int | None:
    rows = marker_region_rows(model)
    candidates = [row for row in rows if (row.units > current_units if direction > 0
                                           else row.units < current_units)]
    if not candidates:
        return None
    return (candidates[0] if direction > 0 else candidates[-1]).indices[0]
