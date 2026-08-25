from pathlib import Path

from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.live_midi import LiveEventClass, LiveMidiDispatcher, second_helix_events

FIXTURE = Path('tests/fixtures/wanna_be_429.json')


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
