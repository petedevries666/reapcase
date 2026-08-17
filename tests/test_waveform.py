import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import wave

from stadium_reaper_bridge.editor.layout import HEADER_WIDTH
from stadium_reaper_bridge.editor.waveform import (
    choose_peak_level, display_peaks, extract_waveform, frame_x,
)


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


if __name__ == "__main__":
    unittest.main()
