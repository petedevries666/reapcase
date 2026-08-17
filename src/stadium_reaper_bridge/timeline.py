"""Format-neutral timeline exchanged by future Stadium and REAPER adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .stadium import MusicalPosition


class TimelineEventKind(str, Enum):
    FLAG = "flag"
    TEMPO = "tempo"
    TIME_SIGNATURE = "time_signature"
    MARKER = "marker"


@dataclass
class TimelineEvent:
    kind: TimelineEventKind
    position: MusicalPosition
    data: dict[str, Any] = field(default_factory=dict)
    source: Any = None


@dataclass
class Timeline:
    ppqn: int
    events: list[TimelineEvent] = field(default_factory=list)
