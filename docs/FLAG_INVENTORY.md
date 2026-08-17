# Empirical Stadium Flag Inventory

This inventory is derived from all five unmodified Showcase fixtures. The machine-readable source is [`config/stadium_flag_inventory.json`](../config/stadium_flag_inventory.json); it records field-count/shape variants, examples, and detailed per-fixture reports.

Fields below are zero-based after splitting the exact payload at `;`. Empty fields are significant. Every parser view retains the original payload unchanged.

| Type | Fields | Empirically interpreted layout |
|---|---:|---|
| `START` | 12 | 3 tempo, 5/6 signature, 9 setlist, 10 preset, 11 snapshot |
| `END` | 8 | End boundary; opaque options retained |
| `TIME` | 7 | 1 label, 3 tempo, 5/6 signature |
| `MARKER` | 10 | 1 name, 3 count-in, 4 pause, 5 cycle, 6 recalls-preset, 7–9 target |
| `PRESETSNAP` | 6 | 3 setlist, 4 preset, 5 snapshot |
| `MIDI_CC` | 7 | 1 label, 4 channel, 5 CC, 6 value |
| `MIDI_BANK_PROGRAM` | 8 | 1 label, 4 channel, 5/6 bank MSB/LSB (`Off` observed), 7 program |
| `LOOPER` | 4 | 1 exact label, 3 exact action |
| `CYCLE_START` | 5 | 3 repeat (`Infinite` observed), 4 option |
| `CYCLE_END` | 3 | cycle closing boundary |

## Structural and semantic variants

Marker option tuples observed are `Off/Off/Off/false` and `Off/On/Off/false`; preset targets include `[Current]` and explicit values. Native looper actions are **Clear Loop**, **Record**, **Stop**, **Play**, and **Play Once**, while labels vary in case (`Clear`, `CLEAR`, `REC`, `RECORD`). Bank fields are `Off` throughout this corpus. `TIME` occurs only in PERFECT PICTURE; cycle boundaries only in CLOCKSICK.

Across the fixtures, MARKER field 4 is the only changing option in examples
created with the vendor's **Pause at Marker** control (`Off`/`On`); it is the
only advanced marker option Reapcase authors. PRESETSNAP field 3 is the setlist,
field 4 the preset, and field 5 the `Snap 1`–`Snap 8` selection. Snapshot
authoring therefore inherits fields 3–4 from the active, explicit START or
PRESETSNAP context and changes only field 5. The fixture corpus does not provide
a safe arbitrary preset-selection representation, so Preset Change is omitted.

Rig decoding is external configuration, not Stadium syntax. CLOCKSICK contains second-Helix CC60 Record/Overdub and CC61 Play/Stop alongside no native `LOOPER`. PERFECT PICTURE has no CC60 Record and its CC61 values 66, 127, 0 decode to Play, Play, Stop; this suspicious but valid sequence is preserved in its report. Video examples include Ch16 video 4/6/8 one-shot or loop commands.

## Per-fixture reports

The `reports` object in the machine inventory lists, for each fixture: exact flag count/type counts, tempo/signature entries, marker option variants, native looper events, external Helix MIDI, and video MIDI. Counts are MONZTER 21, PERFECT PICTURE 33, CLOCKSICK 32, WANNA BE 46, and LATE NIGHT PARTY 47.
