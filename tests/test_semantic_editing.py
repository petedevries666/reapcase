import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from stadium_reaper_bridge.editor.editing import (editor_for_event, update_marker,
    update_midi_cc, update_stadium_snapshot)
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import MusicalPosition, StadiumFlag, StadiumSong
from stadium_reaper_bridge.timeline import stadium_to_timeline


ROOT = Path(__file__).parents[1]
DECODER = RigMidiDecoder.from_file(ROOT / "config/rig_midi.json")


class SemanticEditingTests(unittest.TestCase):
    def event(self, payload):
        flag = StadiumFlag(MusicalPosition(2, 1, 1), payload)
        return stadium_to_timeline(StadiumSong.from_dict({"name": "x", "ppqn": 240,
            "params": "", "flags": [flag.render()], "tracks": []}),
            midi_decoder=DECODER).events[0]

    def model(self, flags, directory):
        path = Path(directory) / "song.json"
        path.write_text(json.dumps({"name": "x", "ppqn": 240, "params": "",
            "flags": flags, "tracks": []}), encoding="utf-8")
        return EditorModel.open(path, ROOT / "config/rig_midi.json")

    def test_marker_fixture_layout_changes_only_proven_fields(self):
        original = self.event("MARKER;STOP;7;Off;On;Off;false;ANONYMALZ;[Current];Snap 6;future")
        edited = update_marker(original, name="CHORUS EXTENDED", pause_at_marker=False,
                               cycle_marker=True)
        self.assertEqual(edited.source.type, "MARKER")
        self.assertEqual(edited.source.fields,
            ("MARKER", "CHORUS EXTENDED", "7", "Off", "Off", "On", "false",
             "ANONYMALZ", "[Current]", "Snap 6", "future"))
        self.assertNotEqual(edited.source.type, "CYCLE_START")

    def test_snapshot_preserves_context_unknown_fields_and_round_trips_undo(self):
        with TemporaryDirectory() as directory:
            model = self.model([
                "001-01.001|START;;9;120;0;4;4;Off;true;SET;PRESET;Snap 1",
                "002-01.001|PRESETSNAP;;3;SET;PRESET;Snap 2;future"], directory)
            model.selected = {1}
            position = model.timeline.events[1].position
            self.assertTrue(model.edit_event(1, {"snapshot": 7, "context": "SET / PRESET"}))
            self.assertEqual(model.timeline.events[1].source.fields[3:],
                             ("SET", "PRESET", "Snap 7", "future"))
            self.assertEqual(model.timeline.events[1].position, position)
            output = Path(directory) / "edited.json"
            model.save_as(output)
            reopened = EditorModel.open(output, ROOT / "config/rig_midi.json")
            self.assertEqual(reopened.timeline.events[1].data["snapshot"], "Snap 7")
            self.assertTrue(model.undo())
            self.assertEqual(model.timeline.events[1].source.payload,
                             "PRESETSNAP;;3;SET;PRESET;Snap 2;future")
            self.assertEqual(model.selected, {1})

    def test_generic_midi_ranges_and_source_type(self):
        event = self.event("MIDI_CC;CUSTOM;4;CC;1;0;0;future")
        edited = update_midi_cc(event, channel=16, cc=127, value=127)
        self.assertEqual(edited.source.type, "MIDI_CC")
        self.assertEqual(edited.source.fields[-4:], ("16", "127", "127", "future"))
        for values in ({"channel": 0, "cc": 1, "value": 1},
                       {"channel": 1, "cc": 128, "value": 1},
                       {"channel": 1, "cc": 1, "value": -1}):
            with self.assertRaises(ValueError): update_midi_cc(event, **values)

    def test_protected_events_have_no_editor(self):
        with TemporaryDirectory() as directory:
            model = self.model(["001-01.001|START;;9;120;0;4;4;Off;true;S;P;Snap 1",
                "002-01.001|TIME;X;5;130;0;4;4", "003-01.001|END;;0"], directory)
            for event in model.timeline.events:
                self.assertIsNone(editor_for_event(event, model))

    def test_instruction_edit_is_undoable_and_preserves_future_sidecar_namespace(self):
        with TemporaryDirectory() as directory:
            model = self.model(["001-01.001|START;;9;120;0;4;4;Off;true;S;P;Snap 1"], directory)
            sidecar = model.show_path(model.path)
            sidecar.write_text(json.dumps({"future": {"keep": 42}, "reapcase": {"sequence": {
                "instructions": [{"id": "one", "position": "001-01.001", "label": "ONE",
                    "sample_id": "one", "muted": False, "origin": "user"}], "click_mutes": []}}}),
                encoding="utf-8")
            model = EditorModel.open(model.path, ROOT / "config/rig_midi.json")
            self.assertTrue(model.edit_instruction("one", label="CHORUS", sample_id="chorus", muted=True))
            model.save_as(model.path)
            saved = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(saved["future"], {"keep": 42})
            self.assertEqual(saved["reapcase"]["sequence"]["instructions"][0]["label"], "CHORUS")
            self.assertTrue(model.undo())
            self.assertEqual((model.instructions[0].label, model.instructions[0].sample_id,
                              model.instructions[0].muted), ("ONE", "one", False))


if __name__ == "__main__":
    unittest.main()
