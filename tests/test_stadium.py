import json
import unittest

from stadium_reaper_bridge.stadium import MusicalPosition, StadiumFlag, StadiumSong


class StadiumModelTests(unittest.TestCase):
    def test_no_op_round_trip_is_byte_exact_and_keeps_unknown_data(self):
        source = '{\n "name":"Démo", "ppqn":240, "params":{"future":true},\n "flags":["026-06.200|ALIEN;x;;y"], "tracks":[], "unknown":{"x":1}\n}\n'
        song = StadiumSong.from_json_text(source)

        self.assertEqual(song.to_json_text(), source)
        self.assertEqual(song.flags[0].type, "ALIEN")
        self.assertEqual(song.to_dict()["unknown"], {"x": 1})

    def test_position_edit_preserves_opaque_payload(self):
        flag = StadiumFlag.parse("026-06.200|MIDI_CC;CHORUS;4;CC;3;69;1")
        moved = StadiumFlag(MusicalPosition(27, 1, 1), flag.payload, flag.original)

        self.assertEqual(moved.render(), "027-01.001|MIDI_CC;CHORUS;4;CC;3;69;1")

    def test_observed_positions_are_valid_at_240_ppqn(self):
        examples = ("001-01.001", "026-06.200", "125-03.085")

        self.assertEqual(
            [MusicalPosition.parse(value, ppqn=240).render() for value in examples],
            list(examples),
        )

    def test_ticks_are_one_based_and_bounded_by_song_ppqn(self):
        with self.assertRaisesRegex(ValueError, "one-based"):
            MusicalPosition.parse("001-01.000", ppqn=240)
        with self.assertRaisesRegex(ValueError, "exceeds Song PPQN"):
            MusicalPosition.parse("001-01.241", ppqn=240)

        document = {
            "name": "Invalid tick", "ppqn": 240, "params": {},
            "flags": ["001-01.241|MARKER;Too late"], "tracks": [],
        }
        with self.assertRaisesRegex(ValueError, "exceeds Song PPQN"):
            StadiumSong.from_dict(document)

    def test_song_edit_preserves_unknown_fields(self):
        song = StadiumSong.from_dict({
            "name": "Before", "ppqn": 960, "params": {}, "flags": [],
            "tracks": [], "vendorExtension": [1, 2],
        })
        song.name = "After"

        exported = song.to_dict()
        self.assertEqual(exported["name"], "After")
        self.assertEqual(exported["vendorExtension"], [1, 2])

    def test_malformed_positions_and_flags_are_rejected_explicitly(self):
        with self.assertRaises(ValueError):
            MusicalPosition.parse("26:6:200")
        with self.assertRaises(ValueError):
            StadiumFlag.parse("026-06.200")

    def test_alias_file_is_external_valid_json(self):
        with open("config/aliases.json", encoding="utf-8") as stream:
            aliases = json.load(stream)
        self.assertEqual(aliases["aliases"]["VB SNAP 3"]["value"], 2)


if __name__ == "__main__":
    unittest.main()
