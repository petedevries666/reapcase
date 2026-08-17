"""Read-only audio views, WAV inspection, path resolution, and tempo mapping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import wave
from typing import Any, Callable, Iterable

from ..stadium import MusicalPosition

MAX_AUDIO_TRACKS = 8


@dataclass(frozen=True)
class AudioFileInfo:
    path: Path
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float


def read_wav_info(path: str | Path) -> AudioFileInfo:
    """Read only a WAV header; audio sample data is never loaded into memory."""
    path = Path(path)
    with wave.open(str(path), "rb") as source:
        frames, rate = source.getnframes(), source.getframerate()
        return AudioFileInfo(path, rate, source.getnchannels(), frames,
                             frames / rate if rate else 0.0)


class AudioResolver:
    """Resolve Stadium references without ever guessing between duplicates."""

    def __init__(self, song_directory: str | Path, audio_root: str | Path | None = None):
        self.song_directory = Path(song_directory)
        self.audio_root = Path(audio_root) if audio_root else None

    def resolve(self, filename: Any) -> Path | None:
        if not isinstance(filename, str) or not filename:
            return None
        stored = Path(filename)
        for candidate in (stored, self.song_directory / stored):
            if candidate.is_file():
                return candidate.resolve()
        if not self.audio_root or not self.audio_root.is_dir():
            return None
        # A root commonly points at .../workspace/Audio while the JSON contains
        # .../Audio/453/CLICK.wav. Prefer unique longest relative-tail matches.
        normalized = PurePosixPath(filename.replace("\\", "/"))
        parts = normalized.parts
        files = [p for p in self.audio_root.rglob(normalized.name) if p.is_file()]
        for length in range(min(len(parts), 6), 1, -1):
            tail = tuple(part.casefold() for part in parts[-length:])
            matches = [p for p in files if tuple(part.casefold() for part in p.parts[-length:]) == tail]
            if len(matches) == 1:
                return matches[0].resolve()
            if len(matches) > 1:
                return None
        return files[0].resolve() if len(files) == 1 else None


@dataclass(frozen=True)
class AudioTrackView:
    number: int
    source: dict[str, Any]
    resolved_path: Path | None = None
    file_info: AudioFileInfo | None = None

    @property
    def name(self) -> str:
        return str(self.source.get("name") or f"Track {self.number}")

    @property
    def filename(self) -> str:
        return PurePosixPath(str(self.source.get("filename") or "").replace("\\", "/")).name

    @property
    def offset(self) -> Any:
        return self.source.get("offset", 0)


def audio_track_views(tracks: Any, resolver: AudioResolver) -> tuple[AudioTrackView, ...]:
    if not isinstance(tracks, list):
        return ()
    views = []
    for number, source in enumerate(tracks[:MAX_AUDIO_TRACKS], 1):
        if not isinstance(source, dict):
            source = {"name": str(source)}
        path = resolver.resolve(source.get("filename"))
        try:
            info = read_wav_info(path) if path else None
        except (wave.Error, OSError, EOFError):
            info = None
        views.append(AudioTrackView(number, source, path, info))
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
