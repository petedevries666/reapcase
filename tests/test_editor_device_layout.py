import unittest

from stadium_reaper_bridge.editor.device_layout import (
    COMMANDS, DEVICE_LANE_HEIGHT, LOOPER, device_event_bounds,
    device_sublane, device_sublane_bounds,
)
from stadium_reaper_bridge.editor.layout import marquee_candidates
from stadium_reaper_bridge.editor.looper import derive_looper_regions
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import StadiumSong
from stadium_reaper_bridge.timeline import stadium_to_timeline


DECODER = RigMidiDecoder.from_file("config/rig_midi.json")


def events_for(flags):
    song = StadiumSong.from_dict({"name": "Device layout", "ppqn": 240,
                                  "params": None, "flags": flags, "tracks": []})
    return stadium_to_timeline(song, midi_decoder=DECODER).events


class DeviceSublaneTests(unittest.TestCase):
    def test_stadium_semantics_classify_snapshot_play_and_stop(self):
        events = events_for(["001-01.001|PRESETSNAP;SNAP 2;1;2",
                             "002-01.001|LOOPER;PLAY;1;Play",
                             "003-01.001|LOOPER;STOP;1;Stop"])
        self.assertEqual([device_sublane(event, "STADIUM") for event in events],
                         [COMMANDS, LOOPER, LOOPER])

    def test_second_helix_semantics_classify_snapshot_overdub_and_stop(self):
        events = events_for(["001-01.001|MIDI_CC;BASS SNAPSHOT;4;CC;3;69;6",
                             "002-01.001|MIDI_CC;BASS OVERDUB;4;CC;3;60;0",
                             "003-01.001|MIDI_CC;BASS STOP;4;CC;3;61;0"])
        self.assertEqual([device_sublane(event, "SECOND HELIX") for event in events],
                         [COMMANDS, LOOPER, LOOPER])

    def test_composite_geometry_is_separate_for_both_device_lanes(self):
        for top in (26, 102):
            commands = device_sublane_bounds(top, COMMANDS)
            looper = device_sublane_bounds(top, LOOPER)
            self.assertEqual(commands[1], looper[0])
            self.assertLess(device_event_bounds(top, COMMANDS)[1],
                            device_event_bounds(top, LOOPER)[0])
            self.assertEqual(looper[1] - top, DEVICE_LANE_HEIGHT)

    def test_hit_bounds_and_marquee_cover_both_rows(self):
        top = 100
        command_y = device_event_bounds(top, COMMANDS)
        looper_y = device_event_bounds(top, LOOPER)
        bounds = {4: (150, command_y[0], 215, command_y[1]),
                  5: (220, looper_y[0], 400, looper_y[1]),
                  6: (410, looper_y[0], 455, looper_y[1])}
        self.assertEqual(marquee_candidates((140, top, 460, top + DEVICE_LANE_HEIGHT),
                                            bounds), {4, 5, 6})
        for index, x, row in ((4, 160, command_y), (5, 300, looper_y),
                              (6, 420, looper_y)):
            self.assertIn(index, marquee_candidates((x, row[0], x + 1, row[1]), bounds))

    def test_repeated_regions_keep_individual_semantic_sources(self):
        events = events_for(["001-01.001|LOOPER;PLAY;1;Play",
                             "003-01.001|LOOPER;PLAY;1;Play",
                             "005-01.001|LOOPER;STOP;1;Stop"])
        units = lambda p: ((p.bar - 1) * 4 + p.beat - 1) * 240
        regions = derive_looper_regions(events, units, "STADIUM", 5 * 4 * 240)
        self.assertEqual([region.source_event_indices for region in regions], [(0,), (1,)])
        self.assertEqual(regions[0].end_units, regions[1].start_units)
