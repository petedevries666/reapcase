"""Format-neutral timeline exchanged by future Stadium and REAPER adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .stadium import MusicalPosition, StadiumFlag, StadiumSong


class TimelineEventKind(str, Enum):
    FLAG = "flag"
    TEMPO = "tempo"
    TIME_SIGNATURE = "time_signature"
    MARKER = "marker"
    CYCLE = "cycle"
    END = "end"


@dataclass
class TimelineEvent:
    kind: TimelineEventKind
    position: MusicalPosition
    data: dict[str, Any] = field(default_factory=dict)
    source: Any = None
    source_index: Optional[int] = None


@dataclass
class Timeline:
    ppqn: int
    events: list[TimelineEvent] = field(default_factory=list)


def stadium_to_timeline(song: StadiumSong, *, midi_decoder: Any = None) -> Timeline:
    """Map every source flag once, in source order, to the neutral timeline."""
    kinds = {
        "START": TimelineEventKind.TEMPO,
        "TIME": TimelineEventKind.TEMPO,
        "MARKER": TimelineEventKind.MARKER,
        "CYCLE_START": TimelineEventKind.CYCLE,
        "CYCLE_END": TimelineEventKind.CYCLE,
        "END": TimelineEventKind.END,
    }
    events = []
    for index, flag in enumerate(song.flags):
        data = flag.semantic_data()
        if flag.type in {"CYCLE_START", "CYCLE_END"}:
            data["boundary"] = "start" if flag.type == "CYCLE_START" else "end"
        if flag.type == "MIDI_CC" and midi_decoder is not None:
            decoded = midi_decoder.decode(data)
            if decoded is not None:
                data["rig_alias"] = decoded
        events.append(TimelineEvent(kinds.get(flag.type, TimelineEventKind.FLAG),
                                    flag.position, data, flag, index))
    return Timeline(song.ppqn, events)


def timeline_source_flags(timeline: Timeline) -> list[StadiumFlag]:
    """Recover lossless source flags, applying only edited event positions."""
    from dataclasses import replace
    return [replace(event.source, position=event.position) for event in timeline.events
            if isinstance(event.source, StadiumFlag)]
