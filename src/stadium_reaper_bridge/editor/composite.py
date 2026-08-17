"""Pure geometry and semantic routing for composite event lanes."""

from __future__ import annotations

from ..timeline import TimelineEvent
from .layout import LANE_HEIGHT, RULER_HEIGHT
from .structure import STRUCTURE_HEIGHT

COMMANDS_HEIGHT = 34
LOOPER_HEIGHT = 40
COMPOSITE_HEIGHT = COMMANDS_HEIGHT + LOOPER_HEIGHT
COMPOSITE_LANES = frozenset({"STADIUM", "SECOND HELIX"})

LOOPER_ACTIONS = frozenset({
    "record", "play", "overdub", "stop", "play once", "reverse", "forward",
    "half speed", "full speed", "undo/redo", "undo / redo", "clear", "clear loop",
    "on", "off", "block on", "block off",
})


def lane_height(lane: str) -> int:
    if lane == "STRUCTURE":
        return STRUCTURE_HEIGHT
    if lane in COMPOSITE_LANES:
        return COMPOSITE_HEIGHT
    return LANE_HEIGHT


def lane_top(lanes: tuple[str, ...], lane_or_index: str | int) -> int:
    """Return a lane top using the same cumulative geometry as the canvas."""
    index = lanes.index(lane_or_index) if isinstance(lane_or_index, str) else lane_or_index
    return RULER_HEIGHT + sum(lane_height(lane) for lane in lanes[:index])


def sublane_bounds(lanes: tuple[str, ...], lane: str, sublane: str) -> tuple[int, int]:
    """Return inclusive top/exclusive bottom bounds for a composite row."""
    if lane not in COMPOSITE_LANES:
        raise ValueError(f"Not a composite lane: {lane!r}")
    top = lane_top(lanes, lane)
    if sublane == "commands":
        return top, top + COMMANDS_HEIGHT
    if sublane == "looper":
        return top + COMMANDS_HEIGHT, top + COMPOSITE_HEIGHT
    raise ValueError(f"Unknown sublane: {sublane!r}")


def sublane_content_bounds(lanes: tuple[str, ...], lane: str, sublane: str,
                           padding: int = 4) -> tuple[int, int]:
    """Return padded drawing/hit bounds wholly contained in one sub-lane."""
    top, bottom = sublane_bounds(lanes, lane, sublane)
    if padding < 0 or top + padding >= bottom - padding:
        raise ValueError("Padding leaves no sublane content area")
    return top + padding, bottom - padding


def looper_item_bounds(lanes: tuple[str, ...], lane: str, x1: float,
                       x2: float) -> tuple[float, int, float, int]:
    """Give every looper item canonical Y bounds while preserving its X semantics."""
    y1, y2 = sublane_content_bounds(lanes, lane, "looper")
    return x1, y1, max(x1 + 1, x2), y2


def event_sublane(event: TimelineEvent, lane: str) -> str:
    """Classify by source/decoded rig semantics, never by the displayed badge."""
    if lane == "STADIUM":
        return "looper" if event.source.type == "LOOPER" else "commands"
    if lane == "SECOND HELIX":
        alias = event.data.get("rig_alias", {})
        action = alias.get("action")
        normalized = action.strip().replace("_", " ").casefold() if isinstance(action, str) else ""
        return "looper" if normalized in LOOPER_ACTIONS else "commands"
    raise ValueError(f"Not a composite lane: {lane!r}")
