"""Pure derivation for Reapcase's visual sequencing lanes.

Nothing in this module is serialized into a Stadium Song.  The current sequence
end is the longest resolved audio file; callers should fall back to the native
END/song extent when no audio can be resolved.
"""

from __future__ import annotations

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

    @property
    def identity(self) -> str:
        return f"{self.position.render()}:{self.kind.value}"


@dataclass(frozen=True)
class SequenceInstructionClip:
    """A derived placeholder for a future editable instruction sample clip."""

    position: MusicalPosition
    units: int
    label: str

    @property
    def identity(self) -> str:
        return f"COUNT:{self.position.render()}:{self.label}"


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
        points.append(SequenceClickPoint(beat.position, beat.units, kind))
    return tuple(points)


def derive_count_in(timing_map: TimingMap, *, bar: int = 3
                    ) -> tuple[SequenceInstructionClip, ...]:
    """Derive one semantic COUNT placeholder per beat in the requested bar."""
    beats = timing_map.beats_in_bar(bar)
    if beats > len(COUNT_LABELS):
        raise ValueError(f"COUNT vocabulary supports 1-{len(COUNT_LABELS)} beats; bar {bar} has {beats}")
    return tuple(
        SequenceInstructionClip(
            MusicalPosition(bar, beat, 1),
            timing_map.position_to_units(MusicalPosition(bar, beat, 1)),
            COUNT_LABELS[beat - 1],
        )
        for beat in range(1, beats + 1)
    )


def derive_sequence_layout(timing_map: TimingMap, end_units: int) -> SequenceLayout:
    instructions = tuple(clip for clip in derive_count_in(timing_map) if clip.units <= end_units)
    return SequenceLayout(derive_seq_clicks(timing_map, end_units), instructions, end_units)
