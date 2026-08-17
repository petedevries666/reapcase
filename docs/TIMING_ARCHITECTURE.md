# Canonical musical timing

`TimingMap` is the editor's derived, read-only authority for musical geometry.
It indexes START/TIME changes as piecewise-constant segments containing their
musical start, absolute unit start, elapsed-second start, tempo, and signature.
Bar/signature and tempo/seconds searches use binary search. Source flags are
never rewritten to build the map.

Bar shifts preserve beat and tick and reject a move when the destination bar
has fewer beats. Beat shifts are absolute beat-count movement, while tick
shifts are absolute unit movement. Dragging always applies an absolute-unit
delta before converting through the map.

## Fixture timing audit

| Song | START | TIME events | Classification |
| --- | --- | --- | --- |
| MONZTER | 141 BPM, 6/4 | none | constant |
| PERFECT PICTURE | 102 BPM, 4/4 | `005-02.052`: 123 BPM, 4/4; `010-01.001`: 123 BPM, 4/4 | tempo-only, then unchanged restatement |
| CLOCKSICK | 200 BPM, 4/4 | none | constant |
| WANNA BE | 89 BPM, 4/4 | none | constant |
| LATE NIGHT PARTY | 160 BPM, 4/4 | none | constant |

The audit is fixture-driven and does not alter fixture JSON or opaque Stadium
payloads. No-op serialization therefore retains the exact original text.
