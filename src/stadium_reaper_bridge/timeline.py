"""Format-neutral timeline exchanged by future Stadium and REAPER adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from .stadium import MusicalPosition

if TYPE_CHECKING:
    from .stadium import StadiumSong


class TimelineEventKind(str, Enum):
    FLAG = "flag"
    TEMPO = "tempo"
    TIME_SIGNATURE = "time_signature"
    MARKER = "marker"


@dataclass
class TimelineEvent:
    """One source event in the neutral timeline.

    Stadium ``START`` and ``TIME`` flags produce one :attr:`TEMPO` event whose
    data contains ``tempo``, ``numerator``, and ``denominator``.  They do *not*
    produce a second :attr:`TIME_SIGNATURE` event: retaining one event and its
    ``source`` preserves source order and permits a lossless round trip.  A
    REAPER adapter must use all three fields to emit its combined tempo/time-
    signature marker.  ``TIME_SIGNATURE`` remains available for formats where
    a signature is an independent source event.
    """
    kind: TimelineEventKind
    position: MusicalPosition
    data: dict[str, Any] = field(default_factory=dict)
    source: Any = None


@dataclass
class Timeline:
    ppqn: int
    events: list[TimelineEvent] = field(default_factory=list)


def stadium_to_timeline(song: "StadiumSong") -> Timeline:
    """Project Stadium flags into a source-ordered neutral timeline."""
    events: list[TimelineEvent] = []
    for flag in song.flags:
        data: dict[str, Any] = {"payload": flag.payload}
        kind = TimelineEventKind.FLAG
        if flag.type in {"START", "TIME"}:
            fields = flag.payload.split(";")
            try:
                data = {
                    "tempo": float(fields[3]),
                    "numerator": int(fields[5]),
                    "denominator": int(fields[6]),
                }
            except (IndexError, ValueError) as error:
                raise ValueError(
                    f"Malformed Stadium {flag.type} flag: {flag.payload!r}"
                ) from error
            kind = TimelineEventKind.TEMPO
        elif flag.type == "MARKER":
            kind = TimelineEventKind.MARKER
        events.append(TimelineEvent(kind, flag.position, data, source=flag))
    return Timeline(song.ppqn, events)
