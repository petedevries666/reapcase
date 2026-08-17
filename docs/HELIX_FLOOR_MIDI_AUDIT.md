# Helix Floor 3.80 MIDI Audit

## Scope, source, and evidence

This audit covers the second Helix Floor in the ANONYMALZ rig. Its primary authority is the official Line 6 **Helix 3.80 Owner's Manual**, published through the [Helix manuals index](https://line6.com/support/manuals/helix/), including its MIDI implementation and Command Center chapters. It does not use community MIDI charts.

The evidence vocabulary is shared with the Stadium audit: `vendor_documented`, `fixture_observed`, `rig_defined`, explicit `+` combinations, and `unknown`. The manual establishes generic Helix Floor capability. `config/rig_midi.json` establishes our stage choices. Showcase fixtures establish only messages that our Songs actually send.

## Capability and rig comparison

**HELIX FLOOR CAPABILITY** has no fixed MIDI channel in this audit. Helix receives on its configured MIDI Base Channel (or Omni where configured), and the generic capability inventory therefore contains no `channel` property.

**ANONYMALZ SECOND HELIX CONFIGURATION** assigns the second Helix to **channel 3** in `rig_midi.json`. Thus vendor “CC69 selects snapshots” becomes rig-specific “channel 3 + CC69 selects snapshots.” Channel 3 is our routing convention, not a universal Helix behavior.

| Function | MIDI | Values | Vendor documented | Fixture observed | Current Reapcase |
|---|---|---|---|---|---|
| Preset recall (receive) | CC32 then PC | CC32 0–7 setlist; PC 0–127 preset | Yes | Bank fields are `Off`; PC values are observed in outgoing flags | Not decoded as incoming second-Helix control |
| Snapshot select | CC69 | 0–7 = Snapshots 1–8 | Yes | Yes: Ch3 values 0–7 | Configured and decoded |
| Snapshot next/previous | CC69 | 8 next; 9 previous | Yes | No | Not configured for second Helix |
| Looper Record/Overdub | CC60 | 0–63 Overdub; 64–127 Record | Yes | Yes: values 6 and 127 | Configured |
| Looper Play/Stop | CC61 | 0–63 Stop; 64–127 Play | Yes | Yes: values 0/1 and 66/126/127 | Configured |
| Looper Play Once | CC62 | 64–127 active | Yes | No | Configured high range |
| Looper Undo/Redo | CC63 | 64–127 active | Yes | No | Configured high range |
| Tap Tempo | CC64 | 64–127 active | Yes | No | Configured high range; low is no-op |
| Looper Forward/Reverse | CC65 | 0–63 Forward; 64–127 Reverse | Yes | No | Configured |
| Looper Full/Half Speed | CC66 | 0–63 Full; 64–127 Half | Yes | No | Configured |
| Looper block Bypass/Enable | CC67 | 0–63 bypass; 64–127 enable | Yes | No | Configured as Off/On |
| Tuner | CC68 | 0–63 off; 64–127 on | Yes | No | CC is configured, but both ranges currently collapse to `Tuner` |
| EXP 1/2/3 emulation | CC1/2/3 | 0–127 position | Yes | No | Not configured |
| FS1–FS11 emulation | CC49–59 | 0–63 release; 64–127 press | Yes | No | Not configured |
| All Bypass | CC70 | 0–63 off; 64–127 on | Yes | No | Not configured |
| Block bypass assignment | User-assigned CC | Preset-specific | Yes | No proven assignment | Generic codec does not model it |
| Parameter control | User-assigned CC | 0–127 mapped to configured min/max | Yes | No proven assignment | Generic codec does not model it |
| MIDI Clock / tempo | MIDI Clock | Global send/receive/off | Yes | No | Not configured |
| Command Center output | CC, CC Toggle, Bank/Program, Note, MMC, hardware commands | Per command | Yes | CC and Bank/Program only | Showcase sends remain generic flags |

“Fixture observed” is intentionally narrow: current Songs demonstrate channel 3 CC69, CC60, CC61, and Bank/Program output. They do **not** demonstrate CC62–68, expression, footswitch emulation, arbitrary block assignments, MIDI Clock, Notes, MMC, or hardware commands.

## Preset recall and message direction

For **messages received by Helix**, CC32 Bank Select LSB values 0–7 select a setlist and the following Program Change value 0–127 selects a preset within it. This is Helix preset recall.

For **messages sent by Helix**, Command Center's Bank/Program command emits Bank MSB/LSB and Program Change according to its configured command. This is an output capability.

Our Showcase `MIDI_BANK_PROGRAM` flags are a third context: Stadium sends Bank/Program to the second Helix during playback. Fixtures use channel 3 and have both bank fields `Off`, so they empirically demonstrate direct PC messages, not bank-selection values. Shared MIDI message names do not make these directions interchangeable.

