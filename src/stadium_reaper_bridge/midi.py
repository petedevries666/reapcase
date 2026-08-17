"""Configuration-driven rig MIDI translation, separate from Stadium flags."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _midi_int(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


class RigMidiDecoder:
    """Decode and encode aliases described by ``rig_midi.json``."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @classmethod
    def from_file(cls, path: str | Path) -> "RigMidiDecoder":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def decode(self, midi: dict[str, Any]) -> dict[str, Any] | None:
        channel = _midi_int("channel", midi.get("channel"), 1, 16)
        cc = _midi_int("cc", midi.get("cc"), 0, 127)
        value = _midi_int("value", midi.get("value"), 0, 127)

        second = self.config["second_helix"]
        if channel == second["channel"]:
            snap = second["snapshot"]
            if cc == snap["cc"] and snap["value_min"] <= value <= snap["value_max"]:
                return {"system": "second_helix", "action": "snapshot", "snapshot": value + snap["offset"]}
            actions = second["cc"].get(str(cc))
            if actions:
                action = actions.get("high" if value >= 64 else "low")
                if action and action != "Noop":
                    return {"system": "second_helix", "action": action}

        video = self.config["video"]
        if channel == video["channel"]:
            action = video["values"].get(str(value))
            if action:
                result = {"system": "video", "action": action}
                if value != 127:
                    result["video"] = cc
                return result

        stadium = self.config["stadium_transport"]
        if stadium.get("channel") in (None, channel):
            mapping = stadium["cc"].get(str(cc))
            if mapping:
                if mapping["type"] == "trigger":
                    return {"system": "stadium_transport", "action": mapping["action"]}
                if mapping["type"] == "range":
                    key = "high" if value >= 64 else "low"
                    return {"system": "stadium_transport", "action": mapping[key]}
                result = {
                    "system": "stadium_transport",
                    "action": mapping["action"],
                    mapping["field"]: value,
                }
                if mapping.get("zero_name") and value == 0:
                    result[f'{mapping["field"]}_name'] = mapping["zero_name"]
                return result
        return None

    def encode_rig_command(self, command: dict[str, Any]) -> dict[str, int]:
        """Encode a deterministic semantic alias; reject missing or ambiguous data."""
        system, action = command.get("system"), command.get("action")
        if not isinstance(system, str) or not isinstance(action, str):
            raise ValueError("command requires string system and action")
        if system == "stadium_transport":
            return self._encode_stadium(action, command)
        if system == "second_helix":
            return self._encode_second_helix(action, command)
        if system == "video":
            return self._encode_video(action, command)
        raise ValueError(f"unknown rig MIDI system: {system!r}")

    encode = encode_rig_command

    def _encode_stadium(self, action: str, command: dict[str, Any]) -> dict[str, int]:
        stadium = self.config["stadium_transport"]
        channel = command.get("channel", stadium.get("channel"))
        if channel is not None:
            channel = _midi_int("channel", channel, 1, 16)
        matches: list[tuple[int, int]] = []
        for cc_text, mapping in stadium["cc"].items():
            if mapping.get("type") == "selector" and mapping.get("action") == action:
                field = mapping.get("field")
                if field not in command:
                    raise ValueError(f"{action} requires {field}")
                matches.append((int(cc_text), _midi_int(field, command[field], 0, 127)))
            if mapping.get("type") == "trigger" and mapping["action"] == action:
                matches.append((int(cc_text), mapping.get("canonical_value", 0)))
            if mapping.get("type") == "range":
                if mapping["low"] == action:
                    matches.append((int(cc_text), mapping.get("low_value", 0)))
                if mapping["high"] == action:
                    matches.append((int(cc_text), mapping.get("high_value", 127)))
        return self._one_match(matches, channel, action)

    def _encode_second_helix(self, action: str, command: dict[str, Any]) -> dict[str, int]:
        second = self.config["second_helix"]
        if action == "snapshot":
            snapshot = _midi_int("snapshot", command.get("snapshot"), 1, 128)
            snap = second["snapshot"]
            value = snapshot - snap["offset"]
            if not snap["value_min"] <= value <= snap["value_max"]:
                raise ValueError("snapshot is outside the configured range")
            return {"channel": second["channel"], "cc": snap["cc"], "value": value}
        matches = []
        for cc_text, ranges in second["cc"].items():
            if ranges.get("low") == action and action != "Noop":
                matches.append((int(cc_text), 0))
            if ranges.get("high") == action and action != "Noop":
                matches.append((int(cc_text), 127))
        return self._one_match(matches, second["channel"], action)

    def _encode_video(self, action: str, command: dict[str, Any]) -> dict[str, int]:
        video = self.config["video"]
        values = [int(value) for value, name in video["values"].items() if name == action]
        if len(values) != 1:
            raise ValueError(f"video action {action!r} is unknown or ambiguous")
        if "video" not in command:
            raise ValueError(f"video action {action!r} requires video")
        cc = _midi_int("video", command["video"], 0, 127)
        return {"channel": video["channel"], "cc": cc, "value": values[0]}

    @staticmethod
    def _one_match(matches: list[tuple[int, int]], channel: int | None, action: str) -> dict[str, int]:
        if len(matches) != 1:
            reason = "unknown" if not matches else "ambiguous"
            raise ValueError(f"action {action!r} is {reason} in the configuration")
        cc, value = matches[0]
        result = {"cc": cc, "value": value}
        if channel is not None:
            result["channel"] = channel
        return result
