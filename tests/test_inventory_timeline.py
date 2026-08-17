import json
from dataclasses import replace
from pathlib import Path
import unittest

from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import MusicalPosition, StadiumSong
from stadium_reaper_bridge.timeline import TimelineEventKind, stadium_to_timeline, timeline_source_flags

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("*.json"))

class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.decoder = RigMidiDecoder.from_file("config/rig_midi.json")
        cls.inventory = json.loads(Path("config/stadium_flag_inventory.json").read_text())

    def test_every_fixture_round_trips_and_maps_one_to_one_in_order(self):
        for path in FIXTURES:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                song = StadiumSong.from_json_text(source)
                timeline = stadium_to_timeline(song, midi_decoder=self.decoder)
                self.assertEqual(song.to_json_text(), source)
                self.assertEqual(len(timeline.events), len(song.flags))
                self.assertEqual([e.source for e in timeline.events], song.flags)
                self.assertEqual([e.source_index for e in timeline.events], list(range(len(song.flags))))
                self.assertEqual([f.render() for f in timeline_source_flags(timeline)], json.loads(source)["flags"])

    def test_inventory_covers_every_observed_type_and_fixture(self):
        expected = {"START", "END", "TIME", "MARKER", "PRESETSNAP", "MIDI_CC",
                    "MIDI_BANK_PROGRAM", "LOOPER", "CYCLE_START", "CYCLE_END"}
        self.assertEqual(set(self.inventory["types"]), expected)
        self.assertEqual(set(self.inventory["fixtures"]), {p.name for p in FIXTURES})

    def test_semantics_and_moving_event_preserves_payload(self):
        song = StadiumSong.from_json_text(FIXTURES[0].read_text())
        timeline = stadium_to_timeline(song)
        start = next(e for e in timeline.events if e.source.type == "START")
        self.assertIn("tempo", start.data); self.assertIn("time_signature_numerator", start.data)
        midi = next(e for e in timeline.events if e.source.type == "MIDI_CC")
        payload = midi.source.payload
        midi.position = MusicalPosition(2, 1, 1)
        moved = timeline_source_flags(timeline)[midi.source_index]
        self.assertEqual(moved.payload, payload)
        self.assertTrue(moved.render().endswith("|" + payload))

    def test_generic_parser_has_no_rig_inference(self):
        flag = StadiumSong.from_json_text((Path('tests/fixtures/clocksick_453.json')).read_text()).flags
        cc60 = next(f for f in flag if f.type == "MIDI_CC" and f.semantic_data().get("cc") == 60)
        self.assertNotIn("action", cc60.semantic_data())

    def test_native_looper_and_second_helix_looper_are_distinct(self):
        clocksick = StadiumSong.from_json_text(Path('tests/fixtures/clocksick_453.json').read_text())
        timeline = stadium_to_timeline(clocksick, midi_decoder=self.decoder)
        self.assertFalse(any(e.source.type == "LOOPER" for e in timeline.events))
        actions = [e.data.get("rig_alias", {}).get("action") for e in timeline.events]
        self.assertIn("Record", actions); self.assertIn("Overdub", actions)
        wanna = StadiumSong.from_json_text(Path('tests/fixtures/wanna_be_429.json').read_text())
        native = [f.semantic_data()["action"] for f in wanna.flags if f.type == "LOOPER"]
        self.assertIn("Record", native); self.assertIn("Play", native)

    def test_video_and_snapshot_decode_from_real_fixtures(self):
        events = []
        for path in FIXTURES:
            events += stadium_to_timeline(StadiumSong.from_json_text(path.read_text()), midi_decoder=self.decoder).events
        aliases = [e.data["rig_alias"] for e in events if "rig_alias" in e.data]
        self.assertTrue(any(a == {"system":"video", "action":"play_one_shot", "video":4} for a in aliases))
        snapshots = {a["snapshot"] for a in aliases if a.get("action") == "snapshot"}
        self.assertTrue({1, 2, 3, 5, 6, 7, 8}.issubset(snapshots))

    def test_second_helix_cc64_low_is_noop_and_high_is_tap_tempo(self):
        song = StadiumSong.from_dict({
            "name": "CC64 regression",
            "ppqn": 240,
            "params": "",
            "flags": [
                "001-01.001|MIDI_CC;Tap low;4;CC;3;64;0",
                "001-02.001|MIDI_CC;Tap high;4;CC;3;64;127",
            ],
            "tracks": [],
        })

        low, high = stadium_to_timeline(song, midi_decoder=self.decoder).events

        self.assertNotIn("rig_alias", low.data)
        self.assertEqual(
            high.data["rig_alias"],
            {"system": "second_helix", "action": "Tap Tempo"},
        )

    def test_perfect_picture_suspicious_sequence_is_diagnostic_not_invalid(self):
        report = self.inventory["reports"]["perfect_picture_336.json"]
        commands = [x for x in report["external_helix_midi"] if x["cc"] in (60, 61)]
        self.assertEqual([(x["cc"], x["value"]) for x in commands], [(61,66),(61,127),(61,0)])
        self.assertEqual([self.decoder.decode({"channel":3, **x})["action"] for x in commands], ["Play","Play","Stop"])

if __name__ == '__main__': unittest.main()
