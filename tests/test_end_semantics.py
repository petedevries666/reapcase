import json
from pathlib import Path

import pytest

from stadium_reaper_bridge.analysis import SongAnalyzer
from stadium_reaper_bridge.editor.display import badge_text
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.navigation import structure_manager_rows
from stadium_reaper_bridge.editor.structure import derive_structure_layout
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import StadiumSong
from stadium_reaper_bridge.timeline import timeline_source_flags


DECODER = RigMidiDecoder.from_file("config/rig_midi.json")
BEHAVIORS = ("Pause", "Play Next", "Cue Next", "Repeat", "Cue Same")


def make_model(behavior="Pause", *, fade="Off", fade_length="8.0", gap="Off",
               gap_length="2.0", trailing="future"):
    flags = ["001-01.001|START;;9;120;0;4;4;Off;true;SET;PRESET;Snap 1",
             "002-01.001|MARKER;VERSE;7;Off;Off;Off;false;SET;PRESET;Snap 1",
             f"009-03.121|END;;5;{fade};{fade_length};{behavior};{gap};{gap_length};{trailing}"]
    song = StadiumSong.from_dict({"name": "End Test", "ppqn": 240, "params": "",
                                  "flags": flags, "tracks": []})
    return EditorModel(song, Path("end.json"), DECODER, resolve_audio_on_init=False)


@pytest.mark.parametrize("behavior", BEHAVIORS)
def test_each_native_end_behavior_parses_noop_edits_and_reopens(behavior):
    model = make_model(behavior)
    end = model.timeline.events[2]
    assert end.data["end_behavior"] == behavior
    assert model.lane(end) == "STRUCTURE"
    assert model.edit_event(2, dict(model.edit_capability(2).values)) is False
    reopened = StadiumSong.from_json_text(model.song.to_json_text())
    assert reopened.flags[2].semantic_data()["end_behavior"] == behavior
    assert reopened.flags[2].fields[-1] == "future"


@pytest.mark.parametrize("target", BEHAVIORS)
def test_end_behavior_edit_preserves_position_structure_and_unknown_fields(target):
    model = make_model()
    position = model.timeline.events[2].position
    values = dict(model.edit_capability(2).values, end_behavior=target)
    model.edit_event(2, values)
    end = model.timeline.events[2]
    assert end.position == position
    assert end.source.fields[2] == "5"
    assert end.source.fields[-1] == "future"
    assert badge_text(end) == f"END · {target.upper()}"
    row = next(row for row in structure_manager_rows(model) if row.kind == "END")
    assert row.name == f"END · {target.upper()}"
    # END remains only a terminator: the preceding region ends at END and no
    # region is created from END itself.
    regions = derive_structure_layout(model.timeline.events, model._units,
                                      model.song_end_units).regions
    assert len(regions) == 1
    assert regions[0].end_units == model._units(position)


def test_fade_gap_and_analysis_summary_round_trip():
    model = make_model("Play Next", fade="On", fade_length="3.5", gap="On",
                       gap_length="1.25")
    values = model.edit_capability(2).values
    assert values == {"end_behavior": "Play Next", "fade_out": True,
                      "fade_length": 3.5, "gap_before_next_song": True,
                      "gap_length": 1.25}
    model.edit_event(2, dict(values, end_behavior="Cue Same", fade_length=4.0,
                             gap_length=2.5))
    document = model.song.to_dict()
    document["flags"] = [flag.render() for flag in timeline_source_flags(model.timeline)]
    reopened = StadiumSong.from_dict(document)
    assert reopened.flags[2].fields[3:8] == ("On", "4.0", "Cue Same", "On", "2.5")
    assert SongAnalyzer().analyze(model).summary.end_behavior == "Cue Same"


def test_noop_song_save_retains_exact_end_source_text(tmp_path):
    source = json.dumps({"name": "x", "ppqn": 240, "params": "", "tracks": [],
                         "flags": ["004-01.001|END;;5;Off;8.0;Pause;Off;2.0"]},
                        separators=(",", ":"))
    song = StadiumSong.from_json_text(source)
    assert song.to_json_text() == source
    assert song.flags[0].render() == "004-01.001|END;;5;Off;8.0;Pause;Off;2.0"
