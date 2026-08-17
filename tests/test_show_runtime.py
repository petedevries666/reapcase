import json
from pathlib import Path
import tempfile
import time
import unittest

from stadium_reaper_bridge.runtime import (LiveRuntime, PreparedSongCache, Readiness,
                                            ShowPreloader, SongPreparer)
from stadium_reaper_bridge.show import MidiRoute, ReapcaseShow


def write_song(path, *, flags=None, tracks=None):
    path.write_text(json.dumps({"name": path.stem, "ppqn": 240, "params": None,
                                "flags": flags or ["001-01.001|START;;0;120;0;4;4;Off;true;Show;Preset;1",
                                                   "002-01.001|END"],
                                "tracks": tracks or []}), encoding="utf-8")


class ShowDocumentTests(unittest.TestCase):
    def test_round_trip_order_relative_paths_stable_ids_and_routing(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); songs = root / "songs"; songs.mkdir()
            for name in ("one", "two", "three"): write_song(songs / f"{name}.json")
            path = root / "live.reapcase-show.json"; show = ReapcaseShow("LIVE", path=path)
            one = show.add_song(songs / "one.json", "Same"); two = show.add_song(songs / "two.json", "Same")
            show.add_song(songs / "three.json"); show.move_song(2, 0); show.remove_song(2)
            show.midi["stadium"] = MidiRoute(True, "Stage USB", 5); show.save()
            loaded = ReapcaseShow.open(path)
            self.assertEqual([s.title for s in loaded.songs], ["three", "Same"])
            self.assertEqual(loaded.songs[1].id, one.id)
            self.assertNotEqual(one.id, two.id)
            self.assertEqual(loaded.songs[0].song_json, "songs/three.json")
            self.assertEqual(loaded.midi["stadium"], MidiRoute(True, "Stage USB", 5))

    def test_all_routes_validate_channels(self):
        for destination in ("stadium", "second_helix", "lights"):
            for channel in (1, 16): MidiRoute(channel=channel)
            for channel in (0, 17):
                with self.assertRaises(ValueError, msg=destination): MidiRoute(channel=channel)


class RuntimeTests(unittest.TestCase):
    def test_preflight_semantic_intents_and_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); path = root / "song.json"
            flags = ["001-01.001|START;;0;120;0;4;4;Off;true;Show;Preset;1",
                     "002-01.001|PRESETSNAP;;0;Show;Preset;4",
                     "003-01.001|MIDI_CC;VOICE;0;CC;3;69;2", "004-01.001|END"]
            write_song(path, flags=flags); source = path.read_text()
            sidecar = path.with_name(path.name + ".reapcase.json")
            sidecar.write_text(json.dumps({"reapcase": {"lights": [
                {"id": "big", "name": "BIG", "kind": "STATE", "position": "002-01.001"},
                {"id": "white_hit", "name": "WHITE HIT", "kind": "HIT", "position": "003-01.001"}]}}))
            show = ReapcaseShow(path=root / "show.reapcase-show.json"); ref = show.add_song(path)
            prepared = SongPreparer().prepare(show, ref)
            intents = [(c.destination, c.action, c.payload) for c in prepared.runtime_events]
            self.assertIn(("stadium", "snapshot", {"snapshot": 4}), intents)
            self.assertTrue(any(d == "second_helix" for d, _, _ in intents))
            self.assertEqual([(c.action, c.payload["name"]) for c in prepared.runtime_events if c.destination == "lights"],
                             [("state", "BIG"), ("hit", "WHITE HIT")])
            self.assertEqual(path.read_text(), source)
            self.assertEqual(prepared.readiness, Readiness.READY)

    def test_errors_warnings_staleness_bounded_cache_and_fast_switch(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); show = ReapcaseShow(path=root / "show.reapcase-show.json")
            paths = []
            for number in range(5):
                path = root / f"{number}.json"; write_song(path); paths.append(path); show.add_song(path)
            missing = show.add_song(root / "missing.json")
            preparer = SongPreparer(); self.assertEqual(preparer.prepare(show, missing).readiness, Readiness.ERROR)
            invalid_sidecar = paths[0].with_name(paths[0].name + ".reapcase.json"); invalid_sidecar.write_text("{")
            first = preparer.prepare(show, show.songs[0]); self.assertEqual(first.readiness, Readiness.WARNING)
            invalid_sidecar.unlink(); first = preparer.prepare(show, show.songs[0]); self.assertFalse(first.is_stale())
            time.sleep(.002); paths[0].touch(); self.assertTrue(first.is_stale())
            cache = PreparedSongCache(3)
            for reference in show.songs[:5]: cache.put(preparer.prepare(show, reference))
            self.assertEqual(len(cache), 3)
            calls = []
            class Counting:
                def prepare(self, current_show, reference):
                    calls.append(reference.id); return preparer.prepare(current_show, reference)
            preloader = ShowPreloader(Counting(), PreparedSongCache(3))
            runtime = LiveRuntime(show, preloader, lambda: calls.append("stop")); runtime.select(0)
            preloader._futures[show.songs[1].id].result(timeout=2)
            before = calls.count(show.songs[1].id); switched = runtime.next()
            self.assertIs(switched, runtime.current_song); self.assertEqual(calls.count(show.songs[1].id), before)
            self.assertIn("stop", calls); self.assertEqual(runtime.current_time_seconds, 0)
            preloader.shutdown()


if __name__ == "__main__": unittest.main()
