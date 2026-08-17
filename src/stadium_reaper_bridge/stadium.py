"""Lossless models for the Stadium side of the bridge.

Known fields are exposed for convenient editing while the complete decoded JSON
and original source text remain available for an exact, no-op round trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import json
import re
from typing import Any


_POSITION = re.compile(r"^(?P<bar>\d+)-(?P<beat>\d+)\.(?P<tick>\d+)$")


@dataclass(frozen=True, order=True)
class MusicalPosition:
    """A one-based Stadium `BAR-BEAT.TICK` position.

    Observed Stadium files use tick ``001`` for an exact beat boundary. Tick
    zero is therefore rejected until a real-world fixture demonstrates that it
    is valid.
    """

    bar: int
    beat: int
    tick: int
    original: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.bar < 1 or self.beat < 1 or self.tick < 1:
            raise ValueError("Stadium bar, beat, and tick values must be one-based")

    @classmethod
    def parse(cls, value: str, *, ppqn: int | None = None) -> "MusicalPosition":
        match = _POSITION.fullmatch(value)
        if not match:
            raise ValueError(f"Invalid Stadium musical position: {value!r}")
        position = cls(
            *(int(match.group(key)) for key in ("bar", "beat", "tick")),
            original=value,
        )
        position.validate(ppqn)
        return position

    def validate(self, ppqn: int | None = None) -> None:
        """Validate the tick against a Song PPQN when one is available."""
        if ppqn is not None:
            if isinstance(ppqn, bool) or not isinstance(ppqn, int) or ppqn < 1:
                raise ValueError(f"PPQN must be a positive integer, got {ppqn!r}")
            if self.tick > ppqn:
                raise ValueError(f"Tick {self.tick} exceeds Song PPQN {ppqn}")

    def render(self) -> str:
        if self.original is not None:
            parsed = type(self).parse(self.original)
            if (self.bar, self.beat, self.tick) == (parsed.bar, parsed.beat, parsed.tick):
                return self.original
        return f"{self.bar:03d}-{self.beat:02d}.{self.tick:03d}"


@dataclass(frozen=True)
class StadiumFlag:
    """A flag split only at the position delimiter.

    `payload` is deliberately opaque: known and future flag types receive the
    same lossless treatment.
    """

    position: MusicalPosition
    payload: str
    original: str | None = field(default=None, compare=False)

    @classmethod
    def parse(cls, value: str, *, ppqn: int | None = None) -> "StadiumFlag":
        position, separator, payload = value.partition("|")
        if not separator:
            raise ValueError(f"Stadium flag has no payload delimiter: {value!r}")
        return cls(MusicalPosition.parse(position, ppqn=ppqn), payload, original=value)

    @property
    def type(self) -> str:
        """Best-effort type for dispatch; never used to discard payload data."""
        return self.payload.partition(";")[0]

    def render(self) -> str:
        rendered = f"{self.position.render()}|{self.payload}"
        return self.original if self.original == rendered else rendered


@dataclass
class StadiumSong:
    """Editable known Song fields backed by a lossless JSON document."""

    name: Any
    ppqn: Any
    params: Any
    flags: list[StadiumFlag]
    tracks: Any
    _document: dict[str, Any] = field(repr=False)
    _original_text: str | None = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "StadiumSong":
        data = copy.deepcopy(document)
        return cls(
            name=data.get("name"),
            ppqn=data.get("ppqn"),
            params=copy.deepcopy(data.get("params")),
            flags=[
                StadiumFlag.parse(value, ppqn=data.get("ppqn"))
                for value in data.get("flags", [])
            ],
            tracks=copy.deepcopy(data.get("tracks")),
            _document=data,
        )

    @classmethod
    def from_json_text(cls, source: str) -> "StadiumSong":
        document = json.loads(source)
        if not isinstance(document, dict):
            raise ValueError("A Stadium Song JSON document must be an object")
        song = cls.from_dict(document)
        song._original_text = source
        return song

    def to_dict(self) -> dict[str, Any]:
        document = copy.deepcopy(self._document)
        document.update(
            name=copy.deepcopy(self.name),
            ppqn=copy.deepcopy(self.ppqn),
            params=copy.deepcopy(self.params),
            flags=[flag.render() for flag in self.flags],
            tracks=copy.deepcopy(self.tracks),
        )
        return document

    def to_json_text(self) -> str:
        """Return exact input bytes for a no-op, otherwise stable readable JSON."""
        if self._original_text is not None and self.to_dict() == self._document:
            return self._original_text
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
