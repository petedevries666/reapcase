import json
from pathlib import Path
import tempfile
import unittest

from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.structure import (derive_structure_layout,
                                                     sticky_label_x,
                                                     structure_sublane)
from stadium_reaper_bridge.editor.style import LANE_PALETTE, lane_colors
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import StadiumSong


DECODER = RigMidiDecoder.from_file("config/rig_midi.json")


def model_for(flags):
    document = {"name": "Synthetic", "ppqn": 240, "params": None,
                "flags": flags, "tracks": [], "unknown": {"kept": True}}
    return EditorModel(StadiumSong.from_dict(document), Path("synthetic.json"), DECODER)


class StructureLayoutTests(unittest.TestCase):
    def test_marker_regions_ignore_semantic_pause(self):
        model = model_for([
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "001-01.001|MARKER;INTRO;7;Off;Off;Off;false;A;B;C",
            "009-01.001|MARKER;VERSE;7;Off;Off;Off;false;A;B;C",
            "013-01.001|MARKER;BREAK;7;Off;On;Off;false;A;B;C",
            "017-01.001|MARKER;CHORUS;7;Off;Off;Off;false;A;B;C",
            "025-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ])
        layout = derive_structure_layout(model.timeline.events, model._units,
                                           model.song_end_units)
        regions = [region for region in layout.regions if region.kind == "marker"]
        self.assertEqual([region.label for region in regions], ["INTRO", "VERSE", "CHORUS"])
        self.assertEqual([(region.start_units, region.end_units) for region in regions],
                         [(model._units(model._position(0)), model._units(model._position(7680))),
                          (7680, 15360), (15360, model.song_end_units)])
        self.assertEqual(structure_sublane(model.timeline.events[3]), "pauses")
        self.assertEqual(structure_sublane(model.timeline.events[2]), "markers")

    def test_cycles_pair_chronologically_and_malformed_remains_unmatched(self):
        model = model_for([
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "003-01.001|CYCLE_START;;2;Infinite;Off",
            "007-01.001|CYCLE_END;;0",
            "009-01.001|CYCLE_END;;0",
            "011-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ])
        layout = derive_structure_layout(model.timeline.events, model._units,
                                           model.song_end_units)
        cycle = next(region for region in layout.regions if region.kind == "cycle")
        self.assertEqual(cycle.source_event_indices, (1, 2))
        self.assertEqual(layout.unmatched_cycle_indices, (3,))

    def test_sticky_label_clamping_and_narrow_suppression(self):
        self.assertEqual(sticky_label_x(100, 1000, 500, 80), 506)
        self.assertEqual(sticky_label_x(100, 1000, 0, 80), 106)
        self.assertEqual(sticky_label_x(100, 550, 500, 30), 506)
        self.assertIsNone(sticky_label_x(100, 150, 0, 45))

    def test_lane_palette_identity_and_selection(self):
        self.assertEqual(set(LANE_PALETTE), {"STRUCTURE", "STADIUM", "SECOND HELIX",
                                             "VIDEO", "MIDI / OTHER"})
        for lane in LANE_PALETTE:
            self.assertNotEqual(lane_colors(lane).normal, lane_colors(lane).selected)


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.model = model_for([
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "003-01.001|MARKER;VERSE;7;Off;Off;Off;false;A;B;C;future",
            "005-01.001|MIDI_CC;UNKNOWN;4;CC;1;10;99;opaque",
            "009-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ])

    def test_duplicate_group_is_independent_lossless_and_undoable(self):
        originals = list(self.model.timeline.events)
        self.model.selected = {1, 2}
        self.assertEqual(self.model.duplicate_selected(), 2)
        copies = self.model.timeline.events[-2:]
        self.assertEqual([event.source.payload for event in copies],
                         [event.source.payload for event in originals[1:3]])
        self.assertTrue(all(copy is not original for copy, original in zip(copies, originals[1:3])))
        self.assertEqual(len({event.source_index for event in copies}), 2)
        self.assertEqual(self.model.selected, {4, 5})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            self.model.save_as(path)
            reopened = EditorModel.open(path)
            self.assertEqual(len(reopened.timeline.events), 6)
            self.assertTrue(json.loads(path.read_text())["unknown"]["kept"])
        self.assertTrue(self.model.undo())
        self.assertEqual(self.model.timeline.events, originals)
        self.assertEqual(self.model.selected, {1, 2})

    def test_delete_group_and_undo(self):
        before = list(self.model.timeline.events)
        self.model.selected = {1, 2}
        self.assertEqual(self.model.delete_selected(), 2)
        self.assertEqual([event.source.type for event in self.model.timeline.events],
                         ["START", "END"])
        self.assertTrue(self.model.undo())
        self.assertEqual(self.model.timeline.events, before)
        self.assertEqual(self.model.selected, {1, 2})

    def test_start_and_end_are_protected(self):
        for index in (0, 3):
            self.model.selected = {index}
            self.assertEqual(self.model.delete_selected(), 0)
            self.assertEqual(self.model.duplicate_selected(), 0)


if __name__ == "__main__":
    unittest.main()
