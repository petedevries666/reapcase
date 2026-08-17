"""Pure layout policy for the composite device lanes."""

from __future__ import annotations

from ..timeline import TimelineEvent

COMMANDS = "COMMANDS"
LOOPER = "LOOPER"
DEVICE_LANES = frozenset({"STADIUM", "SECOND HELIX"})
DEVICE_LANE_HEIGHT = 76
DEVICE_SUBLANE_HEIGHT = DEVICE_LANE_HEIGHT // 2

# These are the semantic actions exposed by the existing looper capabilities.
# Snapshot, expression, tuner, and tap-tempo aliases consequently stay commands.
SECOND_HELIX_LOOPER_ACTIONS = frozenset({
    "Record", "Overdub", "Play", "Stop", "Play Once", "Undo/Redo",
    "Forward", "Reverse", "Full Speed", "Half Speed", "On", "Off",
})


def device_sublane(event: TimelineEvent, lane: str) -> str:
    """Classify an event from semantic source/capability data, never its label."""
    if lane not in DEVICE_LANES:
        raise ValueError(f"Not a composite device lane: {lane!r}")
    if lane == "STADIUM":
        return LOOPER if event.source.type == "LOOPER" else COMMANDS
    alias = event.data.get("rig_alias", {})
    is_looper = (alias.get("system") == "second_helix"
                 and alias.get("action") in SECOND_HELIX_LOOPER_ACTIONS)
    return LOOPER if is_looper else COMMANDS


def device_sublane_bounds(lane_top: float, sublane: str) -> tuple[float, float]:
    """Return the non-overlapping vertical bounds for a device sub-lane."""
    if sublane == COMMANDS:
        return lane_top, lane_top + DEVICE_SUBLANE_HEIGHT
    if sublane == LOOPER:
        return lane_top + DEVICE_SUBLANE_HEIGHT, lane_top + DEVICE_LANE_HEIGHT
    raise ValueError(f"Unknown device sub-lane: {sublane!r}")


def device_event_bounds(lane_top: float, sublane: str,
                        padding: float = 5) -> tuple[float, float]:
    top, bottom = device_sublane_bounds(lane_top, sublane)
    return top + padding, bottom - padding
