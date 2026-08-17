# Showcase Flag Mapping Matrix

This matrix separates the vendor's UI contract from the empirical Song JSON contract. “Observed JSON” names only types present in the five unmodified fixtures; an empty entry is not a proposed type name. The authoritative machine inventory is [`vendor_capabilities.json`](../config/vendor_capabilities.json).

| Vendor Feature | Observed JSON | Current parser | Evidence | Action |
|---|---|---|---|---|
| Start | `START` | Partial: tempo, signature, setlist/preset/snapshot | `vendor_documented+fixture_observed` | Retain lossless remainder |
| End | `END` | Partial: label only | `vendor_documented+fixture_observed` | Keep fade/playback options opaque until mapped by fixtures |
| Marker | `MARKER` | Partial: name and tentative option/recall fields | `vendor_documented+fixture_observed` | Validate field associations with controlled exports |
| Cycle | `CYCLE_START`, `CYCLE_END` | Partial: repeat and opaque option | `vendor_documented+fixture_observed` | Validate option semantics with controlled exports |
| Preset/Snap | `PRESETSNAP` | Partial: setlist/preset/snapshot | `vendor_documented+fixture_observed` | Retain current view |
| Looper | `LOOPER` | Partial: label/action strings | `vendor_documented+fixture_observed` | Keep native flags separate from MIDI remotes |
| Utility | — | Unsupported semantically; generic preservation only | `vendor_documented` | Acquire real exports; no speculative serializer |
| Ext Amp | — | Unsupported semantically; generic preservation only | `vendor_documented` | Acquire real exports; no speculative serializer |
| MIDI: Bank/Program | `MIDI_BANK_PROGRAM` | Partial: channel, MSB, LSB, program | `vendor_documented+fixture_observed` | Keep outgoing flag distinct from incoming preset recall |
| MIDI: CC | `MIDI_CC` | Partial: channel, CC, value | `vendor_documented+fixture_observed` | Retain current view |
| MIDI: MMC | — | Unsupported semantically; generic preservation only | `vendor_documented` | Acquire real export; do not invent payload fields |
| Hotkey | — | Unsupported semantically; generic preservation only | `vendor_documented` | Acquire real exports; no speculative serializer |
| Time | `TIME` | Partial: label, tempo, signature | `vendor_documented+fixture_observed` | Retain current view |

## Reading evidence

* `vendor_documented` means Line 6 documents the capability or behavior, not its private JSON layout.
* `fixture_observed` means a literal serialized type/payload occurs in the checked-in corpus.
* `rig_defined` means a mapping exists in `config/rig_midi.json`; it is not evidence of a Showcase payload.
* `unknown` is required when none of those sources establishes a claim. Evidence tokens may be joined with `+` only when each source independently applies.

The generic `StadiumFlag` parser splits only the position delimiter and retains the entire payload. Consequently, future types remain round-trippable even though the four vendor-only families above deliberately have no semantic handler.
