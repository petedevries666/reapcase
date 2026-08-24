import copy
from pathlib import Path

from stadium_reaper_bridge.analysis import AnalysisConfig, Severity, SongAnalyzer
from stadium_reaper_bridge.editor.model import EditorModel
from stadium_reaper_bridge.midi import RigMidiDecoder
from stadium_reaper_bridge.stadium import StadiumSong


def model(flags):
    song = StadiumSong.from_dict({"name":"Analysis Test","ppqn":240,"params":None,
                                  "flags":flags,"tracks":[]})
    return EditorModel(song, Path("test.json"), RigMidiDecoder.from_file("config/rig_midi.json"),
                       resolve_audio_on_init=False)


def good_flags():
    return ["001-01.001|START;;0;120;0;4;4;Off;Off;;;",
            "001-01.001|MIDI_BANK_PROGRAM;BASS PRG 0;3;Program Change;3;Off;Off;0",
            "001-01.020|MIDI_BANK_PROGRAM;BASS PRG 4;3;Program Change;3;Off;Off;4",
            "001-01.040|MIDI_CC;BASS SNAP 2;4;CC;3;69;1",
            "001-01.060|MIDI_CC;EXP PDL 1;4;CC;3;1;0",
            "001-01.080|MIDI_CC;EXP PDL 2;4;CC;3;2;0",
            "001-02.001|LOOPER;CLEAR;1;Clear Loop",
            "008-01.001|END;;0"]


def config(**changes):
    cfg=AnalysisConfig(bass_preset=4,bass_snapshot=2)
    for key,value in changes.items(): setattr(cfg,key,value)
    return cfg


def ids(report): return {r.rule_id for r in report.results}


def test_correct_start_passes_and_analysis_is_read_only():
    m=model(good_flags()); before=copy.deepcopy(m.timeline.events)
    report=SongAnalyzer().analyze(m,config())
    assert "start.initialization" not in ids(report)
    assert [(e.position,e.data) for e in m.timeline.events] == [(e.position,e.data) for e in before]


def test_missing_late_wrong_and_order_are_detected():
    missing=good_flags(); del missing[1]
    assert "start.initialization" in ids(SongAnalyzer().analyze(model(missing),config()))
    wrong=good_flags(); wrong[2]=wrong[2].replace(";4", ";5", 1).replace(";Off;Off;4",";Off;Off;5")
    assert any("BASS PRG CHANGE = 4" in r.message for r in SongAnalyzer().analyze(model(wrong),config()).results)
    late=[x.replace("001-01.060","002-01.060") if "EXP PDL 1" in x else x for x in good_flags()]
    assert any("too late" in r.message for r in SongAnalyzer().analyze(model(late),config()).results)
    ordered=good_flags()
    ordered[1]=ordered[1].replace("001-01.001","001-01.020")
    ordered[2]=ordered[2].replace("001-01.020","001-01.001")
    assert "start.order" in ids(SongAnalyzer().analyze(model(ordered),config()))


def test_current_looper_and_program_bank_diagnostics():
    flags=good_flags()[:-1]+["003-01.001|PRESETSNAP;;3;CURRENT;CURRENT;CURRENT",
        "004-01.001|LOOPER;PLAY;1;Play",
        "005-01.001|MIDI_BANK_PROGRAM;BAD;3;Program Change;2;0;0;7",
        "008-01.001|END;;0"]
    report=SongAnalyzer().analyze(model(flags),config())
    assert {"state.current_preset","state.current_snapshot","looper.play_without_rec","midi.program_zero_bank"} <= ids(report)
    play=next(r for r in report.results if r.rule_id=="looper.play_without_rec")
    assert play.severity is Severity.WARNING and "manually" in play.message
    # The deliberate channel-3 program zero has Off banks, so is not 0/0.
    assert sum(r.rule_id=="midi.program_zero_bank" for r in report.results)==1


def test_expression_hold_end_and_timing_collisions():
    flags=good_flags()[:-1]+["002-01.001|MIDI_CC;EXP PDL 1;4;CC;3;1;127",
        "007-01.001|MIDI_CC;EXP PDL 1;4;CC;3;1;64",
        "007-01.001|MIDI_CC;EXP PDL 1;4;CC;3;1;64",
        "008-01.001|END;;0"]
    report=SongAnalyzer().analyze(model(flags),config(max_hold_bars=2))
    assert {"expression.long_max","end.expression_rest","timing.duplicate"} <= ids(report)


def test_structure_after_end_and_summary_use_live_timeline():
    m=model(good_flags()); m.timeline.events[-1].position=m.timeline.events[-1].position.__class__(6,1,1)
    m.timeline.events[1].position=m.timeline.events[1].position.__class__(7,1,1)
    report=SongAnalyzer().analyze(m,config())
    assert report.summary.bars==6 and "structure.after_end" in ids(report)