## Snapshots

CC69 values 0 through 7 select Snapshots 1 through 8 respectively; value 8 selects the next snapshot and value 9 the previous snapshot. The current second-Helix rig mapping implements only direct selection. Fixture evidence includes every direct-select value 0–7, but not next/previous.

## Looper, Tap Tempo, and Tuner

The official low/high boundary is 0–63 versus 64–127. CC60, CC61, CC65, CC66, and CC67 assign an action to each range. CC62 and CC63 act only in the high range. Tap Tempo CC64 is active at 64–127. Tuner CC68 is explicitly stateful: 0–63 turns it off and 64–127 turns it on.

The rig's looper controller numbers and directions agree with the manual. Its CC64 high-only mapping also agrees. Its CC68 entry does **not** preserve the documented off/on distinction: both ranges map to the same `Tuner` alias. This audit records that defect but does not redesign or change the codec/configuration.

## Expression, footswitch, bypass, and parameters

CC1, CC2, and CC3 emulate EXP 1, EXP 2, and EXP 3 over 0–127. CC49 through CC59 emulate FS1 through FS11, with low values representing release/off and high values press/on. These are globally reserved controls, not evidence that our rig uses those messages.

Helix also supports assigning incoming CCs to block bypass and to parameters. Those assignments live in a Helix preset and include parameter-specific minimum/maximum behavior; Reapcase has no fixture or rig evidence for any particular assignment. The vendor inventory records capability only and does not manufacture aliases.

## MIDI Clock and tempo

Helix Global Settings document MIDI Clock receive/send/off behavior and tempo synchronization. This is a real vendor capability, but no current fixture or `second_helix` rig entry establishes its use. It is not the same as Stadium's Showcase `TIME` flag or CC64 Tap Tempo.

## Command Center output inventory

Command Center documents outgoing **MIDI CC**, **MIDI CC Toggle**, **Bank/Program**, **MIDI Note**, and **MMC**, plus model/connector-dependent hardware commands such as **CV Out**, **Ext Amp**, and **Hotkey**. Footswitches and Instant Commands are the documented command sources; the command menu is source- and model-dependent. A snapshot is not a third physical attachment source: preset/snapshot recall sends its Instant Commands and recalls supported footswitch command values or toggle states. This should not be generalized to an undocumented source/type combination.

Only MIDI CC and Bank/Program have fixture evidence in this repository. CV Out is the applicable Helix Floor voltage-control function; “expression” should not be invented as a MIDI message type merely because the hardware has expression connections.

## Reserved controller inventory

The machine-readable table is `config/helix_floor_midi.json`. Reserved controllers must not later be offered as if they were unconditionally free user CCs.

| CC | Category | Official function |
|---|---|---|
| 0 | Global | Bank Select MSB |
| 1–3 | Expression | EXP 1–3 emulation |
| 32 | Global | Bank Select LSB / setlist |
| 49–59 | Footswitch | FS1–FS11 emulation |
| 60–63, 65–67 | Looper | Looper commands |
| 64 | Tap | Tap Tempo |
| 68 | Tuner | Tuner off/on |
| 69 | Snapshot | Select 1–8, next, previous |
| 70 | Global | All Bypass |

User assignments should use non-reserved controller numbers. Because MIDI standards and Helix's own reserved controls constrain the choice, the capability file does not claim an unverified single contiguous “free” range.

## Discrepancy report

### 1. Vendor mappings confirmed by our rig

CC69 direct snapshot selection, CC60 Record/Overdub, CC61 Play/Stop, CC62 Play Once, CC63 Undo/Redo, CC64 Tap Tempo, CC65 Forward/Reverse, CC66 Full/Half Speed, and CC67 looper block Off/On use the official controller numbers and ranges in `rig_midi.json`. Fixtures independently confirm only CC69, CC60, CC61, and outgoing Program Change behavior.

### 2. Vendor capabilities we do not currently use

Snapshot next/previous, expression emulation, footswitch emulation, All Bypass, arbitrary block bypass and parameter assignments, MIDI Clock, MIDI Note, MMC, CC Toggle, CV Out, Ext Amp, and Hotkey have no current second-Helix rig mapping. Command Center send behavior is also not modeled as a second-Helix decoder feature.

### 3. Rig behavior not clearly documented

Fixture labels and values describe intended show behavior but do not prove the receiving preset's internal assignments. Program-only fixture messages with Bank MSB/LSB `Off`, and action labels such as “VOICE LOOP,” are observed stage choices rather than additions to the Helix MIDI specification.

### 4. Current Reapcase mapping needing correction

CC68 currently maps both low and high ranges to the identical `Tuner` action. The vendor definition is 0–63 **Tuner Off** and 64–127 **Tuner On**. A future focused codec/config correction should introduce distinct, round-trippable actions and regression tests. No such behavior change is included in this documentation audit.
