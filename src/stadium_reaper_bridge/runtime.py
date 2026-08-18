"""Lightweight live-show preparation, cache and transport foundation.

No class in this module opens a hardware output stream or sends MIDI.  Runtime
commands are semantic intentions: protocol translation and show routing are
deliberately later stages.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import threading
from typing import Any, Callable, Iterable, Optional, Union

from .editor.audio import AudioResolver, AudioTrackView, audio_track_views, stadium_backup_audio_paths
from .editor.lighting import LightingEventSource, create_lighting_event
from .midi import RigMidiDecoder
from .show import ReapcaseShow, ShowSong
from .stadium import MusicalPosition, StadiumSong
from .timeline import TimelineEvent, stadium_to_timeline
from .timing import TimingMap
import json


class Readiness(str, Enum):
    READY = "READY"
    WARNING = "WARNING"
    ERROR = "ERROR"


class TransportState(str, Enum):
    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    TRANSITIONING = "TRANSITIONING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Diagnostic:
    readiness: Readiness
    message: str


@dataclass(frozen=True)
class RuntimeCommand:
    destination: str
    action: str
    payload: dict[str, Any]
    position: MusicalPosition


@dataclass(frozen=True)
class FileIdentity:
    path: Path
    size: int
    mtime_ns: int

    @classmethod
    def read(cls, path: Path) -> "FileIdentity":
        stat = path.stat()
        return cls(path.resolve(), stat.st_size, stat.st_mtime_ns)


@dataclass(frozen=True)
class PreparedSong:
    song_id: str
    title: str
    path: Path
    stadium_song: Optional[StadiumSong]
    timing_map: Optional[TimingMap]
    timeline_events: tuple[TimelineEvent, ...]
    audio_tracks: tuple[AudioTrackView, ...]
    duration_seconds: float
    lighting_metadata: tuple[TimelineEvent, ...]
    runtime_events: tuple[RuntimeCommand, ...]
    diagnostics: tuple[Diagnostic, ...]
    identities: tuple[FileIdentity, ...]

    @property
    def readiness(self) -> Readiness:
        levels = {item.readiness for item in self.diagnostics}
        return Readiness.ERROR if Readiness.ERROR in levels else (Readiness.WARNING if Readiness.WARNING in levels else Readiness.READY)

    @property
    def audio_total(self) -> int:
        return len(self.audio_tracks)

    @property
    def audio_resolved(self) -> int:
        return sum(track.resolved_path is not None and track.file_info is not None for track in self.audio_tracks)

    @property
    def sample_rate(self) -> Optional[int]:
        rates = {t.file_info.sample_rate for t in self.audio_tracks if t.file_info}
        return next(iter(rates)) if len(rates) == 1 else None

    @property
    def channels(self) -> tuple[int, ...]:
        return tuple(t.file_info.channels for t in self.audio_tracks if t.file_info)

    @property
    def compatibility(self) -> bool:
        infos = [t.file_info for t in self.audio_tracks if t.file_info]
        return self.audio_resolved == self.audio_total and len({i.sample_rate for i in infos}) <= 1

    def is_stale(self) -> bool:
        try:
            return any(FileIdentity.read(item.path) != item for item in self.identities)
        except OSError:
            return True


def _runtime_commands(events: Iterable[TimelineEvent]) -> tuple[RuntimeCommand, ...]:
    commands: list[RuntimeCommand] = []
    for event in events:
        if isinstance(event.source, LightingEventSource):
            commands.append(RuntimeCommand("lights", event.source.cue.kind.value.casefold(),
                                           {"cue_id": event.source.cue.id, "name": event.source.cue.name}, event.position))
            continue
        data = event.data
        if event.source.type in {"PRESETSNAP", "START", "MARKER"} and data.get("snapshot") not in (None, "", "Off"):
            try: snapshot: Any = int(data["snapshot"])
            except (TypeError, ValueError): snapshot = data["snapshot"]
            commands.append(RuntimeCommand("stadium", "snapshot", {"snapshot": snapshot}, event.position))
        alias = data.get("rig_alias")
        if isinstance(alias, dict) and alias.get("system") == "second_helix":
            commands.append(RuntimeCommand("second_helix", str(alias.get("action")),
                                           {k: v for k, v in alias.items() if k not in {"system", "action"}}, event.position))
    return tuple(commands)


class SongPreparer:
    """Parse JSON and headers only; WAV PCM and waveforms are never loaded."""

    def __init__(self, decoder: Optional[RigMidiDecoder] = None):
        config = Path(__file__).resolve().parents[2] / "config" / "rig_midi.json"
        self.decoder = decoder or RigMidiDecoder.from_file(config)

    def prepare(self, show: ReapcaseShow, reference: ShowSong) -> PreparedSong:
        path = show.resolve_song_path(reference).resolve()
        diagnostics: list[Diagnostic] = []
        if not path.is_file():
            return PreparedSong(reference.id, reference.title, path, None, None, (), (), 0, (), (),
                                (Diagnostic(Readiness.ERROR, f"Missing Song JSON: {reference.song_json}"),), ())
        identities = [FileIdentity.read(path)]
        try:
            song = StadiumSong.from_json_text(path.read_text(encoding="utf-8"))
            timeline = stadium_to_timeline(song, midi_decoder=self.decoder)
            has_start = any(flag.type == "START" for flag in song.flags)
            timing = (TimingMap.from_song(song) if has_start else
                      TimingMap(song.ppqn, [(MusicalPosition(1, 1, 1), 120, 4, 4)]))
        except Exception as exc:
            return PreparedSong(reference.id, reference.title, path, None, None, (), (), 0, (), (),
                                (Diagnostic(Readiness.ERROR, f"Invalid Song JSON: {exc}"),), tuple(identities))
        sidecar = path.with_name(path.name + ".reapcase.json")
        lights: list[TimelineEvent] = []
        if sidecar.exists():
            identities.append(FileIdentity.read(sidecar))
            try:
                document = json.loads(sidecar.read_text(encoding="utf-8"))
                values = document.get("reapcase", {}).get("lights", [])
                if not isinstance(values, list): raise ValueError("lights must be an array")
                for item in values:
                    lights.append(create_lighting_event(MusicalPosition.parse(item["position"], ppqn=song.ppqn),
                                                        item["name"], item["kind"], item["id"]))
                timeline.events.extend(lights)
            except Exception as exc:
                diagnostics.append(Diagnostic(Readiness.WARNING, f"Invalid LIGHTS sidecar: {exc}"))
        automatic = stadium_backup_audio_paths(path)
        resolver = AudioResolver(path.parent, automatic_audio_dir=automatic[0] if automatic else None,
                                 backup_audio_root=automatic[1] if automatic else None)
        tracks = audio_track_views(song.tracks, resolver, inspect_files=True)
        for track in tracks:
            if track.resolved_path is None:
                diagnostics.append(Diagnostic(Readiness.ERROR, f"Missing audio: {track.filename or track.name}"))
            elif track.file_info is None:
                diagnostics.append(Diagnostic(Readiness.ERROR, f"Invalid WAV: {track.resolved_path.name}"))
            else:
                identities.append(FileIdentity.read(track.resolved_path))
        rates = {track.file_info.sample_rate for track in tracks if track.file_info}
        if len(rates) > 1:
            diagnostics.append(Diagnostic(Readiness.WARNING, "Audio tracks use mixed sample rates"))
        end_units = max((timing.position_to_units(event.position) for event in timeline.events), default=0)
        event_duration = timing.units_to_seconds(end_units)
        audio_duration = max((track.file_info.duration_seconds + float(track.offset or 0)
                              for track in tracks if track.file_info), default=0)
        if not diagnostics:
            diagnostics.append(Diagnostic(Readiness.READY, "Ready"))
        events = tuple(timeline.events)
        return PreparedSong(reference.id, reference.title, path, song, timing, events, tracks,
                            max(event_duration, audio_duration), tuple(lights), _runtime_commands(events),
                            tuple(diagnostics), tuple(identities))


class PreparedSongCache:
    def __init__(self, capacity: int = 3):
        if capacity < 2: raise ValueError("PreparedSong cache capacity must be at least 2")
        self.capacity = capacity
        self._items: OrderedDict[str, PreparedSong] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, song_id: str) -> Optional[PreparedSong]:
        with self._lock:
            item = self._items.get(song_id)
            if item and item.is_stale():
                del self._items[song_id]; return None
            if item: self._items.move_to_end(song_id)
            return item

    def put(self, item: PreparedSong) -> PreparedSong:
        with self._lock:
            self._items[item.song_id] = item; self._items.move_to_end(item.song_id)
            while len(self._items) > self.capacity: self._items.popitem(last=False)
        return item

    def clear(self) -> None:
        with self._lock: self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


class ShowPreloader:
    """Single-worker, cancellable metadata preloader, isolated from Tk/audio callbacks."""
    def __init__(self, preparer: Union[SongPreparer, Any] = None, cache: Optional[PreparedSongCache] = None):
        self.preparer = preparer or SongPreparer(); self.cache = cache or PreparedSongCache()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="show-preflight")
        self._generation = 0; self._futures: dict[str, Future] = {}

    def prepare_now(self, show: ReapcaseShow, song: ShowSong) -> PreparedSong:
        cached = self.cache.get(song.id)
        return cached or self.cache.put(self.preparer.prepare(show, song))

    def prepare(self, show: ReapcaseShow, song: ShowSong, callback: Optional[Callable[[PreparedSong], None]] = None) -> Future:
        cached = self.cache.get(song.id)
        if cached:
            future = Future(); future.set_result(cached); return future
        generation = self._generation
        future = self._executor.submit(self.preparer.prepare, show, song); self._futures[song.id] = future
        def complete(done: Future) -> None:
            if generation == self._generation and not done.cancelled():
                item = self.cache.put(done.result())
                if callback: callback(item)
        future.add_done_callback(complete); return future

    def restart(self) -> None:
        self._generation += 1
        for future in self._futures.values(): future.cancel()
        self._futures.clear(); self.cache.clear()

    def shutdown(self) -> None:
        self.restart(); self._executor.shutdown(wait=False, cancel_futures=True)


class LiveRuntime:
    def __init__(self, show: ReapcaseShow, preloader: Optional[ShowPreloader] = None,
                 stop_callback: Optional[Callable[[], None]] = None):
        self.show, self.preloader = show, preloader or ShowPreloader()
        self.stop_callback = stop_callback or (lambda: None)
        self.state = TransportState.STOPPED; self.current_index: Optional[int] = None
        self.current_song: Optional[PreparedSong] = None; self.next_song: Optional[PreparedSong] = None
        self.current_time_seconds = 0.0; self.current_units = 0

    @property
    def runtime_events(self) -> tuple[RuntimeCommand, ...]:
        return self.current_song.runtime_events if self.current_song else ()

    @property
    def midi_routing(self): return self.show.midi

    def select(self, index: int) -> PreparedSong:
        if not 0 <= index < len(self.show.songs): raise IndexError(index)
        self.current_index = index
        self.current_song = self.preloader.prepare_now(self.show, self.show.songs[index])
        self.next_song = self.preloader.cache.get(self.show.songs[index + 1].id) if index + 1 < len(self.show.songs) else None
        if index + 1 < len(self.show.songs):
            self.preloader.prepare(self.show, self.show.songs[index + 1], lambda item: setattr(self, "next_song", item))
        return self.current_song

    def next(self) -> Optional[PreparedSong]:
        if self.current_index is None or self.current_index + 1 >= len(self.show.songs): return None
        target = self.preloader.cache.get(self.show.songs[self.current_index + 1].id)
        if target is None: return None
        self.state = TransportState.TRANSITIONING; self.stop_callback()
        self.current_index += 1; self.current_song = target; self.current_time_seconds = 0; self.current_units = 0
        self.state = TransportState.STOPPED; self.next_song = None
        following = self.current_index + 1
        if following < len(self.show.songs):
            self.preloader.prepare(self.show, self.show.songs[following], lambda item: setattr(self, "next_song", item))
        return target

    def previous(self) -> Optional[PreparedSong]:
        if self.current_index is None or self.current_index == 0: return None
        target = self.preloader.cache.get(self.show.songs[self.current_index - 1].id)
        if target is None: return None
        self.state = TransportState.TRANSITIONING; self.stop_callback(); self.current_index -= 1
        self.current_song = target; self.current_time_seconds = 0; self.current_units = 0; self.state = TransportState.STOPPED
        return target


def preflight_show(show: ReapcaseShow, preparer: Optional[SongPreparer] = None) -> tuple[PreparedSong, ...]:
    """Synchronous core used by the background service and command-line tests."""
    show.validate()
    loader = preparer or SongPreparer()
    return tuple(loader.prepare(show, song) for song in show.songs)
