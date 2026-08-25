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
import math
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


@dataclass(frozen=True)
class TimingSnapshot:
    """One internally consistent view of the PortAudio output clock."""
    rendered_time: float
    stream_time: Optional[float]
    output_buffer_dac_time: Optional[float]
    stream_reported_latency: Optional[float]
    dac_estimated_audible_time: Optional[float]
    final_audible_time: float

    @property
    def effective_compensation(self) -> float:
        return max(0.0, self.rendered_time - self.final_audible_time)


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
        self._pause_frame: Optional[int] = None
        # outputBufferDacTime names when the first frame of a callback buffer
        # reaches the DAC.  Some Windows host APIs report that timestamp for
        # only the immediate callback buffer while stream.latency includes the
        # rest of the device pipeline, so both estimates must be considered.
        self._stream_output_latency: Optional[float] = None
        self._dac_first_frame: Optional[int] = None
        self._dac_time: Optional[float] = None
        self._play_start_frame = 0
        self._paused_audible_frame: Optional[int] = None
        self._last_timing_log = 0.0
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
    def rendered_time(self) -> float:
        """Audio position rendered/submitted to the output stream."""
        return self.current_time

    def _stream_time(self) -> Optional[float]:
        try:
            value = float(self._stream.time)
            return value if math.isfinite(value) else None
        except (AttributeError, TypeError, ValueError):
            return None

    @property
    def audible_time(self) -> float:
        """Best backend-supported estimate of the position heard at the DAC."""
        with self._lock:
            return self._timing_snapshot().final_audible_time

    def _timing_snapshot(self) -> TimingSnapshot:
        """Resolve DAC and full-pipeline latency clocks without mixing domains."""
        rendered = self.current_time
        stream_time = self._stream_time()
        dac_audible = None
        if (self.sample_rate and self._dac_time is not None and
                self._dac_first_frame is not None and stream_time is not None):
            # _dac_first_frame is captured before _mix advances _frame and is
            # therefore exactly the first sample described by this timestamp.
            dac_audible = (self._dac_first_frame / self.sample_rate +
                           max(0.0, stream_time - self._dac_time))

        final = rendered
        if (self.sample_rate and self._state is PlaybackState.PAUSED and
                self._paused_audible_frame is not None):
            final = self._paused_audible_frame / self.sample_rate
        elif self.sample_rate and self._state is PlaybackState.PLAYING:
            candidates = []
            if dac_audible is not None:
                candidates.append(dac_audible)
            if self._stream_output_latency is not None:
                candidates.append(rendered - self._stream_output_latency)
            if candidates:
                # stream.latency is PortAudio's full known output pipeline.
                # Taking the earlier valid position prevents a one-block DAC
                # timestamp from masking a substantially longer Windows path.
                floor = self._play_start_frame / self.sample_rate
                final = min(rendered, max(floor, min(candidates)))
        return TimingSnapshot(rendered, stream_time, self._dac_time,
                              self._stream_output_latency, dac_audible, final)

    @property
    def output_latency(self) -> Optional[float]:
        """Effective output latency, or ``None`` when the backend has no clock."""
        with self._lock:
            if self._state is not PlaybackState.PLAYING:
                return self._stream_output_latency
            snapshot = self._timing_snapshot()
            if snapshot.dac_estimated_audible_time is not None or \
                    snapshot.stream_reported_latency is not None:
                return snapshot.effective_compensation
            return None

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
        self._stream_output_latency = self._read_output_latency(prepared.stream)
        self._dac_first_frame = self._dac_time = None
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
            if self._state is not PlaybackState.PAUSED:
                self._play_start_frame = self._frame
            self._state = PlaybackState.PLAYING
            self._paused_audible_frame = None
            # A pre-pause callback timestamp cannot be extrapolated through a
            # stopped/silent interval; the next callback establishes a new one.
            self._dac_first_frame = self._dac_time = None
            self._stream.start()
            self._log_timing(force=True)

    def pause_at(self, seconds: Optional[float]) -> None:
        """Arm an exact audio-frame pause boundary for the callback thread."""
        with self._lock:
            self._pause_frame = (None if seconds is None else
                                 min(self.total_frames, max(0, round(seconds * self.sample_rate))))

    def pause(self):
        with self._lock:
            if self._state is PlaybackState.PLAYING:
                self._paused_audible_frame = round(self.audible_time * self.sample_rate)
                self._state = PlaybackState.PAUSED

    def stop(self):
        with self._lock:
            self._state = PlaybackState.STOPPED; self._frame = 0; self._pause_frame = None
            self._dac_first_frame = self._dac_time = None
            self._paused_audible_frame = None

    def seek(self, seconds: float):
        with self._lock:
            self._frame = min(self.total_frames, max(0, round(seconds * self.sample_rate)))
            if self._frame == self.total_frames: self._state = PlaybackState.ENDED
            elif self._state is PlaybackState.ENDED: self._state = PlaybackState.STOPPED
            for reader, info in zip(self._readers, self._infos):
                reader.setpos(min(self._frame, info.frames))
            self._pause_frame = None
            self._play_start_frame = self._frame
            self._dac_first_frame = self._dac_time = None
            self._paused_audible_frame = None

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
        requested_frames = frames
        if self._pause_frame is not None:
            frames = min(frames, max(0, self._pause_frame - start))
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
        if self._pause_frame is not None and self._frame >= self._pause_frame:
            self._state = PlaybackState.PAUSED
            self._pause_frame = None
        elif self._frame >= self.total_frames: self._state = PlaybackState.ENDED
        output.extend([[0.0, 0.0]] * (requested_frames - frames))
        return output

    def _callback(self, outdata, frames, time_info=None, status=None):
        with self._lock:
            if self._state is PlaybackState.PLAYING:
                dac_time = self._callback_time(time_info, "outputBufferDacTime")
                if dac_time is not None:
                    self._dac_first_frame, self._dac_time = self._frame, dac_time
            mixed = self._mix(frames) if self._state is PlaybackState.PLAYING else [[0.0, 0.0]] * frames
        outdata[:] = mixed
        self._log_timing()

    @staticmethod
    def _callback_time(time_info, name: str) -> Optional[float]:
        try:
            value = getattr(time_info, name)
        except (AttributeError, TypeError):
            try:
                value = time_info[name]
            except (KeyError, TypeError):
                return None
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _read_output_latency(stream) -> Optional[float]:
        try:
            latency = stream.latency
            if isinstance(latency, (tuple, list)):
                latency = latency[-1]
            latency = float(latency)
            return latency if math.isfinite(latency) and latency >= 0 else None
        except (AttributeError, TypeError, ValueError):
            return None

    def _log_timing(self, force: bool = False) -> None:
        if not PERF:
            return
        now = time.monotonic()
        if not force and now - self._last_timing_log < 1.0:
            return
        self._last_timing_log = now
        with self._lock:
            timing = self._timing_snapshot()
        show = lambda value: "unavailable" if value is None else f"{value:.6f}"
        latency = timing.stream_reported_latency
        LOG.debug(
            "AUDIO TIMING rendered_time=%.6f stream_time=%s outputBufferDacTime=%s "
            "stream_reported_latency_ms=%s dac_estimated_audible_time=%s "
            "effective_compensation_ms=%.3f final_audible_time=%.6f",
            timing.rendered_time, show(timing.stream_time),
            show(timing.output_buffer_dac_time),
            "unavailable" if latency is None else f"{latency * 1000:.3f}",
            show(timing.dac_estimated_audible_time),
            timing.effective_compensation * 1000, timing.final_audible_time)

    def close(self):
        with self._lock:
            if self._stream:
                try: self._stream.stop(); self._stream.close()
                finally: self._stream = None
            for reader in self._readers: reader.close()
            self._readers = []; self._infos = []
            self._state = PlaybackState.STOPPED; self._frame = 0
            self._stream_output_latency = None
            self._dac_first_frame = self._dac_time = None
            self._paused_audible_frame = None
