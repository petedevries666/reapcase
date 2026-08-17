import json
from pathlib import Path
import tempfile
import unittest

from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import StadiumSong


FIXTURES = Path(__file__).parent / "fixtures"
DECODER = RigMidiDecoder.from_file("config/rig_midi.json")


class EditorModelTests(unittest.TestCase):
    def load(self, name):
        path = FIXTURES / name
        return EditorModel(StadiumSong.from_json_text(path.read_text()), path, DECODER)

    def test_real_song_lane_inventory(self):
        monzter = self.load("monzter_332.json")
        self.assertEqual(monzter.lane_counts(), {"STRUCTURE": 4, "STADIUM": 4,
                         "SECOND HELIX": 12, "VIDEO": 1, "MIDI / OTHER": 0})
        clocksick = self.load("clocksick_453.json")
        self.assertEqual(clocksick.lane_counts()["STRUCTURE"], 10)
        self.assertGreater(clocksick.lane_counts()["SECOND HELIX"], 0)
        self.assertEqual(sum(e.source.type.startswith("CYCLE") and clocksick.lane(e) == "STRUCTURE"
                             for e in clocksick.timeline.events), 2)

    def test_native_looper_time_and_video_lanes(self):
        wanna = self.load("wanna_be_429.json")
        self.assertTrue(all(wanna.lane(e) == "STADIUM" for e in wanna.timeline.events if e.source.type == "LOOPER"))
        self.assertTrue(any(wanna.lane(e) == "SECOND HELIX" for e in wanna.timeline.events if e.source.type.startswith("MIDI")))
        perfect = self.load("perfect_picture_336.json")
        self.assertEqual(sum(e.source.type == "TIME" and perfect.lane(e) == "STRUCTURE" for e in perfect.timeline.events), 2)
        all_models = [self.load(p.name) for p in FIXTURES.glob("*.json")]
        self.assertTrue(any(m.lane(e) == "VIDEO" for m in all_models for e in m.timeline.events))

    def test_bulk_shift_validation_payload_and_order(self):
        model = self.load("monzter_332.json")
        model.select_all(); before = [(e.position.bar, e.source.payload, e.source_index) for e in model.timeline.events]
        self.assertEqual(model.shift_selected(bars=4), len(before))
        self.assertEqual([(e.position.bar, e.source.payload, e.source_index) for e in model.timeline.events],
                         [(bar + 4, payload, index) for bar, payload, index in before])
        self.assertTrue(model.undo())
        first = min(range(len(model.timeline.events)), key=lambda i: model._units(model.timeline.events[i].position))
        model.selected = {first}
        with self.assertRaises(ValueError): model.shift_selected(bars=-1)
        self.assertEqual([e.source_index for e in model.timeline.events], list(range(len(model.timeline.events))))

    def test_save_as_preserves_document_and_noop_exact(self):
        source_path = FIXTURES / "monzter_332.json"; source = source_path.read_text()
        model = self.load(source_path.name)
        with tempfile.TemporaryDirectory() as directory:
            noop = Path(directory) / "noop.json"; model.save_as(noop)
            self.assertEqual(noop.read_text(), source)
            model.selected = {1}; payload = model.timeline.events[1].source.payload
            model.shift_selected(bars=4); moved = Path(directory) / "moved.json"; model.save_as(moved)
            original, result = json.loads(source), json.loads(moved.read_text())
            self.assertEqual(result["tracks"], original["tracks"])
            self.assertEqual({k:v for k,v in result.items() if k != "flags"},
                             {k:v for k,v in original.items() if k != "flags"})
            self.assertEqual(result["flags"][1].split("|",1)[1], payload)

    def test_unknown_visible_and_round_trippable(self):
        source = '{"name":"Future","ppqn":240,"params":null,"mystery":{"x":1},"flags":["001-01.001|UTILITY_FUTURE;a;;b"],"tracks":[{"unknown":true}]}\n'
        song = StadiumSong.from_json_text(source); model = EditorModel(song, Path("future.json"), DECODER)
        self.assertEqual(model.unsupported_types, ("UTILITY_FUTURE",))
        self.assertEqual(model.lane(model.timeline.events[0]), "MIDI / OTHER")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"future.json"; model.save_as(path)
            self.assertEqual(path.read_text(), source)


if __name__ == "__main__": unittest.main()
