"""Compare waveform resolutions on a real WAV.

Run with ``PYTHONPATH=src python tools/benchmark_waveform_resolution.py song.wav``.
The min/max figure is the extraction time left after measured WAV reads, PCM
conversion, and pyramid construction; WAV open/finalization make it a slightly
conservative estimate.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import time
import wave

from stadium_reaper_bridge.editor import waveform
from stadium_reaper_bridge.editor.layout import MAX_PIXELS_PER_BEAT


@contextmanager
def measured_phases():
    totals = {"reads": 0.0, "decode": 0.0, "pyramid": 0.0}
    original_read = wave.Wave_read.readframes
    original_decode = waveform._pcm_integers
    original_next = waveform._next_level

    def timed(name, function):
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            result = function(*args, **kwargs)
            totals[name] += time.perf_counter() - started
            return result
        return wrapper

    wave.Wave_read.readframes = timed("reads", original_read)
    waveform._pcm_integers = timed("decode", original_decode)
    waveform._next_level = timed("pyramid", original_next)
    try:
        yield totals
    finally:
        wave.Wave_read.readframes = original_read
        waveform._pcm_integers = original_decode
        waveform._next_level = original_next


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wav")
    parser.add_argument("--bpm", type=float, default=120.0,
                        help="tempo used for maximum-zoom visual comparison")
    args = parser.parse_args()
    print("bucket  total_s  minmax_s pyramid_s buckets memory_MiB "
          "milliseconds bucket_pixels@max_zoom")
    for size in (32, 128, 256, 512, 1024):
        with measured_phases() as phases:
            started = time.perf_counter()
            summary = waveform.extract_waveform(args.wav, base_bucket_frames=size)
            total = time.perf_counter() - started
        minmax = max(0.0, total - sum(phases.values()))
        milliseconds = size / summary.sample_rate * 1000
        pixels = (size / summary.sample_rate * args.bpm / 60 *
                  MAX_PIXELS_PER_BEAT)
        print(f"{size:6d} {total:8.3f} {minmax:9.3f} "
              f"{phases['pyramid']:9.3f} {len(summary.levels[0]):7,d} "
              f"{summary.memory_bytes / 2**20:10.2f} {milliseconds:12.2f} "
              f"{pixels:22.2f}")


if __name__ == "__main__":
    main()
