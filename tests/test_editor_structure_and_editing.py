import json
from pathlib import Path
import tempfile
import unittest

from stadium_reaper_bridge.editor.looper import (derive_looper_regions,
                                                  looper_display_label)
from stadium_reaper_bridge.editor.model import EditorModel, LANES
from stadium_reaper_bridge.editor.composite import (
    COMMANDS_HEIGHT, COMPOSITE_HEIGHT, event_sublane, lane_height, lane_top,
    looper_item_bounds, sublane_bounds, sublane_content_bounds)
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
    def test_end_flag_terminates_previous_region_without_starting_one(self):
        model = model_for([
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "005-01.001|MARKER;SOLO GUITAR;7;Off;Off;Off;false;A;B;C",
            "013-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ])
        layout = derive_structure_layout(model.timeline.events, model._units,
                                         model.song_end_units + 1920)
        regions = [region for region in layout.regions if region.kind == "marker"]
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].end_units, model._units(model.timeline.events[2].position))
        self.assertNotIn("END", [region.label for region in regions])

    def test_named_end_marker_is_boundary_not_trailing_region(self):
        model = model_for([
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "005-01.001|MARKER;SOLO;7;Off;Off;Off;false;A;B;C",
            "009-01.001|MARKER;END;7;Off;Off;Off;false;A;B;C",
            "013-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ])
        layout = derive_structure_layout(model.timeline.events, model._units,
                                         model.song_end_units)
        regions = [region for region in layout.regions if region.kind == "marker"]
        self.assertEqual([region.label for region in regions], ["SOLO (4m)"])
        self.assertEqual(regions[0].end_units, model._units(model.timeline.events[2].position))

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
        self.assertEqual([region.label for region in regions],
                         ["INTRO (8m)", "VERSE (8m)", "CHORUS (8m)"])
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
                                             "VIDEO", "LIGHTS", "MIDI / OTHER",
                                             "SEQCLICK", "SEQ INSTRUCTIONS"})
        for lane in LANE_PALETTE:
            self.assertNotEqual(lane_colors(lane).normal, lane_colors(lane).selected)


