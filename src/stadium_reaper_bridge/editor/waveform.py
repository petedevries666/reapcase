"""Incremental, transient-preserving, multi-resolution waveform analysis.

The cache deliberately contains only min/max envelopes.  Unlike an RMS or
average summary, an impulse therefore survives every pyramid reduction.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from pathlib import Path
import struct
import time
import wave
import zlib
from typing import Callable, Hashable, MutableMapping, Optional, Protocol, TypeVar, Union

from .audio_engine import AudioEngine


# 32 frames is below one display pixel at common DAW zoom levels at 48 kHz,
# while the upper pyramid levels keep long recordings compact to render.
DEFAULT_BASE_BUCKET_FRAMES = 32
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


def ghost_raster_cache_key(waveform_identity: Hashable, viewport_left: int,
                           viewport_width: int, pixels_per_beat: float,
                           ghost_bounds: tuple[int, int],
                           visible_lanes: tuple[str, ...], *, ppqn: int,
                           tempo_identity: Hashable = None) -> tuple:
    """Return the identity of a rendered FULL-SONG viewport.

    Deliberately absent is transport/playhead position: moving the playhead
    cannot change the pixels in this static, viewport-sized raster.
    """
    return (waveform_identity, tempo_identity, int(viewport_left),
            int(viewport_width), float(pixels_per_beat), int(ppqn),
            tuple(ghost_bounds), tuple(visible_lanes))


_CachedRaster = TypeVar("_CachedRaster")


def cached_ghost_raster(cache: MutableMapping[tuple, _CachedRaster], key: tuple,
                        render: Callable[[], _CachedRaster]) -> _CachedRaster:
    """Return a cached ghost raster, retaining only the current viewport."""
    if key not in cache:
        cache.clear()
        cache[key] = render()
    return cache[key]


@dataclass(frozen=True)
class SyncTransient:
    frame: int
    audio_seconds: float
    grid: str
    deviation_samples: int
    deviation_ms: float


def _next_level(source: PeakLevel) -> PeakLevel:
    low, high = array("f"), array("f")
    for start in range(0, len(source), 2):
        stop = min(start + 2, len(source))
        low.append(min(source.minimum[start:stop]))
        high.append(max(source.maximum[start:stop]))
    return PeakLevel(source.frames_per_bucket * 2, low, high)


def extract_waveform(path: Union[str, Path], base_bucket_frames: int = DEFAULT_BASE_BUCKET_FRAMES,
                     read_frames: int = DEFAULT_READ_FRAMES,
                     pause_requested: Optional[Callable[[], bool]] = None,
                     buckets: Optional[int] = None) -> WaveformPyramid:
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


class _TempoMap(Protocol):
    def seconds_to_units(self, seconds: float) -> int: ...
    def units_to_seconds(self, units: int) -> float: ...


def timeline_units_to_x(units: int, ppqn: int, pixels_per_beat: float,
                        origin_x: float = 0.0) -> float:
    return origin_x + units / ppqn * pixels_per_beat


def frame_to_canvas_x(frame: int, sample_rate: int, tempo_map: _TempoMap,
                      ppqn: int, pixels_per_beat: float,
                      origin_x: float = 0.0) -> float:
    """Canonical frame -> seconds -> tempo units -> canvas coordinate chain."""
    units = tempo_map.seconds_to_units(frame / sample_rate)
    return timeline_units_to_x(units, ppqn, pixels_per_beat, origin_x)


def frame_x(frame: int, sample_rate: int, pixels_per_second: float,
            clip_start_x: float = 0.0) -> float:
    """Legacy constant-tempo helper retained for external callers."""
    return clip_start_x + frame / sample_rate * pixels_per_second


def viewport_columns(summary: WaveformPyramid, tempo_map: _TempoMap, ppqn: int,
                     pixels_per_beat: float, viewport_left: int,
                     viewport_width: int, origin_x: float = 0.0,
                     margin: int = 32) -> tuple[int, list[tuple[float, float]]]:
    """Raster-ready min/max columns for only the visible canvas interval.

    This consumes the cached pyramid only: it never opens the WAV. Each cached
    extrema bucket is placed through the canonical tempo-aware mapping, rather
    than stretched across the clip's final width.
    """
    # Never allocate or paint clip pixels on the fixed track-header side of
    # the canonical timeline origin, even when the viewport margin reaches it.
    left = max(round(origin_x), int(viewport_left) - margin)
    right = max(left + 1, int(viewport_left + viewport_width) + margin)
    width = right - left
    columns: list[Optional[tuple[float, float]]] = [None] * width
    full_width = frame_to_canvas_x(summary.total_frames, summary.sample_rate,
                                   tempo_map, ppqn, pixels_per_beat, origin_x) - origin_x
    level = choose_peak_level(summary, max(1, full_width), max_objects=2_000_000_000)
    left_units = max(0, round((left - origin_x) / pixels_per_beat * ppqn))
    right_units = max(0, round((right - origin_x) / pixels_per_beat * ppqn))
    first_bucket = max(0, int(tempo_map.units_to_seconds(left_units) *
                              summary.sample_rate // level.frames_per_bucket) - 1)
    last_bucket = min(len(level), int(tempo_map.units_to_seconds(right_units) *
                                      summary.sample_rate // level.frames_per_bucket) + 2)
    for index in range(first_bucket, last_bucket):
        low, high = level.minimum[index], level.maximum[index]
        frame = index * level.frames_per_bucket
        x = frame_to_canvas_x(frame, summary.sample_rate, tempo_map, ppqn,
                              pixels_per_beat, origin_x)
        next_frame = min(summary.total_frames, frame + level.frames_per_bucket)
        next_x = frame_to_canvas_x(next_frame, summary.sample_rate, tempo_map,
                                   ppqn, pixels_per_beat, origin_x)
        first = max(0, int(round(x)) - left)
        stop = min(width, max(first + 1, int(round(next_x)) - left))
        for column in range(first, stop):
            old = columns[column]
            columns[column] = ((low, high) if old is None else
                               (min(old[0], low), max(old[1], high)))
    return left, [(0.0, 0.0) if value is None else value for value in columns]


def raster_ppm(columns: list[tuple[float, float]], height: int = 40,
               foreground: tuple[int, int, int] = (183, 220, 255),
               background: tuple[int, int, int] = (82, 107, 138)) -> bytes:
    """Draw independent vertical extrema columns (no diagonal interpolation)."""
    width, center = max(1, len(columns)), height // 2
    pixels = bytearray(background * (width * height))
    for x, (low, high) in enumerate(columns):
        y0 = max(0, min(height - 1, round(center - high * (center - 2))))
        y1 = max(0, min(height - 1, round(center - low * (center - 2))))
        for y in range(min(y0, y1), max(y0, y1) + 1):
            offset = (y * width + x) * 3
            pixels[offset:offset + 3] = bytes(foreground)
    return f"P6\n{width} {height}\n255\n".encode() + pixels


def raster_transparent_png(columns: list[tuple[float, float]], height: int,
                           foreground: tuple[int, int, int], *, stride: int = 1,
                           vertical_padding: int = 2) -> bytes:
    """Rasterize an envelope into one transparent RGBA PNG.

    The returned image is intentionally a single Tk canvas primitive no matter
    how wide the viewport is.  It consumes already-cached extrema columns and
    never performs audio analysis.
    """
    width, height = max(1, len(columns)), max(1, int(height))
    stride = max(1, int(stride))
    center = height / 2
    amplitude = max(1, center - max(0, vertical_padding))
    pixels = bytearray(width * height * 4)
    color = bytes((*foreground, 255))
    for x in range(0, len(columns), stride):
        low, high = columns[x]
        if low == high == 0:
            continue
        y0 = max(0, min(height - 1, round(center - high * amplitude)))
        y1 = max(0, min(height - 1, round(center - low * amplitude)))
        for y in range(min(y0, y1), max(y0, y1) + 1):
            offset = (y * width + x) * 4
            pixels[offset:offset + 4] = color
    scanlines = b"".join(
        b"\0" + pixels[y * width * 4:(y + 1) * width * 4]
        for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(scanlines, 6)) + chunk(b"IEND", b""))


def analyze_grid_sync(path: Union[str, Path], tempo_map: _TempoMap, ppqn: int,
                      units_to_position: Callable[[int], object],
                      threshold_ratio: float = 0.6) -> tuple[SyncTransient, ...]:
    """Find strong transient frames and measure them against nearest beats."""
    def amplitudes(source: wave.Wave_read):
        channels, width = source.getnchannels(), source.getsampwidth()
        while data := source.readframes(DEFAULT_READ_FRAMES):
            samples = AudioEngine._samples(data, width)
            for start in range(0, len(samples), channels):
                yield max(abs(value) for value in samples[start:start + channels])

    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        maximum = max(amplitudes(source), default=0.0)
    if maximum <= 0:
        return ()
    candidates: list[int] = []
    with wave.open(str(path), "rb") as source:
        active_frame = None
        active_value = -1.0
        for frame, value in enumerate(amplitudes(source)):
            if value >= maximum * threshold_ratio:
                if value > active_value:
                    active_frame, active_value = frame, value
            elif active_frame is not None:
                if not candidates or active_frame - candidates[-1] >= rate // 50:
                    candidates.append(active_frame)
                active_frame, active_value = None, -1.0
        if active_frame is not None and (not candidates or active_frame - candidates[-1] >= rate // 50):
            candidates.append(active_frame)
    results = []
    for frame in candidates:
        seconds = frame / rate
        units = tempo_map.seconds_to_units(seconds)
        beat_units = (tempo_map.nearest_beat_units(units)
                      if hasattr(tempo_map, "nearest_beat_units")
                      else round(units / ppqn) * ppqn)
        beat_seconds = tempo_map.units_to_seconds(beat_units)
        delta_seconds = seconds - beat_seconds
        results.append(SyncTransient(frame, seconds,
                                     units_to_position(beat_units).render(),
                                     round(delta_seconds * rate),
                                     delta_seconds * 1000))
    return tuple(results)


def format_grid_sync(results: tuple[SyncTransient, ...]) -> str:
    lines = ["CLICK SYNC", ""]
    for index, item in enumerate(results, 1):
        lines.extend((f"transient {index}", f"frame       {item.frame}",
                      f"audio time  {item.audio_seconds:.6f} s",
                      f"grid        {item.grid}",
                      f"deviation   {item.deviation_ms:+.3f} ms / "
                      f"{item.deviation_samples:+d} samples", ""))
    return "\n".join(lines)
