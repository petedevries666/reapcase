"""Testable, GUI-independent state and positional editing commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..midi import RigMidiDecoder
from ..stadium import MusicalPosition, StadiumSong
from ..timeline import Timeline, TimelineEvent, stadium_to_timeline, timeline_source_flags
from .audio import (AudioResolver, TempoChange, TempoMap, audio_track_views,
                    stadium_backup_audio_paths)

LANES = ("STRUCTURE", "STADIUM", "SECOND HELIX", "VIDEO", "MIDI / OTHER")
STRUCTURE = {"START", "END", "TIME", "MARKER", "CYCLE_START", "CYCLE_END"}
KNOWN = STRUCTURE | {"PRESETSNAP", "LOOPER", "MIDI_CC", "MIDI_BANK_PROGRAM"}


@dataclass(frozen=True)
class SaveSummary:
    events_moved: int
    payloads_changed: int = 0
    tracks_changed: int = 0


@dataclass(frozen=True)
class MovePreview:
    """An immutable proposed edit; constructing one cannot alter the timeline."""

    indices: tuple[int, ...]
    original: tuple[MusicalPosition, ...]
    targets: tuple[MusicalPosition, ...]
    delta_units: int
    valid: bool

    @property
    def destination(self) -> MusicalPosition | None:
        return self.targets[0] if self.targets else None


class EditorModel:
    """Own editor state while leaving payloads and source documents immutable."""

    def __init__(self, song: StadiumSong, path: Path, decoder: RigMidiDecoder):
        self.song, self.path = song, path
        self.timeline: Timeline = stadium_to_timeline(song, midi_decoder=decoder)
        self.decoder = decoder
        self.selected: set[int] = set()
        self.cursor = MusicalPosition(1, 1, 1)
        self._original_positions = [event.position for event in self.timeline.events]
        self._undo: list[tuple] = []
        self._created = 0
        start = next((e for e in self.timeline.events if e.source.type == "START"), None)
        self.tempo = start.data.get("tempo") if start else None
        self.numerator = start.data.get("time_signature_numerator", 4) if start else 4
        self.denominator = start.data.get("time_signature_denominator", 4) if start else 4
        self.audio_root: Path | None = None
        self.audio_tracks = ()
        changes = [TempoChange(self._units(e.position), float(e.data["tempo"]))
                   for e in self.timeline.events
                   if e.source.type in {"START", "TIME"} and e.data.get("tempo")]
        self.tempo_map = (TempoMap(self.song.ppqn, changes, self._units, self._position)
                          if changes and changes[0].units == 0 else None)
        self.resolve_audio()

    @classmethod
    def open(cls, path: str | Path, decoder_path: str | Path = "config/rig_midi.json") -> "EditorModel":
        path = Path(path)
        return cls(StadiumSong.from_json_text(path.read_text(encoding="utf-8")), path,
                   RigMidiDecoder.from_file(decoder_path))

    @property
    def modified(self) -> bool:
        return (self._created > 0 or len(self.timeline.events) != len(self._original_positions)
                or any(e.position != p for e, p in zip(self.timeline.events, self._original_positions)))

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

    def resolve_audio(self, root: str | Path | None = None) -> None:
        if root is not None:
            self.audio_root = Path(root)
        automatic = stadium_backup_audio_paths(self.path)
        resolver = AudioResolver(self.path.parent, self.audio_root,
                                 automatic[0] if automatic else None,
                                 automatic[1] if automatic else None)
        self.audio_tracks = audio_track_views(self.song.tracks, resolver)

    @property
    def audio_overflow(self) -> int:
        return max(0, len(self.song.tracks) - 8) if isinstance(self.song.tracks, list) else 0

    @property
    def audio_end_units(self) -> int:
        # All controlled fixtures use offset=0. Unknown non-zero offset units are
        # deliberately not interpreted; such clips remain visible at Song start.
        if not self.tempo_map:
            return 0
        return max((self.tempo_map.seconds_to_units(track.file_info.duration_seconds)
                    for track in self.audio_tracks if track.file_info), default=0)

    @property
    def song_end_units(self) -> int:
        return max(max((self._units(e.position) for e in self.timeline.events), default=0),
                   self.audio_end_units)

    def apply_marquee(self, indices: Iterable[int], mode: str = "replace") -> None:
        indices = set(indices)
        if mode == "replace":
            self.selected = indices
        elif mode == "add":
            self.selected.update(indices)
        elif mode == "toggle":
            self.selected.symmetric_difference_update(indices)
        else:
            raise ValueError(f"Unknown marquee mode: {mode}")

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

    def select_for_drag(self, index: int, toggle: bool = False) -> None:
        """Apply pointer-down selection rules before constructing a drag preview.

        A plain click only replaces the selection when it lands outside the
        current selection.  This lets an already-selected event act as the drag
        handle for the entire selection.
        """
        if toggle:
            self.selected.symmetric_difference_update({index})
        elif index not in self.selected:
            self.selected = {index}

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
        self._undo.append(("move", indices, previous, set(self.selected)))
        for index, target in zip(indices, targets):
            self.timeline.events[index].position = target
        return len(indices)

    def preview_shift(self, delta_units: int) -> MovePreview:
        """Calculate an atomic selected-event move without mutating model state."""
        indices = tuple(sorted(self.selected, key=lambda i: self._units(self.timeline.events[i].position)))
        original = tuple(self.timeline.events[i].position for i in indices)
        target_units = tuple(self._units(position) + delta_units for position in original)
        if any(units < 0 for units in target_units):
            return MovePreview(indices, original, (), delta_units, False)
        return MovePreview(indices, original, tuple(self._position(units) for units in target_units),
                           delta_units, True)

    def commit_preview(self, preview: MovePreview) -> int:
        """Apply one still-current preview as one undoable model edit."""
        if not preview.valid:
            return 0
        if any(self.timeline.events[i].position != position
               for i, position in zip(preview.indices, preview.original)):
            raise ValueError("Timeline changed since the drag began")
        if not preview.indices or preview.delta_units == 0:
            return 0
        self._undo.append(("move", list(preview.indices), list(preview.original), set(self.selected)))
        for index, target in zip(preview.indices, preview.targets):
            self.timeline.events[index].position = target
        return len(preview.indices)

    def undo(self) -> bool:
        if not self._undo:
            return False
        operation = self._undo.pop()
        if operation[0] == "create":
            _, event, previous_selection = operation
            self.timeline.events.remove(event)
            self.selected = previous_selection
            self._created -= 1
            return True
        _, indices, positions, previous_selection = operation
        for index, position in zip(indices, positions):
            self.timeline.events[index].position = position
        self.selected = previous_selection
        return True

    def insert_event(self, event: TimelineEvent) -> int:
        """Append a created event with stable source order as one undo operation."""
        previous_selection = set(self.selected)
        next_index = max((e.source_index for e in self.timeline.events
                          if e.source_index is not None), default=-1) + 1
        event.source_index = next_index
        self.timeline.events.append(event)
        index = len(self.timeline.events) - 1
        self.selected = {index}
        self._undo.append(("create", event, previous_selection))
        self._created += 1
        return index

    def save_as(self, path: str | Path) -> SaveSummary:
        # Serialize a timeline projection without turning it into new source
        # state.  This keeps Undo after Save As capable of restoring the exact
        # opened document.
        source_flags = self.song.flags
        try:
            self.song.flags = timeline_source_flags(self.timeline)
            Path(path).write_text(self.song.to_json_text(), encoding="utf-8")
        finally:
            self.song.flags = source_flags
        return SaveSummary(sum(e.position != p for e, p in zip(self.timeline.events, self._original_positions)))
