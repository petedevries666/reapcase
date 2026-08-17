import json
from pathlib import Path
import unittest

from stadium_reaper_bridge.midi import RigMidiDecoder


class RigMidiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(Path("config/rig_midi.json").read_text())
        cls.decoder = RigMidiDecoder(cls.config)

    def decode(self, cc, value, channel=1):
        return self.decoder.decode({"channel": channel, "cc": cc, "value": value})

    def test_configuration_keeps_three_independent_systems(self):
        self.assertEqual(set(self.config), {"second_helix", "video", "stadium_transport"})
        self.assertEqual(self.config["second_helix"]["channel"], 3)
        self.assertEqual(self.config["second_helix"]["snapshot"]["cc"], 69)
        self.assertEqual(self.config["video"]["channel"], 16)
        self.assertEqual(self.config["video"]["values"]["11"], "play_one_shot")
        self.assertEqual(self.config["stadium_transport"]["channel"], 1)

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
            (48, 64, "cycle_toggle"), (48, 127, "cycle_toggle"),
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
        self.assertEqual(self.decode(32, 3), {"system": "stadium_transport",
                                              "action": "select_setlist", "setlist": 3})
        self.assertEqual(self.decode(46, 0), {"system": "stadium_transport",
                                              "action": "select_marker", "marker": 0,
                                              "marker_name": "song_start"})
        self.assertEqual(self.decode(46, 5), {"system": "stadium_transport",
                                              "action": "select_marker", "marker": 5})

    def test_stadium_round_trips_on_configured_channel(self):
        commands = [
            {"system": "stadium_transport", "action": "return_to_zero"},
            {"system": "stadium_transport", "action": "cycle_clear"},
            {"system": "stadium_transport", "action": "next_song"},
            {"system": "stadium_transport", "action": "next_marker"},
            {"system": "stadium_transport", "action": "play_pause"},
            {"system": "stadium_transport", "action": "select_playlist", "playlist": 12},
            {"system": "stadium_transport", "action": "select_song", "song": 4},
            {"system": "stadium_transport", "action": "select_setlist", "setlist": 3},
            {"system": "stadium_transport", "action": "select_marker", "marker": 5},
        ]
        for command in commands:
            with self.subTest(command=command):
                midi = self.decoder.encode_rig_command(command)
                self.assertEqual(midi["channel"], 1)
                self.assertEqual(self.decoder.decode(midi), command)

    def test_stadium_only_decodes_on_configured_global_channel(self):
        expected = {"system": "stadium_transport", "action": "return_to_zero"}
        self.assertEqual(self.decode(47, 0, channel=1), expected)
        for channel in range(2, 17):
            with self.subTest(channel=channel):
                decoded = self.decode(47, 0, channel=channel)
                self.assertFalse(decoded and decoded["system"] == "stadium_transport")

    def test_stadium_and_second_helix_loopers_remain_distinct(self):
        self.assertEqual(self.decode(58, 127, channel=1),
                         {"system": "stadium_transport", "action": "record"})
        self.assertEqual(self.decode(60, 127, channel=3),
                         {"system": "second_helix", "action": "Record"})
        self.assertEqual(self.decode(60, 127, channel=1),
                         {"system": "stadium_transport", "action": "play_once"})
        self.assertEqual(self.decode(61, 0, channel=3),
                         {"system": "second_helix", "action": "Stop"})
        self.assertEqual(self.decode(61, 127, channel=3),
                         {"system": "second_helix", "action": "Play"})
        self.assertIsNone(self.decode(58, 127, channel=3))
        self.assertIsNone(self.decode(61, 127, channel=1))

    def test_all_stadium_looper_controls_round_trip(self):
        cases = [
            (52, 0, "clear_loop"), (53, 0, "undo_redo"),
            (54, 0, "full_speed"), (54, 127, "half_speed"),
            (55, 0, "forward"), (55, 127, "reverse"),
            (58, 0, "overdub"), (58, 127, "record"),
            (59, 0, "stop"), (59, 127, "play"),
            (60, 0, "play_once"),
            (62, 0, "off"), (62, 127, "on"),
        ]
        for cc, value, action in cases:
            with self.subTest(cc=cc, action=action):
                command = {"system": "stadium_transport", "action": action}
                self.assertEqual(self.decode(cc, value), command)
                self.assertEqual(self.decoder.encode_rig_command(command),
                                 {"channel": 1, "cc": cc, "value": value})

    def test_stadium_tap_tempo_only_uses_high_range(self):
        self.assertIsNone(self.decode(64, 0, channel=1))
        self.assertIsNone(self.decode(64, 63, channel=1))
        self.assertEqual(self.decode(64, 64, channel=1),
                         {"system": "stadium_transport", "action": "tap_tempo"})
        self.assertEqual(self.decode(64, 127, channel=1),
                         {"system": "stadium_transport", "action": "tap_tempo"})

    def test_stadium_snapshot_controls(self):
        cases = [
            (0, {"action": "snapshot", "snapshot": 1}),
            (7, {"action": "snapshot", "snapshot": 8}),
            (8, {"action": "next_snapshot"}),
            (9, {"action": "previous_snapshot"}),
        ]
        for value, alias in cases:
            with self.subTest(value=value):
                expected = {"system": "stadium_transport", **alias}
                midi = {"channel": 1, "cc": 69, "value": value}
                self.assertEqual(self.decoder.decode(midi), expected)
                self.assertEqual(self.decoder.encode_rig_command(expected), midi)

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
