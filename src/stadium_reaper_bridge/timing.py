"""Canonical, derived musical and real-time geometry for a Stadium Song."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Iterable, Iterator

from .stadium import MusicalPosition, StadiumSong


@dataclass(frozen=True)
class TimingSegment:
    start_position: MusicalPosition
    start_units: int
    start_seconds: float
    tempo: float
    numerator: int
    denominator: int


@dataclass(frozen=True)
class GridPoint:
    position: MusicalPosition
    units: int

    @property
    def is_bar(self) -> bool:
        """Whether this quarter-note grid point starts a measure."""
        return self.position.beat == 1


class TimingMap:
    """Indexed piecewise timing derived without modifying START/TIME flags.

    A signature change applies at its TIME event's bar and must therefore be on
    that bar's first tick. Tempo changes may occur at any valid position.
    """

    def __init__(self, ppqn: int, changes: Iterable[tuple[MusicalPosition, float, int, int]]):
        self.ppqn = ppqn
        ordered = sorted(changes, key=lambda item: item[0])
        if not ordered or ordered[0][0] != MusicalPosition(1, 1, 1):
            raise ValueError("TimingMap requires START at 001-01.001")
        first = ordered[0]
        if first[1] <= 0 or first[2] <= 0 or first[3] <= 0:
            raise ValueError("Tempo and time signature values must be positive")

        # Signature ranges are indexed by bar, making conversions logarithmic
        # in the number of TIME events rather than linear in Song length.
        signatures = [(1, int(first[2]), int(first[3]))]
        active_num, active_den = signatures[0][1:]
        for position, _, numerator, denominator in ordered[1:]:
            numerator, denominator = int(numerator), int(denominator)
            if (numerator, denominator) != (active_num, active_den):
                if position.beat != 1 or position.tick != 1:
                    raise ValueError("Time-signature changes must occur at a bar boundary")
                signatures.append((position.bar, numerator, denominator))
                active_num, active_den = numerator, denominator
        self._signatures = tuple(signatures)
        self._signature_bars = tuple(item[0] for item in signatures)
        starts = []
        units = 0
        for index, (bar, numerator, denominator) in enumerate(signatures):
            if index:
                previous_bar, previous_num, _ = signatures[index - 1]
                units += (bar - previous_bar) * previous_num * ppqn
            starts.append(units)
        self._signature_units = tuple(starts)

        raw_segments = []
        seconds = 0.0
        previous_units = 0
        previous_tempo = float(first[1])
        for index, (position, tempo, numerator, denominator) in enumerate(ordered):
            change_units = self.position_to_units(position)
            if index:
                seconds += (change_units - previous_units) / ppqn * 60.0 / previous_tempo
            raw_segments.append(TimingSegment(position, change_units, seconds, float(tempo),
                                               int(numerator), int(denominator)))
            previous_units, previous_tempo = change_units, float(tempo)
        self.segments = tuple(raw_segments)
        self._segment_units = tuple(segment.start_units for segment in self.segments)
        self._segment_seconds = tuple(segment.start_seconds for segment in self.segments)

    @classmethod
    def from_song(cls, song: StadiumSong) -> "TimingMap":
        changes = []
        for flag in song.flags:
            if flag.type not in {"START", "TIME"}:
                continue
            data = flag.semantic_data()
            if {"tempo", "time_signature_numerator", "time_signature_denominator"} <= data.keys():
                changes.append((flag.position, data["tempo"],
                                data["time_signature_numerator"],
                                data["time_signature_denominator"]))
        return cls(song.ppqn, changes)

    def _signature_index_for_bar(self, bar: int) -> int:
        if bar < 1:
            raise ValueError("Bar must be positive")
        return bisect_right(self._signature_bars, bar) - 1

    def bar_start_units(self, bar: int) -> int:
        index = self._signature_index_for_bar(bar)
        return self._signature_units[index] + (bar - self._signature_bars[index]) * self._signatures[index][1] * self.ppqn

    def beats_in_bar(self, bar: int) -> int:
        return self._signatures[self._signature_index_for_bar(bar)][1]

    def bar_end_units(self, bar: int) -> int:
        return self.bar_start_units(bar + 1)

    def position_to_units(self, position: MusicalPosition) -> int:
        position.validate(self.ppqn)
        beats, denominator = self.signature_at_bar(position.bar)
        if position.beat > beats:
            raise ValueError(f"Beat {position.beat} is invalid in bar {position.bar} "
                             f"({beats}/{denominator})")
        return self.bar_start_units(position.bar) + (position.beat - 1) * self.ppqn + position.tick - 1

    def units_to_position(self, units: int) -> MusicalPosition:
        if units < 0:
            raise ValueError("Position precedes Song start")
        index = bisect_right(self._signature_units, units) - 1
        start_bar, numerator, _ = self._signatures[index]
        relative = units - self._signature_units[index]
        bar_offset, within = divmod(relative, numerator * self.ppqn)
        beat, tick = divmod(within, self.ppqn)
        return MusicalPosition(start_bar + bar_offset, beat + 1, tick + 1)

    def _segment_at_units(self, units: int) -> TimingSegment:
        if units < 0:
            raise ValueError("Position precedes Song start")
        return self.segments[bisect_right(self._segment_units, units) - 1]

    def tempo_at_units(self, units: int) -> float:
        return self._segment_at_units(units).tempo

    def tempo_at_position(self, position: MusicalPosition) -> float:
        return self.tempo_at_units(self.position_to_units(position))

    def signature_at_units(self, units: int) -> tuple[int, int]:
        position = self.units_to_position(units)
        item = self._signatures[self._signature_index_for_bar(position.bar)]
        return item[1], item[2]

    def signature_at_bar(self, bar: int) -> tuple[int, int]:
        item = self._signatures[self._signature_index_for_bar(bar)]
        return item[1], item[2]

    def signature_at_position(self, position: MusicalPosition) -> tuple[int, int]:
        self.position_to_units(position)  # validation
        item = self._signatures[self._signature_index_for_bar(position.bar)]
        return item[1], item[2]

    def units_to_seconds(self, units: int) -> float:
        segment = self._segment_at_units(units)
        return segment.start_seconds + (units - segment.start_units) / self.ppqn * 60.0 / segment.tempo

    def seconds_to_units(self, seconds: float) -> int:
        if seconds < 0:
            raise ValueError("Time precedes Song start")
        index = bisect_right(self._segment_seconds, seconds) - 1
        segment = self.segments[index]
        return segment.start_units + round((seconds - segment.start_seconds) * segment.tempo / 60.0 * self.ppqn)

    def position_to_seconds(self, position: MusicalPosition) -> float:
        return self.units_to_seconds(self.position_to_units(position))

    def seconds_to_position(self, seconds: float) -> MusicalPosition:
        return self.units_to_position(self.seconds_to_units(seconds))

    # Compatibility names used by waveform and transport callers.
    musical_position_to_seconds = position_to_seconds
    seconds_to_musical_position = seconds_to_position

    def iter_bars(self, start_units: int, end_units: int) -> Iterator[GridPoint]:
        bar = self.units_to_position(max(0, start_units)).bar
        while self.bar_start_units(bar) < start_units:
            bar += 1
        while self.bar_start_units(bar) <= end_units:
            yield GridPoint(MusicalPosition(bar, 1, 1), self.bar_start_units(bar))
            bar += 1

    def iter_beats(self, start_units: int, end_units: int) -> Iterator[GridPoint]:
        first = self.units_to_position(max(0, start_units))
        bar = first.bar
        while self.bar_start_units(bar) <= end_units:
            for beat in range(1, self.beats_in_bar(bar) + 1):
                units = self.bar_start_units(bar) + (beat - 1) * self.ppqn
                if start_units <= units <= end_units:
                    yield GridPoint(MusicalPosition(bar, beat, 1), units)
            bar += 1

    def nearest_beat_units(self, units: int) -> int:
        lower = max(0, units // self.ppqn * self.ppqn)
        upper = lower + self.ppqn
        return lower if units - lower <= upper - units else upper

    def nearest_bar_units(self, units: int) -> int:
        bar = self.units_to_position(max(0, units)).bar
        candidates = (self.bar_start_units(bar), self.bar_start_units(bar + 1))
        return min(candidates, key=lambda value: (abs(value - units), value))

    def shift_position(self, position: MusicalPosition, *, bars: int = 0,
                       beats: int = 0, ticks: int = 0) -> MusicalPosition:
        target_bar = position.bar + bars
        if target_bar < 1:
            raise ValueError("Movement would place an event before 001-01.001")
        if position.beat > self.beats_in_bar(target_bar):
            raise ValueError("Destination bar has fewer beats; bar shift rejected")
        units = self.position_to_units(MusicalPosition(target_bar, position.beat, position.tick))
        return self.units_to_position(units + beats * self.ppqn + ticks)
