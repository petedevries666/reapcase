import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

from stadium_reaper_bridge.editor import app as editor_app
from stadium_reaper_bridge.editor.app import ReapcaseEditor
from stadium_reaper_bridge.editor.audio_engine import PlaybackState
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.model import AudioProgress, AudioProgressPhase
from stadium_reaper_bridge.editor.audio import AudioResolver
from stadium_reaper_bridge.editor.waveform import TimedWaveformResult


FIXTURE = Path("tests/fixtures/perfect_picture_336.json")


class Variable:
    def __init__(self): self.value = None
    def set(self, value): self.value = value


class Button:
    def __init__(self): self.states = []
    def state(self, value): self.states.append(value)


class Prepared:
    def __init__(self): self.closed = False
    def close(self): self.closed = True


class Engine:
    def __init__(self):
        self.state = PlaybackState.STOPPED
        self.commits = self.plays = 0
    def commit(self, _prepared): self.commits += 1
    def prepare(self, _tracks): return Prepared()
    def play(self): self.plays += 1
    def pause(self): pass


class Progressbar:
    def __init__(self): self.config = {}; self.running = False
    def configure(self, **values): self.config.update(values)
    def start(self, _interval): self.running = True
    def stop(self): self.running = False


class ImmediatePool:
    def submit(self, function, *args):
        future = Future()
        try: future.set_result(function(*args))
        except BaseException as exc: future.set_exception(exc)
        return future


def polling_editor(model):
    callbacks, redraws, idle = [], [], []
    editor = editor_state(model)
    editor._audio_pool = ImmediatePool()
    editor.manual_audio_root = None
    editor.after = lambda _delay, callback: callbacks.append(callback)
    editor.after_idle = idle.append
    editor.winfo_exists = lambda: True
    editor.redraw = lambda: redraws.append(True)
    editor._show_audio_progress = lambda *_args: None
    editor._audio_load_current = lambda candidate, generation, cancel: \
        ReapcaseEditor._audio_load_current(editor, candidate, generation, cancel)
    editor._timed_loading_redraw = lambda completed: \
        ReapcaseEditor._timed_loading_redraw(editor, completed)
    editor._commit_prepared_audio = lambda prepared, candidate, generation, cancel, received_at=None: \
        ReapcaseEditor._commit_prepared_audio(
            editor, prepared, candidate, generation, cancel, received_at)
    editor._set_audio_ready = lambda candidate: ReapcaseEditor._set_audio_ready(editor, candidate)
    editor._request_initial_waveforms = lambda candidate, generation, cancel: None
    return editor, callbacks, redraws


def editor_state(model=None, generation=2):
    idle = []
    return SimpleNamespace(
        model=model, _load_generation=generation, _audio_ready=False,
        _audio_error=None, loading=False, audio_engine=Engine(),
        play_button=Button(), audio_status=Variable(), status=Variable(),
        monitor_muted=[], monitor_solo=[], _idle_callbacks=idle,
        after_idle=idle.append)


def test_resolution_stops_between_tracks_when_cancelled(monkeypatch):
    model = EditorModel.open_phased(FIXTURE)
    calls = []
    monkeypatch.setattr("stadium_reaper_bridge.editor.model.AudioResolver.resolve",
                        lambda _resolver, filename: calls.append(filename))
    cancel = threading.Event()
    results = model.audio_resolution_results(cancelled=cancel.is_set)
    next(results)
    cancel.set()
    assert list(results) == []
    assert len(calls) == 1


def test_new_job_does_not_wait_for_all_cancelled_song_tracks(monkeypatch):
    old = EditorModel.open_phased(FIXTURE)
    new = EditorModel.open_phased(FIXTURE)
    cancel = threading.Event()
    first_track = threading.Event()
    release = threading.Event()
    calls = []

    def resolve(_resolver, filename):
        calls.append(filename)
        first_track.set()
        release.wait(1)

    monkeypatch.setattr("stadium_reaper_bridge.editor.model.AudioResolver.resolve", resolve)
    with ThreadPoolExecutor(max_workers=1) as pool:
        stale = pool.submit(lambda: list(old.audio_resolution_results(cancelled=cancel.is_set)))
        assert first_track.wait(1)
        cancel.set()
        current = pool.submit(lambda: list(new.audio_resolution_results()))
        release.set()
        stale.result(timeout=1)
        current.result(timeout=1)
    assert len(calls) == 1 + len(new.audio_tracks)


