"""Pure semantic edit dispatch and lossless, same-type event mutations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

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
                   label: str | None = None, alias: dict | None = None) -> TimelineEvent:
    fields = _fields(event, "MIDI_CC", 7)
    fields[4] = str(_integer("MIDI channel", channel, 1, 16))
    fields[5] = str(_integer("CC", cc, 0, 127))
    fields[6] = str(_integer("Value", value, 0, 127))
    if label is not None:
        fields[1] = label
    updated = _replace_flag(event, fields)
    if alias is not None:
        updated.data["rig_alias"] = alias
    return updated


def update_second_helix(event: TimelineEvent, decoder, *, command: dict, channel: int,
                        label: str) -> TimelineEvent:
    midi = decoder.encode(command)
    midi["channel"] = _integer("MIDI channel", channel, 1, 16)
    return update_midi_cc(event, **midi, label=label, alias=command)


def update_second_helix_preset(event: TimelineEvent, *, bank_msb: int | None,
                               bank_lsb: int | None, program: int, channel: int) -> TimelineEvent:
    fields = _fields(event, "MIDI_BANK_PROGRAM", 8)
    fields[4] = str(_integer("MIDI channel", channel, 1, 16))
    for index, name, value in ((5, "Bank MSB", bank_msb), (6, "Bank LSB", bank_lsb)):
        fields[index] = "Off" if value is None else str(_integer(name, value, 0, 127))
    program = _integer("Program", program, 0, 127)
    fields[1], fields[7] = f"BASS PRESET {program}", str(program)
    return _replace_flag(event, fields)


def update_lighting_cue(event: TimelineEvent, *, name: str) -> TimelineEvent:
    if not isinstance(event.source, LightingEventSource):
        raise ValueError("Expected a lighting event")
    return replace(create_lighting_event(event.position, name, event.source.cue.kind,
                                         event.source.cue.id), source_index=event.source_index)


@dataclass(frozen=True)
class EditCapability:
    family: str
    title: str
    values: dict[str, Any]
    apply: Callable[..., TimelineEvent]


def editor_for_event(event: TimelineEvent, model) -> EditCapability | None:
    """Return one semantic editor descriptor; semantic aliases win over raw MIDI."""
    if isinstance(event.source, LightingEventSource):
        return EditCapability("lighting", f"EDIT LIGHTING {event.source.cue.kind.value}",
                              {"name": event.source.cue.name}, update_lighting_cue)
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
        return EditCapability("helix_preset", "EDIT SECOND HELIX PRESET", dict(data),
                              update_second_helix_preset)
    if kind != "MIDI_CC":
        return None
    if alias and alias.get("system") == "second_helix":
        values = {**alias, "channel": data["channel"]}
        return EditCapability("helix_" + alias["action"], "EDIT SECOND HELIX " + alias["action"].upper(),
                              values, update_second_helix)
    if alias and alias.get("system") == "video":
        return EditCapability("video", "EDIT VIDEO COMMAND", {**alias, "channel": data["channel"]},
                              update_second_helix)
    return EditCapability("midi_cc", "EDIT MIDI CC", {k: data[k] for k in ("channel", "cc", "value")},
                          update_midi_cc)


def apply_semantic_edit(event: TimelineEvent, model, capability: EditCapability,
                        values: dict[str, Any]) -> TimelineEvent:
    """Apply a descriptor without leaking family switches into Tk callbacks."""
    family = capability.family
    if family.startswith("helix_"):
        action = values["action"]
        command = {k: v for k, v in values.items() if k not in {"channel", "context"}}
        if action == "snapshot":
            label = f"BASS SNAP {values['snapshot']}"
        elif action == "expression":
            label = f"EXP{values['expression']} {0 if values['value'] == 0 else 100}%"
        else:
            label = f"BASS {action.upper()}"
        return update_second_helix(event, model.decoder, command=command,
                                   channel=values["channel"], label=label)
    if family == "video":
        action = values["action"]
        command = {"system": "video", "action": action,
                   "video": 0 if action == "rescan_playlist" else values.get("video")}
        alias = action.replace("play_", "").replace("_", " ").upper()
        label = ("VIDEO RESCAN PLAYLIST" if action == "rescan_playlist" else
                 f"VIDEO {values['video']} {alias}")
        return update_second_helix(event, model.decoder, command=command,
                                   channel=values["channel"], label=label)
    clean = {k: v for k, v in values.items() if k != "context"}
    return capability.apply(event, **clean)
