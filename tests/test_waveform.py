import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import wave
from concurrent.futures import CancelledError

from stadium_reaper_bridge.editor.layout import HEADER_WIDTH
from stadium_reaper_bridge.editor.waveform import (
    WaveformRenderCache, analyze_grid_sync, choose_peak_level, display_peaks, extract_waveform,
    frame_to_canvas_x, frame_x, raster_ppm, timed_extract_waveform, viewport_columns,
)
from stadium_reaper_bridge.editor.audio import TempoChange, TempoMap
from stadium_reaper_bridge.stadium import MusicalPosition


RATE = 48_000


def write_impulses(path: Path, frames: int, impulses: dict[int, tuple[float, float]]):
    with wave.open(str(path), "wb") as target:
        target.setnchannels(2); target.setsampwidth(2); target.setframerate(RATE)
        block = bytearray(frames * 4)
        for frame, (left, right) in impulses.items():
            struct.pack_into("<hh", block, frame * 4,
                             round(left * 32767), round(right * 32767))
        target.writeframes(block)


class WaveformTests(unittest.TestCase):
    @staticmethod
    def tempo(changes=((0, 120),)):
        ppqn = 240
        units = lambda p: ((p.bar - 1) * 4 + p.beat - 1) * ppqn + p.tick - 1
        position = lambda value: MusicalPosition(value // (ppqn * 4) + 1,
                                                  value // ppqn % 4 + 1, value % ppqn + 1)
        return TempoMap(ppqn, tuple(TempoChange(*item) for item in changes), units, position), position

    def test_pyramid_has_doubling_resolutions_and_preserves_transients(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "click.wav"
            write_impulses(path, 4096, {129: (1, 0), 2050: (0, -1)})
            summary = extract_waveform(path, base_bucket_frames=128, read_frames=257)
        self.assertGreater(len(summary.levels), 1)
        self.assertEqual([level.frames_per_bucket for level in summary.levels[:4]],
                         [128, 256, 512, 1024])
        for level in summary.levels:
            self.assertGreaterEqual(max(level.maximum), 0.99)
            self.assertLessEqual(min(level.minimum), -0.99)
        self.assertEqual(summary.channels, 2)

    def test_stale_waveform_scan_cancels_cooperatively(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cancel.wav"
            write_impulses(path, 4096, {})
            with self.assertRaises(CancelledError):
                extract_waveform(path, read_frames=32, cancel_requested=lambda: True)

    def test_worker_timing_measures_actual_extraction_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timed.wav"
            write_impulses(path, 512, {})
            result = timed_extract_waveform(path)
        self.assertLessEqual(result.worker_started, result.worker_completed)
        self.assertEqual(result.summary.total_frames, 512)

    def test_zoom_selects_the_finest_useful_pyramid_level(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silence.wav"
            write_impulses(path, RATE * 10, {})
            summary = extract_waveform(path)
        high = choose_peak_level(summary, pixel_width=4000)
        medium = choose_peak_level(summary, pixel_width=1000)
        low = choose_peak_level(summary, pixel_width=100)
        self.assertLessEqual(high.frames_per_bucket, medium.frames_per_bucket)
        self.assertLessEqual(medium.frames_per_bucket, low.frames_per_bucket)
        self.assertLessEqual(len(display_peaks(summary, 4000)), 2000)

    def test_beat_clicks_and_sync_errors_have_distinct_grid_positions(self):
        # Four beats at 120 BPM. A stereo-only-side click must remain visible.
        beats = [round(index * RATE * 0.5) for index in range(4)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "beat-click.wav"
            impulses = {frame: ((1.0, 0.0) if index % 2 else (0.0, -1.0))
                        for index, frame in enumerate(beats)}
            write_impulses(path, RATE * 2, impulses)
            summary = extract_waveform(path)
        pixels_per_beat = 180
        width = 4 * pixels_per_beat
        points = display_peaks(summary, width)
        peak_x = [x for x, low, high in points if low < -0.9 or high > 0.9]
        for expected in range(0, width, pixels_per_beat):
            self.assertLessEqual(min(abs(actual - expected) for actual in peak_x), 1.0)

        beat_frame = beats[2]
        pixels_per_second = pixels_per_beat * 2
        before = frame_x(beat_frame - 480, RATE, pixels_per_second, HEADER_WIDTH)
        exact = frame_x(beat_frame, RATE, pixels_per_second, HEADER_WIDTH)
        after = frame_x(beat_frame + 480, RATE, pixels_per_second, HEADER_WIDTH)
        self.assertGreater(exact - before, 1)
        self.assertGreater(after - exact, 1)

    def test_frame_zero_clip_and_grid_start_share_exact_coordinate(self):
        self.assertEqual(frame_x(0, RATE, 360, HEADER_WIDTH), HEADER_WIDTH)
        tempo, _ = self.tempo()
        self.assertEqual(frame_to_canvas_x(0, RATE, tempo, 240, 360, HEADER_WIDTH),
                         HEADER_WIDTH)

    def test_canonical_mapping_stays_aligned_across_tempo_change(self):
        tempo, _ = self.tempo(((0, 120), (960, 90)))
        for units in (0, 240, 720, 960, 1200, 1440):
            frame = round(tempo.units_to_seconds(units) * RATE)
            expected = HEADER_WIDTH + units / 240 * 180
            self.assertLessEqual(abs(frame_to_canvas_x(frame, RATE, tempo, 240, 180,
                                                       HEADER_WIDTH) - expected), 1)
        beat = round(tempo.units_to_seconds(1200) * RATE)
        positions = [frame_to_canvas_x(beat + delta, RATE, tempo, 240, 180, HEADER_WIDTH)
                     for delta in (-480, 0, 480)]
        self.assertLess(positions[0], positions[1]); self.assertLess(positions[1], positions[2])

    def test_viewport_raster_uses_cache_and_draws_columns_not_diamonds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "truth.wav"
            write_impulses(path, RATE, {RATE // 2: (1, 1)})
            summary = extract_waveform(path)
            tempo, _ = self.tempo()
            with patch("stadium_reaper_bridge.editor.waveform.wave.open",
                       side_effect=AssertionError("renderer reread WAV")):
                left, columns = viewport_columns(summary, tempo, 240, 400, 0, 900,
                                                 HEADER_WIDTH, margin=0)
                ppm = raster_ppm(columns)
        active = [index for index, (low, high) in enumerate(columns) if high > .9]
        self.assertTrue(active)
        self.assertLessEqual(len(active), 2)  # narrow vertical impulse, never a polygon slope
        self.assertTrue(ppm.startswith(b"P6\n"))
        self.assertEqual(left, HEADER_WIDTH)

    def test_viewport_waveform_is_clipped_to_canonical_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "origin.wav"
            write_impulses(path, RATE, {0: (1, 1)})
            summary = extract_waveform(path)
            tempo, _ = self.tempo()
            left, columns = viewport_columns(summary, tempo, 240, 180, 0, 400,
                                              HEADER_WIDTH, margin=32)
        self.assertEqual(left, HEADER_WIDTH)
        self.assertGreater(columns[0][1], .9)

    def test_click_sync_reports_exact_frames_and_signed_deviation(self):
        tempo, position = self.tempo()
        frames = {0: (1, 1), RATE // 2 - 480: (1, 1),
                  RATE: (1, 1), RATE * 3 // 2 + 480: (1, 1)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sync.wav"; write_impulses(path, RATE * 2, frames)
            results = analyze_grid_sync(path, tempo, 240, position)
        self.assertEqual([item.frame for item in results], sorted(frames))
        self.assertEqual(results[0].grid, "001-01.001")
        self.assertEqual(results[0].deviation_samples, 0)
        self.assertEqual(results[1].deviation_samples, -480)
        self.assertEqual(results[3].deviation_samples, 480)

    def test_extraction_is_chunked_and_does_not_mutate_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bounded.wav"
            write_impulses(path, RATE, {0: (1, 1)})
            before = hashlib.sha256(path.read_bytes()).digest()
            real_open = wave.open
            requests = []

            class Reader:
                def __init__(self, wrapped): self.wrapped = wrapped
                def __getattr__(self, name): return getattr(self.wrapped, name)
                def __enter__(self): return self
                def __exit__(self, *args): self.wrapped.close()
                def readframes(self, count):
                    requests.append(count)
                    return self.wrapped.readframes(count)

            with patch("stadium_reaper_bridge.editor.waveform.wave.open",
                       side_effect=lambda name, mode: Reader(real_open(name, mode))):
                extract_waveform(path, base_bucket_frames=128, read_frames=1000)
            after = hashlib.sha256(path.read_bytes()).digest()
        self.assertEqual(before, after)
        self.assertTrue(requests)
        self.assertLessEqual(max(requests), 1000)

    def test_analysis_cooperatively_pauses_when_playback_has_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pause.wav"
            write_impulses(path, 512, {})
            states = iter((True, True, False, False))
            with patch("stadium_reaper_bridge.editor.waveform.time.sleep") as sleep:
                summary = extract_waveform(
                    path, read_frames=256,
                    pause_requested=lambda: next(states, False))
        self.assertEqual(summary.total_frames, 512)
        self.assertEqual(sleep.call_count, 2)

    def test_tile_cache_reuses_playback_and_follow_scroll_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tiles.wav"
            write_impulses(path, RATE * 20, {RATE: (1, 1)})
            summary = extract_waveform(path)
        tempo, _ = self.tempo()
        cache = WaveformRenderCache(max_tiles=12, tile_width=256)
        first = cache.visible_tiles("song", summary, tempo, 240, 100, 256, 300,
                                    HEADER_WIDTH)
        misses = cache.stats["tile_miss"]
        # Playhead coordinates are intentionally not accepted by this API.
        for _playhead in range(100):
            again = cache.visible_tiles("song", summary, tempo, 240, 100, 280, 300,
                                        HEADER_WIDTH)
        self.assertEqual(cache.stats["tile_miss"], misses)
        self.assertIs(first[0][1], again[0][1])

        cache.visible_tiles("song", summary, tempo, 240, 100, 700, 300,
                            HEADER_WIDTH)
        self.assertEqual(cache.stats["tile_miss"], misses + 1)
        before = cache.stats["tile_miss"]
        cache.visible_tiles("song", summary, tempo, 240, 100, 256, 300,
                            HEADER_WIDTH)
        self.assertEqual(cache.stats["tile_miss"], before)

    def test_display_level_is_shared_and_source_invalidation_is_precise(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shared.wav"
            write_impulses(path, RATE * 4, {})
            summary = extract_waveform(path)
        tempo, _ = self.tempo()
        cache = WaveformRenderCache(max_tiles=4, tile_width=128)
        normal = cache.tile("full-song", summary, tempo, 240, 100, 1, HEADER_WIDTH)[1]
        # Ghost presentation asks for the same source tile and shares display data.
        ghost = cache.tile("full-song", summary, tempo, 240, 100, 1, HEADER_WIDTH)[1]
        self.assertIs(normal.display, ghost.display)
        # Nearby zoom can select the same multi-resolution level even though its
        # geometrically exact raster tile is distinct.
        zoomed = cache.tile("full-song", summary, tempo, 240, 101, 1, HEADER_WIDTH)[1]
        self.assertIs(normal.display, zoomed.display)
        cache.invalidate_source("full-song")
        self.assertEqual(cache.tile_count, 0)
        rebuilt = cache.tile("full-song", summary, tempo, 240, 100, 1, HEADER_WIDTH)[1]
        self.assertIsNot(rebuilt, normal)

    def test_tile_cache_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bounded-tiles.wav"
            write_impulses(path, RATE * 10, {})
            summary = extract_waveform(path)
        tempo, _ = self.tempo()
        cache = WaveformRenderCache(max_tiles=3, tile_width=64)
        for index in range(20):
            cache.tile("song", summary, tempo, 240, 100, index, HEADER_WIDTH)
        self.assertEqual(cache.tile_count, 3)
        self.assertEqual(cache.stats["tile_evict"], 17)


if __name__ == "__main__":
    unittest.main()
