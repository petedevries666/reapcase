import json
from pathlib import Path
import tempfile

from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import StadiumSong


DECODER = RigMidiDecoder.from_file("config/rig_midi.json")


def model_for(markers, *, end=13, times=()):
    flags = ["001-01.001|START;;9;120;0;3;4;Off;true;A;B;Snap 1"]
    flags += list(times)
    flags += [f"{bar:03d}-01.001|MARKER;{name};7;Off;Off;Off;false;A;B;C"
              for bar, name in markers]
    flags += [f"{end:03d}-01.001|END;;5;Off;8.0;Pause;Off;2.0"]
    song = StadiumSong.from_dict({"name": "Synthetic", "ppqn": 240, "params": None,
                                  "flags": flags, "tracks": []})
    return EditorModel(song, Path("synthetic.json"), DECODER)


def names(model):
    return [event.data["name"] for event in model.timeline.events
            if event.source.type == "MARKER"]


def test_missing_correct_incorrect_and_parenthetical_suffixes_normalize():
    model = model_for([(1, "INTRO"), (5, "VERSE (4m)"),
                       (9, "REFRAIN (ALT) (2m)")])
    assert names(model) == ["INTRO (4m)", "VERSE (4m)", "REFRAIN (ALT) (4m)"]
    assert model.modified
    assert not model._normalize_structure_labels()


def test_move_insert_delete_renormalize_whole_sequence():
    model = model_for([(1, "A"), (5, "B")], end=9)
    model.selected = {2}
    model.shift_selected(bars=2)
    assert names(model) == ["A (6m)", "B (2m)"]
    marker = model.timeline.events[2]
    import copy
    inserted = copy.deepcopy(marker)
    inserted.position = model._position(model.timing_map.bar_start_units(3))
    inserted.data["name"] = "C"
    fields = list(inserted.source.fields); fields[1] = "C"
    from dataclasses import replace
    inserted.source = replace(inserted.source, payload=";".join(fields), original=None)
    model.insert_event(inserted)
    assert names(model) == ["A (2m)", "B (2m)", "C (4m)"]
    model.delete_selected()
    assert names(model) == ["A (6m)", "B (2m)"]


def test_signature_change_counts_musical_bars_and_save_reload_survives():
    model = model_for([(1, "A"), (5, "B")], end=9, times=(
        "005-01.001|TIME;;9;120;0;5;4",))
    assert names(model) == ["A (4m)", "B (4m)"]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "song.json"
        model.save_as(path)
        reopened = EditorModel.open(path)
        assert names(reopened) == ["A (4m)", "B (4m)"]
        assert not reopened.modified
