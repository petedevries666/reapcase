"""GUI-independent text projections used by the editor."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ..stadium import StadiumSong
from ..timeline import TimelineEvent


@dataclass(frozen=True)
class SongHeaderMetadata:
    """The stable, initial-context identity shown above the timeline."""

    title: str
    filename: str
    bpm: Optional[float]
    numerator: int
    denominator: int
    flag_count: int
    ppqn: int

    @property
    def detail(self) -> str:
        tempo = f"{self.bpm:g} BPM" if self.bpm is not None else "tempo unavailable"
        return (f"{self.filename}  ·  {tempo}  ·  {self.numerator}/{self.denominator}"
                f"  ·  {self.flag_count} flags  ·  PPQN {self.ppqn}")


def song_header_metadata(song: StadiumSong, path: Union[str, Path]) -> SongHeaderMetadata:
    """Project native Song identity, using only its initial START context."""
    # Normalizing separators also makes Windows paths testable on POSIX.
    filename = Path(str(path).replace("\\", "/")).name
    start = next((flag.semantic_data() for flag in song.flags if flag.type == "START"), {})
    return SongHeaderMetadata(
        title=str(song.name), filename=filename, bpm=start.get("tempo"),
        numerator=start.get("time_signature_numerator", 4),
        denominator=start.get("time_signature_denominator", 4),
        flag_count=len(song.flags), ppqn=song.ppqn,
    )


def badge_text(event: TimelineEvent) -> str:
    if event.source.type == "LIGHTS":
        return event.data.get("name", "LIGHTS")
    source, data = event.source, event.data
    alias = data.get("rig_alias", {})
    if alias.get("system") == "video":
        return f"VIDEO {alias.get('video', '')} {alias['action'].replace('_', ' ').upper()}".replace("  ", " ")
    if alias.get("system") == "second_helix":
        if alias.get("action") == "expression":
            percentage = 0 if alias["value"] == 0 else 100
            return f"EXP{alias['expression']} {percentage}%"
        if alias.get("action") == "snapshot":
            return f"BASS SNAP {alias['snapshot']}"
        return f"BASS {alias['action'].upper()}"
    if source.type == "TIME":
        return (f"{data.get('tempo', '?'):g} BPM · "
                f"{data.get('time_signature_numerator', '?')}/{data.get('time_signature_denominator', '?')}")
    if source.type == "LOOPER":
        return f"LOOPER {data.get('action', '').upper()}".strip()
    if source.type == "PRESETSNAP" and data.get("snapshot"):
        snapshot = str(data["snapshot"])
        return snapshot.upper() if snapshot.lower().startswith("snap ") else f"SNAP {snapshot}"
    human = data.get("name") or data.get("label")
    return str(human or source.type).strip()
