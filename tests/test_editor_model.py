import json
from pathlib import Path
import tempfile
import unittest

from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.layout import (MAX_PIXELS_PER_BEAT, MIN_PIXELS_PER_BEAT,
                                                  drag_units, fit_song_scale,
                                                  snap_drag_delta, zoom_about_cursor)
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

    def test_drag_pixel_conversion_and_all_snap_modes(self):
        self.assertEqual(drag_units(45, 90, 240), 120)
        anchor = 250
        self.assertEqual(snap_drag_delta(anchor, 300, "1 bar", 240, 4), 710)
        self.assertEqual(snap_drag_delta(anchor, 100, "1 beat", 240, 4), -10)
        self.assertEqual(snap_drag_delta(anchor, 31, "quarter beat", 240, 4), 50)
        self.assertEqual(snap_drag_delta(anchor, 31, "no snap", 240, 4), 31)

    def test_preview_is_non_mutating_and_commit_is_once(self):
        model = self.load("monzter_332.json")
        model.selected = {1, 2}
        before = [event.position for event in model.timeline.events]
        preview = model.preview_shift(60)
        self.assertEqual([event.position for event in model.timeline.events], before)
        self.assertEqual(model.commit_preview(preview), 2)
        after = [event.position for event in model.timeline.events]
        self.assertNotEqual(after, before)
        with self.assertRaises(ValueError):
            model.commit_preview(preview)
        self.assertEqual([event.position for event in model.timeline.events], after)

    def test_preview_preserves_offsets_and_rejects_before_start(self):
        model = self.load("monzter_332.json")
        model.selected = {0, 1, 2}
        preview = model.preview_shift(123)
        original_units = [model._units(position) for position in preview.original]
        target_units = [model._units(position) for position in preview.targets]
        self.assertEqual([b - a for a, b in zip(original_units, target_units)], [123] * 3)
        earliest = min(model._units(model.timeline.events[i].position) for i in model.selected)
        invalid = model.preview_shift(-earliest - 1)
        self.assertFalse(invalid.valid)
        before = [event.position for event in model.timeline.events]
        self.assertEqual(model.commit_preview(invalid), 0)
        self.assertEqual([event.position for event in model.timeline.events], before)

    def test_zoom_anchor_limits_and_fit(self):
        result = zoom_about_cursor(90, 180, 400, 200)
        old_beat = (400 + 200 - 140) / 90
        new_beat = (result.scroll_x + 200 - 140) / result.pixels_per_beat
        self.assertAlmostEqual(old_beat, new_beat)
        self.assertEqual(zoom_about_cursor(90, 10000, 0, 300).pixels_per_beat,
                         MAX_PIXELS_PER_BEAT)
        self.assertEqual(zoom_about_cursor(90, 0.1, 0, 300).pixels_per_beat,
                         MIN_PIXELS_PER_BEAT)
        scale = fit_song_scale(240 * 32, 240, 1000)
        self.assertGreaterEqual(scale, MIN_PIXELS_PER_BEAT)
        self.assertLessEqual(scale, MAX_PIXELS_PER_BEAT)
        self.assertLessEqual(140 + (32 + 1) * scale, 1000)


if __name__ == "__main__": unittest.main()
