import json
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

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

    def test_phased_open_reports_work_before_returning_candidate(self):
        phases = []
        model = EditorModel.open_phased(FIXTURES / "perfect_picture_336.json", phases.append)
        self.assertEqual(model.path.name, "perfect_picture_336.json")
        self.assertEqual(phases, ["Parsing song…",
                                 "Loading sidecar and building timeline…",
                                 "Resolving audio…", "Preparing views…"])

    def test_failed_phased_open_never_produces_a_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text("not json", encoding="utf-8")
            phases = []
            with self.assertRaises(ValueError):
                EditorModel.open_phased(invalid, phases.append)
            self.assertEqual(phases, ["Parsing song…"])

    def test_phased_open_resolves_manual_audio_root_on_worker(self):
        calls = []

        def record_resolution(_model, root):
            calls.append((root, threading.current_thread().name))

        with patch.object(EditorModel, "resolve_audio", autospec=True,
                          side_effect=record_resolution):
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="song-open-test") as pool:
                candidate = pool.submit(
                    EditorModel.open_phased,
                    FIXTURES / "perfect_picture_336.json",
                    audio_root="/manual/audio").result()

        self.assertIsInstance(candidate, EditorModel)
        self.assertEqual(calls, [("/manual/audio", "song-open-test_0")])

    def test_real_song_lane_inventory(self):
        monzter = self.load("monzter_332.json")
        self.assertEqual(monzter.lane_counts(), {"STRUCTURE": 4, "STADIUM": 4,
                         "SECOND HELIX": 12, "VIDEO": 1, "LIGHTS": 0,
                         "MIDI / OTHER": 0})
        clocksick = self.load("clocksick_453.json")
        self.assertEqual(clocksick.lane_counts()["STRUCTURE"], 10)
        self.assertGreater(clocksick.lane_counts()["SECOND HELIX"], 0)
        self.assertEqual(sum(e.source.type.startswith("CYCLE") and clocksick.lane(e) == "STRUCTURE"
                             for e in clocksick.timeline.events), 2)

    def test_lane_inventory_classifies_each_event_once(self):
        model = self.load("clocksick_453.json")
        with patch.object(model, "lane", wraps=model.lane) as classify:
            counts = model.lane_counts()

        self.assertEqual(classify.call_count, len(model.timeline.events))
        self.assertEqual(sum(counts.values()), len(model.timeline.events))

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
            reopened = StadiumSong.from_json_text(noop.read_text())
            self.assertTrue(all(flag.semantic_data()["name"].endswith("m)")
                                for flag in reopened.flags if flag.type == "MARKER"
                                and flag.semantic_data().get("pause_at_marker") == "Off"))
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

    def test_pointer_down_on_selected_event_preserves_multi_selection(self):
        model = self.load("monzter_332.json")
        model.selected = {1, 2, 3}
        model.select_for_drag(2)
        self.assertEqual(model.selected, {1, 2, 3})

    def test_dragging_selected_member_moves_entire_selection(self):
        model = self.load("monzter_332.json")
        model.selected = {1, 2, 3}
        before = {i: model._units(model.timeline.events[i].position) for i in model.selected}
        model.select_for_drag(2)
        preview = model.preview_shift(60)
        self.assertEqual(preview.indices, tuple(sorted(before,
                         key=lambda i: before[i])))
        self.assertEqual(model.commit_preview(preview), 3)
        self.assertEqual({i: model._units(model.timeline.events[i].position) - before[i]
                          for i in before}, {1: 60, 2: 60, 3: 60})

    def test_pointer_down_on_unselected_event_replaces_selection(self):
        model = self.load("monzter_332.json")
        model.selected = {1, 2, 3}
        model.select_for_drag(4)
        self.assertEqual(model.selected, {4})

    def test_control_pointer_down_toggles_selection(self):
        model = self.load("monzter_332.json")
        model.selected = {1, 2}
        model.select_for_drag(2, toggle=True)
        self.assertEqual(model.selected, {1})
        model.select_for_drag(3, toggle=True)
        self.assertEqual(model.selected, {1, 3})

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

    def test_fit_song_fits_longest_fixture_in_standard_editor_width(self):
        model = self.load("clocksick_453.json")
        end = max(model._units(event.position) for event in model.timeline.events)
        scale = fit_song_scale(end, model.song.ppqn, 1180)
        self.assertGreater(scale, MIN_PIXELS_PER_BEAT)
        self.assertLessEqual(140 + (end / model.song.ppqn + 1) * scale + 16, 1180)


if __name__ == "__main__": unittest.main()
