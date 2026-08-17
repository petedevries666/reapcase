import json
from pathlib import Path
import tempfile
import unittest
import wave

from stadium_reaper_bridge.editor.audio import (AudioResolver, TempoChange, TempoMap,
                                                 read_wav_info)
from stadium_reaper_bridge.editor.layout import (HEADER_WIDTH, horizontal_wheel_units,
                                                  marquee_candidates, normalized_rectangle,
                                                  x_for_position)
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import MusicalPosition, StadiumSong


FIXTURES = Path(__file__).parent / "fixtures"
DECODER = RigMidiDecoder.from_file("config/rig_midi.json")


class AudioAndMarqueeTests(unittest.TestCase):
    def load(self, name):
        path = FIXTURES / name
        return EditorModel(StadiumSong.from_json_text(path.read_text()), path, DECODER)

    def test_fixture_track_counts_are_real_and_capped_only_for_display(self):
        self.assertEqual(len(self.load("monzter_332.json").audio_tracks), 8)
        self.assertEqual(len(self.load("perfect_picture_336.json").audio_tracks), 8)
        self.assertEqual(len(self.load("clocksick_453.json").audio_tracks), 3)
        self.assertEqual(len(self.load("late_night_party_431.json").audio_tracks), 2)

    def test_missing_audio_remains_a_visible_track(self):
        model = self.load("clocksick_453.json")
        self.assertEqual(model.audio_tracks[0].name, "CLICK")
        self.assertIsNone(model.audio_tracks[0].resolved_path)
        self.assertIsNone(model.audio_tracks[0].file_info)

    def test_wav_header_duration_without_sample_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tone.wav"
            with wave.open(str(path), "wb") as target:
                target.setnchannels(2); target.setsampwidth(2); target.setframerate(8000)
                target.writeframes(b"\0\0" * 2 * 4000)
            info = read_wav_info(path)
            self.assertEqual((info.sample_rate, info.channels, info.frames), (8000, 2, 4000))
            self.assertEqual(info.duration_seconds, 0.5)

    def test_resolver_matches_relative_tail_from_audio_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Audio"; path = root / "453" / "CLICK.wav"
            path.parent.mkdir(parents=True); path.touch()
            resolver = AudioResolver(Path(directory) / "song", root)
            self.assertEqual(resolver.resolve("../../sd-stadium/songs/workspace/Audio/453/CLICK.wav"),
                             path.resolve())

    def test_resolver_refuses_ambiguous_basename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("one", "two"):
                path = root / folder / "CLICK.wav"; path.parent.mkdir(); path.touch()
            self.assertIsNone(AudioResolver(root / "song", root).resolve("CLICK.wav"))

    def test_tempo_map_and_audio_use_shared_musical_scale(self):
        ppqn = 240
        units = lambda p: ((p.bar - 1) * 4 + p.beat - 1) * ppqn + p.tick - 1
        position = lambda value: MusicalPosition(value // (ppqn * 4) + 1,
                                                  value // ppqn % 4 + 1, value % ppqn + 1)
        tempo = TempoMap(ppqn, (TempoChange(0, 120), TempoChange(960, 60)), units, position)
        self.assertEqual(tempo.musical_position_to_seconds(MusicalPosition(2, 1, 1)), 2)
        self.assertEqual(tempo.seconds_to_musical_position(4), MusicalPosition(2, 3, 1))
        audio_x = HEADER_WIDTH + tempo.seconds_to_units(4) / ppqn * 90
        self.assertEqual(audio_x, x_for_position(MusicalPosition(2, 3, 1), ppqn, 4, 90))

    def test_fit_extent_includes_resolved_audio_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); wav = root / "clip.wav"
            with wave.open(str(wav), "wb") as target:
                target.setnchannels(1); target.setsampwidth(2); target.setframerate(100)
                target.writeframes(b"\0\0" * 1000)
            document = {"name": "Audio", "ppqn": 240, "params": None,
                        "flags": ["001-01.001|START;;;120;;4;4;;;;;",
                                  "001-02.001|END;End"],
                        "tracks": [{"name": "clip", "filename": str(wav), "offset": 0}]}
            model = EditorModel(StadiumSong.from_json_text(json.dumps(document)), root / "song.json", DECODER)
            self.assertEqual(model.audio_end_units, 4800)
            self.assertEqual(model.song_end_units, 4800)

    def test_marquee_intersection_and_reverse_directions(self):
        boxes = {0: (10, 10, 30, 20), 1: (40, 20, 60, 40), 2: (80, 80, 90, 90)}
        expected = {0, 1}
        self.assertEqual(marquee_candidates((5, 5, 65, 45), boxes), expected)
        self.assertEqual(marquee_candidates((65, 45, 5, 5), boxes), expected)
        self.assertEqual(normalized_rectangle(65, 45, 5, 5), (5, 5, 65, 45))

    def test_marquee_modes_and_wheel_are_selection_navigation_only(self):
        model = self.load("monzter_332.json")
        before = [event.position for event in model.timeline.events]
        model.selected = {1}; model.apply_marquee({2, 3}, "add")
        self.assertEqual(model.selected, {1, 2, 3})
        model.apply_marquee({2, 4}, "toggle")
        self.assertEqual(model.selected, {1, 3, 4})
        self.assertEqual(horizontal_wheel_units(1), -3)
        self.assertEqual([event.position for event in model.timeline.events], before)


if __name__ == "__main__":
    unittest.main()
