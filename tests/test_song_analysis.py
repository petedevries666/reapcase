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
    cfg=AnalysisConfig(bass_snapshot=2)
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
    wrong=good_flags(); del wrong[2]
    assert any("BASS PRG CHANGE != 0" in r.message for r in SongAnalyzer().analyze(model(wrong),config()).results)
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


def test_missing_stadium_clear_loop_and_independent_expression_initialization():
    no_clear=[f for f in good_flags() if "LOOPER" not in f]
    assert "start.clear_loop" in ids(SongAnalyzer().analyze(model(no_clear),config()))
    for label in ("EXP PDL 1", "EXP PDL 2"):
        report=SongAnalyzer().analyze(model([f for f in good_flags() if label not in f]),config())
        assert any(label in r.message for r in report.results if r.rule_id=="start.initialization")


def test_looper_state_is_completely_device_specific():
    base=good_flags()[:-1]
    # Real rig mapping: Second Helix CC60 high=Record cannot satisfy Stadium PLAY.
    stadium_play=base+["002-01.001|MIDI_CC;HELIX REC;4;CC;3;60;127",
                       "003-01.001|LOOPER;PLAY;1;Play","008-01.001|END;;0"]
    report=SongAnalyzer().analyze(model(stadium_play),config())
    warning=next(r for r in report.results if r.rule_id=="looper.play_without_rec")
    assert warning.device=="Stadium" and "Stadium" in warning.message
    # A Stadium REC likewise cannot satisfy Second Helix CC61 high=Play.
    helix_play=base+["002-01.001|LOOPER;RECORD;1;Record",
                    "003-01.001|MIDI_CC;HELIX PLAY;4;CC;3;61;127","008-01.001|END;;0"]
    report=SongAnalyzer().analyze(model(helix_play),config())
    warning=next(r for r in report.results if r.rule_id=="looper.play_without_rec")
    assert warning.device=="Second Helix" and "Second Helix" in warning.message
    end_devices={r.device for r in report.results if r.rule_id=="end.looper_active"}
    assert end_devices=={"Stadium", "Second Helix"}


def test_real_configured_expression_mapping_recognizes_non_extreme_values():
    m=model(good_flags())
    mappings=dict(m.decoder.second_helix_expressions())
    assert mappings[1]==1 and mappings[2]==2
    flags=good_flags()[:-1]+[f"007-01.001|MIDI_CC;EXP;4;CC;3;{mappings[2]};64",
                            "008-01.001|END;;0"]
    report=SongAnalyzer().analyze(model(flags),config())
    assert report.summary.inventory["Second Helix expression events"]==3
    assert any("EXP PDL 2" in r.message for r in report.results if r.rule_id=="end.expression_rest")


def test_simultaneous_device_isolation_and_same_device_conflict():
    legitimate=good_flags()[:-1]+["003-01.001|PRESETSNAP;;3;SET;PRESET;Snap 2",
        "003-01.001|MIDI_CC;BASS SNAP 3;4;CC;3;69;2","008-01.001|END;;0"]
    assert "timing.conflict" not in ids(SongAnalyzer().analyze(model(legitimate),config()))
    conflict=good_flags()[:-1]+["003-01.001|MIDI_CC;BASS SNAP 2;4;CC;3;69;1",
        "003-01.001|MIDI_CC;BASS SNAP 3;4;CC;3;69;2","008-01.001|END;;0"]
    assert "timing.conflict" in ids(SongAnalyzer().analyze(model(conflict),config()))


def test_current_fields_are_diagnosed_independently():
    preset=good_flags()[:-1]+["003-01.001|PRESETSNAP;;3;SET;CURRENT;Snap 2","008-01.001|END;;0"]
    assert {r.rule_id for r in SongAnalyzer().analyze(model(preset),config()).results if "current" in r.rule_id}=={"state.current_preset"}
    snap=good_flags()[:-1]+["003-01.001|PRESETSNAP;;3;SET;PRESET;CURRENT","008-01.001|END;;0"]
    assert {r.rule_id for r in SongAnalyzer().analyze(model(snap),config()).results if "current" in r.rule_id}=={"state.current_snapshot"}


