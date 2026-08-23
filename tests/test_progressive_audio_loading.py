import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from stadium_reaper_bridge.editor.app import ReapcaseEditor
from stadium_reaper_bridge.editor.audio_engine import PlaybackState
from stadium_reaper_bridge.editor.model import EditorModel


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
    def play(self): self.plays += 1
    def pause(self): pass


def editor_state(model=None, generation=2):
    return SimpleNamespace(
        model=model, _load_generation=generation, _audio_ready=False,
        _audio_error=None, loading=False, audio_engine=Engine(),
        play_button=Button(), audio_status=Variable(), status=Variable(),
        monitor_muted=[], monitor_solo=[])


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
    assert editor.audio_status.value == "Audio: READY — 1/2 resolved, 1 missing"


def test_engine_failure_keeps_play_disabled_and_non_modal():
    editor = editor_state()
    ReapcaseEditor._set_audio_error(editor, "device unavailable")
    assert not editor._audio_ready
    assert editor.play_button.states == [["disabled"]]
    assert editor.audio_status.value == "Audio: ERROR"
    assert editor.status.value == "device unavailable"
