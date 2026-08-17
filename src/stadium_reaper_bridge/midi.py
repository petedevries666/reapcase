"""Configurable rig MIDI decoding, deliberately separate from Stadium parsing."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

class RigMidiDecoder:
    def __init__(self, config: dict[str, Any]): self.config = config
    @classmethod
    def from_file(cls, path: str | Path) -> "RigMidiDecoder":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))
    def decode(self, midi: dict[str, Any]) -> dict[str, Any] | None:
        channel, cc, value = midi.get("channel"), midi.get("cc"), midi.get("value")
        if channel == self.config["second_helix"]["channel"]:
            snap = self.config["second_helix"]["snapshot"]
            if cc == snap["cc"] and snap["value_min"] <= value <= snap["value_max"]:
                return {"system": "second_helix", "action": "snapshot", "snapshot": value + snap["offset"]}
            actions = self.config["second_helix"]["cc"].get(str(cc))
            if actions:
                action = actions.get("high" if value >= 64 else "low")
                if action and action != "Noop":
                    return {"system": "second_helix", "action": action}
        if channel == self.config["video"]["channel"]:
            action = self.config["video"]["values"].get(str(value))
            if action:
                result = {"system": "video", "action": action}
                if value != 127: result["video"] = cc
                return result
        return None
