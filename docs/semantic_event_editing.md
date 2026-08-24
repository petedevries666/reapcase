# Semantic event editing milestone

## Capability matrix

| Family | Source type | Editable properties |
|---|---|---|
| Structure marker | `MARKER` | name, Pause at Marker, Cycle Marker |
| Explicit cycle start | `CYCLE_START` | fixture-proven count and option values |
| Stadium snapshot | `PRESETSNAP` | snapshot 1–8 |
| Stadium looper | `LOOPER` | Clear Loop, Record, Stop, Play, Play Once |
| Second Helix snapshot/expression/looper | `MIDI_CC` | semantic command properties and event channel |
| Second Helix preset | `MIDI_BANK_PROGRAM` | MSB, LSB, program, event channel |
| Video command | `MIDI_CC` | video, action, event channel |
| Generic MIDI | `MIDI_CC` | channel, CC, value |
| Lighting | show-layer `LIGHTS` | name (kind is fixed) |
| Instruction | show-layer instruction | label, sample ID, muted |

`START`, `TIME`, `CYCLE_END`, and derived `SEQCLICK` points are intentionally
non-editable. START/TIME edits require validated timing-map reconstruction;
CYCLE_END has no proven editable property; SEQCLICK timing is derived from that map.
Native `END` flags expose Stadium fade, gap, and End of Song behavior fields while
retaining their structural position and any unknown trailing payload fields.

## Dispatch and replacement

`editor_for_event` is the single dispatch point. It gives Tk a semantic family,
initial values, title, and pure mutation function. Device aliases are considered
before generic MIDI, so a Helix snapshot never falls through to a raw CC form.
`EditorModel.edit_event` replaces exactly one timeline entry at its existing index
and position. It retains a deep copy for Undo and retains the prior selection,
including a multi-selection, while changing only the invoked event.

## Native-field audit and preservation

The real fixtures establish the ten-field marker layout parsed by `StadiumFlag`:
field 1 is name, field 4 is Pause at Marker, and field 5 is Cycle Marker. Fields 3,
6, 7, 8, and 9 carry count-in, preset recall, setlist, preset, and snapshot context.
Semantic mutations split the original payload and replace only established field
offsets, leaving extra future fields byte-for-byte unchanged. Snapshot edits likewise
replace only field 5, preserving their native setlist and preset context rather than
recomputing context at the new value.

A marker whose Cycle Marker switch is On remains a `MARKER`. It is separate from an
explicit `CYCLE_START`/`CYCLE_END` region and no editor converts between those types.
The only explicit-cycle values found in controlled fixtures are `Infinite` and `Off`;
the editor deliberately does not invent additional native choices.

## Undo, derived views, and sidecars

Undo restores the exact copied event payload, semantic data, and selection. Redraw
after Save or Undo derives structure, pause, cycle, looper, lighting regions, lane
classification, and labels afresh. Lighting renames retain the stable cue ID and
fixed STATE/HIT kind. Instruction edits retain stable ID and position. Existing
sidecar documents are deep-copied on save, and only the owned `reapcase.lights` and
`reapcase.sequence` collections are replaced; unrelated and future namespaces remain
untouched.
