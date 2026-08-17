# Line 6 Vendor Documentation Audit

## Scope and source discipline

This audit compares the current official Line 6 documentation with five real, unmodified Helix Stadium Showcase Song fixtures. It specifies future work; it does **not** implement a REAPER adapter, alter fixtures, or derive private JSON layouts from UI labels.

Primary sources are the official Stadium pages for [Flags](https://manuals.line6.com/en/helix-stadium/live/flags), [MIDI](https://manuals.line6.com/en/helix-stadium/live/midi), [Flag List](https://manuals.line6.com/en/helix-stadium/live/flag-list), [Global Settings](https://manuals.line6.com/en/helix-stadium/live/global-settings), and [Song View](https://manuals.line6.com/en/helix-stadium/live/song-view). The second rig is checked against the official [Helix Floor manuals](https://line6.com/support/manuals/helix/). No community source is evidence here.

Evidence uses only `vendor_documented`, `fixture_observed`, `rig_defined`, their explicit `+` combinations, and `unknown`. Vendor documentation proves device behavior; fixtures alone prove serialized layout.

## Timing contract

Showcase positions use `Bar-Beat.Tick`. Stadium has **240 ticks per beat**, exact beats begin at tick **`001`**, and quarter-beat points are **`001`, `061`, `121`, `181`**. This confirms the current `MusicalPosition` one-based tick contract and its 240-PPQN fixture validation; it does not imply that flags must lie on that grid. The fixtures contain legitimate arbitrary tick positions (for example `120`, `230`, and `085`). Evidence: `vendor_documented+fixture_observed`.

## Official flag-family inventory

The detailed parameters, statuses, and notes live in the [mapping matrix](FLAG_MAPPING_MATRIX.md) and machine-readable inventory. In summary:

* **Start** — initial tempo, initial time signature, and preset/snapshot recall. `START` is observed and partially parsed. Field identities currently exposed are supported by corpus regularity; opaque positions stay opaque.
* **End** — end position plus fade and playback/end behavior. `END` is observed. The vendor wording confirms the concepts, but not which of the observed eight fields encodes each option, so only label is exposed and options remain opaque.
* **Marker** — Pause at Marker, Count In, Cycle Marker, and preset/snapshot recall are official options. `MARKER` is observed. Name and literal values are fixture facts; associations of fields 3–9 with UI options are inferred from field position, so field order/names must not be rewritten solely from UI wording.
* **Cycle** — observed as `CYCLE_START`/`CYCLE_END`; only repeat and an unlabelled option receive a partial view.
* **Preset/Snap** — observed as `PRESETSNAP`; setlist, preset, and snapshot are partially parsed.
* **Looper** — observed as native `LOOPER` flags; exact fixture action strings are exposed.
* **Utility**, **Ext Amp**, and **Hotkey** — documented families with no fixture examples and no semantic support. Their JSON type names and payload layouts remain `unknown`.
* **MIDI** — the official variants are Bank/Program, CC, and MMC. `MIDI_BANK_PROGRAM` and `MIDI_CC` are observed and partially parsed. MMC is only `vendor_documented`; no JSON type or payload is proposed.
* **Time** — official tempo/time-signature change, observed as `TIME`, with both values partially parsed.

### Marker evidence boundary

The fixtures directly confirm a marker name, option-like strings (`On`/`Off`), a boolean-like recall value, and recall targets. The UI confirms that Pause at Marker, Count In, Cycle Marker, and preset/snapshot recall exist. The correspondence `fields[3]` through `fields[9]` is still **inferred from field position**, not proven by an official serialization specification. Controlled exports toggling one option at a time are required before renaming or reordering fields.

### Start, Time, and End boundary

The corpus and documentation agree on START initial tempo/signature and recall, and TIME tempo/signature changes. END's position is structural (before `|`), while its fade and end/playback controls occur only as opaque literal payload options today. This deliberately preserves the parser's conservative behavior.

## Three distinct looper concepts

1. **Native Showcase `LOOPER` flags** are serialized inside Song JSON and trigger Showcase actions. They are not MIDI CC mappings.
2. **Stadium Global MIDI looper remote control** is received on the configured Global MIDI Channel: CC52 Clear Loop; CC53 Undo/Redo; CC54 Full/Half Speed; CC55 Forward/Reverse; CC58 Overdub/Record; CC59 Stop/Play; CC60 Play Once; CC62 Looper Off/On.
3. **The second Helix Floor looper remote** is an independent mapping (rig channel 3): CC60 Record/Overdub; CC61 Play/Stop; CC62 Play Once; CC63 Undo/Redo; CC65 Forward/Reverse; CC66 Full/Half Speed; CC67 Looper Block On/Off.

The overlapping CC numbers have different meanings, so both configured sets must remain independent. Fixture `MIDI_CC` events aimed at channel 3 provide fixture evidence for the second rig; they do not turn those commands into native `LOOPER` flags.

## Stadium Global MIDI audit

Global MIDI Channel is configurable and defaults to channel 1; the current rig definition uses channel 1. The existing `stadium_transport` entries agree with the official commands: CC10 Song, CC32 Setlist, CC46 Marker (value zero is song start), CC47 Return to Zero, CC48 Cycle, CC49 Previous/Next Song, CC50 Previous/Next Marker, CC51 Play/Pause, CC63 Playlist, CC64 Tap Tempo, and CC69 snapshot selection (values 0–7), next (8), and previous (9). The global looper list above also agrees. Additional vendor MIDI capabilities should be inventoried before implementation rather than added automatically.

### Preset recall direction matters

Stadium can **receive** Bank Select/Program Change to recall its presets. A Showcase `MIDI_BANK_PROGRAM` flag **sends** Bank/Program data during song playback. These have similar message names but opposite roles and are separate capabilities in the machine inventory.

## Findings

### 1. Documented and observed

Start, End, Marker, Cycle, Preset/Snap, native Looper, MIDI Bank/Program, MIDI CC, and Time have both vendor and fixture evidence. Stadium timing also agrees with fixture metadata. Second-Helix commands have vendor, fixture, and rig evidence.

### 2. Documented but not observed

Utility, Ext Amp, Hotkey, and MIDI MMC have no fixture examples. Incoming Stadium Bank/PC preset recall and global control mappings are documented device interfaces, not Song-payload observations.

### 3. Observed but not clearly documented

Private payload type tokens, field counts, reserved numeric fields, boolean/string encodings, `CYCLE_START`'s trailing option, and END's exact option ordering are fixture-observed but not specified by the public UI documentation. They remain empirical or opaque.

### 4. Contradictions and ambiguities

No timing or configured MIDI contradiction was found. Ambiguity remains in MARKER field-to-option associations and most END fields. Fixture tick values such as `120` near a documented quarter subdivision (`121`) show why arbitrary positions must be preserved rather than quantized. UI family names also cannot safely predict JSON type tokens.

### 5. Prerequisites before a REAPER adapter

* Export controlled Songs that toggle one Marker or END option at a time and diff their payloads.
* Capture real Utility, Ext Amp, Hotkey, and MIDI MMC flags, ideally multiple option variants.
* Confirm round trips on those exports before adding any semantic serializer.
* Define an explicit REAPER timing/tempo-map policy that preserves one-based Stadium ticks and off-grid positions.
* Specify directional MIDI routing (Showcase output versus Stadium input) and keep both looper rigs isolated by channel/system.
* Add golden conversion fixtures only after the empirical mappings above are proven.
