import json
from pathlib import Path
import unittest

from stadium_reaper_bridge.stadium import MusicalPosition, StadiumSong
from stadium_reaper_bridge.timing import TimingMap


P = MusicalPosition


class TimingMapTests(unittest.TestCase):
    def timing(self, *changes):
        return TimingMap(240, [(P(bar, 1, 1), tempo, numerator, 4)
                               for bar, tempo, numerator in changes])

    def test_sequential_bar_starts_after_signature_change(self):
        timing = self.timing((1, 120, 4), (9, 120, 3))
        self.assertEqual([timing.bar_start_units(bar) for bar in (1, 8, 9, 10)],
                         [0, 7 * 4 * 240, 8 * 4 * 240, (8 * 4 + 3) * 240])
        for position in (P(8, 4, 240), P(9, 1, 1), P(10, 3, 121)):
            self.assertEqual(timing.units_to_position(timing.position_to_units(position)), position)
        with self.assertRaisesRegex(ValueError, "invalid"):
            timing.position_to_units(P(10, 4, 1))

    def test_multiple_signatures_and_grid_iteration(self):
        timing = self.timing((1, 120, 4), (5, 120, 3), (9, 120, 5), (13, 120, 7))
        expected = {1: 0, 5: 16 * 240, 9: 28 * 240,
                    13: 48 * 240, 14: 55 * 240}
        self.assertEqual({bar: timing.bar_start_units(bar) for bar in expected}, expected)
        self.assertEqual([timing.beats_in_bar(bar) for bar in (4, 5, 9, 13)], [4, 3, 5, 7])
        beats = list(timing.iter_beats(timing.bar_start_units(8), timing.bar_end_units(9) - 1))
        self.assertEqual([(point.position.bar, point.position.beat) for point in beats],
                         [(8, 1), (8, 2), (8, 3),
                          (9, 1), (9, 2), (9, 3), (9, 4), (9, 5)])

    def test_tempo_and_signature_are_independent_and_round_trip_seconds(self):
        timing = self.timing((1, 120, 4), (5, 90, 4), (9, 90, 3), (13, 140, 5))
        self.assertEqual(timing.bar_start_units(6), 20 * 240)
        self.assertEqual(timing.signature_at_position(P(6, 1, 1)), (4, 4))
        self.assertEqual(timing.tempo_at_position(P(6, 1, 1)), 90)
        self.assertEqual(timing.signature_at_position(P(10, 1, 1)), (3, 4))
        self.assertEqual(timing.tempo_at_position(P(10, 1, 1)), 90)
        for units in (0, 959, 960, timing.bar_start_units(9), timing.bar_start_units(13), 20000):
            self.assertEqual(timing.seconds_to_units(timing.units_to_seconds(units)), units)

    def test_bar_shift_rejects_destination_with_fewer_beats(self):
        timing = self.timing((1, 120, 4), (2, 120, 3))
        with self.assertRaisesRegex(ValueError, "fewer beats"):
            timing.shift_position(P(1, 4, 1), bars=1)
        self.assertEqual(timing.shift_position(P(1, 3, 61), bars=1), P(2, 3, 61))

    def test_real_perfect_picture_time_events_and_lossless_source(self):
        path = Path("tests/fixtures/perfect_picture_336.json")
        source = path.read_text(encoding="utf-8")
        song = StadiumSong.from_json_text(source)
        timing = TimingMap.from_song(song)
        self.assertEqual([(segment.start_position.render(), segment.tempo,
                           segment.numerator, segment.denominator)
                          for segment in timing.segments],
                         [("001-01.001", 102.0, 4, 4),
                          ("005-02.052", 123.0, 4, 4),
                          ("010-01.001", 123.0, 4, 4)])
        self.assertEqual(song.to_json_text(), source)


if __name__ == "__main__":
    unittest.main()