def test_stale_generation_cannot_commit_engine_or_enable_play():
    old_model, current_model = object(), object()
    editor = editor_state(current_model)
    editor._audio_load_current = lambda model, generation, cancel: \
        ReapcaseEditor._audio_load_current(editor, model, generation, cancel)
    prepared = Prepared()
    ReapcaseEditor._commit_prepared_audio(
        editor, prepared, old_model, 1, threading.Event())
    assert prepared.closed
    assert editor.audio_engine.commits == 0
    assert editor.play_button.states == []
    assert not ReapcaseEditor._audio_load_current(
        editor, old_model, 1, threading.Event())  # same guard protects progress updates


def test_play_is_refused_until_ready_then_starts_without_waveforms():
    editor = editor_state()
    ReapcaseEditor.play_pause(editor)
    assert editor.audio_engine.plays == 0
    assert editor.status.value == "Audio is still loading…"
    editor._audio_ready = True
    ReapcaseEditor.play_pause(editor)
    assert editor.audio_engine.plays == 1


def test_single_engine_commit_enables_ready_without_waveform_completion():
    tracks = [SimpleNamespace(status="ready"), SimpleNamespace(status="missing")]
    model = SimpleNamespace(audio_tracks=tracks)
    editor = editor_state(model)
    editor._audio_load_current = lambda candidate, generation, cancel: \
        ReapcaseEditor._audio_load_current(editor, candidate, generation, cancel)
    editor._set_audio_ready = lambda candidate: ReapcaseEditor._set_audio_ready(editor, candidate)
    ReapcaseEditor._commit_prepared_audio(
        editor, Prepared(), model, 2, threading.Event())
    assert editor.audio_engine.commits == 1
    assert editor._audio_ready
    assert editor.play_button.states == [["!disabled"]]
    assert editor.audio_status.value == ("Audio: READY — 1/2 resolved, 1 missing"
                                         "  •  WAVEFORMS 0/0")
    assert len(editor._idle_callbacks) == 1


def test_initial_waveforms_are_scheduled_only_after_ready():
    track = SimpleNamespace(resolved_path=Path("track.wav"), file_info=object(), status="ready")
    model = SimpleNamespace(audio_tracks=[track])
    editor = editor_state(model)
    requested = []
    editor._audio_load_current = lambda candidate, generation, cancel: \
        ReapcaseEditor._audio_load_current(editor, candidate, generation, cancel)
    editor._set_audio_ready = lambda candidate: ReapcaseEditor._set_audio_ready(editor, candidate)
    editor._request_initial_waveforms = lambda candidate, generation, cancel: \
        ReapcaseEditor._request_initial_waveforms(editor, candidate, generation, cancel)
    editor._request_waveform = lambda path, generation: requested.append((path, generation))

    ReapcaseEditor._commit_prepared_audio(editor, Prepared(), model, 2, threading.Event())
    assert editor._audio_ready
    assert requested == []
    editor._idle_callbacks.pop()()
    assert requested == [(track.resolved_path, 2)]


class CanvasViewport:
    def __init__(self, top, height):
        self.top, self.height = top, height
    def canvasy(self, _y): return self.top
    def winfo_height(self): return self.height


class BoolVariable:
    def __init__(self, value): self.value = value
    def get(self): return self.value


def waveform_scheduling_editor(monkeypatch, *, ghost_visible):
    """Editor double whose viewport contains audio track indices two and three."""
    audio_top = 100
    monkeypatch.setattr(editor_app, "visible_lane_layout",
                        lambda *_args: SimpleNamespace(audio_top=audio_top))
    requested = []
    editor = SimpleNamespace(
        _audio_ready=True, canvas=CanvasViewport(
            audio_top + editor_app.LANE_HEIGHT * 2 + 1,
            editor_app.LANE_HEIGHT * 2 - 2),
        lane_order=[], full_song_ghost_visible=BoolVariable(ghost_visible),
        _effective_lane_visibility=lambda: {},
        _audio_load_current=lambda *_args: True,
        _request_waveform=lambda path, generation: requested.append((path, generation)))
    return editor, requested


def scheduling_model():
    names = ("CUES", "FULL-SONG", "VISIBLE", "CLICK")
    return SimpleNamespace(audio_tracks=[
        SimpleNamespace(name=name, resolved_path=Path(f"{name}.wav"), file_info=object())
        for name in names])


