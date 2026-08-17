"""Testable, GUI-independent state and positional editing commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..midi import RigMidiDecoder
from ..stadium import MusicalPosition, StadiumSong
from ..timeline import Timeline, TimelineEvent, stadium_to_timeline, timeline_source_flags

LANES = ("STRUCTURE", "STADIUM", "SECOND HELIX", "VIDEO", "MIDI / OTHER")
STRUCTURE = {"START", "END", "TIME", "MARKER", "CYCLE_START", "CYCLE_END"}
KNOWN = STRUCTURE | {"PRESETSNAP", "LOOPER", "MIDI_CC", "MIDI_BANK_PROGRAM"}


@dataclass(frozen=True)
class SaveSummary:
    events_moved: int
    payloads_changed: int = 0
    tracks_changed: int = 0


class EditorModel:
    """Own editor state while leaving payloads and source documents immutable."""

    def __init__(self, song: StadiumSong, path: Path, decoder: RigMidiDecoder):
        self.song, self.path = song, path
        self.timeline: Timeline = stadium_to_timeline(song, midi_decoder=decoder)
        self.selected: set[int] = set()
        self.cursor = MusicalPosition(1, 1, 1)
        self._original_positions = [event.position for event in self.timeline.events]
        self._undo: list[tuple[list[int], list[MusicalPosition]]] = []
        start = next((e for e in self.timeline.events if e.source.type == "START"), None)
        self.tempo = start.data.get("tempo") if start else None
        self.numerator = start.data.get("time_signature_numerator", 4) if start else 4
        self.denominator = start.data.get("time_signature_denominator", 4) if start else 4

    @classmethod
    def open(cls, path: str | Path, decoder_path: str | Path = "config/rig_midi.json") -> "EditorModel":
        path = Path(path)
        return cls(StadiumSong.from_json_text(path.read_text(encoding="utf-8")), path,
                   RigMidiDecoder.from_file(decoder_path))

    @property
    def modified(self) -> bool:
        return any(e.position != p for e, p in zip(self.timeline.events, self._original_positions))

    @property
    def unsupported_types(self) -> tuple[str, ...]:
        return tuple(sorted({e.source.type for e in self.timeline.events if e.source.type not in KNOWN}))

    def lane(self, event: TimelineEvent) -> str:
        kind = event.source.type
        if kind in STRUCTURE:
            return "STRUCTURE"
        if kind in {"PRESETSNAP", "LOOPER"}:
            return "STADIUM"
        alias = event.data.get("rig_alias", {})
        if alias.get("system") == "second_helix" or (kind == "MIDI_BANK_PROGRAM" and event.data.get("channel") == 3):
            return "SECOND HELIX"
        if alias.get("system") == "video":
            return "VIDEO"
        return "MIDI / OTHER"

    def lane_counts(self) -> dict[str, int]:
        return {lane: sum(self.lane(e) == lane for e in self.timeline.events) for lane in LANES}

    def label(self, event: TimelineEvent) -> str:
        source, data = event.source, event.data
        alias = data.get("rig_alias", {})
        if alias.get("system") == "video":
            return f"VIDEO {alias.get('video', '')} {alias['action'].replace('_', ' ').upper()}".replace("  ", " ")
        if alias.get("system") == "second_helix":
            if alias.get("action") == "snapshot":
                return f"BASS SNAP {alias['snapshot']}"
            return f"BASS {alias['action'].upper()}"
        if source.type == "TIME":
            return f"TIME {data.get('tempo', '?'):g} BPM {data.get('time_signature_numerator', '?')}/{data.get('time_signature_denominator', '?')}"
        if source.type == "LOOPER":
            return f"LOOPER {data.get('action', '').upper()}".strip()
        human = data.get("name") or data.get("label")
        if source.type == "PRESETSNAP" and data.get("snapshot"):
            human = human or f"SNAP {data['snapshot']}"
        return str(human or source.type).strip()

    def select_all(self) -> None:
        self.selected = set(range(len(self.timeline.events)))

    def select_lane(self, lane: str) -> None:
        self.selected = {i for i, event in enumerate(self.timeline.events) if self.lane(event) == lane}

    def select_all_after_cursor(self) -> None:
        cutoff = self._units(self.cursor)
        self.selected = {i for i, event in enumerate(self.timeline.events) if self._units(event.position) >= cutoff}

    def _units(self, position: MusicalPosition) -> int:
        return ((position.bar - 1) * self.numerator + position.beat - 1) * self.song.ppqn + position.tick - 1

    def _position(self, units: int) -> MusicalPosition:
        if units < 0:
            raise ValueError("Movement would place an event before 001-01.001")
        beat, tick = divmod(units, self.song.ppqn)
        bar, beat = divmod(beat, self.numerator)
        return MusicalPosition(bar + 1, beat + 1, tick + 1)

    def shift_selected(self, bars: int = 0, beats: int = 0, ticks: int = 0) -> int:
        indices = sorted(self.selected)
        if not indices:
            return 0
        delta = (bars * self.numerator + beats) * self.song.ppqn + ticks
        targets = [self._position(self._units(self.timeline.events[i].position) + delta) for i in indices]
        previous = [self.timeline.events[i].position for i in indices]
        self._undo.append((indices, previous))
        for index, target in zip(indices, targets):
            self.timeline.events[index].position = target
        return len(indices)

    def undo(self) -> bool:
        if not self._undo:
            return False
        indices, positions = self._undo.pop()
        for index, position in zip(indices, positions):
            self.timeline.events[index].position = position
        return True

    def save_as(self, path: str | Path) -> SaveSummary:
        self.song.flags = timeline_source_flags(self.timeline)
        Path(path).write_text(self.song.to_json_text(), encoding="utf-8")
        return SaveSummary(sum(e.position != p for e, p in zip(self.timeline.events, self._original_positions)))

