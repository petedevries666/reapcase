from pathlib import Path
import json
import shutil

from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.editor.creation import (
    SECOND_HELIX_LOOPER_ACTIONS, create_second_helix_expression,
    create_second_helix_looper, create_second_helix_preset,
    create_second_helix_snapshot, create_stadium_looper)
from stadium_reaper_bridge.live_midi import (LiveEventClass, LiveMidiDispatcher,
                                              build_live_event_set, second_helix_events)
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


def dispatch_messages(events, clock_positions):
    sent = []
    dispatcher = LiveMidiDispatcher(
        lambda message, recall, generation: sent.append(message))
    dispatcher.load(events)
    dispatcher.set_enabled(True)
    dispatcher.start(0)
    for units in clock_positions:
        dispatcher.poll(units)
    return sent


def test_real_observed_sequence_dispatches_every_message_in_source_order():
    """Regress the exact inventory and audio-clock progression seen on stage."""
    events = [
        ev(60, 0, {'type': 'program_change', 'program': 0}, ('program', None)),
        ev(480, 1, {'type': 'program_change', 'program': 7}, ('program', None)),
        ev(480, 2, {'type': 'control_change', 'cc': 69, 'value': 6},
           ('snapshot', None)),
        ev(660, 3, {'type': 'control_change', 'cc': 1, 'value': 0}, ('cc', 1)),
        ev(720, 4, {'type': 'control_change', 'cc': 2, 'value': 0}, ('cc', 2)),
    ]
    # Approximate the UI's 33 ms audio-clock polling, including polls that do
    # not cross an event and one repeated clock reading.
    clock_positions = list(range(16, 737, 16))
    clock_positions.insert(clock_positions.index(480) + 1, 480)

    assert dispatch_messages(events, clock_positions) == [
        event.message for event in events]


def test_three_same_position_events_are_each_dispatched_once():
    events = [
        ev(100, order, {'type': 'future_event', 'value': order}, ('future', order))
        for order in range(3)
    ]
    assert dispatch_messages(events, [99, 100, 100, 101]) == [
        event.message for event in events]


def test_large_polling_jump_dispatches_every_crossed_event_once():
    events = [
        ev(units, order, {'type': 'event', 'value': order}, ('event', order))
        for order, units in enumerate((10, 20, 20, 35, 80))
    ]
    assert dispatch_messages(events, [5, 80, 160]) == [
        event.message for event in events]


def test_small_increments_repeated_clock_and_exact_boundary_lose_nothing():
    events = [
        ev(units, order, {'type': 'event', 'value': order}, ('event', order))
        for order, units in enumerate((1, 2, 3, 4, 5))
    ]
    # Every event lands exactly on an interval boundary; repeated values must
    # neither duplicate the boundary event nor prevent the next one.
    assert dispatch_messages(events, [0, 1, 1, 2, 2, 3, 4, 4, 5, 5]) == [
        event.message for event in events]


def test_dispatcher_debug_logs_each_advance_and_decision(caplog):
    event = ev(10, 0, {'type': 'event', 'value': 1}, ('event', 1))
    dispatcher = LiveMidiDispatcher(lambda *_: None)
    dispatcher.load([event])
    dispatcher.start(0)
    with caplog.at_level("DEBUG", logger="stadium_reaper_bridge.live_midi"):
        dispatcher.poll(10)
    assert "LIVE ADVANCE" in caplog.text
    assert "previous_units=0" in caplog.text
    assert "current_units=10" in caplog.text
    assert "next_index=0" in caplog.text
    assert "next_event_units=10" in caplog.text
    assert "LIVE SKIP" in caplog.text
    assert "reason=LIVE disabled" in caplog.text


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


def bind_live_inventory(editor):
    """Mirror the application's model -> translated set -> dispatcher lifecycle."""
    dispatcher = LiveMidiDispatcher(lambda *_: None)
    rebuilds = []
    def rebuild():
        dispatcher.load(build_live_event_set(
            editor.timeline.events, editor.decoder, editor._units))
        rebuilds.append(dispatcher.generation)
    editor.set_timeline_change_listener(rebuild)
    rebuild()
    return dispatcher, rebuilds


def messages(dispatcher):
    return [event.message for event in dispatcher.events]


def test_loaded_song_real_source_types_and_live_snapshot_inventory(caplog):
    """Exercise the real Song parser, rather than feeding translator-shaped data."""
    editor = model()
    with caplog.at_level("DEBUG", logger="stadium_reaper_bridge.live_midi"):
        dispatcher, _ = bind_live_inventory(editor)
    snapshots = [event for event in editor.timeline.events
                 if event.data.get("rig_alias", {}).get("action") == "snapshot"]
    assert snapshots
    assert {event.source.type for event in snapshots} == {"MIDI_CC"}
    assert {event.data["cc"] for event in snapshots} == {69}
    assert any(message.get("cc") == 69 for message in messages(dispatcher))
    assert "LIVE SOURCE" in caplog.text
    assert "source.type=MIDI_CC" in caplog.text
    assert "LIVE BUILD" in caplog.text
    assert "LIVE EVENT" in caplog.text


def test_loaded_song_with_expression_builds_correct_live_cc_without_save(tmp_path):
    song_path = tmp_path / "expression.json"
    shutil.copyfile(FIXTURE, song_path)
    author = EditorModel.open(song_path)
    author.insert_event(create_second_helix_expression(
        MusicalPosition(100, 1, 1), 2, 127, author.decoder))
    author.save_as(song_path)

    loaded = EditorModel.open(song_path)
    dispatcher, _ = bind_live_inventory(loaded)
    assert {"type": "control_change", "cc": 2, "value": 127} in messages(dispatcher)


def test_live_inventory_tracks_create_edit_delete_without_save_or_redraw():
    editor = model()
    dispatcher, rebuilds = bind_live_inventory(editor)
    initial_generation = dispatcher.generation
    position = MusicalPosition(100, 1, 1)

    snapshot_index = editor.insert_event(
        create_second_helix_snapshot(position, 6, editor.decoder))
    assert dispatcher.generation == initial_generation + 1
    assert {"type": "control_change", "cc": 69, "value": 5} in messages(dispatcher)

    editor.insert_event(create_second_helix_expression(position, 1, 127, editor.decoder))
    assert {"type": "control_change", "cc": 1, "value": 127} in messages(dispatcher)
    editor.insert_event(create_second_helix_looper(position, "Record", editor.decoder))
    assert {"type": "control_change", "cc": 60, "value": 127} in messages(dispatcher)

    # A view repaint has no model operation and therefore cannot control inventory.
    generation_before_redraw = dispatcher.generation
    assert rebuilds[-1] == generation_before_redraw
    assert dispatcher.generation == generation_before_redraw

    capability = editor.edit_capability(snapshot_index)
    old_message = {"type": "control_change", "cc": 69, "value": 5}
    new_message = {"type": "control_change", "cc": 69, "value": 6}
    assert editor.edit_event(snapshot_index, dict(capability.values, snapshot=7))
    assert old_message not in messages(dispatcher)
    assert new_message in messages(dispatcher)

    editor.selected = {snapshot_index}
    assert editor.delete_selected() == 1
    assert new_message not in messages(dispatcher)
