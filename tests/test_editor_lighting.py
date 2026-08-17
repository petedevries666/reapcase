import json
from pathlib import Path
import tempfile
import unittest

from stadium_reaper_bridge.editor.lighting import (
    LightingKind, create_lighting_event, derive_lighting_regions,
    normalized_cue_id, validate_cue_name,
)
from stadium_reaper_bridge.editor.model import EditorModel, LANES
from stadium_reaper_bridge.editor.style import LANE_PALETTE, lane_colors
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import MusicalPosition, StadiumSong


FIXTURE = Path(__file__).parent / "fixtures" / "monzter_332.json"
DECODER = RigMidiDecoder.from_file("config/rig_midi.json")


class LightingTests(unittest.TestCase):
    def model(self, path=FIXTURE):
        return EditorModel(StadiumSong.from_json_text(Path(path).read_text()), Path(path), DECODER)

    def test_lane_order_palette_and_semantic_identity(self):
        self.assertEqual(LANES[3:6], ("VIDEO", "LIGHTS", "MIDI / OTHER"))
        self.assertIn("LIGHTS", LANE_PALETTE)
        self.assertNotEqual(lane_colors("LIGHTS").normal, lane_colors("LIGHTS").selected)
        event = create_lighting_event(MusicalPosition(9, 1, 1), "SINGER ONLY", "STATE")
        self.assertEqual((event.data["cue_id"], event.data["kind"]), ("singer_only", "STATE"))
        self.assertEqual(self.model().lane(event), "LIGHTS")
        self.assertEqual(normalized_cue_id("Singer Backlight"), "singer_backlight")

    def test_states_derive_to_next_state_and_hits_do_not_split(self):
        events = [
            create_lighting_event(MusicalPosition(bar, 1, 1), name, kind)
            for bar, name, kind in ((1, "DARK", "STATE"), (9, "SINGER ONLY", "STATE"),
                                    (17, "BIG", "STATE"), (18, "WHITE HIT", "HIT"),
                                    (19, "BLINDER HIT", "HIT"), (25, "BLACKOUT", "STATE"))
        ]
        units = lambda position: (position.bar - 1) * 960
        regions = derive_lighting_regions(events, units, 32 * 960)
        self.assertEqual([(r.label, r.start_units // 960 + 1, r.end_units // 960 + 1)
                          for r in regions],
                         [("DARK", 1, 9), ("SINGER ONLY", 9, 17),
                          ("BIG", 17, 25), ("BLACKOUT", 25, 33)])
        self.assertTrue(regions[-1].open_ended)

    def test_validation_editing_undo_duplicate_and_recomputation(self):
        for invalid in ("", "bad;name", "bad|name", "x" * 81):
            with self.assertRaises(ValueError):
                validate_cue_name(invalid)
        model = self.model()
        first = model.insert_event(create_lighting_event(MusicalPosition(9, 1, 1), "DARK", LightingKind.STATE))
        second = model.insert_event(create_lighting_event(MusicalPosition(17, 1, 1), "BIG", LightingKind.STATE))
        hit = model.insert_event(create_lighting_event(MusicalPosition(21, 1, 1), "WHITE HIT", LightingKind.HIT))
        model.selected = {second, hit}
        self.assertEqual(model.duplicate_selected(), 2)
        copies = [model.timeline.events[i] for i in model.selected]
        self.assertEqual([(e.data["cue_id"], e.data["kind"]) for e in copies],
                         [("big", "STATE"), ("white_hit", "HIT")])
        self.assertTrue(model.undo())
        model.selected = {second}
        self.assertEqual(model.delete_selected(), 1)
        regions = derive_lighting_regions(model.timeline.events, model._units, model.song_end_units)
        self.assertEqual(regions[0].cue_id, "dark")
        self.assertTrue(model.undo())
        self.assertEqual(model.timeline.events[first].data["cue_id"], "dark")

    def test_sidecar_round_trip_never_changes_native_stadium_document(self):
        native = FIXTURE.read_text()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "song.json"
            model = self.model()
            model.insert_event(create_lighting_event(MusicalPosition(9, 1, 1), "SINGER ONLY", "STATE"))
            model.insert_event(create_lighting_event(MusicalPosition(21, 1, 1), "WHITE HIT", "HIT"))
            model.save_as(destination)
            self.assertEqual(destination.read_text(), native)
            sidecar = EditorModel.show_path(destination)
            self.assertEqual(json.loads(sidecar.read_text())["reapcase"]["version"], 1)
            reopened = EditorModel.open(destination)
            lights = [event for event in reopened.timeline.events if reopened.lane(event) == "LIGHTS"]
            self.assertEqual([(e.data["name"], e.data["kind"]) for e in lights],
                             [("SINGER ONLY", "STATE"), ("WHITE HIT", "HIT")])


if __name__ == "__main__":
    unittest.main()
