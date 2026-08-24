"""Pure semantic edit dispatch and lossless, same-type event mutations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from ..stadium import StadiumFlag
from ..timeline import TimelineEvent
from .creation import FLAG_CAPABILITIES
from .lighting import LightingEventSource, create_lighting_event


STADIUM_LOOPER_ACTIONS = ("Clear Loop", "Record", "Stop", "Play", "Play Once")
CYCLE_COUNTS = ("Infinite",)  # the sole value established by native fixtures
CYCLE_OPTIONS = ("Off",)     # the sole value established by native fixtures


def _integer(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _fields(event: TimelineEvent, expected: str, minimum: int) -> list[str]:
    if not isinstance(event.source, StadiumFlag) or event.source.type != expected:
        raise ValueError(f"Expected a {expected} event")
    fields = list(event.source.fields)
    if len(fields) < minimum:
        raise ValueError(f"{expected} does not use a proven editable payload layout")
    return fields


def _replace_flag(event: TimelineEvent, fields: list[str]) -> TimelineEvent:
    source = replace(event.source, payload=";".join(fields), original=None)
    data = source.semantic_data()
    return replace(event, data=data, source=source)


def _label(value: Any) -> str:
    """Validate a user-authored Stadium flag label, including an empty one."""
    if not isinstance(value, str):
        raise ValueError("Label must be text")
    if any(character in value for character in ";|\r\n"):
        raise ValueError("Label cannot contain semicolons, pipes, or line breaks")
    return value


def update_marker(event: TimelineEvent, *, name: str, pause_at_marker: bool,
                  cycle_marker: bool) -> TimelineEvent:
    fields = _fields(event, "MARKER", 10)
    name = name.strip()
    if not name:
        raise ValueError("Marker name is required")
    if any(c in name for c in ";|\r\n"):
        raise ValueError("Marker name cannot contain semicolons, pipes, or line breaks")
    if fields[4] not in {"On", "Off"} or fields[5] not in {"On", "Off"}:
        raise ValueError("Marker options do not use proven On/Off values")
    fields[1], fields[4], fields[5] = name, "On" if pause_at_marker else "Off", "On" if cycle_marker else "Off"
    return _replace_flag(event, fields)


def update_stadium_snapshot(event: TimelineEvent, *, snapshot: int) -> TimelineEvent:
    fields = _fields(event, "PRESETSNAP", 6)
    fields[5] = f"Snap {_integer('Snapshot', snapshot, 1, 8)}"
    return _replace_flag(event, fields)


def update_cycle_start(event: TimelineEvent, *, repeat_count: str, option: str) -> TimelineEvent:
    fields = _fields(event, "CYCLE_START", 5)
    if repeat_count not in CYCLE_COUNTS or option not in CYCLE_OPTIONS:
        raise ValueError("Cycle values are not proven native Stadium options")
    fields[3], fields[4] = repeat_count, option
    return _replace_flag(event, fields)


def update_stadium_looper(event: TimelineEvent, *, action: str) -> TimelineEvent:
    fields = _fields(event, "LOOPER", 4)
    if action not in STADIUM_LOOPER_ACTIONS:
        raise ValueError(f"Stadium looper action is not proven: {action!r}")
    tokens = {"Clear Loop": "CLEAR", "Record": "RECORD", "Stop": "STOP",
              "Play": "PLAY", "Play Once": "PLAY ONCE"}
    fields[1], fields[3] = tokens[action], action
    return _replace_flag(event, fields)


def update_midi_cc(event: TimelineEvent, *, channel: int, cc: int, value: int,
                   label: Optional[str] = None, alias: Optional[dict] = None) -> TimelineEvent:
    fields = _fields(event, "MIDI_CC", 7)
    fields[4] = str(_integer("MIDI channel", channel, 1, 16))
    fields[5] = str(_integer("CC", cc, 0, 127))
    fields[6] = str(_integer("Value", value, 0, 127))
    if label is not None:
        fields[1] = _label(label)
    updated = _replace_flag(event, fields)
    if alias is not None:
        updated.data["rig_alias"] = alias
    return updated


def update_second_helix(event: TimelineEvent, decoder, *, command: dict, channel: int,
                        label: str) -> TimelineEvent:
    midi = decoder.encode(command)
    midi["channel"] = _integer("MIDI channel", channel, 1, 16)
    return update_midi_cc(event, **midi, label=label, alias=command)


def update_second_helix_preset(event: TimelineEvent, *, bank_msb: Optional[int],
                               bank_lsb: Optional[int], program: int, channel: int,
                               label: str) -> TimelineEvent:
    fields = _fields(event, "MIDI_BANK_PROGRAM", 8)
    fields[4] = str(_integer("MIDI channel", channel, 1, 16))
    for index, name, value in ((5, "Bank MSB", bank_msb), (6, "Bank LSB", bank_lsb)):
        fields[index] = "Off" if value is None else str(_integer(name, value, 0, 127))
    program = _integer("Program", program, 0, 127)
    fields[1] = _label(label)
    fields[7] = str(program)
    return _replace_flag(event, fields)


def update_lighting_cue(event: TimelineEvent, *, label: str) -> TimelineEvent:
    if not isinstance(event.source, LightingEventSource):
        raise ValueError("Expected a lighting event")
    return replace(create_lighting_event(event.position, label, event.source.cue.kind,
                                         event.source.cue.id), source_index=event.source_index)


@dataclass(frozen=True)
class EditCapability:
    family: str
    title: str
    values: dict[str, Any]
    apply: Callable[..., TimelineEvent]


def editor_for_event(event: TimelineEvent, model) -> Optional[EditCapability]:
    """Return one semantic editor descriptor; semantic aliases win over raw MIDI."""
    if isinstance(event.source, LightingEventSource):
        return EditCapability("lighting", f"EDIT LIGHTING {event.source.cue.kind.value}",
                              {"label": event.source.cue.name}, update_lighting_cue)
    kind = event.source.type
    if not FLAG_CAPABILITIES.get(kind, {}).get("editable"):
        return None
    data, alias = event.data, event.data.get("rig_alias")
    if kind == "MARKER":
        return EditCapability("marker", "EDIT MARKER", {"name": data["name"],
            "pause_at_marker": data["pause_at_marker"] == "On",
            "cycle_marker": data["cycle_marker"] == "On"}, update_marker)
    if kind == "PRESETSNAP":
        try: snapshot = int(str(data["snapshot"]).removeprefix("Snap "))
        except (ValueError, KeyError): return None
        return EditCapability("stadium_snapshot", "EDIT STADIUM SNAPSHOT",
                              {"snapshot": snapshot, "context": f"{data['setlist']} / {data['preset']}"},
                              update_stadium_snapshot)
    if kind == "CYCLE_START" and data.get("repeat_count") in CYCLE_COUNTS and data.get("option") in CYCLE_OPTIONS:
        return EditCapability("cycle", "EDIT CYCLE", dict(data), update_cycle_start)
    if kind == "LOOPER" and data.get("action") in STADIUM_LOOPER_ACTIONS:
        return EditCapability("stadium_looper", "EDIT STADIUM LOOPER",
                              {"action": data["action"]}, update_stadium_looper)
    if kind == "MIDI_BANK_PROGRAM" and model.lane(event) == "SECOND HELIX":
        values = {key: data[key] for key in ("label", "channel", "bank_msb", "bank_lsb", "program")}
        return EditCapability("helix_preset", "EDIT SECOND HELIX PRESET", values,
                              update_second_helix_preset)
    if kind != "MIDI_CC":
        return None
    if (alias and alias.get("system") == "second_helix"
            and isinstance(alias.get("action"), str)):
        values = {"label": data.get("label", ""), **alias, "channel": data["channel"]}
        return EditCapability("helix_" + alias["action"], "EDIT SECOND HELIX " + alias["action"].upper(),
                              values, update_second_helix)
    if alias and alias.get("system") == "video" and isinstance(alias.get("action"), str):
        return EditCapability("video", "EDIT VIDEO COMMAND", {"label": data.get("label", ""), **alias, "channel": data["channel"]},
                              update_second_helix)
    return EditCapability("midi_cc", "EDIT MIDI CC", {k: data[k] for k in ("label", "channel", "cc", "value")},
                          update_midi_cc)


def apply_semantic_edit(event: TimelineEvent, model, capability: EditCapability,
                        values: dict[str, Any]) -> TimelineEvent:
    """Apply a descriptor without leaking family switches into Tk callbacks."""
    family = capability.family
    # MIDI_BANK_PROGRAM has Helix in its UI family name, but it is not an
    # alias-decoded MIDI_CC action.  Dispatch it directly to its proven
    # channel/bank/program editor before considering action-based commands.
    if family == "helix_preset":
        clean = {k: v for k, v in values.items() if k != "context"}
        return capability.apply(event, **clean)
    if family in {"helix_snapshot", "helix_expression"} or (
            family.startswith("helix_") and "action" in values):
        action = values["action"]
        command = {k: v for k, v in values.items() if k not in {"channel", "context", "label"}}
        return update_second_helix(event, model.decoder, command=command,
                                   channel=values["channel"], label=values["label"])
    if family == "video":
        action = values["action"]
        command = {"system": "video", "action": action,
                   "video": 0 if action == "rescan_playlist" else values.get("video")}
        return update_second_helix(event, model.decoder, command=command,
                                   channel=values["channel"], label=values["label"])
    clean = {k: v for k, v in values.items() if k != "context"}
    return capability.apply(event, **clean)
