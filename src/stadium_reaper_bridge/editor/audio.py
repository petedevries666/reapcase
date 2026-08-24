"""Read-only audio views, WAV inspection, path resolution, and tempo mapping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import logging
import os
import time
import wave
from typing import Any, Callable, Iterable, Optional, Union

from ..stadium import MusicalPosition

MAX_AUDIO_TRACKS = 8
LOG = logging.getLogger(__name__)
PERF = os.environ.get("REAPCASE_LOAD_PERF", "").lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class AudioFileInfo:
    path: Path
    sample_rate: int
    channels: int
    sample_width: int
    frames: int
    duration_seconds: float


def read_wav_info(path: Union[str, Path]) -> AudioFileInfo:
    """Read only a WAV header; audio sample data is never loaded into memory."""
    path = Path(path)
    with wave.open(str(path), "rb") as source:
        frames, rate = source.getnframes(), source.getframerate()
        if source.getcomptype() != "NONE":
            raise wave.Error(f"Unsupported compressed WAV: {source.getcomptype()}")
        return AudioFileInfo(path, rate, source.getnchannels(), source.getsampwidth(), frames,
                             frames / rate if rate else 0.0)


class AudioResolver:
    """Resolve Stadium references without ever guessing between duplicates."""

    def __init__(self, song_directory: Union[str, Path], audio_root: Optional[Union[str, Path]] = None,
                 automatic_audio_dir: Optional[Union[str, Path]] = None,
                 backup_audio_root: Optional[Union[str, Path]] = None, *,
                 cancelled: Callable[[], bool] = lambda: False,
                 progress: Callable[[str], None] = lambda _phase: None):
        self.song_directory = Path(song_directory)
        self.audio_root = Path(audio_root) if audio_root else None
        self.automatic_audio_dir = Path(automatic_audio_dir) if automatic_audio_dir else None
        self.backup_audio_root = Path(backup_audio_root) if backup_audio_root else None
        self.cancelled = cancelled
        self.progress = progress
        self._indexes: dict[Path, dict[str, list[Path]]] = {}

    def resolve(self, filename: Any) -> Optional[Path]:
        if not isinstance(filename, str) or not filename:
            return None
        stored = Path(filename)
        started = time.perf_counter()
        for candidate in (stored, self.song_directory / stored):
            if candidate.is_file():
                self._timing("direct path lookup", started)
                return candidate.resolve()
        self._timing("direct path lookup", started)
        # Stadium backups deliberately keep JSON and audio in sibling trees.
        # Resolve the Song-specific directory before any recursive fallback.
        if self.automatic_audio_dir:
            started = time.perf_counter()
            candidate = self.automatic_audio_dir / PurePosixPath(
                filename.replace("\\", "/")).name
            if candidate.is_file():
                self._timing("Song-specific audio lookup", started)
                return candidate.resolve()
            self._timing("Song-specific audio lookup", started)
        match = self._unique_tail_match(filename, self.backup_audio_root)
        if match:
            return match
        return self._unique_tail_match(filename, self.audio_root)

    def _unique_tail_match(self, filename: str, root: Optional[Path]) -> Optional[Path]:
        if not root or not root.is_dir():
            return None
        # A root commonly points at .../workspace/Audio while the JSON contains
        # .../Audio/453/CLICK.wav. Prefer unique longest relative-tail matches.
        normalized = PurePosixPath(filename.replace("\\", "/"))
        parts = normalized.parts
        files = self._index(root).get(normalized.name.casefold(), [])
        if self.cancelled():
            return None
        started = time.perf_counter()
        for length in range(min(len(parts), 6), 1, -1):
            tail = tuple(part.casefold() for part in parts[-length:])
            matches = [p for p in files if tuple(part.casefold() for part in p.parts[-length:]) == tail]
            if len(matches) == 1:
                result = matches[0].resolve()
                self._timing("tail-match lookup", started)
                return result
            if len(matches) > 1:
                self._timing("tail-match lookup", started)
                return None
        result = files[0].resolve() if len(files) == 1 else None
        self._timing("tail-match lookup", started)
        return result

    def _index(self, root: Path) -> dict[str, list[Path]]:
        """Return this session's lazy, cancellation-aware recursive file index."""
        root = root.resolve()
        if root in self._indexes:
            return self._indexes[root]
        if self.cancelled():
            return {}
        self.progress("INDEXING_AUDIO")
        started = time.perf_counter()
        index: dict[str, list[Path]] = {}
        count = 0
        for directory, _directories, filenames in os.walk(root):
            if self.cancelled():
                return {}
            base = Path(directory)
            for name in filenames:
                if self.cancelled():
                    return {}
                index.setdefault(name.casefold(), []).append(base / name)
                count += 1
        self._indexes[root] = index
        if PERF:
            LOG.debug("Song load timing: fallback Audio-root indexing %.1f ms (%d files, %s)",
                      (time.perf_counter() - started) * 1000, count, root)
        return index

    @staticmethod
    def _timing(label: str, started: float) -> None:
        if PERF:
            LOG.debug("Song load timing: %s %.1f ms", label,
                      (time.perf_counter() - started) * 1000)


