"""Validated construction of new, losslessly serializable timeline events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Union

from ..midi import RigMidiDecoder
from ..stadium import MusicalPosition, StadiumFlag
from ..timeline import TimelineEvent, TimelineEventKind


def _event(position: MusicalPosition, payload: str, decoder: Optional[RigMidiDecoder] = None) -> TimelineEvent:
    flag = StadiumFlag(position, payload)
    data = flag.semantic_data()
    if flag.type == "MIDI_CC" and decoder:
        alias = decoder.decode(data)
        if alias:
            data["rig_alias"] = alias
    kind = TimelineEventKind.MARKER if flag.type == "MARKER" else TimelineEventKind.FLAG
    return TimelineEvent(kind, position, data, flag)


@dataclass(frozen=True)
class MarkerOptions:
    """The fixture-proven, authorable subset of a Stadium MARKER.

    The remaining fields deliberately stay at the sole observed safe template;
    callers do not manipulate semicolon offsets directly.
    """

    name: str
    pause_at_marker: bool = False
    cycle_marker: bool = False


def serialize_marker(options: MarkerOptions) -> str:
    name = options.name.strip() if isinstance(options.name, str) else ""
    if not name:
        raise ValueError("Marker name is required")
    if any(character in name for character in ";|\r\n"):
        raise ValueError("Marker name cannot contain semicolons, pipes, or line breaks")
    pause = "On" if options.pause_at_marker else "Off"
    cycle = "On" if options.cycle_marker else "Off"
    return f"MARKER;{name};7;Off;{pause};{cycle};false;[Current];[Current];[Current]"


def parse_marker(flag: StadiumFlag) -> MarkerOptions:
    fields = flag.fields
    if flag.type != "MARKER" or len(fields) != 10 or fields[4] not in {"On", "Off"}:
        raise ValueError("Marker does not use the proven ten-field option layout")
    if fields[5] not in {"On", "Off"}:
        raise ValueError("Marker cycle option is not a proven On/Off value")
    return MarkerOptions(fields[1], fields[4] == "On", fields[5] == "On")


def create_structure_marker(position: MusicalPosition, name: Union[str, MarkerOptions],
                            pause_at_marker: bool = False) -> TimelineEvent:
    options = name if isinstance(name, MarkerOptions) else MarkerOptions(name, pause_at_marker)
    return _event(position, serialize_marker(options))


@dataclass(frozen=True)
class StadiumContext:
    setlist: str
    preset: str


def stadium_context_at(events: Iterable[TimelineEvent], position: MusicalPosition) -> Optional[StadiumContext]:
    """Return the last explicit preset context at or before *position*.

    START and PRESETSNAP share the fixture-proven setlist/preset fields. A
    ``[Current]`` value preserves an already-known component but cannot create
    knowledge where none exists.
    """
    context: Optional[StadiumContext] = None
    for event in sorted(events, key=lambda item: item.position):
        if event.position > position:
            break
        if event.source.type not in {"START", "PRESETSNAP"}:
            continue
        setlist, preset = event.data.get("setlist"), event.data.get("preset")
        previous_setlist = context.setlist if context else None
        previous_preset = context.preset if context else None
        resolved_setlist = previous_setlist if setlist == "[Current]" else setlist
        resolved_preset = previous_preset if preset == "[Current]" else preset
        if resolved_setlist and resolved_preset:
            context = StadiumContext(str(resolved_setlist), str(resolved_preset))
    return context


def create_stadium_snapshot(position: MusicalPosition, snapshot: int,
                            events: Iterable[TimelineEvent]) -> TimelineEvent:
    if isinstance(snapshot, bool) or not isinstance(snapshot, int) or not 1 <= snapshot <= 8:
        raise ValueError("Snapshot must be between 1 and 8")
    context = stadium_context_at(events, position)
    if context is None:
        raise ValueError("No proven Stadium preset context is active at this position")
    return _event(position,
                  f"PRESETSNAP;;3;{context.setlist};{context.preset};Snap {snapshot}")


def create_cycle_start(position: MusicalPosition) -> TimelineEvent:
    return _event(position, "CYCLE_START;;2;Infinite;Off")


def create_cycle_end(position: MusicalPosition, events: Iterable[TimelineEvent]) -> TimelineEvent:
    depth = 0
    for event in sorted(events, key=lambda item: item.position):
        if event.position > position:
            break
        if event.source.type == "CYCLE_START":
            depth += 1
        elif event.source.type == "CYCLE_END" and depth:
            depth -= 1
    if depth == 0:
        raise ValueError("Cycle End requires an unmatched Cycle Start before this position")
    return _event(position, "CYCLE_END;;0")


# Parseability is intentionally distinct from safe authoring capability.
FLAG_CAPABILITIES = {
    "MARKER": {"parseable": True, "creatable": True, "editable": True},
    "PRESETSNAP": {"parseable": True, "creatable": True, "editable": True},
    "CYCLE_START": {"parseable": True, "creatable": True, "editable": True},
    "CYCLE_END": {"parseable": True, "creatable": True, "editable": False},
    "START": {"parseable": True, "creatable": False, "editable": False},
    "TIME": {"parseable": True, "creatable": False, "editable": False},
    "END": {"parseable": True, "creatable": False, "editable": True},
    "LOOPER": {"parseable": True, "creatable": True, "editable": True},
    "MIDI_CC": {"parseable": True, "creatable": True, "editable": True},
    "MIDI_BANK_PROGRAM": {"parseable": True, "creatable": True, "editable": True},
}


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


def create_second_helix_expression(position: MusicalPosition, expression: int, value: int,
                                   decoder: RigMidiDecoder) -> TimelineEvent:
    """Create one configured Helix expression endpoint CC event."""
    if isinstance(expression, bool) or not isinstance(expression, int) or expression not in (1, 2, 3):
        raise ValueError("Expression must be 1, 2, or 3")
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 127):
        raise ValueError("Expression value must be 0 or 127")
    midi = decoder.encode({"system": "second_helix", "action": "expression",
                           "expression": expression, "value": value})
    percentage = 0 if value == 0 else 100
    return _midi_cc(position, midi, f"EXP{expression} {percentage}%", decoder)


def create_second_helix_looper(position: MusicalPosition, action: str,
                               decoder: RigMidiDecoder) -> TimelineEvent:
    midi = decoder.encode({"system": "second_helix", "action": action})
    return _midi_cc(position, midi, f"BASS {action.upper()}", decoder)


def create_second_helix_preset(position: MusicalPosition, bank_msb: Optional[int],
                               bank_lsb: Optional[int], program: int,
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


def create_video_command(position: MusicalPosition, video_number: Optional[int], action: str,
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