def test_analysis_sidecar_round_trip(tmp_path):
    path=tmp_path/"song.json"; path.write_text(StadiumSong.from_dict({"name":"x","ppqn":240,"params":None,"flags":good_flags(),"tracks":[]}).to_json_text())
    m=EditorModel.open(path); configured=config(require_bass_program_nonzero=False,max_hold_bars=3.5)
    m.set_analysis_config(configured); m.save_as(path)
    restored=EditorModel.open(path).analysis_config()
    assert not restored.require_bass_program_nonzero and restored.max_hold_bars==3.5
    assert "analysis" not in StadiumSong.from_json_text(path.read_text()).to_dict()


def test_song_summary_semantic_inventory_and_positions():
    flags=good_flags()[:-1]+["002-01.001|PRESETSNAP;;3;SET;PRESET;Snap 2",
        "003-01.001|MIDI_CC;HELIX REC;4;CC;3;60;127","008-01.001|END;;0"]
    summary=SongAnalyzer().analyze(model(flags),config()).summary
    assert summary.inventory["Stadium snapshots"]==1
    assert summary.inventory["Stadium presets"]==1
    assert summary.inventory["Second Helix snapshots"]==1
    assert summary.inventory["Second Helix program changes"]==2
    assert summary.inventory["Second Helix expression events"]==2
    assert summary.inventory["Second Helix looper actions"]==1
    assert summary.first_position.render()=="001-01.001"
    assert summary.last_position.render()==summary.end_position.render()=="008-01.001"


def test_second_helix_start_accepts_any_later_nonzero_program():
    for program in (1, 17):
        flags=[f.replace(";Off;Off;4", f";Off;Off;{program}") for f in good_flags()]
        assert "start.initialization" not in ids(SongAnalyzer().analyze(model(flags),config()))


def test_second_helix_program_pair_is_required_and_ordered():
    no_nonzero=[f for f in good_flags() if "BASS PRG 4" not in f]
    assert "start.initialization" in ids(SongAnalyzer().analyze(model(no_nonzero),config()))
    no_zero=[f for f in good_flags() if "BASS PRG 0" not in f]
    assert "start.initialization" in ids(SongAnalyzer().analyze(model(no_zero),config()))
    reversed_pair=good_flags()
    reversed_pair[1]=reversed_pair[1].replace("001-01.001", "001-01.020")
    reversed_pair[2]=reversed_pair[2].replace("001-01.020", "001-01.001")
    assert "start.order" in ids(SongAnalyzer().analyze(model(reversed_pair),config()))


def test_real_clocksick_program_representation_is_canonical_and_counted():
    real="001-03.001|MIDI_BANK_PROGRAM;BASS PRG;5;Bank/Prog;3;Off;Off;17"
    flags=[real if "BASS PRG 4" in f else f for f in good_flags()]
    report=SongAnalyzer().analyze(model(flags),config())
    assert "start.initialization" not in ids(report)
    assert report.summary.inventory["Second Helix program changes"] == 2


def test_marker_collision_identity_distinguishes_structural_subtypes():
    base=good_flags()[:-1]
    region="003-01.001|MARKER;VERSE;7;Off;Off;Off;false;SET;PRESET;Snap 1"
    pause="003-01.001|MARKER;PAUSE;7;Off;On;Off;false;SET;PRESET;Snap 1"
    report=SongAnalyzer().analyze(model(base+[region,pause,"008-01.001|END;;0"]),config())
    assert not any(r.rule_id in {"timing.conflict", "timing.duplicate"} and r.position.bar == 3 for r in report.results)

    duplicate=SongAnalyzer().analyze(model(base+[pause,pause,"008-01.001|END;;0"]),config())
    assert any(r.rule_id == "timing.duplicate" and r.position.bar == 3 for r in duplicate.results)

    other_region=region.replace(";VERSE;", ";CHORUS;")
    compatible=SongAnalyzer().analyze(model(base+[region,other_region,"008-01.001|END;;0"]),config())
    assert not any(r.rule_id == "timing.conflict" and r.position.bar == 3 for r in compatible.results)

    duplicate_region=SongAnalyzer().analyze(model(base+[region,region,"008-01.001|END;;0"]),config())
    assert any(r.rule_id == "timing.duplicate" and r.position.bar == 3 for r in duplicate_region.results)