def stadium_backup_audio_paths(json_path: Union[str, Path]) -> Optional[tuple[Path, Path]]:
    """Return (Song audio directory, Audio root) for a real extracted backup.

    Recognition is suffix based and case-insensitive. Standalone JSON documents
    simply return ``None``.
    """
    path = Path(json_path)
    parents = path.parents
    if len(parents) < 4:
        return None
    tail = tuple(part.casefold() for part in path.parts[-4:-1])
    if tail != ("showcase", "songs", "workspace"):
        return None
    backup_root = parents[3]
    audio_root = backup_root / "songs" / "workspace" / "Audio"
    return audio_root / path.stem, audio_root


@dataclass(frozen=True)
class AudioTrackView:
    number: int
    source: dict[str, Any]
    resolved_path: Optional[Path] = None
    file_info: Optional[AudioFileInfo] = None
    status: str = "resolving"

    @property
    def name(self) -> str:
        return str(self.source.get("name") or f"Track {self.number}")

    @property
    def filename(self) -> str:
        return PurePosixPath(str(self.source.get("filename") or "").replace("\\", "/")).name

    @property
    def offset(self) -> Any:
        return self.source.get("offset", 0)


def full_song_track(tracks: Iterable[AudioTrackView], *, resolved: bool = True
                    ) -> Optional[AudioTrackView]:
    """Return the named FULL-SONG orientation source, if it is usable.

    Track identity comes from Stadium's display name rather than its filename.
    An unresolved match is deliberately treated as absent by the UI.
    """
    for track in tracks:
        if track.name.strip().casefold() == "full-song":
            if not resolved or track.resolved_path is not None:
                return track
    return None


def waveform_cache_key(track: AudioTrackView) -> Optional[str]:
    """Canonical key shared by normal and background waveform renderers."""
    return str(track.resolved_path) if track.resolved_path is not None else None


def audio_track_views(tracks: Any, resolver: AudioResolver, *,
                      inspect_files: bool = True) -> tuple[AudioTrackView, ...]:
    if not isinstance(tracks, list):
        return ()
    views = []
    for number, source in enumerate(tracks[:MAX_AUDIO_TRACKS], 1):
        if not isinstance(source, dict):
            source = {"name": str(source)}
        path = resolver.resolve(source.get("filename"))
        try:
            info = read_wav_info(path) if path and inspect_files else None
        except (wave.Error, OSError, EOFError):
            info = None
        status = "ready" if info else ("invalid" if path else "missing")
        views.append(AudioTrackView(number, source, path, info, status))
    return tuple(views)


@dataclass(frozen=True)
class TempoChange:
    units: int
    tempo: float


class TempoMap:
    """Derived piecewise-constant tempo view over START/TIME source events."""

    def __init__(self, ppqn: int, changes: Iterable[TempoChange],
                 position_to_units: Callable[[MusicalPosition], int],
                 units_to_position: Callable[[int], MusicalPosition]):
        self.ppqn = ppqn
        self.position_to_units = position_to_units
        self.units_to_position = units_to_position
        ordered = sorted(changes, key=lambda change: change.units)
        if not ordered or ordered[0].units != 0:
            raise ValueError("TempoMap requires a START tempo at Song start")
        self.changes = tuple(ordered)

    def units_to_seconds(self, units: int) -> float:
        if units < 0:
            raise ValueError("Position precedes Song start")
        seconds = 0.0
        for index, change in enumerate(self.changes):
            end = self.changes[index + 1].units if index + 1 < len(self.changes) else units
            span = max(0, min(units, end) - change.units)
            seconds += span / self.ppqn * 60.0 / change.tempo
            if units <= end:
                break
        return seconds

    def seconds_to_units(self, seconds: float) -> int:
        if seconds < 0:
            raise ValueError("Time precedes Song start")
        remaining = seconds
        for index, change in enumerate(self.changes):
            if index + 1 == len(self.changes):
                return change.units + round(remaining * change.tempo / 60.0 * self.ppqn)
            span_units = self.changes[index + 1].units - change.units
            span_seconds = span_units / self.ppqn * 60.0 / change.tempo
            if remaining <= span_seconds:
                return change.units + round(remaining * change.tempo / 60.0 * self.ppqn)
            remaining -= span_seconds
        raise AssertionError("unreachable")

    def musical_position_to_seconds(self, position: MusicalPosition) -> float:
        return self.units_to_seconds(self.position_to_units(position))

    def seconds_to_musical_position(self, seconds: float) -> MusicalPosition:
        return self.units_to_position(self.seconds_to_units(seconds))
