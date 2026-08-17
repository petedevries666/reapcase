import json
from pathlib import Path
import unittest

from stadium_reaper_bridge.midi import RigMidiDecoder


class RigMidiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(Path("config/rig_midi.json").read_text())
        cls.decoder = RigMidiDecoder(cls.config)

    def decode(self, cc, value, channel=7):
        return self.decoder.decode({"channel": channel, "cc": cc, "value": value})

    def test_configuration_keeps_three_independent_systems(self):
        self.assertEqual(set(self.config), {"second_helix", "video", "stadium_transport"})
        self.assertEqual(self.config["second_helix"]["channel"], 3)
        self.assertEqual(self.config["second_helix"]["snapshot"]["cc"], 69)
        self.assertEqual(self.config["video"]["channel"], 16)
        self.assertEqual(self.config["video"]["values"]["11"], "play_one_shot")
        self.assertIsNone(self.config["stadium_transport"]["channel"])

    def test_any_value_triggers_cc47_and_cc51(self):
        for value in range(128):
            with self.subTest(cc=47, value=value):
                self.assertEqual(self.decode(47, value),
                                 {"system": "stadium_transport", "action": "return_to_zero"})
            with self.subTest(cc=51, value=value):
                self.assertEqual(self.decode(51, value),
                                 {"system": "stadium_transport", "action": "play_pause"})

    def test_range_commands(self):
        cases = [
            (48, 0, "cycle_clear"), (48, 63, "cycle_clear"),
            (48, 64, "cycle_start_end_continue"), (48, 127, "cycle_start_end_continue"),
            (49, 0, "previous_song"), (49, 63, "previous_song"),
            (49, 64, "next_song"), (49, 127, "next_song"),
            (50, 0, "previous_marker"), (50, 63, "previous_marker"),
            (50, 64, "next_marker"), (50, 127, "next_marker"),
        ]
        for cc, value, action in cases:
            with self.subTest(cc=cc, value=value):
                self.assertEqual(self.decode(cc, value),
                                 {"system": "stadium_transport", "action": action})

    def test_direct_selectors(self):
        self.assertEqual(self.decode(63, 12), {"system": "stadium_transport",
                                               "action": "select_playlist", "playlist": 12})
        self.assertEqual(self.decode(10, 4), {"system": "stadium_transport",
                                              "action": "select_song", "song": 4})
        self.assertEqual(self.decode(46, 0), {"system": "stadium_transport",
                                              "action": "select_marker", "marker": 0,
                                              "marker_name": "song_start"})
        self.assertEqual(self.decode(46, 5), {"system": "stadium_transport",
                                              "action": "select_marker", "marker": 5})

    def test_stadium_round_trips_with_wildcard_or_explicit_channel(self):
        commands = [
            {"system": "stadium_transport", "action": "return_to_zero"},
            {"system": "stadium_transport", "action": "cycle_clear"},
            {"system": "stadium_transport", "action": "next_song"},
            {"system": "stadium_transport", "action": "next_marker"},
            {"system": "stadium_transport", "action": "play_pause"},
            {"system": "stadium_transport", "action": "select_playlist", "playlist": 12},
            {"system": "stadium_transport", "action": "select_song", "song": 4},
            {"system": "stadium_transport", "action": "select_marker", "marker": 5},
        ]
        for command in commands:
            with self.subTest(command=command):
                midi = self.decoder.encode_rig_command(command)
                self.assertNotIn("channel", midi)
                self.assertEqual(self.decoder.decode({"channel": 9, **midi}), command)
                self.assertEqual(self.decoder.encode_rig_command({**command, "channel": 9})["channel"], 9)

    def test_existing_system_round_trips(self):
        commands = [
            ({"system": "second_helix", "action": "snapshot", "snapshot": 3},
             {"channel": 3, "cc": 69, "value": 2}),
            ({"system": "video", "action": "play_one_shot", "video": 6},
             {"channel": 16, "cc": 6, "value": 11}),
            ({"system": "video", "action": "play_loop", "video": 8},
             {"channel": 16, "cc": 8, "value": 12}),
        ]
        for command, expected in commands:
            with self.subTest(command=command):
                midi = self.decoder.encode_rig_command(command)
                self.assertEqual(midi, expected)
                self.assertEqual(self.decoder.decode(midi), command)

    def test_cc64_regression(self):
        self.assertIsNone(self.decode(64, 0, channel=3))
        self.assertEqual(self.decode(64, 127, channel=3),
                         {"system": "second_helix", "action": "Tap Tempo"})

    def test_invalid_midi_and_commands_fail_explicitly(self):
        for midi in [
            {"channel": 0, "cc": 47, "value": 0},
            {"channel": 17, "cc": 47, "value": 0},
            {"channel": 1, "cc": -1, "value": 0},
            {"channel": 1, "cc": 128, "value": 0},
            {"channel": 1, "cc": 47, "value": -1},
            {"channel": 1, "cc": 47, "value": 128},
            {"channel": 1, "cc": 47, "value": "0"},
        ]:
            with self.subTest(midi=midi), self.assertRaises(ValueError):
                self.decoder.decode(midi)
        for command in [
            {"system": "stadium_transport", "action": "select_song", "song": 128},
            {"system": "stadium_transport", "action": "select_marker"},
            {"system": "video", "action": "play_loop"},
            {"system": "second_helix", "action": "Tuner"},
            {"system": "unknown", "action": "go"},
        ]:
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.decoder.encode_rig_command(command)


if __name__ == "__main__":
    unittest.main()
