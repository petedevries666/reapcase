"""Semantic lighting intentions and their derived timeline regions.

This module deliberately has no MIDI, DMX, or Tk dependency.  LIGHTS data is
part of Reapcase's show layer, not the native Stadium Song format.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Callable, Iterable, Optional, Union

from ..stadium import MusicalPosition
from ..timeline import TimelineEvent, TimelineEventKind


class LightingKind(str, Enum):
    STATE = "STATE"
    HIT = "HIT"


STATE_PRESETS = (
    "BLACKOUT", "DARK", "SINGER ONLY", "BAND", "FULL STAGE", "BIG", "HUGE",
    "RED", "BLUE", "WHITE", "SILHOUETTE", "AUDIENCE",
)
HIT_PRESETS = ("WHITE HIT", "BLINDER HIT", "BLACKOUT HIT", "STROBE", "FLASH")


def normalized_cue_id(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
    return value or "lighting_cue"


def validate_cue_name(name: str) -> str:
    value = name.strip() if isinstance(name, str) else ""
    if not value:
        raise ValueError("Lighting cue name is required")
    if len(value) > 80:
        raise ValueError("Lighting cue name must be 80 characters or fewer")
    if any(character in value for character in ";|\r\n"):
        raise ValueError("Lighting cue name cannot contain semicolons, pipes, or line breaks")
    return value


@dataclass(frozen=True)
class LightingCue:
    id: str
    name: str
    kind: LightingKind


@dataclass(frozen=True)
class LightingEventSource:
    """Source identity analogous to StadiumFlag, but never serialized as one."""
    cue: LightingCue
    type: str = "LIGHTS"


@dataclass(frozen=True)
class LightingRegion:
    cue_id: str
    label: str
    start_units: int
    end_units: int
    source_event_index: int
    open_ended: bool = False


def create_lighting_event(position: MusicalPosition, name: str, kind: Union[LightingKind, str],
                          cue_id: Optional[str] = None) -> TimelineEvent:
    name = validate_cue_name(name)
    kind = LightingKind(kind)
    identity = cue_id or normalized_cue_id(name)
    if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", identity):
        raise ValueError("Lighting cue ID must be a normalized identifier")
    cue = LightingCue(identity, name, kind)
    return TimelineEvent(TimelineEventKind.FLAG, position,
                         {"cue_id": cue.id, "name": cue.name, "kind": cue.kind.value},
                         LightingEventSource(cue))


def derive_lighting_regions(events: Iterable[TimelineEvent], units_for: Callable,
                            song_end_units: int) -> tuple[LightingRegion, ...]:
    states = [(index, event) for index, event in enumerate(events)
              if isinstance(event.source, LightingEventSource)
              and event.source.cue.kind is LightingKind.STATE]
    states.sort(key=lambda item: (units_for(item[1].position), item[0]))
    regions = []
    for offset, (source, event) in enumerate(states):
        start = units_for(event.position)
        final = offset == len(states) - 1
        end = song_end_units if final else units_for(states[offset + 1][1].position)
        cue = event.source.cue
        regions.append(LightingRegion(cue.id, cue.name, start, max(start, end), source, final))
    return tuple(regions)
