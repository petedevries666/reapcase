from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stadium_reaper_bridge.editor.ergonomics import (
    BackupError, DialogPositions, backup_existing, centered_position,
    clamp_dialog_position, follow_scroll,
)
from stadium_reaper_bridge.editor.layout import snapped_units_at_x, timeline_x
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.stadium import MusicalPosition


FIXTURE = Path(__file__).parent / "fixtures" / "perfect_picture_336.json"


class GeometryAndFollowTests(unittest.TestCase):
    def test_dialog_first_open_remember_and_clamp(self):
        store = DialogPositions({})
        self.assertEqual((400, 350), centered_position((100, 100, 800, 600), (200, 100)))
        self.assertEqual((400, 350), store.position("marker", (100, 100, 800, 600),
                                                    (200, 100), (0, 0, 1920, 1080)))
        store.remember("marker", (700, 220))
        self.assertEqual((700, 220), store.position("marker", (0, 0, 1, 1),
                                                    (200, 100), (0, 0, 1920, 1080)))
        self.assertEqual((752, 0), clamp_dialog_position((4000, -500), (300, 200),
                                                        (0, 0, 800, 600)))

    def test_threshold_follow_only_while_playing(self):
        self.assertIsNone(follow_scroll(20, 0, 100, playing=True))
        self.assertIsNone(follow_scroll(50, 0, 100, playing=True))
        self.assertIsNone(follow_scroll(75, 0, 100, playing=True))
        self.assertEqual(55, follow_scroll(85, 0, 100, playing=True))
        self.assertIsNone(follow_scroll(85, 0, 100, playing=False))
        self.assertIsNone(follow_scroll(85, 0, 100, playing=True, suspended=True))
        self.assertEqual(75, follow_scroll(105, 0, 100, playing=True, suspended=True))


class PlayheadSnapTests(unittest.TestCase):
    def test_pointer_uses_canonical_snap_modes_across_timing_change(self):
        model = EditorModel.open(FIXTURE)
        ppqn, scale = model.song.ppqn, 90
        change = next(point for point in model.timing_map.segments if point.start_units > 0)
        raw = change.start_units + round(ppqn * .62)
        x = timeline_x(raw, ppqn, scale)
        for mode in ("1 beat", "half beat", "quarter beat", "no snap"):
            actual = snapped_units_at_x(x, ppqn, scale, mode, model.numerator,
                                        model.timing_map)
            expected = model.timing_map.nearest_beat_units(raw) if mode == "1 beat" else (
                round(raw / (ppqn / 2)) * round(ppqn / 2) if mode == "half beat" else
                round(raw / (ppqn / 4)) * round(ppqn / 4) if mode == "quarter beat" else raw)
            self.assertEqual(expected, actual)


class ClipboardAndBackupTests(unittest.TestCase):
    def test_copy_paste_at_playhead_selects_and_undoes(self):
        model = EditorModel.open(FIXTURE)
        source = next(i for i, event in enumerate(model.timeline.events)
                      if event.source.type == "PRESETSNAP")
        original = model.timeline.events[source]
        original_position = original.position
        model.selected = {source}
        self.assertEqual(1, model.copy_selected())
        model.cursor = MusicalPosition(50, 1, 1)
        self.assertEqual(1, model.paste_at_cursor())
        pasted = model.timeline.events[next(iter(model.selected))]
        self.assertEqual(MusicalPosition(50, 1, 1), pasted.position)
        self.assertEqual(original.source.payload, pasted.source.payload)
        self.assertEqual(original_position, original.position)
        self.assertTrue(model.undo())
        self.assertNotIn(pasted, model.timeline.events)

    def test_protected_events_are_not_copied(self):
        model = EditorModel.open(FIXTURE)
        for kind in ("START", "TIME", "END"):
            index = next((i for i, event in enumerate(model.timeline.events)
                          if event.source.type == kind), None)
            if index is not None:
                model.selected = {index}
                self.assertEqual(0, model.copy_selected())

    def test_overwrite_creates_collision_safe_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "song.json"
            target.write_text("old", encoding="utf-8")
            moment = datetime(2026, 8, 18, 21, 3, 1)
            first = backup_existing([target], now=moment)
            second = backup_existing([target], now=moment)
            self.assertEqual("old", first[0].read_text(encoding="utf-8"))
            self.assertNotEqual(first, second)

    def test_backup_failure_prevents_model_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "song.json"
            target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            model = EditorModel.open(target)
            original = target.read_bytes()
            with patch("stadium_reaper_bridge.editor.model.backup_existing",
                       side_effect=BackupError("Backup could not be created.")):
                with self.assertRaises(BackupError):
                    model.save_as(target)
            self.assertEqual(original, target.read_bytes())

    def test_same_name_save_backs_up_original_then_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "song.json"
            original = FIXTURE.read_text(encoding="utf-8")
            target.write_text(original, encoding="utf-8")
            model = EditorModel.open(target)
            movable = next(i for i, event in enumerate(model.timeline.events)
                           if event.source.type == "PRESETSNAP")
            model.selected = {movable}
            model.shift_selected(beats=1)
            model.save_as(target)
            self.assertNotEqual(original, target.read_text(encoding="utf-8"))
            backups = list((target.parent / ".reapcase-backups").glob("song_*.json"))
            self.assertEqual(1, len(backups))
            self.assertEqual(original, backups[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
