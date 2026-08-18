"""Testable, GUI-independent state and positional editing commands."""

from __future__ import annotations

from dataclasses import dataclass
import copy
import json
from pathlib import Path
import shutil
import wave
from typing import Iterable

from ..midi import RigMidiDecoder
from ..stadium import MusicalPosition, StadiumSong
from ..timeline import Timeline, TimelineEvent, stadium_to_timeline, timeline_source_flags
from .audio import (MAX_AUDIO_TRACKS, AudioResolver, audio_track_views, read_wav_info,
                    stadium_backup_audio_paths)
from ..timing import TimingMap
from .display import badge_text
from .lighting import (LightingEventSource, create_lighting_event,
                       normalized_cue_id)
from .ergonomics import backup_existing

EVENT_LANES = ("STRUCTURE", "STADIUM", "SECOND HELIX", "VIDEO", "LIGHTS", "MIDI / OTHER")
LANES = EVENT_LANES + ("SEQCLICK", "SEQ INSTRUCTIONS")
STRUCTURE = {"START", "END", "TIME", "MARKER", "CYCLE_START", "CYCLE_END"}
KNOWN = STRUCTURE | {"PRESETSNAP", "LOOPER", "MIDI_CC", "MIDI_BANK_PROGRAM", "LIGHTS"}
NON_COPYABLE = {"START", "TIME", "END", "SEQCLICK"}


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
        self._show_document: dict = {}
        self._load_show_layer()
        self.decoder = decoder
        self.selected: set[int] = set()
        self.cursor = MusicalPosition(1, 1, 1)
        self._original_positions = [event.position for event in self.timeline.events]
        self._undo: list[tuple] = []
        self._event_clipboard: tuple[TimelineEvent, ...] = ()
        self._created = 0
        self._structural_edits = 0
        self._audio_edits = 0
        start = next((e for e in self.timeline.events if e.source.type == "START"), None)
        self.tempo = start.data.get("tempo") if start else None
        self.numerator = start.data.get("time_signature_numerator", 4) if start else 4
        self.denominator = start.data.get("time_signature_denominator", 4) if start else 4
        self.audio_root: Path | None = None
        self.audio_tracks = ()
        self._audio_identities: dict[Path, tuple[int, int]] = {}
        has_start = any(flag.type == "START" for flag in song.flags)
        self.timing_map = (TimingMap.from_song(song) if has_start else
                           TimingMap(song.ppqn, [(MusicalPosition(1, 1, 1), 120, 4, 4)]))
        # Compatibility alias: all callers now receive the canonical map.
        self.tempo_map = self.timing_map if has_start else None
        self.click_mutes: set[str] = set()
        self.instructions = []
        self.sequence_selected: set[str] = set()
        self._sequence_edits = 0
        self._load_sequence_layer()
        self.resolve_audio()

    @classmethod
    def open(cls, path: str | Path, decoder_path: str | Path = "config/rig_midi.json") -> "EditorModel":
        path = Path(path)
        return cls(StadiumSong.from_json_text(path.read_text(encoding="utf-8")), path,
                   RigMidiDecoder.from_file(decoder_path))

    @staticmethod
    def show_path(path: str | Path) -> Path:
        """Return the namespaced Reapcase sidecar beside a native Song."""
        path = Path(path)
        return path.with_name(path.name + ".reapcase.json")

    def _load_show_layer(self) -> None:
        sidecar = self.show_path(self.path)
        if not sidecar.exists():
            return
        document = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("A Reapcase show sidecar must be an object")
        self._show_document = copy.deepcopy(document)
        lights = document.get("reapcase", {}).get("lights", [])
        if not isinstance(lights, list):
            raise ValueError("Invalid Reapcase LIGHTS sidecar")
        for item in lights:
            if not isinstance(item, dict):
                raise ValueError("Invalid lighting cue in Reapcase sidecar")
            position = MusicalPosition.parse(item["position"], ppqn=self.song.ppqn)
            self.timeline.events.append(create_lighting_event(
                position, item["name"], item["kind"], item["id"]))

    def _load_sequence_layer(self) -> None:
        from .sequence import SequenceInstructionClip, derive_count_in
        root = self._show_document.get("reapcase", {})
        sequence_present = isinstance(root, dict) and "sequence" in root
        raw = root.get("sequence", {}) if isinstance(root, dict) else {}
        if not isinstance(raw, dict):
            raise ValueError("Invalid Reapcase sequence sidecar")
        mutes = raw.get("click_mutes", [])
        if not isinstance(mutes, list) or not all(isinstance(item, str) for item in mutes):
            raise ValueError("Invalid Reapcase click mute overrides")
        self.click_mutes = set(mutes)
        if not sequence_present:
            self.instructions = list(derive_count_in(self.timing_map))
            return
        items = raw.get("instructions", [])
        if not isinstance(items, list):
            raise ValueError("Invalid Reapcase sequence instructions")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid sequence instruction")
            position = MusicalPosition.parse(str(item["position"]), ppqn=self.song.ppqn)
            self.instructions.append(SequenceInstructionClip(
                str(item["id"]), position, self._units(position), str(item["label"]),
                bool(item.get("muted", False)), str(item.get("origin", "user")),
                str(item.get("sample_id", str(item["label"]).casefold()))))

    @property
    def modified(self) -> bool:
        return (self._created > 0 or self._structural_edits > 0
                or self._sequence_edits > 0
                or len(self.timeline.events) != len(self._original_positions)
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
        if isinstance(event.source, LightingEventSource):
            return "LIGHTS"
        return "MIDI / OTHER"

    def unique_lighting_id(self, name: str) -> str:
        """Allocate a custom semantic ID without changing existing identities."""
        base = normalized_cue_id(name)
        used = {event.data.get("cue_id") for event in self.timeline.events
                if isinstance(event.source, LightingEventSource)}
        candidate, suffix = base, 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def lane_counts(self) -> dict[str, int]:
        return {lane: sum(self.lane(e) == lane for e in self.timeline.events) for lane in EVENT_LANES}

    def resolve_audio(self, root: str | Path | None = None) -> set[Path]:
        """Resolve audio and return files whose identity changed since the last scan."""
        if root is not None:
            self.audio_root = Path(root)
        automatic = stadium_backup_audio_paths(self.path)
        resolver = AudioResolver(self.path.parent, self.audio_root,
                                 automatic[0] if automatic else None,
                                 automatic[1] if automatic else None)
        previous = {track.resolved_path: track for track in self.audio_tracks if track.resolved_path}
        views = audio_track_views(self.song.tracks, resolver, inspect_files=False)
        changed: set[Path] = set()
        identities: dict[Path, tuple[int, int]] = {}
        refreshed = []
        for view in views:
            path = view.resolved_path
            identity = None
            if path:
                try:
                    stat = path.stat(); identity = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    pass
            if path and identity is not None:
                identities[path] = identity
            old = previous.get(path)
            if path and identity == self._audio_identities.get(path) and old:
                info = old.file_info
            else:
                info = None
                if path:
                    changed.add(path)
                    try: info = read_wav_info(path)
                    except (wave.Error, OSError, EOFError): pass
            refreshed.append(type(view)(view.number, view.source, path, info))
        changed.update(set(self._audio_identities) - set(identities))
        self._audio_identities = identities
        self.audio_tracks = tuple(refreshed)
        return changed

    def refresh_audio(self) -> set[Path]:
        """Refresh derived audio state without changing Song JSON or Undo state."""
        return self.resolve_audio()

    def _replace_tracks(self, tracks: list, *, undo: bool = True) -> None:
        if undo:
            self._undo.append(("audio_tracks", list(self.song.tracks)))
            self._structural_edits += 1
            self._audio_edits += 1
        self.song.tracks = tracks
        self.resolve_audio()

    def add_audio_track(self, source_wav: str | Path, name: str | None = None,
                        destination: str | Path | None = None) -> dict:
        """Copy a PCM WAV into managed storage and append a fixture-safe track."""
        tracks = self.song.tracks
        if not isinstance(tracks, list):
            raise ValueError("This Song has no editable tracks array")
        if len(tracks) >= MAX_AUDIO_TRACKS:
            raise ValueError("A Song can contain at most 8 audio tracks")
        source = Path(source_wav)
        try:
            info = read_wav_info(source)
        except Exception as exc:
            raise ValueError(f"Invalid or unsupported PCM WAV: {exc}") from exc
        rates = {track.file_info.sample_rate for track in self.audio_tracks if track.file_info}
        if rates and info.sample_rate not in rates:
            raise ValueError(f"Sample rate must match the existing Song audio ({min(rates)} Hz)")
        if info.channels not in (1, 2) or info.sample_width not in (2, 3):
            raise ValueError("Only mono/stereo 16-bit and 24-bit PCM WAV is supported")
        automatic = stadium_backup_audio_paths(self.path)
        target_dir = Path(destination) if destination else (automatic[0] if automatic else None)
        if target_dir is None:
            raise ValueError("The backup audio folder could not be derived; choose a destination")
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = source.name
        if source.suffix.casefold() != ".wav":
            raise ValueError("Audio track files must use the .wav extension")
        target = target_dir / filename
        if target.exists():
            raise FileExistsError(f"Audio file already exists: {target.name}")
        shutil.copy2(source, target)
        # Real fixtures consistently use this Stadium path and these neutral defaults.
        existing_filename = next((item.get("filename") for item in tracks
                                  if isinstance(item, dict) and isinstance(item.get("filename"), str)
                                  and "/" in item["filename"].replace("\\", "/")), None)
        prefix = (existing_filename.replace("\\", "/").rsplit("/", 1)[0]
                  if existing_filename else
                  f"../../../../../sd-stadium/songs/workspace/Audio/{self.path.stem}")
        stored = f"{prefix}/{filename}"
        track = {"name": name or source.stem, "filename": stored, "offset": 0,
                 "gain": 1.0, "panning": 0.0, "mute": False, "solo": False,
                 "trim": 1.0, "transpose": False}
        self._replace_tracks([*tracks, track])
        return track

    def delete_audio_track(self, index: int) -> dict:
        if not isinstance(self.song.tracks, list) or not 0 <= index < len(self.song.tracks):
            raise IndexError("Audio track index out of range")
        removed = self.song.tracks[index]
        self._replace_tracks(self.song.tracks[:index] + self.song.tracks[index + 1:])
        return removed

    def move_audio_track(self, old_index: int, new_index: int) -> bool:
        tracks = self.song.tracks
        if not isinstance(tracks, list) or not (0 <= old_index < len(tracks)):
            raise IndexError("Audio track index out of range")
        new_index = max(0, min(new_index, len(tracks) - 1))
        if old_index == new_index:
            return False
        reordered = list(tracks)
        reordered.insert(new_index, reordered.pop(old_index))
        self._replace_tracks(reordered)
        return True

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

    @property
    def sequence_end_units(self) -> int:
        """Use longest resolved audio, falling back conservatively to END/song extent."""
        if self.audio_end_units:
            return self.audio_end_units
        end = next((self._units(e.position) for e in self.timeline.events
                    if e.source.type == "END"), None)
        return end if end is not None else self.song_end_units

    @property
    def sequence_layout(self):
        """Fresh click geometry combined with editable show-layer instructions."""
        from .sequence import derive_sequence_layout
        return derive_sequence_layout(self.timing_map, self.sequence_end_units, self.instructions)

    def toggle_click_mute(self, identity: str) -> bool:
        previous = identity in self.click_mutes
        self._undo.append(("click_mute", identity, previous))
        self.click_mutes.symmetric_difference_update({identity})
        self._sequence_edits += 1
        return not previous

    def toggle_instruction_mute(self, identity: str) -> bool:
        item = next(item for item in self.instructions if item.id == identity)
        self._undo.append(("instruction_mute", identity, item.muted))
        item.muted = not item.muted
        self._sequence_edits += 1
        return item.muted

    def edit_instruction(self, identity: str, *, label: str, sample_id: str,
                         muted: bool) -> bool:
        """Replace instruction properties while retaining its stable identity/position."""
        item = next(item for item in self.instructions if item.id == identity)
        label, sample_id = label.strip(), sample_id.strip()
        if not label:
            raise ValueError("Instruction label is required")
        if not sample_id:
            raise ValueError("Sample ID is required")
        state = (item.label, item.sample_id, item.muted)
        target = (label, sample_id, bool(muted))
        if state == target:
            return False
        self._undo.append(("instruction_edit", identity, state))
        item.label, item.sample_id, item.muted = target
        self._sequence_edits += 1
        return True

    def preview_instruction_shift(self, identities: Iterable[str], delta_units: int):
        ids = tuple(identities)
        items = [next(item for item in self.instructions if item.id == identity) for identity in ids]
        targets = tuple(self._position(item.units + delta_units) for item in items) if all(
            item.units + delta_units >= 0 for item in items) else ()
        return ids, tuple(item.position for item in items), targets, delta_units, bool(targets)

    def move_instructions(self, identities: Iterable[str], delta_units: int) -> int:
        ids, originals, targets, delta, valid = self.preview_instruction_shift(identities, delta_units)
        if not valid or not delta:
            return 0
        self._undo.append(("instruction_move", ids, originals))
        for identity, target in zip(ids, targets):
            item = next(item for item in self.instructions if item.id == identity)
            item.position, item.units = target, self._units(target)
        self._sequence_edits += 1
        return len(ids)

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
        return badge_text(event)

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
        return self.timing_map.position_to_units(position)

    def _position(self, units: int) -> MusicalPosition:
        return self.timing_map.units_to_position(units)

    def shift_selected(self, bars: int = 0, beats: int = 0, ticks: int = 0) -> int:
        indices = sorted(self.selected)
        if not indices:
            return 0
        targets = [self.timing_map.shift_position(self.timeline.events[i].position,
                                                  bars=bars, beats=beats, ticks=ticks)
                   for i in indices]
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
        if operation[0] == "click_mute":
            _, identity, muted = operation
            if muted: self.click_mutes.add(identity)
            else: self.click_mutes.discard(identity)
            self._sequence_edits -= 1
            return True
        if operation[0] == "instruction_mute":
            _, identity, muted = operation
            next(item for item in self.instructions if item.id == identity).muted = muted
            self._sequence_edits -= 1
            return True
        if operation[0] == "instruction_move":
            _, identities, positions = operation
            for identity, position in zip(identities, positions):
                item = next(item for item in self.instructions if item.id == identity)
                item.position, item.units = position, self._units(position)
            self._sequence_edits -= 1
            return True
        if operation[0] == "instruction_edit":
            _, identity, state = operation
            item = next(item for item in self.instructions if item.id == identity)
            item.label, item.sample_id, item.muted = state
            self._sequence_edits -= 1
            return True
        if operation[0] == "audio_tracks":
            self.song.tracks = operation[1]
            self._structural_edits -= 1
            self._audio_edits -= 1
            self.resolve_audio()
            return True
        if operation[0] == "create":
            _, event, previous_selection = operation
            self.timeline.events.remove(event)
            self.selected = previous_selection
            self._created -= 1
            return True
        if operation[0] == "replace":
            _, events, selection = operation
            self.timeline.events = events
            self.selected = selection
            self._structural_edits -= 1
            return True
        if operation[0] == "event_edit":
            _, index, event, selection = operation
            self.timeline.events[index] = event
            self.selected = selection
            self._structural_edits -= 1
            return True
        _, indices, positions, previous_selection = operation
        for index, position in zip(indices, positions):
            self.timeline.events[index].position = position
        self.selected = previous_selection
        return True

    def selection_is_editable(self) -> bool:
        return bool(self.selected) and not any(
            self.timeline.events[i].source.type in {"START", "END"} for i in self.selected)

    def edit_capability(self, index: int):
        from .editing import editor_for_event
        if not 0 <= index < len(self.timeline.events):
            return None
        return editor_for_event(self.timeline.events[index], self)

    def edit_event(self, index: int, values: dict) -> bool:
        """Atomically replace one event at the same index as one undoable edit."""
        from .editing import apply_semantic_edit
        capability = self.edit_capability(index)
        if capability is None:
            raise ValueError("This event has no safe semantic editor")
        original = self.timeline.events[index]
        updated = apply_semantic_edit(original, self, capability, values)
        same_payload = (getattr(updated.source, "payload", None) ==
                        getattr(original.source, "payload", None))
        if (same_payload and updated.position == original.position and updated.data == original.data
                and type(updated.source) is type(original.source)):
            return False
        previous_selection = set(self.selected)
        self._undo.append(("event_edit", index, copy.deepcopy(original), previous_selection))
        self.timeline.events[index] = updated
        self.selected = ({index} if index not in previous_selection else previous_selection)
        self._structural_edits += 1
        return True

    def delete_selected(self) -> int:
        """Delete a safe selection as one undoable, lossless operation."""
        indices = sorted(i for i in self.selected if 0 <= i < len(self.timeline.events))
        if not indices or not self.selection_is_editable():
            return 0
        before = list(self.timeline.events)
        previous_selection = set(self.selected)
        self._undo.append(("replace", before, previous_selection))
        remove = set(indices)
        self.timeline.events = [event for i, event in enumerate(before) if i not in remove]
        self.selected = set()
        self._structural_edits += 1
        return len(indices)

    def duplicate_selected(self) -> int:
        """Append independent lossless copies, preserving group offsets and order."""
        indices = sorted(i for i in self.selected if 0 <= i < len(self.timeline.events))
        if not indices or not self.selection_is_editable():
            return 0
        before = list(self.timeline.events)
        previous_selection = set(self.selected)
        self._undo.append(("replace", before, previous_selection))
        next_source = max((event.source_index for event in before
                           if event.source_index is not None), default=-1) + 1
        copies = []
        for offset, index in enumerate(indices):
            duplicate = copy.deepcopy(before[index])
            duplicate.source_index = next_source + offset
            copies.append(duplicate)
        first = len(self.timeline.events)
        self.timeline.events.extend(copies)
        self.selected = set(range(first, first + len(copies)))
        self._structural_edits += 1
        return len(copies)

    def copy_selected(self) -> int:
        """Capture semantic/native event data without source index identity."""
        indices = sorted((i for i in self.selected if 0 <= i < len(self.timeline.events)),
                         key=lambda i: self._units(self.timeline.events[i].position))
        if not indices or any(self.timeline.events[i].source.type in NON_COPYABLE for i in indices):
            self._event_clipboard = ()
            return 0
        copied = []
        for index in indices:
            event = copy.deepcopy(self.timeline.events[index])
            event.source_index = None
            copied.append(event)
        self._event_clipboard = tuple(copied)
        return len(copied)

    def paste_at_cursor(self) -> int:
        """Paste the clipboard at the playhead, preserving musical-unit offsets."""
        if not self._event_clipboard:
            return 0
        before = list(self.timeline.events)
        previous_selection = set(self.selected)
        anchor = min(self._units(event.position) for event in self._event_clipboard)
        destination = self._units(self.cursor)
        next_source = max((event.source_index for event in before
                           if event.source_index is not None), default=-1) + 1
        copies = []
        for offset, original in enumerate(self._event_clipboard):
            event = copy.deepcopy(original)
            event.position = self._position(destination + self._units(original.position) - anchor)
            event.source_index = next_source + offset
            copies.append(event)
        self._undo.append(("replace", before, previous_selection))
        first = len(self.timeline.events)
        self.timeline.events.extend(copies)
        self.selected = set(range(first, first + len(copies)))
        self._structural_edits += 1
        return len(copies)

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
        path = Path(path)
        sidecar = self.show_path(path)
        # The complete native/sidecar generation is secured before either file
        # can be replaced. A failed copy therefore leaves both originals intact.
        backup_existing([path, sidecar])
        source_flags = self.song.flags
        try:
            self.song.flags = timeline_source_flags(self.timeline)
            song_text = self.song.to_json_text()
        finally:
            self.song.flags = source_flags
        lights = [
            {"position": event.position.render(), "id": event.source.cue.id,
             "name": event.source.cue.name, "kind": event.source.cue.kind.value}
            for event in self.timeline.events if isinstance(event.source, LightingEventSource)
        ]
        show_document = copy.deepcopy(self._show_document)
        if lights or show_document or self.instructions or self.click_mutes:
            reapcase = show_document.setdefault("reapcase", {})
            if not isinstance(reapcase, dict):
                raise ValueError("Invalid Reapcase namespace in show sidecar")
            reapcase.setdefault("version", 1)
            reapcase["lights"] = lights
            reapcase["sequence"] = {
                "version": 1,
                "click_mutes": sorted(self.click_mutes),
                "instructions": [item.to_dict() for item in self.instructions],
            }
            sidecar_text = json.dumps(show_document, ensure_ascii=False, indent=2) + "\n"
        else:
            sidecar_text = None
        # Same-directory temporary files make replacement atomic on normal
        # desktop filesystems and prevent a partial direct write.
        song_temp = path.with_name(path.name + ".reapcase-tmp")
        song_temp.write_text(song_text, encoding="utf-8")
        song_temp.replace(path)
        if sidecar_text is not None:
            sidecar_temp = sidecar.with_name(sidecar.name + ".reapcase-tmp")
            sidecar_temp.write_text(sidecar_text, encoding="utf-8")
            sidecar_temp.replace(sidecar)
        elif sidecar.exists():
            sidecar.unlink()
        self.path = path
        return SaveSummary(sum(e.position != p for e, p in zip(self.timeline.events, self._original_positions)),
                           tracks_changed=self._audio_edits)
