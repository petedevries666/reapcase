import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from stadium_reaper_bridge.editor.editing import (editor_for_event, update_marker,
    update_midi_cc, update_stadium_snapshot)
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.lighting import LightingKind, create_lighting_event
from stadium_reaper_bridge.editor.creation import create_second_helix_preset
from stadium_reaper_bridge.analysis import SongAnalyzer
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

    def test_second_helix_program_change_editor_noop_and_lossless_edits(self):
        fixture = "001-03.001|MIDI_BANK_PROGRAM;BASS PRG;5;Bank/Prog;3;Off;Off;3"
        with TemporaryDirectory() as directory:
            model = self.model([fixture], directory)
            capability = model.edit_capability(0)
            self.assertIsNotNone(capability)
            self.assertEqual(capability.family, "helix_preset")
            self.assertEqual(capability.values, {"label": "BASS PRG", "channel": 3, "bank_msb": None,
                "bank_lsb": None, "program": 3})

            # Saving the form without changing it is a successful no-op.
            self.assertFalse(model.edit_event(0, dict(capability.values)))
            self.assertEqual(model.timeline.events[0].source.render(), fixture)

            changed = dict(capability.values, program=17)
            self.assertTrue(model.edit_event(0, changed))
            self.assertEqual(model.timeline.events[0].source.render(),
                "001-03.001|MIDI_BANK_PROGRAM;BASS PRG;5;Bank/Prog;3;Off;Off;17")

    def test_second_helix_program_change_banks_and_song_document_round_trip(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "song.json"
            document = {"name": "x", "ppqn": 240, "params": "opaque",
                "flags": ["001-03.001|MIDI_BANK_PROGRAM;BASS PRG;5;Bank/Prog;3;1;2;3;future"],
                "tracks": [], "future": {"nested": [1, {"keep": True}]}}
            path.write_text(json.dumps(document), encoding="utf-8")
            model = EditorModel.open(path, ROOT / "config/rig_midi.json")
            values = dict(model.edit_capability(0).values)
            self.assertEqual((values["bank_msb"], values["bank_lsb"]), (1, 2))
            self.assertTrue(model.edit_event(0, dict(values, bank_msb=None, bank_lsb=None)))
            self.assertEqual(model.timeline.events[0].source.fields[5:9],
                             ("Off", "Off", "3", "future"))
            self.assertTrue(model.edit_event(0, dict(values, bank_msb=12, bank_lsb=34)))
            self.assertEqual(model.timeline.events[0].source.fields[5:9],
                             ("12", "34", "3", "future"))
            output = Path(directory) / "edited.json"
            model.save_as(output)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["future"], document["future"])
            self.assertEqual(saved["params"], "opaque")
            reopened = EditorModel.open(output, ROOT / "config/rig_midi.json")
            self.assertEqual((reopened.timeline.events[0].data["bank_msb"],
                              reopened.timeline.events[0].data["bank_lsb"]), (12, 34))

    def test_midi_other_program_label_round_trip_preserves_command_and_lane(self):
        fixture = "009-04.177|MIDI_BANK_PROGRAM;KEYS PATCH;5;Bank/Prog;7;12;34;56;future;opaque"
        with TemporaryDirectory() as directory:
            model = self.model([fixture], directory)
            event = model.timeline.events[0]
            self.assertEqual(model.lane(event), "MIDI / OTHER")
            self.assertNotIn("rig_alias", event.data)
            capability = model.edit_capability(0)
            self.assertEqual(capability.family, "midi_program")
            self.assertEqual(capability.values, {"label": "KEYS PATCH", "channel": 7,
                "bank_msb": 12, "bank_lsb": 34, "program": 56})

            position = event.position
            semantics = tuple(event.source.fields[2:])
            self.assertTrue(model.edit_event(0, dict(capability.values,
                                                     label="CODIE KEYS BRRR")))
            edited = model.timeline.events[0]
            self.assertEqual(edited.position, position)
            self.assertEqual(tuple(edited.source.fields[2:]), semantics)
            self.assertEqual(edited.source.render(), fixture.replace("KEYS PATCH",
                                                                      "CODIE KEYS BRRR"))
            self.assertEqual(model.lane(edited), "MIDI / OTHER")
            self.assertNotIn("rig_alias", edited.data)

            output = Path(directory) / "program-edited.json"
            model.save_as(output)
            reopened = EditorModel.open(output, ROOT / "config/rig_midi.json")
            reopened_event = reopened.timeline.events[0]
            self.assertEqual(reopened_event.data, {"label": "CODIE KEYS BRRR",
                "channel": 7, "bank_msb": 12, "bank_lsb": 34, "program": 56})
            self.assertEqual(reopened_event.position, position)
            self.assertEqual(tuple(reopened_event.source.fields[2:]), semantics)
            self.assertEqual(reopened.lane(reopened_event), "MIDI / OTHER")

    def test_second_helix_program_creation_default_label_is_unchanged(self):
        event = create_second_helix_preset(MusicalPosition(3, 1, 1), None, 2, 19,
                                           DECODER)
        self.assertEqual(event.data["label"], "BASS PRESET 19")
        self.assertEqual(event.source.fields,
            ("MIDI_BANK_PROGRAM", "BASS PRESET 19", "5", "Bank/Prog", "3",
             "Off", "2", "19"))

    def test_second_helix_action_editors_still_dispatch_by_action(self):
        with TemporaryDirectory() as directory:
            flags = [
                "001-01.001|MIDI_CC;BASS SNAP 2;4;CC;3;69;1",
                "002-01.001|MIDI_CC;BASS PLAY;4;CC;3;61;127",
                "003-01.001|MIDI_CC;EXP1 100%;4;CC;3;1;127",
            ]
            model = self.model(flags, directory)
            for index in range(3):
                capability = model.edit_capability(index)
                self.assertIsNotNone(capability)
                self.assertTrue(capability.family.startswith("helix_"))
                # Reapplying decoded values proves action routing remains valid.
                self.assertFalse(model.edit_event(index, dict(capability.values)))

            malformed_alias = model.timeline.events[0]
            malformed_alias.data["rig_alias"] = {"system": "second_helix"}
            capability = editor_for_event(malformed_alias, model)
            self.assertEqual(capability.family, "midi_cc")

    def test_user_labels_round_trip_without_changing_midi_semantics_or_position(self):
        flags = [
            "002-01.001|MIDI_CC;BASS SNAP 3;4;CC;3;69;2",
            "003-02.011|MIDI_BANK_PROGRAM;BASS PRG;5;Bank/Prog;3;Off;Off;17",
            "004-03.021|MIDI_CC;EXP2 100%;4;CC;3;2;127",
            "005-04.031|MIDI_CC;BASS RECORD;4;CC;3;60;127",
            "006-01.041|MIDI_CC;VIDEO 8 PLAY;4;CC;16;8;12",
            "007-02.051|MIDI_CC;LIGHTS GO;4;CC;15;42;99",
            "008-03.061|MIDI_CC;OTHER DEVICE;4;CC;1;7;64",
        ]
        labels = ("CODIE GOES BRRR", "VERSE PROGRAM", "SOLO WAH UP", "LOOP IT",
                  "ROLL CAMERA", "ALL BLUE", "THE MYSTERY BUTTON")
        with TemporaryDirectory() as directory:
            model = self.model(flags, directory)
            before = [(event.position, event.data.get("rig_alias"),
                       tuple(event.source.fields[4:8])) for event in model.timeline.events]
            for index, label in enumerate(labels):
                capability = model.edit_capability(index)
                self.assertIsNotNone(capability)
                self.assertIn("label", capability.values)
                self.assertTrue(model.edit_event(index, dict(capability.values, label=label)))
                self.assertEqual(model.timeline.events[index].data["label"], label)
            after = [(event.position, event.data.get("rig_alias"),
                      tuple(event.source.fields[4:8])) for event in model.timeline.events]
            self.assertEqual(after, before)
            self.assertTrue(model.modified)

            output = Path(directory) / "exported.json"
            model.save_as(output)
            saved = json.loads(output.read_text(encoding="utf-8"))
            for label, rendered in zip(labels, saved["flags"]):
                self.assertIn(f";{label};", rendered)
            reopened = EditorModel.open(output, ROOT / "config/rig_midi.json")
            self.assertEqual([event.data["label"] for event in reopened.timeline.events],
                             list(labels))
            snapshot = reopened.timeline.events[0]
            self.assertEqual(snapshot.data["rig_alias"], {"system": "second_helix",
                "action": "snapshot", "snapshot": 3})
            self.assertEqual(reopened.lane(snapshot), "SECOND HELIX")
            inventory = SongAnalyzer().analyze(reopened).summary.inventory
            self.assertEqual(inventory["Second Helix snapshots"], 1)
            self.assertEqual(inventory["Second Helix program changes"], 1)
            self.assertEqual(inventory["Second Helix expression events"], 1)
            self.assertEqual(inventory["Second Helix looper actions"], 1)
            self.assertEqual(inventory["Video events"], 1)

    def test_native_stadium_and_structure_editors_do_not_expose_label(self):
        with TemporaryDirectory() as directory:
            model = self.model([
                "001-01.001|PRESETSNAP;;3;SET;PRESET;Snap 2",
                "002-01.001|LOOPER;RECORD;1;Record",
                "003-01.001|MARKER;VERSE;7;Off;Off;Off;false;SET;PRESET;Snap 2",
            ], directory)
            snapshot, looper, marker = (model.edit_capability(index) for index in range(3))
            self.assertNotIn("label", snapshot.values)
            self.assertNotIn("label", looper.values)
            self.assertEqual(set(marker.values), {"name", "pause_at_marker", "cycle_marker"})

    def test_lighting_label_uses_existing_cue_source_and_is_undoable(self):
        with TemporaryDirectory() as directory:
            model = self.model([], directory)
            event = create_lighting_event(MusicalPosition(4, 2, 1), "WHITE HIT",
                                          LightingKind.HIT, "white_hit")
            index = model.insert_event(event)
            capability = model.edit_capability(index)
            self.assertEqual(capability.values, {"label": "WHITE HIT"})
            self.assertTrue(model.edit_event(index, {"label": "CODIE LIGHTS BRRR"}))
            changed = model.timeline.events[index]
            self.assertEqual((changed.data["name"], changed.source.cue.name),
                             ("CODIE LIGHTS BRRR", "CODIE LIGHTS BRRR"))
            self.assertEqual(changed.position, event.position)
            self.assertTrue(model.undo())
            self.assertEqual(model.timeline.events[index].data["name"], "WHITE HIT")

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
