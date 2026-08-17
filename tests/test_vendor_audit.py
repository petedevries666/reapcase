import hashlib
import json
from pathlib import Path
import unittest

from stadium_reaper_bridge.stadium import MusicalPosition, StadiumSong


FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("*.json"))


class VendorAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vendor = json.loads(Path("config/vendor_capabilities.json").read_text())
        cls.empirical = json.loads(Path("config/stadium_flag_inventory.json").read_text())
        cls.capabilities = {item["feature"]: item for item in cls.vendor["capabilities"]}

    def test_empirical_inventory_contains_every_type_in_real_fixtures(self):
        observed = {
            flag.partition("|")[2].partition(";")[0]
            for path in FIXTURES
            for flag in json.loads(path.read_text())["flags"]
        }
        self.assertEqual(observed, set(self.empirical["types"]))

    def test_every_official_flag_family_is_in_vendor_inventory(self):
        official = {
            "Start", "End", "Marker", "Cycle", "Preset/Snap", "Looper",
            "Utility", "Ext Amp", "MIDI Bank/Program", "MIDI CC", "MIDI MMC",
            "Hotkey", "Time",
        }
        self.assertTrue(official.issubset(self.capabilities))

    def test_vendor_only_families_are_not_claimed_as_fixture_observed(self):
        for name in ("Utility", "Ext Amp", "Hotkey", "MIDI MMC"):
            with self.subTest(feature=name):
                item = self.capabilities[name]
                self.assertEqual(item["evidence"], "vendor_documented")
                self.assertEqual(item["fixture_status"], "not_observed")
                self.assertEqual(item["observed_json_types"], [])
                self.assertEqual(item["current_reapcase_support"], "unsupported_semantic")

    def test_no_semantic_parser_was_added_for_unobserved_families(self):
        for payload in (
            "UTILITY_FUTURE;do;not;interpret",
            "EXT_AMP_FUTURE;do;not;interpret",
            "HOTKEY_FUTURE;do;not;interpret",
            "MIDI_MMC_FUTURE;do;not;interpret",
        ):
            with self.subTest(payload=payload):
                song = StadiumSong.from_dict({
                    "name": "future", "ppqn": 240, "params": {},
                    "flags": [f"002-03.181|{payload}"], "tracks": [],
                })
                self.assertEqual(song.flags[0].semantic_data(), {})
                self.assertEqual(song.to_dict()["flags"], [f"002-03.181|{payload}"])

    def test_generic_parser_round_trips_unknown_payload_exactly(self):
        source = '{"name":"future","ppqn":240,"params":null,"flags":["007-02.061|VENDOR_FUTURE;;x;0;semi;"],"tracks":[]}\n'
        song = StadiumSong.from_json_text(source)
        self.assertEqual(song.flags[0].payload, "VENDOR_FUTURE;;x;0;semi;")
        self.assertEqual(song.to_json_text(), source)

    def test_vendor_timing_matches_current_one_based_240_ppqn_contract(self):
        timing = self.vendor["timing_contract"]
        self.assertEqual(timing["ppqn"], 240)
        self.assertEqual(timing["exact_beat_tick"], 1)
        self.assertEqual(timing["quarter_beat_ticks"], [1, 61, 121, 181])
        self.assertEqual(timing["indexing"], "one_based")
        for tick in timing["quarter_beat_ticks"]:
            self.assertEqual(MusicalPosition.parse(f"001-01.{tick:03d}", ppqn=240).tick, tick)
        with self.assertRaises(ValueError):
            MusicalPosition.parse("001-01.000", ppqn=240)
        with self.assertRaises(ValueError):
            MusicalPosition.parse("001-01.241", ppqn=240)

    def test_rig_midi_mapping_is_unchanged_by_documentation_audit(self):
        digest = hashlib.sha256(Path("config/rig_midi.json").read_bytes()).hexdigest()
        self.assertEqual(digest, "a8a1f5d345216988e6e81527cba75dc1e6f0d768176deeaeafe169f860b6719f")


if __name__ == "__main__":
    unittest.main()
