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
    "MIDI / OTHER": LaneColors("#4a9b67", "#70c78e", "#a6e1b9"),
}


def lane_colors(lane: str) -> LaneColors:
    """Return the stable visual identity for a top-level lane."""
    return LANE_PALETTE.get(lane, LANE_PALETTE["MIDI / OTHER"])
