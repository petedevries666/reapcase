from types import SimpleNamespace

from stadium_reaper_bridge.editor.app import ReapcaseEditor
from stadium_reaper_bridge.editor.audio_engine import PlaybackState
from stadium_reaper_bridge.editor.transport import resolve_play_start
from stadium_reaper_bridge.live_midi import (LiveEventClass, LiveMidiDispatcher,
                                              LiveMidiEvent)
from stadium_reaper_bridge.stadium import MusicalPosition
from stadium_reaper_bridge.timing import TimingMap


def timing_map():
    return TimingMap(480, [(MusicalPosition(1, 1, 1), 120, 4, 4)])


def units(bar, beat=1, tick=1):
    return timing_map().position_to_units(MusicalPosition(bar, beat, tick))


def test_pre_roll_off_leaves_measure_65_unchanged():
    timing = timing_map()
    assert resolve_play_start(timing, units(65)) == units(65)


def test_pre_roll_subtracts_physical_measures():
    timing = timing_map()
    assert resolve_play_start(timing, units(65), pre_roll_enabled=True,
                              pre_roll_measures=2) == units(63)
    assert resolve_play_start(timing, units(65), pre_roll_enabled=True,
                              pre_roll_measures=4) == units(61)


def test_pre_roll_clamps_to_song_start():
    assert resolve_play_start(timing_map(), units(2), pre_roll_enabled=True,
                              pre_roll_measures=4) == 0


class Variable:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class Engine:
    def __init__(self, state=PlaybackState.STOPPED, position=units(65)):
        self.state = state
        self.current_time = timing_map().units_to_seconds(position)
        self.seeks = []
        self.plays = 0
        self.pause_boundary = None

    def seek(self, seconds):
        self.current_time = seconds
        self.seeks.append(seconds)

    def play(self): self.plays += 1
    def pause_at(self, seconds): self.pause_boundary = seconds


class Midi:
    def __init__(self):
        self.starts = []
        self.generation = 1
        self.playing = False
        self.next_pause_units = None

    def seek(self, _units): pass

    def start(self, start_units):
        self.starts.append(start_units)
        self.playing = True
        return ()


def editor(state=PlaybackState.STOPPED):
    timing = timing_map()
    model = SimpleNamespace(
        tempo_map=timing, cursor=MusicalPosition(65, 1, 1),
        _position=timing.units_to_position)
    return SimpleNamespace(
        loading=False, _audio_ready=True, _audio_error=None,
        status=Variable(""), audio_engine=Engine(state), model=model,
        transport_position=Variable(""),
        live_midi=Midi(), pre_roll_enabled=Variable(True),
        pre_roll_measures=Variable(2), pre_roll_target_units=None,
        time_selection_start_units=None, time_selection_end_units=None,
        seek_units=lambda start: ReapcaseEditor.seek_units(app, start))


def test_new_play_uses_one_effective_start_for_audio_midi_and_cursor():
    global app
    app = editor()
    ReapcaseEditor.play_pause(app)
    assert app.audio_engine.seeks == [timing_map().units_to_seconds(units(63))]
    assert app.live_midi.starts == [units(63)]
    assert app.model.cursor == MusicalPosition(63, 1, 1)
    assert app.pre_roll_target_units == units(65)
    assert app.audio_engine.plays == 1


def test_pause_resume_does_not_apply_pre_roll_again():
    global app
    app = editor(PlaybackState.PAUSED)
    ReapcaseEditor.play_pause(app)
    assert app.audio_engine.seeks == []
    assert app.live_midi.starts == [units(65)]


def test_time_selection_is_requested_target_and_end_is_pause_boundary():
    global app
    app = editor()
    app.time_selection_start_units = units(40)
    app.time_selection_end_units = units(48)
    original = (app.time_selection_start_units, app.time_selection_end_units)
    ReapcaseEditor.play_pause(app)
    assert app.live_midi.starts == [units(38)]
    assert app.audio_engine.seeks == [timing_map().units_to_seconds(units(38))]
    assert app.audio_engine.pause_boundary == timing_map().units_to_seconds(units(48))
    assert (app.time_selection_start_units, app.time_selection_end_units) == original


def test_effective_start_reuses_midi_recall_and_does_not_recall_actions():
    sent = []
    dispatcher = LiveMidiDispatcher(
        lambda message, recall, _generation: sent.append((message, recall)))
    state = LiveMidiEvent(units(60), 0, {"type": "program_change", "program": 7},
                          LiveEventClass.RECALLABLE_STATE, ("program", None))
    action = LiveMidiEvent(units(61), 1, {"type": "control_change", "cc": 60, "value": 127},
                           LiveEventClass.ACTION, ("action", 60))
    dispatcher.load((state, action))
    dispatcher.set_enabled(True)
    effective = resolve_play_start(timing_map(), units(65), pre_roll_enabled=True,
                                   pre_roll_measures=2)
    dispatcher.start(effective)
    assert sent == [(state.message, True)]


def test_pre_roll_defaults_are_read_from_editor_preferences_source():
    source = open("src/stadium_reaper_bridge/editor/app.py", encoding="utf-8").read()
    assert 'preferences.get("pre_roll_enabled", False)' in source
    assert 'preferences.get("pre_roll_measures", 2)' in source


def test_manager_navigation_has_no_pre_roll_branch():
    source = open("src/stadium_reaper_bridge/editor/navigation.py", encoding="utf-8").read()
    assert "pre_roll" not in source
