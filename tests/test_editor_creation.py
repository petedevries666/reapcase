import json
from pathlib import Path
import tempfile
import unittest

from stadium_reaper_bridge.editor.creation import (
    FLAG_CAPABILITIES, MarkerOptions, create_cycle_end, create_cycle_start,
    create_second_helix_expression, create_second_helix_looper, create_second_helix_snapshot,
    create_stadium_snapshot, create_structure_marker, create_video_command,
    parse_marker, stadium_context_at,
)
from stadium_reaper_bridge.editor.display import badge_text
from stadium_reaper_bridge.editor.layout import HEADER_WIDTH, snapped_units_at_x, timeline_x
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import MusicalPosition, StadiumSong


FIXTURES = Path(__file__).parent / "fixtures"
DECODER = RigMidiDecoder.from_file("config/rig_midi.json")


class CreationTests(unittest.TestCase):
    def model(self):
        path = FIXTURES / "monzter_332.json"
        return EditorModel(StadiumSong.from_json_text(path.read_text()), path, DECODER)

    def test_pointer_position_snaps_at_scales_scroll_independent_canvas_x(self):
        ppqn, beats = 240, 4
        cases = [
            (0, 90, "1 beat", 0),
            (56 * 4 * ppqn + 2 * ppqn + 86, 30, "1 bar", 57 * 4 * ppqn),
            (56 * 4 * ppqn + 2 * ppqn + 86, 180, "1 beat", 56 * 4 * ppqn + 2 * ppqn),
            (12 * ppqn + 61, 45, "quarter beat", 12 * ppqn + 60),
            (12 * ppqn + 87, 360, "no snap", 12 * ppqn + 87),
        ]
        for units, scale, grid, expected in cases:
            with self.subTest(grid=grid, scale=scale):
                canvas_x = timeline_x(units, ppqn, scale)
                # canvas_x already includes any viewport scrolling conversion.
                self.assertEqual(snapped_units_at_x(canvas_x, ppqn, scale, grid, beats), expected)
        self.assertEqual(snapped_units_at_x(HEADER_WIDTH - 500, ppqn, 90, "1 bar", beats), 0)

    def test_structure_marker_creation(self):
        event = create_structure_marker(MusicalPosition(57, 3, 1), "BREAKDOWN")
        self.assertEqual(event.source.render(),
                         "057-03.001|MARKER;BREAKDOWN;7;Off;Off;Off;false;[Current];[Current];[Current]")
        self.assertEqual(event.data["name"], "BREAKDOWN")
        for invalid in ("", "bad;name", "bad|name"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                create_structure_marker(MusicalPosition(1, 1, 1), invalid)

    def test_marker_pause_options_round_trip_without_unproven_fields(self):
        position = MusicalPosition(57, 3, 1)
        for enabled, value in ((False, "Off"), (True, "On")):
            with self.subTest(enabled=enabled):
                event = create_structure_marker(position, MarkerOptions("BREAK", enabled))
                self.assertEqual(event.source.fields,
                    ("MARKER", "BREAK", "7", "Off", value, "Off", "false",
                     "[Current]", "[Current]", "[Current]"))
                self.assertEqual(parse_marker(event.source), MarkerOptions("BREAK", enabled))

    def test_stadium_snapshot_uses_active_fixture_context_for_all_snapshots(self):
        model = self.model()
        position = MusicalPosition(57, 1, 1)
        context = stadium_context_at(model.timeline.events, position)
        self.assertEqual((context.setlist, context.preset), ("2 USER", "MONZTER GOOD WIP"))
        original = [flag.render() for flag in model.song.flags]
        for snapshot in range(1, 9):
            event = create_stadium_snapshot(position, snapshot, model.timeline.events)
            self.assertEqual(event.source.render(),
                f"057-01.001|PRESETSNAP;;3;2 USER;MONZTER GOOD WIP;Snap {snapshot}")
            self.assertEqual(event.data["snapshot"], f"Snap {snapshot}")
        self.assertEqual([flag.render() for flag in model.song.flags], original)

    def test_stadium_snapshot_save_reopen_and_unresolved_context(self):
        model = self.model()
        event = create_stadium_snapshot(MusicalPosition(57, 3, 1), 4,
                                         model.timeline.events)
        model.insert_event(event)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            model.save_as(path)
            reopened = EditorModel.open(path)
            created = reopened.timeline.events[-1]
            self.assertEqual(created.position, MusicalPosition(57, 3, 1))
            self.assertEqual(created.data, {"setlist": "2 USER",
                                            "preset": "MONZTER GOOD WIP",
                                            "snapshot": "Snap 4"})
        orphan = StadiumSong.from_dict({"name": "x", "ppqn": 240, "params": "",
                                        "flags": [], "tracks": []})
        orphan_model = EditorModel(orphan, Path("orphan.json"), DECODER)
        with self.assertRaisesRegex(ValueError, "No proven Stadium preset context"):
            create_stadium_snapshot(MusicalPosition(1, 1, 1), 1,
                                     orphan_model.timeline.events)

    def test_cycle_templates_pair_conservatively_and_round_trip(self):
        model = self.model()
        start = create_cycle_start(MusicalPosition(100, 1, 1))
        with self.assertRaisesRegex(ValueError, "requires an unmatched Cycle Start"):
            create_cycle_end(MusicalPosition(99, 1, 1), model.timeline.events)
        model.insert_event(start)
        end = create_cycle_end(MusicalPosition(104, 1, 1), model.timeline.events)
        model.insert_event(end)
        self.assertEqual(start.source.payload, "CYCLE_START;;2;Infinite;Off")
        self.assertEqual(end.source.payload, "CYCLE_END;;0")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cycle.json"; model.save_as(path)
            reopened = EditorModel.open(path)
            types = [(event.source.type, event.position) for event in reopened.timeline.events]
            self.assertIn(("CYCLE_START", MusicalPosition(100, 1, 1)), types)
            self.assertIn(("CYCLE_END", MusicalPosition(104, 1, 1)), types)

    def test_badges_are_semantic_and_capabilities_are_explicit(self):
        model = self.model()
        bass = create_second_helix_snapshot(MusicalPosition(57, 1, 1), 3, DECODER)
        stadium = create_stadium_snapshot(MusicalPosition(57, 1, 1), 4,
                                           model.timeline.events)
        marker = create_structure_marker(MusicalPosition(13, 1, 1), "VERSE 1")
        video = create_video_command(MusicalPosition(3, 1, 1), 6,
                                     "play_one_shot", DECODER)
        self.assertEqual([badge_text(item) for item in (bass, stadium, marker, video)],
                         ["BASS SNAP 3", "SNAP 4", "VERSE 1", "VIDEO 6 PLAY ONE SHOT"])
        for item in (bass, stadium, marker, video):
            self.assertNotIn(item.position.render(), badge_text(item))
        self.assertEqual(FLAG_CAPABILITIES["PRESETSNAP"],
                         {"parseable": True, "creatable": True, "editable": False})

    def test_second_helix_snapshots_use_rig_mapping(self):
        for snapshot, value in ((1, 0), (3, 2), (8, 7)):
            event = create_second_helix_snapshot(MusicalPosition(57, 1, 1), snapshot, DECODER)
            self.assertEqual((event.data["channel"], event.data["cc"], event.data["value"]),
                             (3, 69, value))
            self.assertEqual(event.data["rig_alias"]["snapshot"], snapshot)

    def test_second_helix_proven_looper_actions(self):
        expected = {"Play": (61, 127), "Stop": (61, 0), "Play Once": (62, 127)}
        for action, (cc, value) in expected.items():
            event = create_second_helix_looper(MusicalPosition(2, 1, 1), action, DECODER)
            self.assertEqual((event.data["channel"], event.data["cc"], event.data["value"]),
                             (3, cc, value))

    def test_second_helix_expression_endpoints_use_configured_capabilities(self):
        self.assertEqual(DECODER.second_helix_expressions(), ((1, 1), (2, 2), (3, 3)))
        position = MusicalPosition(57, 1, 1)
        for expression, cc in DECODER.second_helix_expressions():
            for value, label in ((0, f"EXP{expression} 0%"),
                                 (127, f"EXP{expression} 100%")):
                with self.subTest(expression=expression, value=value):
                    event = create_second_helix_expression(position, expression, value, DECODER)
                    self.assertEqual((event.data["channel"], event.data["cc"], event.data["value"]),
                                     (DECODER.second_helix_channel, cc, value))
                    self.assertEqual(event.data["rig_alias"], {
                        "system": "second_helix", "action": "expression",
                        "expression": expression, "value": value,
                    })
                    self.assertEqual(badge_text(event), label)
                    self.assertNotIn(position.render(), badge_text(event))

    def test_second_helix_expression_validation_round_trip_losslessness_and_undo(self):
        position = MusicalPosition(57, 1, 1)
        for expression in (0, 4, True, "1"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                create_second_helix_expression(position, expression, 0, DECODER)
        for value in (-1, 1, 126, 128, True, "127"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                create_second_helix_expression(position, 1, value, DECODER)

        model = self.model()
        original = model.song.to_dict()
        event = create_second_helix_expression(position, 3, 127, DECODER)
        model.insert_event(event)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "expression.json"
            model.save_as(path)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["flags"][:-1], original["flags"])
            self.assertEqual({key: value for key, value in saved.items() if key != "flags"},
                             {key: value for key, value in original.items() if key != "flags"})
            reopened = EditorModel.open(path)
            self.assertEqual(badge_text(reopened.timeline.events[-1]), "EXP3 100%")
            self.assertEqual(reopened.timeline.events[-1].source.payload,
                             "MIDI_CC;EXP3 100%;4;CC;3;3;127")
        self.assertTrue(model.undo())
        self.assertEqual([flag.render() for flag in model.song.flags], original["flags"])
        self.assertFalse(model.modified)

    def test_video_commands_and_validation(self):
        expected = {"stop": 0, "preload": 10, "play_one_shot": 11, "play_loop": 12}
        for action, value in expected.items():
            event = create_video_command(MusicalPosition(3, 6, 1), 6, action, DECODER)
            self.assertEqual((event.data["channel"], event.data["cc"], event.data["value"]),
                             (16, 6, value))
        rescan = create_video_command(MusicalPosition(1, 1, 1), None,
                                      "rescan_playlist", DECODER)
        self.assertEqual((rescan.data["channel"], rescan.data["cc"], rescan.data["value"]),
                         (16, 0, 127))
        for number in (-1, 128, 999):
            with self.subTest(number=number), self.assertRaises(ValueError):
                create_video_command(MusicalPosition(1, 1, 1), number, "stop", DECODER)

    def test_insert_save_reopen_is_lossless_ordered_and_undoable(self):
        model = self.model()
        original = model.song.to_dict()
        original_flags = list(original["flags"])
        original_selection = {2, 4}
        model.selected = set(original_selection)
        event = create_second_helix_snapshot(MusicalPosition(57, 1, 1), 3, DECODER)
        index = model.insert_event(event)
        self.assertEqual(len(model.timeline.events), len(original_flags) + 1)
        self.assertEqual(model.selected, {index})
        self.assertTrue(model.modified)
        self.assertEqual(event.source_index, len(original_flags))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "created.json"
            model.save_as(path)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["flags"][:-1], original_flags)
            self.assertEqual({key: value for key, value in saved.items() if key != "flags"},
                             {key: value for key, value in original.items() if key != "flags"})
            reopened = EditorModel.open(path)
            new = reopened.timeline.events[-1]
            self.assertEqual(new.position, MusicalPosition(57, 1, 1))
            self.assertEqual(new.data["rig_alias"]["snapshot"], 3)
            second = Path(directory) / "second.json"
            reopened.save_as(second)
            self.assertEqual(json.loads(second.read_text())["flags"], saved["flags"])
        self.assertTrue(model.undo())
        self.assertEqual(len(model.timeline.events), len(original_flags))
        self.assertEqual(model.selected, original_selection)
        self.assertFalse(model.modified)
        self.assertEqual([flag.render() for flag in model.song.flags], original_flags)


if __name__ == "__main__":
    unittest.main()
