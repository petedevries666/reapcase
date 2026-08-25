from pathlib import Path
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

from stadium_reaper_bridge.editor.audio_engine import (AudioEngine, PlaybackError,
    PlaybackState, PlaybackTrack)
from stadium_reaper_bridge.editor.waveform import display_peaks, extract_waveform


class FakeStream:
    def __init__(self, callback, latency=None):
        self.callback=callback; self.started=False; self.closed=False
        if latency is not None: self.latency = latency
    def start(self): self.started=True
    def stop(self): self.started=False
    def close(self): self.closed=True
    def pump(self, frames):
        output=[[99.0,99.0] for _ in range(frames)]
        self.callback(output, frames)
        return output

class FakeBackend:
    def __init__(self, latency=None): self.latency = latency
    def open_stream(self, callback, **config):
        self.config=config; self.stream=FakeStream(callback, self.latency); return self.stream

def make_wav(path, *, rate=100, frames=20, channels=2, value=8192, width=2):
    sample=int(value).to_bytes(width, "little", signed=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels); target.setsampwidth(width); target.setframerate(rate)
        target.writeframes(sample * channels * frames)


class CommitPerformanceTests(unittest.TestCase):
    def test_commit_reports_close_and_total_timings(self):
        engine = AudioEngine()
        engine.close = lambda: None
        info = SimpleNamespace(sample_rate=48000, frames=100)
        prepared = SimpleNamespace(readers=[], infos=[info], stream=object())
        with patch("stadium_reaper_bridge.editor.audio_engine.PERF", True):
            with self.assertLogs("stadium_reaper_bridge.editor.audio_engine", "DEBUG") as logs:
                engine.commit(prepared)
        self.assertTrue(any("AudioEngine.close inside commit" in line for line in logs.output))
        self.assertTrue(any("AudioEngine.commit total" in line for line in logs.output))

class AudioEngineTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()

    def engine(self, counts=(20,20), rate=100):
        paths=[]
        for i, count in enumerate(counts):
            path=self.root/f"{i}.wav"; make_wav(path, rate=rate, frames=count); paths.append(path)
        backend=FakeBackend(); engine=AudioEngine(backend, blocksize=4)
        engine.open([PlaybackTrack(p, p.name) for p in paths]); return engine, backend

    def test_opens_eight_and_uses_one_stream_and_master_position(self):
        engine, backend=self.engine((20,)*8)
        self.assertEqual(engine.total_frames,20); self.assertEqual(backend.config["channels"],2)
        engine.play(); backend.stream.pump(4)
        self.assertEqual(engine.current_frame,4)
        self.assertTrue(all(reader.tell()==4 for reader in engine._readers))

    def test_pause_stop_seek_and_return_to_start(self):
        engine, backend=self.engine(); engine.play(); backend.stream.pump(5)
        engine.pause(); backend.stream.pump(5); self.assertEqual(engine.current_frame,5)
        engine.seek(.12); self.assertEqual(engine.current_frame,12)
        self.assertTrue(all(reader.tell()==12 for reader in engine._readers))
        engine.stop(); self.assertEqual(engine.current_frame,0)
        engine.seek(.1); engine.return_to_start(); self.assertEqual(engine.current_frame,0)

    def test_callback_pause_boundary_is_exact_and_pads_remaining_block(self):
        engine, backend=self.engine(rate=100)
        engine.pause_at(.06)
        engine.play()
        backend.stream.pump(4)
        output = backend.stream.pump(4)
        self.assertEqual(engine.current_frame, 6)
        self.assertEqual(engine.state, PlaybackState.PAUSED)
        self.assertNotEqual(output[1], [0.0, 0.0])
        self.assertEqual(output[2:], [[0.0, 0.0], [0.0, 0.0]])

    def test_backend_latency_produces_clamped_audible_clock(self):
        path=self.root/"latency.wav"; make_wav(path, rate=1000, frames=2000)
        backend=FakeBackend(latency=.040); engine=AudioEngine(backend, blocksize=1000)
        engine.open([PlaybackTrack(path)]); engine.play()
        self.assertEqual(engine.audible_time, 0.0)
        backend.stream.pump(1000)
        self.assertAlmostEqual(engine.rendered_time, 1.0)
        self.assertAlmostEqual(engine.output_latency, .040)
        self.assertAlmostEqual(engine.audible_time, .960)

    def test_backend_without_latency_preserves_rendered_clock(self):
        engine, backend=self.engine(counts=(20,)); engine.play(); backend.stream.pump(4)
        self.assertIsNone(engine.output_latency)
        self.assertEqual(engine.audible_time, engine.current_time)

    def test_pause_resume_seek_and_start_floor_keep_audible_clock_stable(self):
        path=self.root/"transport.wav"; make_wav(path, rate=100, frames=200)
        backend=FakeBackend(latency=.04); engine=AudioEngine(backend, blocksize=10)
        engine.open([PlaybackTrack(path)]); engine.seek(.50); engine.play()
        backend.stream.pump(2)
        self.assertEqual(engine.audible_time, .50)  # never before the seek/play origin
        backend.stream.pump(8)
        audible = engine.audible_time
        engine.pause(); self.assertAlmostEqual(engine.audible_time, audible)
        engine.play(); self.assertAlmostEqual(engine.audible_time, audible)
        engine.seek(.20); self.assertEqual(engine.audible_time, .20)

    def test_full_stream_latency_wins_when_dac_clock_only_sees_callback_block(self):
        path=self.root/"dac.wav"; make_wav(path, rate=1000, frames=2000)
        backend=FakeBackend(latency=.200); engine=AudioEngine(backend, blocksize=1000)
        engine.open([PlaybackTrack(path)]); backend.stream.time = 10.960; engine.play()
        output=[[0.0, 0.0] for _ in range(1000)]
        engine._callback(output, 1000, SimpleNamespace(outputBufferDacTime=10.0))
        self.assertAlmostEqual(engine.audible_time, .800)
        self.assertAlmostEqual(engine.output_latency, .200)

    def test_dac_timestamp_uses_callback_entry_frame_and_can_be_selected(self):
        path=self.root/"dac-only.wav"; make_wav(path, rate=1000, frames=3000)
        backend=FakeBackend(); engine=AudioEngine(backend, blocksize=1000)
        engine.open([PlaybackTrack(path)]); backend.stream.time = 10.250; engine.play()
        output=[[0.0, 0.0] for _ in range(1000)]
        engine._callback(output, 1000, SimpleNamespace(outputBufferDacTime=10.0))
        # The timestamp describes frame zero (captured before _mix), not the
        # rendered end frame at 1.0 seconds.
        self.assertEqual(engine._dac_first_frame, 0)
        self.assertAlmostEqual(engine.audible_time, .250)
        self.assertAlmostEqual(engine.output_latency, .750)

    def test_hardware_latency_regression_and_diagnostic_are_self_consistent(self):
        path=self.root/"windows.wav"; make_wav(path, rate=48000, frames=400000)
        backend=FakeBackend(latency=.213333); engine=AudioEngine(backend, blocksize=1024)
        engine.open([PlaybackTrack(path)]); engine._frame = 291136  # 6.065333 s
        engine._state = PlaybackState.PLAYING
        engine._dac_first_frame = 290112
        engine._dac_time = 100.0
        backend.stream.time = 100.0

        self.assertAlmostEqual(engine.rendered_time, 6.065333333)
        self.assertAlmostEqual(engine.audible_time, 5.852000333)
        self.assertGreaterEqual(engine.rendered_time, engine.audible_time)
        self.assertAlmostEqual(
            engine.output_latency * 1000,
            (engine.rendered_time - engine.audible_time) * 1000)

        with patch("stadium_reaper_bridge.editor.audio_engine.PERF", True):
            with self.assertLogs("stadium_reaper_bridge.editor.audio_engine", "DEBUG") as logs:
                engine._log_timing(force=True)
        diagnostic = logs.output[-1]
        for field in ("rendered_time=", "stream_time=", "outputBufferDacTime=",
                      "stream_reported_latency_ms=", "dac_estimated_audible_time=",
                      "effective_compensation_ms=", "final_audible_time="):
            self.assertIn(field, diagnostic)
        self.assertIn("effective_compensation_ms=213.333", diagnostic)

    def test_startup_latency_is_clamped_to_play_origin(self):
        path=self.root/"startup.wav"; make_wav(path, rate=1000, frames=2000)
        backend=FakeBackend(latency=.213333); engine=AudioEngine(backend)
        engine.open([PlaybackTrack(path)]); engine.seek(.5); engine.play()
        self.assertEqual(engine.audible_time, .5)
        self.assertGreaterEqual(engine.rendered_time, engine.audible_time)

    def test_short_track_silence_after_eof_and_end_state(self):
        engine, backend=self.engine((2,6)); engine.play(); output=backend.stream.pump(4)
        self.assertGreater(output[0][0], output[3][0])
        backend.stream.pump(2); self.assertEqual(engine.state,PlaybackState.ENDED)

    def test_mute_and_solo_are_local(self):
        engine, backend=self.engine(); engine.set_monitor(0, muted=True); engine.play()
        muted=backend.stream.pump(1)[0][0]; engine.seek(0)
        engine.set_monitor(0, muted=False); engine.set_monitor(1, solo=True)
        solo=backend.stream.pump(1)[0][0]
        self.assertAlmostEqual(muted,solo)

    def test_mismatch_and_unknown_offset_are_explicit_and_source_untouched(self):
        one=self.root/"one.wav"; two=self.root/"two.wav"
        make_wav(one,rate=100); make_wav(two,rate=200)
        with self.assertRaisesRegex(PlaybackError,"sample-rate mismatch"):
            AudioEngine(FakeBackend()).open([PlaybackTrack(one),PlaybackTrack(two)])
        source={"filename":str(one),"offset":7,"mute":True}; before=source.copy()
        with self.assertRaisesRegex(PlaybackError,"unknown non-zero offset"):
            AudioEngine(FakeBackend()).open([PlaybackTrack(one,offset=source["offset"])])
        self.assertEqual(source,before)

    def test_close_releases_stream_and_readers(self):
        engine, backend=self.engine(); readers=list(engine._readers); engine.close()
        self.assertTrue(backend.stream.closed); self.assertEqual(engine._readers,[])
        self.assertTrue(all(reader._file is None for reader in readers))

    def test_prepare_does_not_change_live_state_until_single_commit(self):
        path=self.root/"prepared.wav"; make_wav(path)
        backend=FakeBackend(); engine=AudioEngine(backend, blocksize=4)
        prepared=engine.prepare([PlaybackTrack(path)])
        self.assertEqual(engine.diagnostic,"Engine: not configured")
        self.assertEqual(engine._readers,[])
        engine.commit(prepared)
        self.assertEqual(len(engine._readers),1)
        self.assertTrue(engine.diagnostic.startswith("Engine: ready"))

    def test_waveform_incremental_peaks_and_zoom_aggregation(self):
        path=self.root/"wave.wav"; make_wav(path,frames=1000,value=16384)
        summary=extract_waveform(path,buckets=10,read_frames=7)
        self.assertEqual(len(summary.peaks),10)
        self.assertAlmostEqual(summary.peaks[0][1],.5)
        self.assertLessEqual(len(display_peaks(summary,3)),3)
        self.assertEqual(len(display_peaks(summary,100)),10)

if __name__ == "__main__": unittest.main()
