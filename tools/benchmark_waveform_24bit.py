"""Focused PCM24 decoder benchmark; run with ``PYTHONPATH=src python tools/...``."""
from array import array
import argparse
from pathlib import Path
import struct
import tempfile
import time
import wave

from stadium_reaper_bridge.editor.waveform import _pcm_integers, extract_waveform


def reference_decode(data):
    view = memoryview(data)
    return array("i", ((view[i] | view[i + 1] << 8 | view[i + 2] << 16) -
                       ((view[i + 2] & 0x80) << 17)
                       for i in range(0, len(view), 3)))


def extrema(values, samples_per_bucket=64):
    result = []
    for start in range(0, len(values), samples_per_bucket):
        bucket = values[start:start + samples_per_bucket]
        result.append((min(bucket), max(bucket)))
    return result


def timed_candidate(name, decoder, payload):
    started = time.perf_counter(); values = decoder(payload); decoded = time.perf_counter()
    peaks = extrema(values); completed = time.perf_counter()
    seconds = decoded - started
    print(f"{name:10} decode={seconds:.4f}s min/max={completed-decoded:.4f}s "
          f"samples/s={len(values)/seconds:,.0f} decoded_buffer={len(values)*values.itemsize:,}B")
    return values, peaks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=1_000_000)
    args = parser.parse_args()
    # Deterministic stereo samples cover both signs without fixture I/O affecting decode.
    samples = args.frames * 2
    payload = b"".join(((index * 104729 & 0xFFFFFF).to_bytes(3, "little")
                        for index in range(samples)))
    old_values, old_peaks = timed_candidate("reference", reference_decode, payload)
    new_values, new_peaks = timed_candidate("optimized", lambda data: _pcm_integers(data, 3), payload)
    assert old_values == new_values and old_peaks == new_peaks
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "benchmark.wav"
        with wave.open(str(path), "wb") as target:
            target.setnchannels(2); target.setsampwidth(3); target.setframerate(48_000)
            target.writeframes(payload)
        started = time.perf_counter(); extract_waveform(path); elapsed = time.perf_counter() - started
    print(f"extraction total={elapsed:.4f}s throughput={len(payload)/(1024*1024)/elapsed:.2f} MiB/s")


if __name__ == "__main__":
    main()