def test_visible_waveform_tracks_are_submitted_before_offscreen_tracks(monkeypatch):
    editor, requested = waveform_scheduling_editor(monkeypatch, ghost_visible=False)
    ReapcaseEditor._request_initial_waveforms(
        editor, scheduling_model(), 7, threading.Event())
    assert [path.name for path, _generation in requested] == [
        "VISIBLE.wav", "CLICK.wav", "CUES.wav", "FULL-SONG.wav"]


def test_visible_ghost_source_precedes_ordinary_offscreen_tracks(monkeypatch):
    editor, requested = waveform_scheduling_editor(monkeypatch, ghost_visible=True)
    ReapcaseEditor._request_initial_waveforms(
        editor, scheduling_model(), 7, threading.Event())
    assert [path.name for path, _generation in requested] == [
        "VISIBLE.wav", "CLICK.wav", "FULL-SONG.wav", "CUES.wav"]


def test_hidden_ghost_has_no_priority_and_order_is_deterministic(monkeypatch):
    orders = []
    for _ in range(2):
        editor, requested = waveform_scheduling_editor(monkeypatch, ghost_visible=False)
        ReapcaseEditor._request_initial_waveforms(
            editor, scheduling_model(), 7, threading.Event())
        orders.append([path.name for path, _generation in requested])
    assert orders == [["VISIBLE.wav", "CLICK.wav", "CUES.wav", "FULL-SONG.wav"]] * 2


def test_waveform_executor_is_bounded_to_two_workers():
    pool = editor_app._new_waveform_executor()
    try:
        assert editor_app.WAVEFORM_MAX_WORKERS == 2
        assert pool._max_workers == 2
    finally:
        pool.shutdown()


def test_stale_initial_waveform_callback_does_nothing():
    track = SimpleNamespace(resolved_path=Path("track.wav"), file_info=object(), status="ready")
    model = SimpleNamespace(audio_tracks=[track])
    editor = editor_state(model)
    editor._audio_ready = True
    editor._audio_load_current = lambda candidate, generation, cancel: \
        ReapcaseEditor._audio_load_current(editor, candidate, generation, cancel)
    editor._request_waveform = lambda *_args: (_ for _ in ()).throw(
        AssertionError("stale waveform request"))
    ReapcaseEditor._request_initial_waveforms(editor, model, 1, threading.Event())


def test_waveform_completion_invalidates_track_without_full_redraw():
    future = Future()
    now = 10.0
    future.set_result(TimedWaveformResult(object(), now, now + .25))
    callbacks, invalidated, redraws = [], [], []
    editor = SimpleNamespace(
        _load_generation=2, _waveform_cancel=threading.Event(),
        _waveform_pending=set(), waveforms={}, audio_engine=Engine(),
        _waveform_pool=SimpleNamespace(submit=lambda *_args, **_kwargs: future),
        after=lambda _delay, callback: callbacks.append(callback),
        winfo_exists=lambda: True, redraw=lambda: redraws.append(True),
        _invalidate_waveform_track=lambda source, generation: invalidated.append(
            (source, generation)))
    ReapcaseEditor._request_waveform(editor, "CLICK.wav", 2)
    callbacks.pop()()
    assert redraws == []
    assert invalidated == [("CLICK.wav", 2)]
    assert "CLICK.wav" in editor.waveforms


def waveform_progress_editor(paths, generation=4):
    tracks = [SimpleNamespace(status="ready", resolved_path=Path(path),
                              file_info=object()) for path in paths]
    model = SimpleNamespace(audio_tracks=tracks)
    editor = editor_state(model, generation)
    editor._audio_ready = True
    editor.waveforms = {}
    editor._waveform_pending = set()
    editor._waveform_cancel = threading.Event()
    editor._initial_waveform_generation = generation
    editor._initial_waveform_targets = set(paths)
    editor._initial_waveform_terminal = set()
    editor._initial_waveform_presentation_scheduled = True
    return editor


def test_audio_ready_coexists_with_real_waveform_progress_without_redraw():
    editor = waveform_progress_editor([f"{index}.wav" for index in range(8)])
    editor._initial_waveform_terminal.update(("0.wav", "1.wav"))
    editor.redraw = lambda: (_ for _ in ()).throw(AssertionError("timeline redraw"))
    ReapcaseEditor._update_waveform_header(editor, 4)
    assert editor.audio_status.value.endswith("WAVEFORMS 2/8")
    assert editor._audio_ready
    assert editor.play_button.states == []


