"""Models and export helpers for guided, observation-only experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import platform
import time
from typing import Callable, Dict, List, Optional
from uuid import uuid4
import wave

from .capture import recognize_capture
from .models import json_value, utc_now


@dataclass(frozen=True)
class ExperimentMarker:
    name: str
    timestamp: datetime
    elapsed_seconds: float
    details: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchAudio:
    filename: str
    size: int
    duration: float
    sample_rate: int
    channels: int
    sha256: str


def inspect_research_wav(path: Path) -> ResearchAudio:
    """Read cheap WAV metadata and hash a user-selected research asset only."""
    path = Path(path)
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        duration = source.getnframes() / rate if rate else 0.0
        channels = source.getnchannels()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return ResearchAudio(path.name, path.stat().st_size, duration, rate, channels,
                         digest.hexdigest())


class ResearchExperiment:
    """A controlled operation nested within ``NetworkResearchSession``."""

    def __init__(self, reapcase_version: str, clock: Callable[[], datetime] = utc_now,
                 monotonic: Callable[[], float] = time.monotonic):
        self.id = str(uuid4())
        self.session_id = str(uuid4())
        self.experiment_type = "OFFICIAL_CREATE_SONG"
        self.reapcase_version = reapcase_version
        self._clock = clock
        self._monotonic = monotonic
        self.started_at = clock()
        self._started_monotonic = monotonic()
        self.completed_at: Optional[datetime] = None
        self.song_name = "REAPCASE NET TEST 001"
        self.tempo = 123
        self.audio: List[ResearchAudio] = []
        self.markers: List[ExperimentMarker] = []
        self.result: Optional[str] = None
        self.notes = ""
        self.target: Optional[dict] = None
        self.capture: Optional[dict] = None
        self.user_observed_operation_duration: Optional[float] = None

    def mark(self, name: str, **details) -> ExperimentMarker:
        marker = ExperimentMarker(name, self._clock(),
                                  self._monotonic() - self._started_monotonic, details)
        self.markers.append(marker)
        return marker

    def set_capture(self, path: Path) -> dict:
        path = Path(path).resolve()
        capture_type = recognize_capture(path)
        self.capture = {"type": capture_type, "filename": path.name,
                        "size": path.stat().st_size, "external_path": str(path)}
        return self.capture

    def finish(self) -> None:
        self.completed_at = self._clock()

    def diagnostic(self) -> dict:
        verification = {"result": self.result, "note": self.notes}
        return json_value({
            "type": "reapcase_stadium_network_research", "version": 1,
            "session": {"id": self.session_id, "experiment_id": self.id,
                        "experiment_type": self.experiment_type,
                        "started_at": self.started_at, "completed_at": self.completed_at,
                        "result": self.result, "reapcase_version": self.reapcase_version,
                        "platform": platform.system()},
            "target": self.target,
            "experiment": {"operation": self.experiment_type,
                           "song": {"name": self.song_name, "tempo": self.tempo},
                           "audio": self.audio,
                           "user_observed_operation_duration_seconds":
                               self.user_observed_operation_duration},
            "markers": self.markers, "capture": self.capture,
            "stadium_verification": verification,
        })

    def export_folder(self, parent: Path) -> Path:
        folder = Path(parent) / "StadiumNetworkResearch" / self.session_id
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "session.json").write_text(
            json.dumps(self.diagnostic(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        trigger = next((m for m in self.markers if m.name == "CREATE_SONG_TRIGGER"), None)
        audio = ", ".join(item.filename for item in self.audio) or "None"
        capture = self.capture["external_path"] if self.capture else "Not associated"
        readme = ("Reapcase Stadium Network Research\n\n"
                  f"Experiment:\n    {self.experiment_type}\n\n"
                  f"Song:\n    {self.song_name}\n\nTempo:\n    {self.tempo} BPM\n\n"
                  f"Audio:\n    {audio}\n\nResult:\n    {self.result or 'NOT RECORDED'}\n\n"
                  "Critical marker:\n    CREATE_SONG_TRIGGER\n    "
                  f"{trigger.timestamp.isoformat() if trigger else 'Not recorded'}\n\n"
                  f"Packet capture:\n    {capture}\n\n"
                  "This session was generated for protocol research.\n"
                  "Reapcase did not send commands to Stadium.\n")
        (folder / "README.txt").write_text(readme, encoding="utf-8")
        return folder
