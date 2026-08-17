"""Incremental, bounded-memory waveform summaries and display aggregation."""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import wave

from .audio_engine import AudioEngine

@dataclass(frozen=True)
class WaveformSummary:
    duration_seconds: float
    sample_rate: int
    channels: int
    peaks: tuple[tuple[float, float], ...]

def extract_waveform(path: str | Path, buckets: int = 2000,
                     read_frames: int = 4096) -> WaveformSummary:
    """Scan a WAV in small chunks; memory is O(bucket count + chunk size)."""
    with wave.open(str(path), "rb") as source:
        frames, rate, channels, width = (source.getnframes(), source.getframerate(),
                                         source.getnchannels(), source.getsampwidth())
        if width not in (2, 3): raise ValueError("Waveforms support 16/24-bit PCM")
        bucket_frames = max(1, (frames + buckets - 1) // buckets)
        result, low, high, used = [], 1.0, -1.0, 0
        while True:
            data = source.readframes(min(read_frames, bucket_frames - used))
            if not data: break
            samples = AudioEngine._samples(data, width)
            # Combined stereo/mono envelope.
            low = min(low, min(samples, default=0.0)); high = max(high, max(samples, default=0.0))
            used += len(samples) // channels
            if used >= bucket_frames:
                result.append((low, high)); low, high, used = 1.0, -1.0, 0
        if used: result.append((low, high))
        return WaveformSummary(frames / rate if rate else 0, rate, channels, tuple(result))

def display_peaks(summary: WaveformSummary, pixel_width: float,
                  max_objects: int = 1500):
    """Return x/min/max points, aggregating cache buckets at low zoom."""
    count = len(summary.peaks)
    target = max(1, min(count, max_objects, round(max(1, pixel_width))))
    group = max(1, (count + target - 1) // target)
    points = []
    for start in range(0, count, group):
        chunk = summary.peaks[start:start + group]
        points.append((pixel_width * start / max(1, count),
                       min(p[0] for p in chunk), max(p[1] for p in chunk)))
    return points
