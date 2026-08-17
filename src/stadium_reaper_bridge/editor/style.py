"""Central colour policy for editor lanes and interaction states."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LaneColors:
    normal: str
    selected: str
    outline: str
    text: str = "#101722"


LANE_PALETTE = {
    "STRUCTURE": LaneColors("#397fc4", "#63aaf0", "#92caff", "#f5f9ff"),
    "STADIUM": LaneColors("#d77a2c", "#f5a253", "#ffc88d"),
    "SECOND HELIX": LaneColors("#8d62b5", "#b38add", "#d8b9f5", "#faf7ff"),
    "VIDEO": LaneColors("#7b8795", "#a9b4c0", "#d5dce3"),
    "LIGHTS": LaneColors("#d5a11e", "#ffd45c", "#ffe49a", "#17130a"),
    "MIDI / OTHER": LaneColors("#4a9b67", "#70c78e", "#a6e1b9"),
    "SEQCLICK": LaneColors("#287e83", "#45aeb3", "#77d8dc", "#c7f5f3"),
    "SEQ INSTRUCTIONS": LaneColors("#9a8159", "#bda273", "#e4cca0", "#fff3d8"),
}


def lane_colors(lane: str) -> LaneColors:
    """Return the stable visual identity for a top-level lane."""
    return LANE_PALETTE.get(lane, LANE_PALETTE["MIDI / OTHER"])
