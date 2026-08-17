from pathlib import Path
import json
import shutil
import tempfile
import unittest

from stadium_reaper_bridge.editor.model import EditorModel, LANES
from stadium_reaper_bridge.editor.sequence import (
    SequenceClickKind, derive_count_in, derive_seq_clicks,
)
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import MusicalPosition, StadiumSong
from stadium_reaper_bridge.timing import TimingMap


P = MusicalPosition
DECODER = RigMidiDecoder.from_file("config/rig_midi.json")


class SequenceDerivationTests(unittest.TestCase):
    def timing(self, *changes):
        return TimingMap(240, [(P(bar, 1, 1), 120, numerator, 4)
                               for bar, numerator in changes])

    def test_lane_order_places_sequence_immediately_before_audio(self):
        self.assertEqual(LANES[-2:], ("SEQCLICK", "SEQ INSTRUCTIONS"))

    def test_four_four_clicks_skip_bar_one_and_accent_each_bar(self):
        timing = self.timing((1, 4))
        clicks = derive_seq_clicks(timing, timing.bar_end_units(3) - 1)
        self.assertEqual(clicks[0].position, P(2, 1, 1))
        self.assertTrue(all(point.position.bar >= 2 for point in clicks))
        self.assertEqual([point.kind for point in clicks[:4]],
                         [SequenceClickKind.ACCENT] + [SequenceClickKind.TICKSECOND] * 3)
        self.assertEqual([point.kind for point in clicks[4:8]],
                         [SequenceClickKind.ACCENT] + [SequenceClickKind.TICKSECOND] * 3)
        self.assertEqual(len({point.identity for point in clicks}), len(clicks))

    def test_variable_signature_click_counts_and_kinds(self):
        timing = self.timing((1, 4), (5, 3), (9, 6))
        clicks = derive_seq_clicks(timing, timing.bar_end_units(10) - 1)
        by_bar = {bar: [point for point in clicks if point.position.bar == bar]
                  for bar in range(2, 11)}
        self.assertEqual([len(by_bar[bar]) for bar in range(2, 11)],
                         [4, 4, 4, 3, 3, 3, 3, 6, 6])
        for points in by_bar.values():
            self.assertEqual(points[0].kind, SequenceClickKind.ACCENT)
            self.assertTrue(all(point.kind is SequenceClickKind.TICKSECOND
                                for point in points[1:]))

    def test_click_rectangles_end_at_the_canonical_next_beat(self):
        timing = self.timing((1, 4), (3, 3), (5, 6))
        clicks = derive_seq_clicks(timing, timing.bar_end_units(5) - 1)
        for click in clicks:
            following = timing.shift_position(click.position, beats=1)
            self.assertEqual(click.end_units, timing.position_to_units(following))
        finals = [click for click in clicks if click.position.beat == timing.beats_in_bar(
            click.position.bar)]
        self.assertTrue(finals)
        for click in finals:
            self.assertEqual(timing.units_to_position(click.end_units),
                             P(click.position.bar + 1, 1, 1))

    def test_count_in_uses_signature_active_at_bar_three(self):
        four = derive_count_in(self.timing((1, 4)))
        self.assertEqual([clip.label for clip in four], ["ONE", "TWO", "THREE", "FOUR"])
        self.assertEqual([clip.position for clip in four], [P(3, beat, 1) for beat in range(1, 5)])
        six = derive_count_in(self.timing((1, 4), (3, 6)))
        self.assertEqual([clip.label for clip in six],
                         ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX"])

    def test_audio_duration_boundary_is_not_exceeded(self):
        timing = self.timing((1, 4))
        end = timing.position_to_units(P(3, 2, 1)) + 100
        clicks = derive_seq_clicks(timing, end)
        self.assertTrue(clicks)
        self.assertLessEqual(max(point.units for point in clicks), end)
        self.assertFalse(any(point.position.beat > 2 and point.position.bar == 3
                             for point in clicks))

    def test_deriving_and_showing_lanes_does_not_change_native_json(self):
        source_path = Path("tests/fixtures/monzter_332.json")
        source = source_path.read_text(encoding="utf-8")
        model = EditorModel(StadiumSong.from_json_text(source), source_path, DECODER)
        self.assertIsNotNone(model.sequence_layout)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "song.json"
            model.save_as(output)
            self.assertEqual(output.read_text(encoding="utf-8"), source)

    def test_sequence_edits_persist_losslessly_and_undo(self):
        source_path = Path("tests/fixtures/monzter_332.json")
        native = source_path.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            opened = directory / "opened.json"
            shutil.copy2(source_path, opened)
            model = EditorModel.open(opened)
            initial_count = model.timing_map.beats_in_bar(3)
            self.assertEqual(len(model.instructions), initial_count)
            click = next(item for item in model.sequence_layout.clicks
                         if item.position == P(2, 3, 1))
            two = next(item for item in model.instructions if item.label == "TWO")
            three = next(item for item in model.instructions if item.label == "THREE")
            two_position = two.position
            three_position = three.position
            model.toggle_click_mute(click.identity)
            model.toggle_instruction_mute(two.id)
            model.move_instructions((three.id,), model.song.ppqn)
            self.assertTrue(model.modified)
            self.assertEqual(three.position, P(3, 4, 1))
            self.assertTrue(model.undo())
            self.assertEqual(three.position, three_position)
            model.move_instructions((three.id,), model.song.ppqn)

            output = directory / "saved.json"
            model.save_as(output)
            self.assertEqual(output.read_text(encoding="utf-8"), native)
            reopened = EditorModel.open(output)
            self.assertEqual(reopened.click_mutes, {click.identity})
            self.assertTrue(next(item for item in reopened.instructions if item.id == two.id).muted)
            self.assertEqual(next(item for item in reopened.instructions if item.id == three.id).position,
                             P(3, 4, 1))
            self.assertEqual(next(item for item in reopened.instructions if item.id == two.id).position,
                             two_position)
            self.assertEqual(len(reopened.instructions), initial_count)

    def test_sequence_save_preserves_unknown_sidecar_namespaces(self):
        source_path = Path("tests/fixtures/monzter_332.json")
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            opened = directory / "opened.json"
            shutil.copy2(source_path, opened)
            original_sidecar = {"custom": {"future": [1, 2]},
                                "reapcase": {"version": 9, "lights": []}}
            EditorModel.show_path(opened).write_text(json.dumps(original_sidecar), encoding="utf-8")
            model = EditorModel.open(opened)
            model.toggle_click_mute(model.sequence_layout.clicks[0].identity)
            output = directory / "saved.json"
            model.save_as(output)
            saved = json.loads(EditorModel.show_path(output).read_text(encoding="utf-8"))
            self.assertEqual(saved["custom"], original_sidecar["custom"])
            self.assertEqual(saved["reapcase"]["version"], 9)
            self.assertEqual(saved["reapcase"]["lights"], [])
            self.assertIn("sequence", saved["reapcase"])


if __name__ == "__main__":
    unittest.main()
