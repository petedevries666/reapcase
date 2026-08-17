# Real-world Stadium fixture study

This repository contains real Helix Stadium Showcase Song JSON fixtures supplied from the working ANONYMALZ live rig. They are evidence, not examples to normalize.

## Current fixtures

- `monzter_332.json`: baseline fixture.
- `perfect_picture_336.json`: contains `TIME` flags and MIDI CC looper control of a second Helix.
- `wanna_be_429.json`: contains native Stadium `LOOPER` flags (`CLEAR`, `REC`, `STOP`, `PLAY`) plus section markers and second-Helix bass control.
- `late_night_party_431.json`: contains native Stadium `LOOPER` variants including `RECORD`, `PLAY ONCE`, `STOP`, `CLEAR`.
- `clocksick_453.json`: contains `CYCLE_START`, `CYCLE_END`, pause markers and second-Helix looper MIDI CC commands.

## Non-negotiable principle

The Stadium JSON is the source of truth. A no-op round trip must preserve the source exactly. Unknown fields and unknown flag payloads must survive unchanged. Moving an event may alter its musical-position prefix but must not casually normalize the payload after `|`.

## Rig-specific MIDI knowledge

These conventions belong in configuration/aliases, not in the generic Stadium parser:

- MIDI channel 3, CC69 controls snapshot selection on the second Helix used for voice/bass. Values 0-7 correspond to snapshots 1-8.
- The second Helix looper is also driven over MIDI channel 3 using Helix looper CCs. Real fixtures contain CC60 and CC61 examples. Do not infer semantics solely from human flag labels; preserve the exact channel, CC and value.
- Video-start MIDI is another external-device convention and must not be baked into the Stadium parser.

## What Codex should discover before the REAPER adapter

Across every fixture:

1. Verify exact lossless no-op round trip.
2. Inventory every observed flag type.
3. Inventory structural/payload variations within each type rather than assuming one fixed shape.
4. Document native Stadium looper variants separately from MIDI CC commands that happen to control a looper on an external Helix.
5. Document `TIME`, `MARKER` option variants, `PRESETSNAP`, `MIDI_CC`, `MIDI_BANK_PROGRAM`, `CYCLE_START`, `CYCLE_END`, `START`, and `END` as actually observed.
6. Preserve tracks and all song-level fields exactly when only timeline data is edited.
7. Produce a machine-readable inventory plus a human-readable report of fixture differences.

Do not implement or freeze the REAPER marker vocabulary yet. The human-readable REAPER-to-Stadium alias dictionary will be designed after this empirical inventory, so that the vocabulary reflects the real corpus instead of assumptions.
