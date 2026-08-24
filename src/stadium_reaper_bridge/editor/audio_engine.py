"""Hardware-independent synchronized multitrack PCM playback.

The engine owns one output stream and one master frame counter.  ``sounddevice``
is imported only by :class:`SoundDeviceBackend`, so model and engine tests never
need audio hardware (or the optional dependency).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import logging
import os
import threading
import time
import wave
from typing import Optional, Protocol, Sequence

from .audio import AudioFileInfo, read_wav_info

LOG = logging.getLogger(__name__)
PERF = os.environ.get("REAPCASE_LOAD_PERF", "").lower() in ("1", "true", "yes")


class PlaybackError(ValueError):
    """A clear, user-facing incompatibility diagnostic."""


class PlaybackState(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    ENDED = "ended"


@dataclass(frozen=True)
class PlaybackTrack:
    path: Path
    name: str = ""
    offset: object = 0
    file_info: Optional[AudioFileInfo] = None


@dataclass
class PreparedAudio:
    """Filesystem/backend resources prepared away from the UI thread."""
    readers: list[wave.Wave_read]
    infos: list[AudioFileInfo]
    stream: object
    blocksize: int

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            for reader in self.readers:
                reader.close()


class OutputBackend(Protocol):
    def open_stream(self, callback, *, samplerate: int, channels: int,
                    blocksize: int): ...


class SoundDeviceBackend:
    """Small callback backend; construction reports a missing dependency."""

    def __init__(self):
        try:
            import sounddevice  # type: ignore
        except ImportError as exc:
            raise PlaybackError("Engine unavailable: sounddevice not installed") from exc
        self._sd = sounddevice

    def open_stream(self, callback, *, samplerate, channels, blocksize):
        return self._sd.OutputStream(callback=callback, samplerate=samplerate,
                                     channels=channels, dtype="float32",
                                     blocksize=blocksize)


class AudioEngine:
    """Mix at most eight WAVs against a single, locked master frame position."""

    def __init__(self, backend: Optional[OutputBackend] = None, blocksize: int = 1024):
        self.backend = backend
        self.blocksize = blocksize
        self.sample_rate = 0
        self.channels = 2
        self.total_frames = 0
        self._frame = 0
        self._state = PlaybackState.STOPPED
        self._readers: list[wave.Wave_read] = []
        self._infos: list[AudioFileInfo] = []
        self._stream = None
        self._muted: list[bool] = []
        self._solo: list[bool] = []
        self._lock = threading.RLock()
        self.diagnostic = "Engine: not configured"

    @property
    def state(self): return self._state

    @property
    def current_frame(self):
        with self._lock: return self._frame

    @property
    def current_time(self):
        return self.current_frame / self.sample_rate if self.sample_rate else 0.0

    @property
    def at_end(self): return self._state is PlaybackState.ENDED

    def open(self, tracks: Sequence[PlaybackTrack]) -> None:
        """Compatibility entry point; prepare then atomically install resources."""
        self.commit(self.prepare(tracks))

    def prepare(self, tracks: Sequence[PlaybackTrack]) -> PreparedAudio:
        """Perform WAV/backend I/O without changing live playback state."""
        if len(tracks) > 8:
            raise PlaybackError("Playback supports at most 8 tracks")
        if not tracks:
            raise PlaybackError("Playback disabled: no resolved WAV tracks")
        unsafe = [t.name or t.path.name for t in tracks if t.offset != 0]
        if unsafe:
            raise PlaybackError("Playback disabled: unknown non-zero offset on " + ", ".join(unsafe))
        # Progressive Song loading already inspected these identities.  Reuse
        # that header data rather than opening every WAV for a second probe.
        infos = [t.file_info or read_wav_info(t.path) for t in tracks]
        rates = {i.sample_rate for i in infos}
        if len(rates) != 1:
            raise PlaybackError("Playback disabled: sample-rate mismatch")
        if any(i.channels not in (1, 2) for i in infos):
            raise PlaybackError("Playback disabled: only mono/stereo PCM WAV is supported")
        if any(i.sample_width not in (2, 3) for i in infos):
            raise PlaybackError("Playback disabled: only 16-bit and 24-bit PCM WAV is supported")
        try:
            readers = [wave.open(str(t.path), "rb") for t in tracks]
            backend = self.backend or SoundDeviceBackend()
            stream = backend.open_stream(self._callback, samplerate=infos[0].sample_rate,
                                         channels=2, blocksize=self.blocksize)
        except Exception:
            for reader in locals().get("readers", []): reader.close()
            raise
        return PreparedAudio(readers, infos, stream, self.blocksize)

    def commit(self, prepared: PreparedAudio) -> None:
        """Install fully prepared resources as one stable engine configuration."""
        started = time.perf_counter()
        close_started = time.perf_counter()
        self.close()
        if PERF:
            LOG.debug("AudioEngine.close inside commit %.1f ms",
                      (time.perf_counter() - close_started) * 1000)
        infos = prepared.infos
        self._readers, self._infos, self._stream = (prepared.readers, prepared.infos,
                                                     prepared.stream)
        self.sample_rate = infos[0].sample_rate
        self.total_frames = max(i.frames for i in infos)
        self._muted = [False] * len(infos); self._solo = [False] * len(infos)
        self.diagnostic = f"Engine: ready | {self.sample_rate} Hz | Stereo | Buffer: {self.blocksize}"
        if PERF:
            LOG.debug("AudioEngine.commit total %.1f ms",
                      (time.perf_counter() - started) * 1000)

    def set_monitor(self, index: int, *, muted: Optional[bool] = None,
                    solo: Optional[bool] = None) -> None:
        with self._lock:
            if muted is not None: self._muted[index] = muted
            if solo is not None: self._solo[index] = solo

    def play(self):
        with self._lock:
            if not self._stream: raise PlaybackError(self.diagnostic)
            if self._frame >= self.total_frames: self._frame = 0
            self._state = PlaybackState.PLAYING
            self._stream.start()

    def pause(self):
        with self._lock:
            if self._state is PlaybackState.PLAYING: self._state = PlaybackState.PAUSED

    def stop(self):
        with self._lock:
            self._state = PlaybackState.STOPPED; self._frame = 0

    def seek(self, seconds: float):
        with self._lock:
            self._frame = min(self.total_frames, max(0, round(seconds * self.sample_rate)))
            if self._frame == self.total_frames: self._state = PlaybackState.ENDED
            elif self._state is PlaybackState.ENDED: self._state = PlaybackState.STOPPED
            for reader, info in zip(self._readers, self._infos):
                reader.setpos(min(self._frame, info.frames))

    return_to_start = stop

    @staticmethod
    def _samples(data: bytes, width: int):
        if width == 2:
            return [int.from_bytes(data[i:i+2], "little", signed=True) / 32768.0
                    for i in range(0, len(data), 2)]
        values = []
        for i in range(0, len(data), 3):
            raw = int.from_bytes(data[i:i+3], "little", signed=False)
            if raw & 0x800000: raw -= 1 << 24
            values.append(raw / 8388608.0)
        return values

    def _mix(self, frames: int) -> list[list[float]]:
        start = self._frame
        output = [[0.0, 0.0] for _ in range(frames)]
        active_solo = any(self._solo)
        for index, (reader, info) in enumerate(zip(self._readers, self._infos)):
            enabled = not self._muted[index] and (not active_solo or self._solo[index])
            if reader.tell() != min(start, info.frames): reader.setpos(min(start, info.frames))
            available = max(0, min(frames, info.frames - start))
            data = reader.readframes(available)
            if not enabled: continue
            samples = self._samples(data, info.sample_width)
            for frame in range(available):
                if info.channels == 1: left = right = samples[frame]
                else: left, right = samples[frame*2:frame*2+2]
                output[frame][0] += left; output[frame][1] += right
        # Conservative averaging avoids clipping when many full-scale stems sum.
        contributing = sum(not mute and (not active_solo or solo)
                           for mute, solo in zip(self._muted, self._solo)) or 1
        for row in output:
            row[0] = max(-1.0, min(1.0, row[0] / contributing))
            row[1] = max(-1.0, min(1.0, row[1] / contributing))
        self._frame = min(self.total_frames, start + frames)
        if self._frame >= self.total_frames: self._state = PlaybackState.ENDED
        return output

    def _callback(self, outdata, frames, time_info=None, status=None):
        with self._lock:
            mixed = self._mix(frames) if self._state is PlaybackState.PLAYING else [[0.0, 0.0]] * frames
        outdata[:] = mixed

    def close(self):
        with self._lock:
            if self._stream:
                try: self._stream.stop(); self._stream.close()
                finally: self._stream = None
            for reader in self._readers: reader.close()
            self._readers = []; self._infos = []
            self._state = PlaybackState.STOPPED; self._frame = 0
