# Waveform resolution benchmark

The focused benchmark was run on a generated 10,500,000-frame, stereo PCM16
WAV at 48 kHz. Times are seconds; memory includes every float32 pyramid level.

| base frames | total | min/max | pyramid | base buckets | memory | duration | pixels at max zoom |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 32 | 0.839 | 0.437 | 0.361 | 328,125 | 5.01 MiB | 0.67 ms | 0.48 px |
| 128 | 0.315 | 0.133 | 0.092 | 82,032 | 1.25 MiB | 2.67 ms | 1.92 px |
| **256** | **0.130** | **0.074** | **0.044** | **41,016** | **0.63 MiB** | **5.33 ms** | **3.84 px** |
| 512 | 0.076 | 0.045 | 0.022 | 20,508 | 0.31 MiB | 10.67 ms | 7.68 px |
| 1024 | 0.052 | 0.032 | 0.011 | 10,254 | 0.16 MiB | 21.33 ms | 15.36 px |

The visual calculation uses Reapcase's real maximum of 360 pixels per beat and
the benchmark's 120 BPM. A sixteenth note is 90 pixels at that zoom. The
256-frame default therefore provides several waveform observations within even
a small anticipation while keeping an isolated transient to roughly four
pixels. 512 frames saves another 54 ms in this in-memory fixture, but doubles
the transient uncertainty to almost eight pixels; 256 is the conservative
musical-editing choice.

Disk and filesystem cache behavior varies, so this fixture is intended to
isolate aggregation and pyramid cost. Reproduce the complete table on a target
WAV with:

```console
PYTHONPATH=src python tools/benchmark_waveform_resolution.py path/to/song.wav
```