class LooperRegionTests(unittest.TestCase):
    def regions(self, model, system):
        return derive_looper_regions(model.timeline.events, model._units, system,
                                     model.song_end_units)

    def test_stadium_states_stop_and_point_actions(self):
        model = model_for([
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "010-01.001|LOOPER;RECORD;1;Record",
            "014-01.001|LOOPER;PLAY;1;Play",
            "016-01.001|LOOPER;REVERSE;1;Reverse",
            "018-01.001|LOOPER;HALF SPEED;1;Half Speed",
            "020-01.001|LOOPER;STOP;1;Stop",
            "024-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ])
        regions = self.regions(model, "STADIUM")
        self.assertEqual([(r.state, r.start_units, r.end_units) for r in regions],
                         [("RECORD", model._units(model.timeline.events[1].position),
                           model._units(model.timeline.events[2].position)),
                          ("PLAY", model._units(model.timeline.events[2].position),
                           model._units(model.timeline.events[5].position))])
        self.assertEqual([r.source_event_indices for r in regions], [(1,), (2,)])

    def test_second_helix_is_independent_and_open_ended(self):
        model = model_for([
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "010-01.001|LOOPER;PLAY;1;Play",
            "012-01.001|MIDI_CC;BASS PLAY;4;CC;3;61;127",
            "014-01.001|LOOPER;STOP;1;Stop",
            "016-01.001|MIDI_CC;BASS OVERDUB;4;CC;3;60;0",
            "020-01.001|MIDI_CC;BASS STOP;4;CC;3;61;0",
            "024-01.001|LOOPER;RECORD;1;Record",
            "028-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ])
        stadium = self.regions(model, "STADIUM")
        helix = self.regions(model, "SECOND HELIX")
        self.assertEqual((stadium[0].start_units, stadium[0].end_units),
                         (model._units(model.timeline.events[1].position),
                          model._units(model.timeline.events[3].position)))
        self.assertEqual([(r.state, r.end_units) for r in helix],
                         [("PLAY", model._units(model.timeline.events[4].position)),
                          ("OVERDUB", model._units(model.timeline.events[5].position))])
        self.assertTrue(stadium[-1].open_ended)
        self.assertEqual(stadium[-1].end_units, model.song_end_units)

    def test_malformed_stop_and_repeated_play_are_conservative(self):
        model = model_for([
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "002-01.001|LOOPER;STOP;1;Stop",
            "003-01.001|LOOPER;PLAY;1;Play",
            "005-01.001|LOOPER;PLAY;1;Play",
            "007-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ])
        regions = self.regions(model, "STADIUM")
        self.assertEqual(len(regions), 2)
        self.assertEqual(regions[0].end_units, regions[1].start_units)
        self.assertTrue(regions[1].open_ended)


class CompositeLaneTests(unittest.TestCase):
    def test_all_looper_items_share_vertical_geometry(self):
        for lane in ("STADIUM", "SECOND HELIX"):
            expected = sublane_content_bounds(LANES, lane, "looper")
            for label, width in (("RECORD", 160), ("OVERDUB", 220),
                                 ("STOP", 44), ("REVERSE", 68),
                                 ("HALF SPEED", 92)):
                with self.subTest(lane=lane, label=label):
                    bounds = looper_item_bounds(LANES, lane, 500, 500 + width)
                    self.assertEqual((bounds[1], bounds[3]), expected)
                    self.assertEqual(bounds[0], 500)

    def test_looper_display_labels_use_semantics_without_mutating_sources(self):
        model = model_for([
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "002-01.001|LOOPER;HALF SPEED;1;Half Speed",
            "003-01.001|MIDI_CC;BASS STOP;4;CC;3;61;0",
            "004-01.001|MIDI_CC;BASS REVERSE;4;CC;3;65;127",
            "005-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ])
        stadium, stop, reverse = model.timeline.events[1:4]
        original_stop = stop.source.render()
        self.assertEqual(looper_display_label(stadium, "STADIUM"), "HALF SPEED")
        self.assertEqual(looper_display_label(stop, "SECOND HELIX"), "STOP")
        self.assertEqual(looper_display_label(reverse, "SECOND HELIX"), "REVERSE")
        self.assertEqual(stop.source.render(), original_stop)
        self.assertIn("BASS STOP", stop.source.render())

    def test_composite_heights_bounds_and_following_lane_positions(self):
        self.assertEqual(lane_height("STADIUM"), COMPOSITE_HEIGHT)
        self.assertEqual(lane_height("SECOND HELIX"), COMPOSITE_HEIGHT)
        for lane in ("STADIUM", "SECOND HELIX"):
            commands = sublane_bounds(LANES, lane, "commands")
            looper = sublane_bounds(LANES, lane, "looper")
            self.assertEqual(commands[1], looper[0])
            self.assertEqual(commands[1] - commands[0], COMMANDS_HEIGHT)
            self.assertLess(commands[0], commands[1])
            self.assertLessEqual(commands[1], looper[0])
            self.assertEqual(looper[1], lane_top(LANES, lane) + lane_height(lane))
        for previous, following in zip(LANES, LANES[1:]):
            self.assertEqual(lane_top(LANES, following),
                             lane_top(LANES, previous) + lane_height(previous))

    def test_semantic_stadium_and_second_helix_classification(self):
        model = model_for([
            "001-01.001|START;;9;120;0;4;4;Off;true;A;B;Snap 1",
            "002-01.001|PRESETSNAP;A;1;7",
            "003-01.001|LOOPER;REVERSE;1;Reverse",
            "004-01.001|MIDI_CC;BASS SNAP;4;CC;3;69;6",
            "005-01.001|MIDI_CC;BASS STOP;4;CC;3;61;0",
            "006-01.001|MIDI_CC;BASS EXP;4;CC;3;1;127",
            "007-01.001|END;;5;Off;8.0;Pause;Off;2.0",
        ])
        expected = ("commands", "looper", "commands", "looper", "commands")
        events = model.timeline.events[1:6]
        self.assertEqual(tuple(event_sublane(event, model.lane(event)) for event in events),
                         expected)

    def test_looper_and_command_content_bounds_cannot_overlap(self):
        for lane in ("STADIUM", "SECOND HELIX"):
            commands = sublane_content_bounds(LANES, lane, "commands")
            looper = sublane_content_bounds(LANES, lane, "looper")
            self.assertLess(commands[1], looper[0])
            lane_bounds = (lane_top(LANES, lane),
                           lane_top(LANES, lane) + lane_height(lane))
            self.assertGreaterEqual(commands[0], lane_bounds[0])
            self.assertLessEqual(looper[1], lane_bounds[1])


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
        self.assertEqual(copies[0].data["name"], "VERSE (2m)")
        self.assertEqual(copies[1].source.payload, originals[2].source.payload)
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
