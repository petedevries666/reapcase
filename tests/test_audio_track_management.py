import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import wave

from stadium_reaper_bridge.editor.model import EditorModel


def write_wav(path, rate=48000, frames=48):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, frames, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * frames)


class AudioTrackManagementTests(unittest.TestCase):
    def fixture(self, root, tracks):
        path = root / "showcase" / "songs" / "workspace" / "431.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"name": "Song", "ppqn": 240, "params": None,
                "flags": ["001-01.001|START;;9;120;0;4;4;Off;true;x;y;z"],
                "tracks": tracks, "future": {"preserved": True}}
        path.write_text(json.dumps(data))
        return path

    def track(self, name, **extra):
        return {"name": name,
                "filename": f"../../../../../sd-stadium/songs/workspace/Audio/431/{name}.wav",
                "offset": 0, "future": extra or {"x": name}}

    def test_add_copy_limit_defaults_and_save_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = self.fixture(root, [self.track("CLICK")])
            write_wav(root / "songs/workspace/Audio/431/CLICK.wav")
            source = root / "external" / "VOCALS.wav"; write_wav(source)
            original = source.read_bytes()
            model = EditorModel.open(path)
            added = model.add_audio_track(source)
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue((root / "songs/workspace/Audio/431/VOCALS.wav").is_file())
            self.assertEqual(added, {"name": "VOCALS", "filename":
                "../../../../../sd-stadium/songs/workspace/Audio/431/VOCALS.wav",
                "offset": 0, "gain": 1.0, "panning": 0.0, "mute": False,
                "solo": False, "trim": 1.0, "transpose": False})
            self.assertIsNotNone(model.audio_tracks[1].file_info)
            saved = root / "saved.json"; model.save_as(saved)
            self.assertEqual(EditorModel.open(saved).song.tracks, model.song.tracks)
            self.assertTrue(model.undo())
            self.assertEqual(len(model.song.tracks), 1)
            self.assertTrue((root / "songs/workspace/Audio/431/VOCALS.wav").exists())

            full = EditorModel.open(self.fixture(root, [self.track(str(i)) for i in range(8)]))
            with self.assertRaisesRegex(ValueError, "at most 8"):
                full.add_audio_track(source)

    def test_delete_move_unknown_fields_and_undo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); original = [self.track(x) for x in ("CLICK", "BASS", "SYNTH", "VOCALS")]
            path = self.fixture(root, original)
            model = EditorModel.open(path)
            self.assertTrue(model.move_audio_track(3, 1))
            self.assertEqual([x["name"] for x in model.song.tracks], ["CLICK", "VOCALS", "BASS", "SYNTH"])
            self.assertEqual(model.song.tracks[1]["future"], {"x": "VOCALS"})
            self.assertFalse(model.move_audio_track(1, 1))
            self.assertTrue(model.undo()); self.assertEqual(model.song.tracks, original)
            wav = root / "songs/workspace/Audio/431/BASS.wav"; write_wav(wav)
            removed = copy.deepcopy(original[1])
            self.assertEqual(model.delete_audio_track(1), removed)
            self.assertTrue(wav.exists())
            self.assertTrue(model.undo()); self.assertEqual(model.song.tracks, original)

    def test_refresh_identity_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = self.fixture(root, [self.track("CLICK")])
            wav = root / "songs/workspace/Audio/431/CLICK.wav"; write_wav(wav, frames=48)
            model = EditorModel.open(path); document = model.song.to_dict()
            info = model.audio_tracks[0].file_info
            self.assertEqual(model.refresh_audio(), set())
            self.assertIs(model.audio_tracks[0].file_info, info)
            time.sleep(.002); write_wav(wav, frames=96); os.utime(wav, None)
            self.assertEqual(model.refresh_audio(), {wav.resolve()})
            self.assertEqual(model.audio_tracks[0].file_info.frames, 96)
            self.assertFalse(model.modified); self.assertEqual(model.song.to_dict(), document)


if __name__ == "__main__":
    unittest.main()
