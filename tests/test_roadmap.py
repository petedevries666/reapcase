import json

import pytest

from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.navigation import ViewState
from stadium_reaper_bridge.editor.roadmap import (
    RoadmapPageStyle, build_roadmap_document, normalize_roadmap_metadata)


def open_fixture(name="wanna_be_429.json"):
    return EditorModel.open("tests/fixtures/" + name)


def test_regions_become_print_neutral_sections_rows_and_measure_blocks():
    model = open_fixture()
    document = build_roadmap_document(model)
    assert document.title == "WANNA BE"
    assert (document.tempo, document.numerator, document.denominator) == (89.0, 4, 4)
    assert document.measures_per_row == 4
    assert document.sections[0].name == "CLIC 2bars"
    assert [b.start_measure for b in document.sections[0].rows[0].blocks] == [1, 2]
    assert all(block.measure_count == 1 and block.display_mode == "measure"
               for block in document.blocks)
    assert isinstance(document.page_style, RoadmapPageStyle)


def test_notes_and_layout_are_optional_sidecar_metadata_and_round_trip(tmp_path):
    source = tmp_path / "song.json"
    source.write_text(open("tests/fixtures/wanna_be_429.json").read(), encoding="utf-8")
    model = EditorModel.open(source)
    assert model.set_roadmap_note(3, "FILL")
    assert model.set_roadmap_measures_per_row(2)
    assert build_roadmap_document(model, model.roadmap_metadata()).blocks[2].measures[0].note.text == "FILL"
    model.save_as(source)
    native = json.loads(source.read_text())
    assert "roadmap" not in native
    reopened = EditorModel.open(source)
    assert reopened.roadmap_metadata()["notes"] == {"3": "FILL"}
    assert reopened.roadmap_metadata()["measures_per_row"] == 2


def test_roadmap_metadata_rejects_unsafe_shapes():
    with pytest.raises(ValueError):
        normalize_roadmap_metadata({"measures_per_row": 3})
    with pytest.raises(ValueError):
        normalize_roadmap_metadata({"notes": {"one": "fill"}})


def test_view_state_accepts_dedicated_roadmap_mode():
    state = ViewState(); state.switch("roadmap")
    assert state.current_view == "roadmap"
