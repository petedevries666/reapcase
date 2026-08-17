"""Validated construction of new, losslessly serializable timeline events."""

from __future__ import annotations

from typing import Any

from ..midi import RigMidiDecoder
from ..stadium import MusicalPosition, StadiumFlag
from ..timeline import TimelineEvent, TimelineEventKind


def _event(position: MusicalPosition, payload: str, decoder: RigMidiDecoder | None = None) -> TimelineEvent:
    flag = StadiumFlag(position, payload)
    data = flag.semantic_data()
    if flag.type == "MIDI_CC" and decoder:
        alias = decoder.decode(data)
        if alias:
            data["rig_alias"] = alias
    kind = TimelineEventKind.MARKER if flag.type == "MARKER" else TimelineEventKind.FLAG
    return TimelineEvent(kind, position, data, flag)


def create_structure_marker(position: MusicalPosition, name: str) -> TimelineEvent:
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        raise ValueError("Marker name is required")
    if any(character in name for character in ";|\r\n"):
        raise ValueError("Marker name cannot contain semicolons, pipes, or line breaks")
    # This exact ten-field MARKER variant is proven by the fixture inventory.
    return _event(position, f"MARKER;{name};7;Off;Off;Off;false;[Current];[Current];[Current]")


def create_stadium_looper(position: MusicalPosition, action: str) -> TimelineEvent:
    proven = {
        "Clear Loop": "CLEAR", "Record": "RECORD", "Stop": "STOP",
        "Play": "PLAY", "Play Once": "PLAY ONCE",
    }
    if action not in proven:
        raise ValueError(f"Stadium looper action is not proven: {action!r}")
    return _event(position, f"LOOPER;{proven[action]};1;{action}")


def _midi_cc(position: MusicalPosition, midi: dict[str, int], label: str,
             decoder: RigMidiDecoder) -> TimelineEvent:
    return _event(position,
                  f"MIDI_CC;{label};4;CC;{midi['channel']};{midi['cc']};{midi['value']}",
                  decoder)


def create_second_helix_snapshot(position: MusicalPosition, snapshot: int,
                                 decoder: RigMidiDecoder) -> TimelineEvent:
    midi = decoder.encode({"system": "second_helix", "action": "snapshot", "snapshot": snapshot})
    return _midi_cc(position, midi, f"BASS SNAP {snapshot}", decoder)


def create_second_helix_looper(position: MusicalPosition, action: str,
                               decoder: RigMidiDecoder) -> TimelineEvent:
    midi = decoder.encode({"system": "second_helix", "action": action})
    return _midi_cc(position, midi, f"BASS {action.upper()}", decoder)


def create_second_helix_preset(position: MusicalPosition, bank_msb: int | None,
                               bank_lsb: int | None, program: int,
                               decoder: RigMidiDecoder) -> TimelineEvent:
    channel = decoder.second_helix_channel
    values: list[Any] = [bank_msb, bank_lsb, program]
    for name, value in zip(("Bank MSB", "Bank LSB", "Program"), values):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 127):
            raise ValueError(f"{name} must be between 0 and 127")
    if program is None:
        raise ValueError("Program is required")
    msb = "Off" if bank_msb is None else bank_msb
    lsb = "Off" if bank_lsb is None else bank_lsb
    return _event(position, f"MIDI_BANK_PROGRAM;BASS PRESET {program};5;Bank/Prog;{channel};{msb};{lsb};{program}")


def create_video_command(position: MusicalPosition, video_number: int | None, action: str,
                         decoder: RigMidiDecoder) -> TimelineEvent:
    command: dict[str, Any] = {"system": "video", "action": action}
    if action == "rescan_playlist":
        command["video"] = 0  # Canonical ignored CC for the global value-127 command.
    else:
        command["video"] = video_number
    midi = decoder.encode(command)
    alias = action.replace("play_", "").replace("_", " ").upper()
    label = "VIDEO RESCAN PLAYLIST" if action == "rescan_playlist" else f"VIDEO {video_number} {alias}"
    return _midi_cc(position, midi, label, decoder)


def create_generic_midi_cc(position: MusicalPosition, channel: int, cc: int, value: int,
                           decoder: RigMidiDecoder) -> TimelineEvent:
    for name, item, low, high in (("Channel", channel, 1, 16), ("CC", cc, 0, 127),
                                  ("Value", value, 0, 127)):
        if isinstance(item, bool) or not isinstance(item, int) or not low <= item <= high:
            raise ValueError(f"{name} must be between {low} and {high}")
    return _midi_cc(position, {"channel": channel, "cc": cc, "value": value}, "MIDI CC", decoder)
