from pathlib import Path
import json
import shutil

from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.creation import (
    SECOND_HELIX_LOOPER_ACTIONS, create_second_helix_expression,
    create_second_helix_looper, create_second_helix_preset,
    create_second_helix_snapshot, create_stadium_looper)
from stadium_reaper_bridge.live_midi import (LiveEventClass, LiveMidiDispatcher,
                                              second_helix_events)
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import MusicalPosition

FIXTURE = Path('tests/fixtures/wanna_be_429.json')
DECODER = RigMidiDecoder(json.loads(Path('config/rig_midi.json').read_text()))


def model():
    return EditorModel.open(FIXTURE)


def test_real_second_helix_translation_excludes_native_stadium_and_video():
    m = model()
    events = second_helix_events(m.timeline.events, m.decoder, m._units)
    assert events
    assert all(e.message['type'] in {'program_change', 'control_change'} for e in events)
    assert not any(e.message.get('cc') == 8 for e in events)  # VIDEO
    # Native PRESETSNAP and LOOPER are Stadium semantics, never LIVE MIDI v1.
    assert not any(e.source_order == 8 for e in events)


def ev(units, order, message, family, classification=LiveEventClass.RECALLABLE_STATE):
    from stadium_reaper_bridge.live_midi import LiveMidiEvent
    return LiveMidiEvent(units, order, message, classification, family)


def test_live_off_crosses_without_sending_and_live_on_uses_boundaries_once():
    sent = []
    d = LiveMidiDispatcher(lambda msg, recall, generation: sent.append((msg, recall)))
    events = [ev(10, 0, {'type': 'program_change', 'program': 4}, ('program', None)),
              ev(10, 1, {'type': 'control_change', 'cc': 1, 'value': 2}, ('cc', 1))]
    d.load(events); d.start(0); d.poll(10); assert sent == []
    d.load(events); d.set_enabled(True); d.start(10); d.poll(10); d.poll(10)
    assert [x[0] for x in sent] == [e.message for e in events]
    assert not any(recall for _, recall in sent)


def test_smart_recall_latest_per_family_order_and_never_actions():
    sent = []
    d = LiveMidiDispatcher(lambda msg, recall, generation: sent.append((msg, recall)))
    action = ev(8, 8, {'type':'control_change','cc':60,'value':127}, ('action',60), LiveEventClass.ACTION)
    events = [
      ev(1,0,{'type':'program_change','program':1},('program',None)),
      ev(2,1,{'type':'program_change','program':9},('program',None)),
      ev(3,2,{'type':'control_change','cc':69,'value':1},('snapshot',None)),
      ev(4,3,{'type':'control_change','cc':69,'value':4},('snapshot',None)),
      ev(5,4,{'type':'control_change','cc':1,'value':20},('cc',1)),
      ev(6,5,{'type':'control_change','cc':2,'value':30},('cc',2)),
      ev(7,6,{'type':'control_change','cc':1,'value':70},('cc',1)), action]
    d.load(events); d.set_enabled(True); d.start(10)
    assert [item[0] for item in sent] == [events[1].message, events[3].message,
                                          events[6].message, events[5].message]
    assert all(recall for _, recall in sent)


def test_action_naturally_crossed_pause_resume_seek_stop_and_generation():
    calls=[]; d=LiveMidiDispatcher(lambda m,r,g: calls.append((m,r,g)))
    action=ev(5,0,{'type':'control_change','cc':60,'value':127},('action',60),LiveEventClass.ACTION)
    state=ev(7,1,{'type':'control_change','cc':2,'value':80},('cc',2))
    old=d.load([action,state]); d.set_enabled(True); d.start(0); d.poll(5)
    assert calls[-1][0] == action.message and not calls[-1][1]
    d.pause(5); d.start(5); d.poll(5); assert len(calls)==1
    d.seek(9); d.start(9); assert calls[-1][0] == state.message and calls[-1][1]
    d.stop(); count=len(calls); d.poll(100); assert len(calls)==count
    assert d.load([]) != old


def test_every_authorable_second_helix_looper_uses_real_mapping_and_is_action():
    expected = {
        "Overdub": (60, 0), "Record": (60, 127),
        "Stop": (61, 0), "Play": (61, 127), "Play Once": (62, 127),
        "Undo/Redo": (63, 127), "Forward": (65, 0), "Reverse": (65, 127),
        "Full Speed": (66, 0), "Half Speed": (66, 127),
        "Off": (67, 0), "On": (67, 127),
    }
    assert set(expected) == SECOND_HELIX_LOOPER_ACTIONS
    # Helix has no established "Clear" command in the authoring config; native
    # Stadium Clear Loop must not be mistaken for one.
    assert "Clear" not in SECOND_HELIX_LOOPER_ACTIONS
    for index, action in enumerate(expected, 1):
        authored = create_second_helix_looper(
            MusicalPosition(index, 1, 1), action, DECODER)
        translated = second_helix_events([authored], DECODER, lambda p: p.bar)
        assert len(translated) == 1
        item = translated[0]
        assert (item.message["cc"], item.message["value"]) == expected[action]
        assert item.event_class is LiveEventClass.ACTION