def test_progress_advances_only_when_extraction_future_completes():
    editor = waveform_progress_editor(["CLICK.wav"])
    future = Future()
    callbacks, invalidated = [], []
    editor._waveform_pool = SimpleNamespace(submit=lambda *_args, **_kwargs: future)
    editor.after = lambda _delay, callback: callbacks.append(callback)
    editor.winfo_exists = lambda: True
    editor._invalidate_waveform_track = lambda path, generation: invalidated.append(path)
    ReapcaseEditor._request_waveform(editor, "CLICK.wav", 4)
    callbacks.pop()()  # not done: this merely schedules another Tk poll
    assert editor.audio_status.value is None
    future.set_result(TimedWaveformResult(object(), 10, 11))
    callbacks.pop()()
    assert invalidated == ["CLICK.wav"]
    assert editor.audio_status.value.endswith("WAVEFORMS READY")


def test_missing_and_invalid_tracks_do_not_stall_waveforms_ready():
    tracks = [SimpleNamespace(status="missing", resolved_path=None, file_info=None),
              SimpleNamespace(status="invalid", resolved_path=None, file_info=None)]
    model = SimpleNamespace(audio_tracks=tracks)
    editor = editor_state(model, 5)
    editor._audio_ready = True
    editor.waveforms = {}; editor._load_generation = 5
    editor._audio_load_current = lambda *_args: True
    editor._request_waveform = lambda *_args: None
    ReapcaseEditor._request_initial_waveforms(editor, model, 5, threading.Event())
    assert editor.audio_status.value.endswith("WAVEFORMS READY")


def test_stale_waveform_result_cannot_advance_new_song_header():
    editor = waveform_progress_editor(["NEW.wav"], generation=9)
    editor.audio_status.set("Audio: READY  •  WAVEFORMS 0/1")
    future = Future(); future.set_result(TimedWaveformResult(object(), 1, 2))
    callbacks = []
    editor._waveform_pool = SimpleNamespace(submit=lambda *_args, **_kwargs: future)
    editor.after = lambda _delay, callback: callbacks.append(callback)
    editor.winfo_exists = lambda: True
    editor._invalidate_waveform_track = lambda *_args: None
    ReapcaseEditor._request_waveform(editor, "OLD.wav", generation=8)
    callbacks.pop()()
    assert editor.audio_status.value.endswith("WAVEFORMS 0/1")


def test_lazy_tile_work_does_not_revert_global_waveform_ready():
    editor = waveform_progress_editor(["CLICK.wav"])
    editor._initial_waveform_terminal.add("CLICK.wav")
    ReapcaseEditor._update_waveform_header(editor, 4)
    ready = editor.audio_status.value
    # Pan/zoom changes tile/pending presentation state, not the initial job set.
    editor._waveform_pending.add("viewport tile")
    ReapcaseEditor._update_waveform_header(editor, 4)
    assert editor.audio_status.value == ready


def test_stale_waveform_completion_cannot_render_items():
    deleted = []
    editor = SimpleNamespace(
        _load_generation=3, model=object(), canvas=SimpleNamespace(
            delete=lambda tag: deleted.append(tag)))
    ReapcaseEditor._invalidate_waveform_track(editor, "CLICK.wav", generation=2)
    assert deleted == []


def test_engine_failure_keeps_play_disabled_and_non_modal():
    editor = editor_state()
    ReapcaseEditor._set_audio_error(editor, "device unavailable")
    assert not editor._audio_ready
    assert editor.play_button.states == [["disabled"]]
    assert editor.audio_status.value == "Audio: ERROR"
    assert editor.status.value == "device unavailable"


def test_resolution_session_indexes_fallback_once_and_preserves_tail_safety(tmp_path, monkeypatch):
    root = tmp_path / "Audio"
    (root / "one" / "deep").mkdir(parents=True)
    (root / "two").mkdir()
    wanted = root / "one" / "deep" / "CLICK.wav"
    wanted.write_bytes(b"wav")
    (root / "two" / "OTHER.wav").write_bytes(b"wav")
    import stadium_reaper_bridge.editor.audio as audio_module
    real_walk, calls = audio_module.os.walk, []
    monkeypatch.setattr(audio_module.os, "walk",
                        lambda path: (calls.append(path) or real_walk(path)))
    resolver = AudioResolver(tmp_path / "song", root)
    assert resolver.resolve("deep/CLICK.wav") == wanted.resolve()
    assert resolver.resolve("two/OTHER.wav") == (root / "two" / "OTHER.wav").resolve()
    assert len(calls) == 1

    (root / "duplicate").mkdir()
    (root / "duplicate" / "CLICK.wav").write_bytes(b"wav")
    fresh = AudioResolver(tmp_path / "song", root)
    assert fresh.resolve("CLICK.wav") is None


