"""Incremental, transient-preserving, multi-resolution waveform analysis.

The cache deliberately contains only min/max envelopes.  Unlike an RMS or
average summary, an impulse therefore survives every pyramid reduction.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from pathlib import Path
import time
import wave
from typing import Callable

from .audio_engine import AudioEngine


DEFAULT_BASE_BUCKET_FRAMES = 128
DEFAULT_READ_FRAMES = 4096


@dataclass(frozen=True)
class PeakLevel:
    """A combined-channel min/max envelope at one frames-per-bucket scale."""

    frames_per_bucket: int
    minimum: array
    maximum: array

    def __len__(self) -> int:
        return len(self.minimum)


@dataclass(frozen=True)
class WaveformPyramid:
    duration_seconds: float
    sample_rate: int
    channels: int
    total_frames: int
    levels: tuple[PeakLevel, ...]

    @property
    def peaks(self) -> tuple[tuple[float, float], ...]:
        """Compatibility view of the highest-detail envelope."""
        level = self.levels[0]
        return tuple(zip(level.minimum, level.maximum))

    @property
    def memory_bytes(self) -> int:
        return sum(len(level.minimum) * level.minimum.itemsize * 2
                   for level in self.levels)


# Old public name retained for callers that only used the summary attributes.
WaveformSummary = WaveformPyramid


def _next_level(source: PeakLevel) -> PeakLevel:
    low, high = array("f"), array("f")
    for start in range(0, len(source), 2):
        stop = min(start + 2, len(source))
        low.append(min(source.minimum[start:stop]))
        high.append(max(source.maximum[start:stop]))
    return PeakLevel(source.frames_per_bucket * 2, low, high)


def extract_waveform(path: str | Path, base_bucket_frames: int = DEFAULT_BASE_BUCKET_FRAMES,
                     read_frames: int = DEFAULT_READ_FRAMES,
                     pause_requested: Callable[[], bool] | None = None,
                     buckets: int | None = None) -> WaveformPyramid:
    """Scan PCM audio incrementally and build a compact float32 peak pyramid.

    At most ``read_frames`` of PCM and one base bucket of decoded samples are
    handled at once.  ``pause_requested`` lets the UI yield disk and CPU while
    playback is active; this function is never called by the audio callback.
    """
    if base_bucket_frames < 1 or read_frames < 1:
        raise ValueError("Waveform bucket and read sizes must be positive")
    low_values, high_values = array("f"), array("f")
    with wave.open(str(path), "rb") as source:
        frames, rate, channels, width = (source.getnframes(), source.getframerate(),
                                         source.getnchannels(), source.getsampwidth())
        # Compatibility for the original fixed-summary API. New callers should
        # select the explicit, stable base resolution instead.
        if buckets is not None:
            if buckets < 1:
                raise ValueError("Waveform bucket count must be positive")
            base_bucket_frames = max(1, (frames + buckets - 1) // buckets)
        if channels not in (1, 2):
            raise ValueError("Waveforms support mono/stereo PCM")
        if width not in (2, 3):
            raise ValueError("Waveforms support 16/24-bit PCM")
        pending = 0
        bucket_low, bucket_high = 1.0, -1.0
        remaining = frames
        while remaining:
            while pause_requested and pause_requested():
                time.sleep(0.05)
            request = min(read_frames, remaining)
            data = source.readframes(request)
            if not data:
                break
            samples = AudioEngine._samples(data, width)
            decoded_frames = len(samples) // channels
            remaining -= decoded_frames
            chunk_frame = 0
            while chunk_frame < decoded_frames:
                used = min(base_bucket_frames - pending,
                           decoded_frames - chunk_frame)
                sample_start = chunk_frame * channels
                sample_stop = (chunk_frame + used) * channels
                portion = samples[sample_start:sample_stop]
                # Combining channels by extrema is compact and cannot hide a
                # click present in only the left or right channel.
                bucket_low = min(bucket_low, min(portion, default=0.0))
                bucket_high = max(bucket_high, max(portion, default=0.0))
                pending += used
                chunk_frame += used
                if pending == base_bucket_frames:
                    low_values.append(bucket_low); high_values.append(bucket_high)
                    pending, bucket_low, bucket_high = 0, 1.0, -1.0
        if pending:
            low_values.append(bucket_low); high_values.append(bucket_high)

    levels = [PeakLevel(base_bucket_frames, low_values, high_values)]
    while len(levels[-1]) > 1:
        levels.append(_next_level(levels[-1]))
    return WaveformPyramid(frames / rate if rate else 0.0, rate, channels,
                           frames, tuple(levels))


def choose_peak_level(summary: WaveformPyramid, pixel_width: float,
                      max_objects: int = 2000) -> PeakLevel:
    """Choose the finest useful level (about one bucket per horizontal pixel)."""
    useful_pixels = max(1.0, min(float(max_objects), pixel_width))
    frames_per_pixel = summary.total_frames / useful_pixels if summary.total_frames else 1
    # Choose the closest scale without selecting a bucket much wider than a
    # pixel. Rendering does a final per-pixel aggregation when needed.
    chosen = summary.levels[0]
    for level in summary.levels[1:]:
        if level.frames_per_bucket <= frames_per_pixel:
            chosen = level
        else:
            break
    return chosen


def display_peaks(summary: WaveformPyramid, pixel_width: float,
                  max_objects: int = 2000) -> list[tuple[float, float, float]]:
    """Return pixel-positioned min/max points from the zoom-appropriate level."""
    if pixel_width <= 0 or summary.total_frames <= 0:
        return []
    level = choose_peak_level(summary, pixel_width, max_objects)
    target = max(1, min(max_objects, round(pixel_width)))
    points = []
    start = 0
    while start < len(level):
        frame = start * level.frames_per_bucket
        # Map to a real display column rather than grouping with ceil(), which
        # can unexpectedly halve detail when cache count is one over width.
        column = min(target - 1, round(target * frame / summary.total_frames))
        stop = start + 1
        while stop < len(level):
            next_frame = stop * level.frames_per_bucket
            next_column = min(target - 1,
                              round(target * next_frame / summary.total_frames))
            if next_column != column:
                break
            stop += 1
        x = pixel_width * column / target
        points.append((x, min(level.minimum[start:stop]),
                       max(level.maximum[start:stop])))
        start = stop
    return points


def frame_x(frame: int, sample_rate: int, pixels_per_second: float,
            clip_start_x: float = 0.0) -> float:
    """Shared exact sample-frame to canvas coordinate mapping."""
    return clip_start_x + frame / sample_rate * pixels_per_second
