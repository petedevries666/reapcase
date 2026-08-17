import json
from dataclasses import replace
from pathlib import Path
import unittest

from stadium_reaper_bridge.stadium import MusicalPosition, StadiumFlag, StadiumSong


MONZTER_FIXTURE = Path(__file__).parent / "fixtures" / "monzter_332.json"


class MonzterFixtureTests(unittest.TestCase):
    """Validation against the unmodified real-world MONZTER Song export."""

    @classmethod
    def setUpClass(cls):
        cls.source = MONZTER_FIXTURE.read_text(encoding="utf-8")
        cls.document = json.loads(cls.source)

    def setUp(self):
        self.song = StadiumSong.from_json_text(self.source)

    def test_song_loads_with_real_ppqn_and_preserves_every_flag(self):
        self.assertEqual(self.song.name, "MONZTER")
        self.assertEqual(self.song.ppqn, 240)
        self.assertEqual(
            [flag.render() for flag in self.song.flags],
            self.document["flags"],
        )

    def test_no_op_round_trip_is_the_exact_fixture_text(self):
        self.assertEqual(self.song.to_json_text(), self.source)

    def test_all_observed_flag_types_are_identified(self):
        self.assertEqual(
            {flag.type for flag in self.song.flags},
            {"START", "MIDI_BANK_PROGRAM", "MIDI_CC", "MARKER", "PRESETSNAP", "END"},
        )

    def test_all_real_musical_positions_parse_at_song_ppqn(self):
        expected_positions = [value.partition("|")[0] for value in self.document["flags"]]

        self.assertEqual(
            [flag.position.render() for flag in self.song.flags],
            expected_positions,
        )
        positions = [flag.position for flag in self.song.flags]
        self.assertIn(MusicalPosition.parse("026-06.200", ppqn=240), positions)
        self.assertIn(MusicalPosition.parse("081-01.002", ppqn=240), positions)

    def test_moving_one_flag_changes_only_its_position(self):
        index = self.document["flags"].index(
            "026-06.200|MIDI_CC;CHORUS;4;CC;3;69;1"
        )
        original_flag = self.song.flags[index]
        original_prefix, separator, original_suffix = original_flag.render().partition("|")
        self.song.flags[index] = replace(
            original_flag,
            position=MusicalPosition(27, 1, 1),
        )

        exported_flags = self.song.to_dict()["flags"]
        moved_prefix, moved_separator, moved_suffix = exported_flags[index].partition("|")
        self.assertEqual((separator, moved_separator), ("|", "|"))
        self.assertEqual(original_prefix, "026-06.200")
        self.assertEqual(moved_prefix, "027-01.001")
        self.assertEqual(moved_suffix, original_suffix)
        self.assertEqual(
            exported_flags[:index] + exported_flags[index + 1:],
            self.document["flags"][:index] + self.document["flags"][index + 1:],
        )

    def test_moving_a_flag_keeps_tracks_and_unknown_song_fields(self):
        self.song.flags[0] = replace(
            self.song.flags[0],
            position=MusicalPosition(1, 2, 1),
        )

        exported = self.song.to_dict()
        self.assertEqual(exported["tracks"], self.document["tracks"])
        self.assertEqual(exported["bypass-flags"], self.document["bypass-flags"])
        self.assertEqual(set(exported), set(self.document))


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
