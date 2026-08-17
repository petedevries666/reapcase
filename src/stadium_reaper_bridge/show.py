"""Reapcase-owned show/setlist documents.

Song JSON remains owned by Stadium.  A show only describes ordering, portable
references and live routing; saving or reordering a show never writes a Song.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any


SHOW_VERSION = 1
SHOW_SUFFIX = ".reapcase-show.json"


def _channel(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16:
        raise ValueError("MIDI channel must be an integer between 1 and 16")
    return value


@dataclass(frozen=True)
class MidiRoute:
    enabled: bool = False
    port: str | None = None
    channel: int = 1

    def __post_init__(self) -> None:
        _channel(self.channel)
        if self.port is not None and not isinstance(self.port, str):
            raise ValueError("MIDI port must be text or null")

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "port": self.port, "channel": self.channel}

    @classmethod
    def from_dict(cls, value: Any, default_channel: int) -> "MidiRoute":
        if not isinstance(value, dict):
            raise ValueError("MIDI route must be an object")
        return cls(value.get("enabled", False), value.get("port"),
                   value.get("channel", default_channel))


def default_midi_routing() -> dict[str, MidiRoute]:
    return {"stadium": MidiRoute(channel=1), "second_helix": MidiRoute(channel=3),
            "lights": MidiRoute(channel=16)}


@dataclass(frozen=True)
class ShowSong:
    id: str
    title: str
    song_json: str


@dataclass
class ReapcaseShow:
    name: str = "Untitled Show"
    songs: list[ShowSong] = field(default_factory=list)
    midi: dict[str, MidiRoute] = field(default_factory=default_midi_routing)
    auto_advance: bool = False
    lights_mappings: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    console_profile: str | None = None
    panic: dict[str, Any] = field(default_factory=dict)
    path: Path | None = field(default=None, repr=False)

    def resolve_song_path(self, song: ShowSong) -> Path:
        path = Path(song.song_json)
        return path if path.is_absolute() else (self.path.parent if self.path else Path.cwd()) / path

    def _stored_path(self, path: Path) -> str:
        path = path.resolve()
        if self.path:
            try:
                return path.relative_to(self.path.resolve().parent).as_posix()
            except ValueError:
                pass
        return str(path)

    def add_song(self, path: str | Path, title: str | None = None) -> ShowSong:
        source = Path(path)
        stored = self._stored_path(source)
        base = re.sub(r"[^a-z0-9]+", "_", (title or source.stem).casefold()).strip("_") or "song"
        identity = base
        if any(item.id == identity for item in self.songs):
            identity = f"{base}_{hashlib.sha256(stored.encode()).hexdigest()[:8]}"
            suffix = 2
            while any(item.id == identity for item in self.songs):
                identity = f"{base}_{hashlib.sha256(stored.encode()).hexdigest()[:8]}_{suffix}"
                suffix += 1
        item = ShowSong(identity, title or source.stem, stored)
        self.songs.append(item)
        return item

    def remove_song(self, index: int) -> ShowSong:
        return self.songs.pop(index)

    def move_song(self, index: int, destination: int) -> None:
        item = self.songs.pop(index)
        self.songs.insert(max(0, min(destination, len(self.songs))), item)

    def relocate_song(self, index: int, path: str | Path) -> None:
        old = self.songs[index]
        self.songs[index] = ShowSong(old.id, old.title, self._stored_path(Path(path)))

    def validate(self) -> None:
        if len({song.id for song in self.songs}) != len(self.songs):
            raise ValueError("Show contains duplicate Song IDs")
        if set(self.midi) != {"stadium", "second_helix", "lights"}:
            raise ValueError("Show must define Stadium, Second Helix, and LIGHTS routing")
        for route in self.midi.values():
            _channel(route.channel)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"reapcase_show": {"version": SHOW_VERSION, "name": self.name,
                "songs": [item.__dict__ for item in self.songs],
                "live": {"auto_advance": self.auto_advance, "panic": self.panic},
                "midi": {name: route.to_dict() for name, route in self.midi.items()},
                "lights": {"mappings": self.lights_mappings}, "notes": self.notes,
                "console_profile": self.console_profile}}

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("A show path is required")
        # Paths entered before the first save become portable when possible.
        old_path, self.path = self.path, target
        if old_path is None:
            self.songs = [ShowSong(s.id, s.title, self._stored_path(Path(s.song_json))) for s in self.songs]
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target

    @classmethod
    def open(cls, path: str | Path) -> "ReapcaseShow":
        target = Path(path)
        document = json.loads(target.read_text(encoding="utf-8"))
        root = document.get("reapcase_show") if isinstance(document, dict) else None
        if not isinstance(root, dict) or root.get("version") != SHOW_VERSION:
            raise ValueError("Unsupported or invalid Reapcase Show document")
        defaults = default_midi_routing()
        raw_midi = root.get("midi", {})
        midi = {name: MidiRoute.from_dict(raw_midi.get(name, defaults[name].to_dict()),
                                          defaults[name].channel) for name in defaults}
        raw_songs = root.get("songs")
        if not isinstance(raw_songs, list):
            raise ValueError("Show songs must be an array")
        show = cls(str(root.get("name", "Untitled Show")),
                   [ShowSong(str(s["id"]), str(s["title"]), str(s["song_json"])) for s in raw_songs],
                   midi, bool(root.get("live", {}).get("auto_advance", False)),
                   dict(root.get("lights", {}).get("mappings", {})), str(root.get("notes", "")),
                   root.get("console_profile"), dict(root.get("live", {}).get("panic", {})), target)
        show.validate()
        return show
