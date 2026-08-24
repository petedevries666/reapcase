"""Fast, read-only Song diagnostics.

This module deliberately has no Tk dependency.  Rules consume the current
Timeline projection, making unsaved edits visible without rewriting Stadium's
document.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

from .stadium import MusicalPosition


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True)
class AnalysisResult:
    severity: Severity
    rule_id: str
    title: str
    message: str
    category: str
    event_index: Optional[int] = None
    position: Optional[MusicalPosition] = None
    device: Optional[str] = None
    lane: Optional[str] = None


@dataclass
class AnalysisConfig:
    initialization_before_bar: int = 2
    require_bass_program_zero: bool = True
    require_target_bass_program: bool = True
    bass_preset: int = 1
    require_target_bass_snapshot: bool = True
    bass_snapshot: int = 1
    require_exp_1: bool = True
    require_exp_2: bool = True
    enforce_start_order: bool = True
    require_clear_loop: bool = True
    reject_current: bool = True
    exp_rest_value: int = 0
    check_exp_end: bool = True
    max_hold_bars: float = 8.0
    close_tolerance_ticks: int = 15
    require_end_clear: bool = False
    require_end_stop: bool = False

    @classmethod
    def from_dict(cls, value: Any) -> "AnalysisConfig":
        if not isinstance(value, dict): return cls()
        known = cls.__dataclass_fields__
        return cls(**{key: val for key, val in value.items() if key in known})

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class SongSummary:
    name: str
    bars: int
    duration_seconds: float
    bpm: Optional[float]
    time_signature: str
    regions: int
    counts: dict[str, int]
    looper_actions: dict[str, dict[str, int]]
    first_position: Optional[MusicalPosition]
    last_position: Optional[MusicalPosition]
    end_position: Optional[MusicalPosition]


@dataclass
class AnalysisContext:
    model: Any
    config: AnalysisConfig
    ordered: list[tuple[int, Any]]
    units: dict[int, int]
    end_units: int


class AnalysisRule(Protocol):
    rule_id: str
    def analyze(self, context: AnalysisContext) -> list[AnalysisResult]: ...


def _pos(p: MusicalPosition) -> str:
    return f"{p.bar}.{p.beat}.{p.tick - 1:02d}"


def _result(severity, rule, title, message, category, pair=None, device=None):
    index, event = pair if pair else (None, None)
    return AnalysisResult(severity, rule, title, message, category, index,
                          event.position if event else None, device,
                          device)


def _identity(model, event):
    data = event.data; alias = data.get("rig_alias") or {}; kind = event.source.type
    if alias:
        return alias.get("system"), alias.get("action"), alias.get("expression"), alias.get("snapshot", data.get("value"))
    if kind == "PRESETSNAP": return "stadium", "snapshot", None, data.get("snapshot")
    if kind == "LOOPER": return "stadium", "looper", None, str(data.get("action", "")).casefold()
    if kind == "MIDI_BANK_PROGRAM": return "second_helix" if data.get("channel") == 3 else "midi", "program", (data.get("bank_msb"), data.get("bank_lsb")), data.get("program")
    return model.lane(event), kind, data.get("label"), tuple(sorted((k, str(v)) for k, v in data.items() if k != "rig_alias"))


class StartRule:
    rule_id = "start.initialization"
    def analyze(self, c):
        cfg, found, output = c.config, [], []
        requirements = []
        if cfg.require_bass_program_zero: requirements.append(("BASS PRG CHANGE = 0", lambda e: e.source.type == "MIDI_BANK_PROGRAM" and e.data.get("channel") == 3 and e.data.get("program") == 0))
        if cfg.require_target_bass_program: requirements.append((f"BASS PRG CHANGE = {cfg.bass_preset}", lambda e: e.source.type == "MIDI_BANK_PROGRAM" and e.data.get("channel") == 3 and e.data.get("program") == cfg.bass_preset))
        if cfg.require_target_bass_snapshot: requirements.append((f"BASS SNAP = {cfg.bass_snapshot}", lambda e: (e.data.get("rig_alias") or {}).get("system") == "second_helix" and (e.data.get("rig_alias") or {}).get("action") == "snapshot" and (e.data.get("rig_alias") or {}).get("snapshot") == cfg.bass_snapshot))
        for pedal, enabled in ((1, cfg.require_exp_1), (2, cfg.require_exp_2)):
            if enabled: requirements.append((f"EXP PDL {pedal} = 0", lambda e, p=pedal: (e.data.get("rig_alias") or {}).get("action") == "expression" and (e.data.get("rig_alias") or {}).get("expression") == p and e.data.get("value") == 0))
        for label, predicate in requirements:
            all_matches = [pair for pair in c.ordered if predicate(pair[1])]
            timely = [pair for pair in all_matches if pair[1].position.bar < cfg.initialization_before_bar]
            if timely: found.append(timely[0]); continue
            why = "occurs too late" if all_matches else "is missing"
            pair = all_matches[0] if all_matches else None
            output.append(_result(Severity.ERROR, self.rule_id, "Second Helix initialization incomplete", f"{label} {why} before {cfg.initialization_before_bar}.1.00", "START", pair, "SECOND HELIX"))
        if cfg.enforce_start_order and len(found) == len(requirements) and [c.units[i] for i, _ in found] != sorted(c.units[i] for i, _ in found):
            output.append(_result(Severity.ERROR, "start.order", "Second Helix initialization out of order", "Required initialization events do not follow the configured sequence.", "START", found[0], "SECOND HELIX"))
        clears = [p for p in c.ordered if p[1].source.type == "LOOPER" and "clear" in str(p[1].data.get("action", "")).casefold()]
        if cfg.require_clear_loop and not any(e.position.bar < cfg.initialization_before_bar for _, e in clears):
            output.append(_result(Severity.ERROR, "start.clear_loop", "Stadium initialization incomplete", f"CLEAR LOOP is missing before {cfg.initialization_before_bar}.1.00", "START", clears[0] if clears else None, "STADIUM"))
        return output


class CurrentAndMidiRule:
    rule_id = "state.explicit"
    def analyze(self, c):
        out=[]
        for pair in c.ordered:
            _, e=pair; d=e.data; kind=e.source.type
            if c.config.reject_current and kind in {"PRESETSNAP", "START", "MARKER"}:
                for field in ("preset", "snapshot"):
                    if str(d.get(field, "")).strip().casefold() == "current":
                        out.append(_result(Severity.ERROR, f"state.current_{field}", f"Stadium {field.upper()} CURRENT", f"Stadium {field.upper()} CURRENT found at {_pos(e.position)}. Use an explicit state instead.", "STADIUM", pair, "STADIUM"))
            if kind == "MIDI_BANK_PROGRAM":
                values=[d.get("channel"),d.get("bank_msb"),d.get("bank_lsb"),d.get("program")]
                if any(v is not None and (not isinstance(v,int) or not 0 <= v <= 127) for v in values): out.append(_result(Severity.ERROR,"midi.range","Invalid MIDI value",f"Program Change contains a value outside 0..127 at {_pos(e.position)}.","MIDI",pair))
                if d.get("bank_msb") == d.get("bank_lsb") == 0: out.append(_result(Severity.ERROR,"midi.program_zero_bank","Invalid PRG CHANGE 0/0",f"Invalid low-level PRG CHANGE bank 0/0 at {_pos(e.position)}.","MIDI",pair))
        return out


class StatefulRule:
    rule_id="stateful"
    def analyze(self,c):
        out=[]; exp={}; loop={"recorded":False,"active":False,"last":None}
        threshold=int(c.config.max_hold_bars*c.model.song.ppqn*max(1,c.model.numerator))
        for pair in c.ordered:
            i,e=pair; alias=e.data.get("rig_alias") or {}; action=str(alias.get("action") or e.data.get("action") or "").casefold().replace("_"," ")
            pedal = alias.get("expression") if alias.get("action") == "expression" else (
                e.data.get("cc") if e.source.type == "MIDI_CC" and
                e.data.get("channel") == 3 and e.data.get("cc") in (1, 2) else None)
            if pedal is not None:
                value=e.data.get("value"); previous=exp.get(pedal)
                if previous and previous[0] == 127 and c.units[i]-previous[1] >= threshold: out.append(_result(Severity.WARNING,"expression.long_max","Expression held at maximum",f"Second Helix EXP PDL {pedal} stays at 100% from {_pos(previous[2].position)} to {_pos(e.position)} ({(c.units[i]-previous[1])/(c.model.song.ppqn*c.model.numerator):.1f} bars).","EXPRESSION",previous[3],"SECOND HELIX"))
                exp[pedal]=(value,c.units[i],e,pair)
            is_loop=e.source.type=="LOOPER" or action in {"record","rec","play","play once","stop","clear","clear loop"}
            if is_loop:
                if action in {"record","rec"}:
                    if loop["last"] in {"record","rec"}: out.append(_result(Severity.WARNING,"looper.repeated_rec","Repeated looper REC",f"REC at {_pos(e.position)} follows REC without PLAY, STOP, or CLEAR.","LOOPER",pair))
                    loop.update(recorded=True,active=True,last=action)
                elif action.startswith("play"):
                    if not loop["recorded"]: out.append(_result(Severity.WARNING,"looper.play_without_rec","Looper PLAY without REC",f"Stadium LOOPER PLAY at {_pos(e.position)} has no previous REC in this Song. This may be intentional if recording is performed manually.","LOOPER",pair))
                    loop.update(active=True,last=action)
                elif "clear" in action: loop.update(recorded=False,active=False,last="clear")
                elif action=="stop":
                    if not loop["active"]: out.append(_result(Severity.INFO,"looper.inactive_stop","STOP with no active looper",f"LOOPER STOP at {_pos(e.position)} occurs while no looper is known active.","LOOPER",pair))
                    loop.update(active=False,last=action)
        for pedal,last in exp.items():
            if c.config.check_exp_end and last[0] != c.config.exp_rest_value: out.append(_result(Severity.WARNING,"end.expression_rest","Expression not at resting value",f"Second Helix EXP PDL {pedal} finishes at {round(last[0]*100/127)}%. Last change: {_pos(last[2].position)}. Expected resting value: {c.config.exp_rest_value}","END",last[3],"SECOND HELIX"))
        if loop["active"]: out.append(_result(Severity.WARNING,"end.looper_active","Looper active at Song end","Song ends while the looper appears active or recording.","END"))
        return out


class TimingRule:
    rule_id="timing.collisions"
    def analyze(self,c):
        out=[]
        for a,b in zip(c.ordered,c.ordered[1:]):
            ia,ea=a; ib,eb=b; delta=c.units[ib]-c.units[ia]; ka=_identity(c.model,ea); kb=_identity(c.model,eb)
            if delta==0 and ka==kb: out.append(_result(Severity.WARNING,"timing.duplicate","Exact duplicate event",f"Duplicate {ka[1]} command at {_pos(eb.position)}.","TIMING",b))
            elif delta==0 and ka[:3]==kb[:3] and ka[3]!=kb[3]: out.append(_result(Severity.ERROR,"timing.conflict","Conflicting simultaneous events",f"Same-device {ka[1]} events set conflicting values at {_pos(eb.position)}.","TIMING",b))
            elif 0 < delta <= c.config.close_tolerance_ticks and ka[:2]==kb[:2]: out.append(_result(Severity.INFO,"timing.close","Events unusually close",f"Two {ka[0]} {ka[1]} events occur unusually close together: {_pos(ea.position)} and {_pos(eb.position)}.","TIMING",b))
        return out


class StructureRule:
    rule_id="structure.end"
    def analyze(self,c):
        ends=[p for p in c.ordered if p[1].source.type=="END"]
        if not ends: return [_result(Severity.ERROR,self.rule_id,"END marker missing","Song has no canonical END marker.","STRUCTURE")]
        end=ends[-1]; out=[]
        for pair in c.ordered:
            if c.units[pair[0]] > c.units[end[0]]: out.append(_result(Severity.ERROR,"structure.after_end","Event after END",f"Event at {_pos(pair[1].position)} occurs after END.","STRUCTURE",pair))
        return out


class SongAnalyzer:
    """Run independently-testable rules over one immutable view of live events."""
    def __init__(self, rules=None): self.rules=tuple(rules or (StartRule(),CurrentAndMidiRule(),StatefulRule(),TimingRule(),StructureRule()))
    def analyze(self, model, config=None):
        config=config or AnalysisConfig(); ordered=sorted(enumerate(model.timeline.events),key=lambda p:(model._units(p[1].position),p[0])); units={i:model._units(e.position) for i,e in ordered}; ends=[units[i] for i,e in ordered if e.source.type=="END"]; context=AnalysisContext(model,config,ordered,units,ends[-1] if ends else model.song_end_units)
        results=[]
        for rule in self.rules: results.extend(rule.analyze(context))
        return AnalysisReport(self._summary(context),tuple(sorted(results,key=lambda r:(list(Severity).index(r.severity),r.position or MusicalPosition(999999,1,1)))) )
    def _summary(self,c):
        counts={}; loops={"Stadium":{},"Second Helix":{}}
        for _,e in c.ordered:
            lane=c.model.lane(e); counts[lane]=counts.get(lane,0)+1; alias=e.data.get("rig_alias") or {}; action=str(alias.get("action") or e.data.get("action") or "")
            if e.source.type=="LOOPER" or action.casefold() in {"record","play","stop","clear loop","clear","play once"}:
                device="Second Helix" if alias.get("system")=="second_helix" else "Stadium"; loops[device][action.upper()]=loops[device].get(action.upper(),0)+1
        events=[e for _,e in c.ordered]; end=next((e.position for e in reversed(events) if e.source.type=="END"),None); bars=end.bar if end else (events[-1].position.bar if events else 0); duration=c.model.timing_map.units_to_seconds(c.end_units) if hasattr(c.model.timing_map,"units_to_seconds") else 0
        from .editor.structure import derive_structure_layout
        regions=len(derive_structure_layout(events,c.model._units,c.end_units).regions)
        return SongSummary(str(c.model.song.name),bars,duration,c.model.tempo,f"{c.model.numerator}/{c.model.denominator}",regions,counts,loops,events[0].position if events else None,events[-1].position if events else None,end)


@dataclass(frozen=True)
class AnalysisReport:
    summary: SongSummary
    results: tuple[AnalysisResult,...]
