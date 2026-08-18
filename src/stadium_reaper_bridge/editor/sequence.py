"""Pure derivation for Reapcase's visual sequencing lanes.

Nothing in this module is serialized into a Stadium Song.  The current sequence
end is the longest resolved audio file; callers should fall back to the native
END/song extent when no audio can be resolved.
"""

from __future__ import annotations
from typing import Optional

from dataclasses import dataclass
from enum import Enum

from ..stadium import MusicalPosition
from ..timing import TimingMap


class SequenceClickKind(str, Enum):
    ACCENT = "ACCENT"
    TICKSECOND = "TICKSECOND"


@dataclass(frozen=True)
class SequenceClickPoint:
    """A locked beat-derived point with an identity suitable for mute overrides."""

    position: MusicalPosition
    units: int
    kind: SequenceClickKind
    end_units: int

    @property
    def identity(self) -> str:
        return f"{self.position.render()}:{self.kind.value}"


@dataclass
class SequenceInstructionClip:
    """An editable, Reapcase-owned instruction sample clip."""

    id: str
    position: MusicalPosition
    units: int
    label: str
    muted: bool = False
    origin: str = "generated_count"
    sample_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {"id": self.id, "position": self.position.render(), "label": self.label,
                "muted": self.muted, "origin": self.origin,
                "sample_id": self.sample_id or self.label.casefold()}


@dataclass(frozen=True)
class SequenceLayout:
    clicks: tuple[SequenceClickPoint, ...]
    instructions: tuple[SequenceInstructionClip, ...]
    end_units: int


COUNT_LABELS = (
    "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX",
    "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN", "TWELVE",
)


def derive_seq_clicks(timing_map: TimingMap, end_units: int, *, start_bar: int = 2
                      ) -> tuple[SequenceClickPoint, ...]:
    """Derive beat clicks, inclusive of a boundary exactly at ``end_units``."""
    if end_units < timing_map.bar_start_units(start_bar):
        return ()
    points = []
    for beat in timing_map.iter_beats(timing_map.bar_start_units(start_bar), end_units):
        kind = (SequenceClickKind.ACCENT if beat.position.beat == 1
                else SequenceClickKind.TICKSECOND)
        next_position = timing_map.shift_position(beat.position, beats=1)
        points.append(SequenceClickPoint(beat.position, beat.units, kind,
                                         timing_map.position_to_units(next_position)))
    return tuple(points)


def derive_count_in(timing_map: TimingMap, *, bar: int = 3
                    ) -> tuple[SequenceInstructionClip, ...]:
    """Derive one semantic COUNT placeholder per beat in the requested bar."""
    beats = timing_map.beats_in_bar(bar)
    if beats > len(COUNT_LABELS):
        raise ValueError(f"COUNT vocabulary supports 1-{len(COUNT_LABELS)} beats; bar {bar} has {beats}")
    return tuple(
        SequenceInstructionClip(
            f"count_{bar}_{beat}",
            MusicalPosition(bar, beat, 1),
            timing_map.position_to_units(MusicalPosition(bar, beat, 1)),
            COUNT_LABELS[beat - 1],
            sample_id=COUNT_LABELS[beat - 1].casefold(),
        )
        for beat in range(1, beats + 1)
    )


def derive_sequence_layout(timing_map: TimingMap, end_units: int,
                           instructions=None) -> SequenceLayout:
    if instructions is None:
        instructions = derive_count_in(timing_map)
    instructions = tuple(clip for clip in instructions if clip.units <= end_units)
    return SequenceLayout(derive_seq_clicks(timing_map, end_units), instructions, end_units)
