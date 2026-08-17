"""Validated, data-driven decoding of rig MIDI messages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_rig_midi_mapping(path: str | Path) -> dict[str, Any]:
    """Load a rig mapping document.

    The mapping stays external because controller assignments belong to a rig,
    not to either file-format adapter.
    """
    with Path(path).open(encoding="utf-8") as stream:
        document = json.load(stream)
    if document.get("version") != 1 or not isinstance(document.get("mappings"), list):
        raise ValueError("Unsupported rig MIDI mapping document")
    return document


def decode_midi_cc(
    mapping: dict[str, Any], *, rig: str, channel: int, controller: int, value: int
) -> str | None:
    """Return the configured action for a CC, treating ``Noop`` as no action."""
    if not all(isinstance(item, int) and not isinstance(item, bool)
               for item in (channel, controller, value)):
        raise ValueError("MIDI channel, controller, and value must be integers")
    if not 1 <= channel <= 16 or not 0 <= controller <= 127 or not 0 <= value <= 127:
        raise ValueError("MIDI CC fields are outside their valid range")

    for entry in mapping["mappings"]:
        if (
            entry.get("rig") == rig
            and entry.get("channel") == channel
            and entry.get("message", "").upper() == "CC"
            and entry.get("controller") == controller
        ):
            for value_range in entry.get("value_ranges", []):
                if value_range["minimum"] <= value <= value_range["maximum"]:
                    action = value_range["action"]
                    return None if action == "Noop" else action
    return None