def test_real_looper_actions_are_not_recalled_but_each_dispatches_when_crossed():
    authored = [create_second_helix_looper(MusicalPosition(i, 1, 1), action, DECODER)
                for i, action in enumerate(sorted(SECOND_HELIX_LOOPER_ACTIONS), 1)]
    events = second_helix_events(authored, DECODER, lambda p: p.bar)
    sent = []
    dispatcher = LiveMidiDispatcher(lambda message, recall, generation:
                                    sent.append((message, recall)))
    dispatcher.load(events); dispatcher.set_enabled(True); dispatcher.start(len(events) + 1)
    assert sent == []
    dispatcher.load(events); dispatcher.set_enabled(True); dispatcher.start(0)
    dispatcher.poll(len(events))
    assert [message for message, _ in sent] == [event.message for event in events]
    assert not any(recall for _, recall in sent)


def test_authorable_second_helix_family_live_contract_and_native_looper_exclusion():
    position = MusicalPosition(2, 1, 1)
    authored = [
        create_second_helix_preset(position, None, None, 14, DECODER),
        create_second_helix_snapshot(position, 3, DECODER),
        create_second_helix_expression(position, 2, 127, DECODER),
        create_second_helix_looper(position, "Record", DECODER),
    ]
    translated = second_helix_events(authored, DECODER, lambda p: p.bar)
    assert [(item.family[0], item.event_class) for item in translated] == [
        ("program", LiveEventClass.RECALLABLE_STATE),
        ("snapshot", LiveEventClass.RECALLABLE_STATE),
        ("cc", LiveEventClass.RECALLABLE_STATE),
        ("action", LiveEventClass.ACTION),
    ]
    native = create_stadium_looper(position, "Clear Loop")
    assert second_helix_events([native], DECODER, lambda p: p.bar) == ()


def test_authored_second_helix_messages_survive_editor_save_and_open(tmp_path):
    """Exercise the same creation/model/native Song path as the editor UI."""
    expected_loopers = {
        "Overdub": (60, 0), "Record": (60, 127),
        "Stop": (61, 0), "Play": (61, 127), "Play Once": (62, 127),
        "Undo/Redo": (63, 127), "Forward": (65, 0), "Reverse": (65, 127),
        "Full Speed": (66, 0), "Half Speed": (66, 127),
        "Off": (67, 0), "On": (67, 127),
    }
    song_path = tmp_path / "round-trip.json"
    shutil.copyfile(FIXTURE, song_path)
    editor = EditorModel.open(song_path)
    first_new_source_index = len(editor.timeline.events)
    position = MusicalPosition(200, 1, 1)
    authored = [
        create_second_helix_preset(position, None, None, 7, editor.decoder),
        create_second_helix_snapshot(position, 3, editor.decoder),
        create_second_helix_expression(position, 1, 0, editor.decoder),
        create_second_helix_expression(position, 2, 127, editor.decoder),
    ] + [create_second_helix_looper(position, action, editor.decoder)
         for action in expected_loopers]
    expected = [
        {"type": "program_change", "program": 7},
        {"type": "control_change", "cc": 69, "value": 2},
        {"type": "control_change", "cc": 1, "value": 0},
        {"type": "control_change", "cc": 2, "value": 127},
    ] + [{"type": "control_change", "cc": cc, "value": value}
         for cc, value in expected_loopers.values()]

    # Fresh UI-created events carry integer MIDI fields and decoder aliases.
    for event in authored[1:]:
        assert event.source.type == "MIDI_CC"
        assert isinstance(event.data["channel"], int)
        assert isinstance(event.data["cc"], int)
        assert isinstance(event.data["value"], int)
        assert event.data["rig_alias"]["system"] == "second_helix"
    fresh = second_helix_events(authored, editor.decoder, lambda p: p.bar)
    assert [item.message for item in fresh] == expected

    for event in authored:
        editor.insert_event(event)
    editor.save_as(song_path)
    reopened = EditorModel.open(song_path)
    reconstructed = [event for event in reopened.timeline.events
                     if event.source_index is not None and
                     event.source_index >= first_new_source_index]
    assert len(reconstructed) == len(authored)
    for event in reconstructed[1:]:
        assert event.source.type == "MIDI_CC"
        assert isinstance(event.data["channel"], int)
        assert isinstance(event.data["cc"], int)
        assert isinstance(event.data["value"], int)
        assert event.data["rig_alias"]["system"] == "second_helix"
    round_tripped = second_helix_events(reconstructed, reopened.decoder,
                                       lambda p: p.bar)
    assert [item.message for item in round_tripped] == expected
    assert [item.event_class for item in round_tripped[4:]] == [
        LiveEventClass.ACTION] * len(expected_loopers)


def test_live_debug_logs_rejected_second_helix_cc(caplog):
    event = create_second_helix_snapshot(MusicalPosition(2, 1, 1), 3, DECODER)
    event.data["value"] = 127  # CC69 is outside the configured snapshot range.
    with caplog.at_level("DEBUG", logger="stadium_reaper_bridge.live_midi"):
        assert second_helix_events([event], DECODER, lambda p: p.bar) == ()
    assert "LIVE MIDI SKIP SECOND HELIX" in caplog.text
    assert "cc=69" in caplog.text
