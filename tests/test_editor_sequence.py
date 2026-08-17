from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