def test_song_specific_match_skips_global_index(tmp_path, monkeypatch):
    automatic = tmp_path / "Audio" / "SONG"
    automatic.mkdir(parents=True)
    match = automatic / "CLICK.wav"
    match.write_bytes(b"wav")
    monkeypatch.setattr("stadium_reaper_bridge.editor.audio.os.walk",
                        lambda _path: (_ for _ in ()).throw(AssertionError("unexpected scan")))
    assert AudioResolver(tmp_path, tmp_path / "Audio", automatic).resolve("CLICK.wav") == match.resolve()


def test_cancellation_interrupts_index_while_files_are_being_added(tmp_path, monkeypatch):
    root = tmp_path / "Audio"
    root.mkdir()
    walked = []

    def walk(path):
        walked.append(path)
        yield path, (), ("first.wav", "second.wav", "third.wav")

    checks = 0
    def cancelled():
        nonlocal checks
        checks += 1
        # Initial, directory, and first-file checks pass.  Cancellation is
        # observed while iterating the remaining files in that directory.
        return checks >= 4

    monkeypatch.setattr("stadium_reaper_bridge.editor.audio.os.walk", walk)
    resolver = AudioResolver(tmp_path, root, cancelled=cancelled)
    assert resolver.resolve("missing.wav") is None
    assert walked == [root.resolve()]
    assert checks >= 4


def test_worker_progress_does_not_publish_completion_before_result_is_applied():
    model = EditorModel.open_phased(FIXTURE)
    messages = []
    results = list(model.audio_resolution_results(progress=messages.append))
    assert results
    assert messages[0].phase == AudioProgressPhase.RESOLVING_PATH
    assert AudioProgressPhase.TRACK_COMPLETE not in {message.phase for message in messages}


def test_header_progress_switches_modes_without_redrawing_timeline():
    editor = SimpleNamespace(audio_progress=Progressbar(), audio_status=Variable())
    editor.redraw = lambda: (_ for _ in ()).throw(AssertionError("timeline redraw"))
    ReapcaseEditor._show_audio_progress(editor, AudioProgress(
        AudioProgressPhase.INDEXING_AUDIO, 1, 8, "CLICK.wav"))
    assert editor.audio_progress.running
    assert editor.audio_progress.config["mode"] == "indeterminate"
    ReapcaseEditor._show_audio_progress(editor, AudioProgress(
        AudioProgressPhase.TRACK_COMPLETE, 1, 8, "CLICK.wav"), 1)
    assert not editor.audio_progress.running
    assert editor.audio_progress.config["mode"] == "determinate"
    assert editor.audio_progress.config["value"] == 1


def test_queued_track_results_are_applied_with_one_redraw():
    results = [SimpleNamespace(track=SimpleNamespace(
        number=index, filename=f"{index}.wav", resolved_path=None, file_info=None,
        status="missing"))
        for index in range(1, 4)]
    applied = []
    model = SimpleNamespace(
        audio_tracks=[result.track for result in results],
        audio_resolution_results=lambda *_args: iter(results),
        apply_audio_resolution=applied.append)
    editor, callbacks, redraws = polling_editor(model)

    ReapcaseEditor._start_audio_resolution(editor, model, 2, threading.Event())
    callbacks.pop(0)()
    assert applied == results
    assert len(redraws) == 1
    assert editor.audio_engine.commits == 1


def test_queue_budget_yields_before_draining_every_track(monkeypatch):
    monkeypatch.setattr("stadium_reaper_bridge.editor.app.UI_AUDIO_POLL_BUDGET_SECONDS", 0)
    results = [SimpleNamespace(track=SimpleNamespace(
        number=index, filename=f"{index}.wav", resolved_path=None, file_info=None,
        status="missing"))
        for index in range(1, 4)]
    applied = []
    model = SimpleNamespace(
        audio_tracks=[result.track for result in results],
        audio_resolution_results=lambda *_args: iter(results),
        apply_audio_resolution=applied.append)
    editor, callbacks, redraws = polling_editor(model)

    ReapcaseEditor._start_audio_resolution(editor, model, 2, threading.Event())
    callbacks.pop(0)()
    assert len(applied) == 1
    assert len(redraws) == 1
    assert editor.audio_engine.commits == 0
    assert callbacks  # after(0, ...) gives Tk a paint/event opportunity.
