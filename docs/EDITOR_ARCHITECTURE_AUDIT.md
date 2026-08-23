# Reapcase editor architecture audit

This audit describes the editor on `main` as inspected in August 2026. It is
an evidence-driven baseline, not a proposal to replace Tk or rewrite the
editor. The current design has several healthy pure-domain seams; the primary
risk is that their orchestration and all timeline presentation remain
concentrated in `ReapcaseEditor`.

## Architecture map and ownership

```text
Stadium Song JSON
  -> StadiumSong (lossless document and opaque flag payloads)
  -> EditorModel (timeline, TimingMap, edit/undo state, sidecar, audio views)
  -> canonical semantic projections
       structure / looper / lighting / sequence / navigation rows
  -> Timeline canvas / Event List / Marker Manager / Inspector

Audio paths in Song + optional located root
  -> AudioResolver + WAV probing
  -> WaveformPyramid analysis (one worker)
  -> WaveformRenderCache tiles -> bounded PhotoImage cache -> Canvas

Audio files
  -> AudioEngine synchronized readers and callback frame counter
  -> current_time / PlaybackState
  -> 33 ms transport tick -> label, playhead coords, optional follow-scroll
```

The domain boundary is mostly healthy. `stadium.py` owns syntax-preserving
JSON, `timeline.py` adapts native flags, `TimingMap` is canonical for musical
conversion, and editor projection modules are GUI-independent. `EditorModel`
owns editable Song and Reapcase sidecar state. Tk variables, manager visibility,
lane visibility, recent files, viewport and raster objects stay in the app and
preferences layer rather than Stadium JSON.

`ReapcaseEditor` is nevertheless a god object in the practical sense: its
roughly 2,400 lines build every view and dialog, route commands, transact Song
loading, configure playback, schedule waveform jobs, own all caches, render the
timeline, manage selection gestures, and coordinate show mode. This is a
change-coupling and testability problem, not by itself a runtime bug. Coherent
future extraction seams are (1) Song-load/audio orchestration, (2) timeline
renderer and render state, (3) manager/dialog lifecycle, and (4) command
routing. Extraction is justified only alongside tests or a concrete change.

## Duplication and drift risks

* **Redraw/refresh:** `_redraw_after_model_change()` canonically combines
  dependent-view refresh with `redraw()`, but many commands call `redraw()` and
  inspector/marker refresh independently. New commands can easily leave a
  manager stale. A single invalidation API with explicit model, selection,
  viewport and transport reasons would reduce this risk.
* **Selection and navigation:** timeline, Event List and Marker Manager have
  separate event handlers, but ultimately use model selection and navigation
  helpers. `navigation.py` already centralizes chronological event/marker/region
  policy and should remain canonical.
* **STRUCTURE interpretation:** `structure.py` is the canonical region engine;
  Timeline consumes `model.structure_layout`, while Marker Manager navigation
  uses canonical row helpers. `is_structure_end_boundary()` covers both END
  flags and END-named markers, and measure-label normalization consumes the same
  layout. This is healthy and should not be re-derived in widgets.
* **Coordinate conversion:** `timeline_x`, `timeline_units_to_x` and model
  TimingMap conversions are intentionally split by responsibility. Call sites
  are numerous in `redraw()`, so adding another inline origin formula would be
  risky; consolidate origins before changing coordinate behavior.
* **Preferences:** recent files, dialog positions and lane order share one
  application config file but use separate read/modify/write implementations.
  Sequential Tk ownership prevents a present race, yet a later background
  writer could lose fields. A small atomic config repository is preferable.
* **Managers:** `_manager_windows` gives lane/track managers singleton-like
  behavior, while inspector and Marker Manager are embedded panels. The
  difference reflects UI design, but show/hide and refresh policy is scattered.
* **Waveforms:** one analysis pyramid feeds normal and ghost render paths, which
  is good. The legacy `_ghost_raster_cache` compatibility path overlaps the tile
  renderer and is normally empty; remove it only after compatibility callers
  and tests are retired.

## Timeline rendering findings

`redraw()` deletes all Canvas items and rebuilds the scroll region, ruler,
grid, lane backgrounds, structure regions, semantic regions, events, audio
clips, waveforms, transient overlays, playhead and fixed headers. It is called
after genuine model edits, zoom/lane changes and asynchronous waveform arrival,
but also after selection, seek, stop, cursor movement and viewport jumps. The
latter cases recreate substantially more than their changed pixels.

Layer classification:

| Layer | Content | Current lifetime |
| --- | --- | --- |
| Static for geometry/theme | lane backgrounds, ruler, grid, headers | recreated by full redraw; lane background `PhotoImage`s are reused |
| Semi-static | waveform tiles, structure/looper/lighting regions, events, audio clips | Canvas objects recreated; prepared tiles and photos reused |
| Dynamic | playhead, selection outlines, marquee, drag previews, hover/fixed overlays | usually recreated; playhead and fixed headers are updated in place during playback/scroll |

The important distinction is Canvas-object churn versus raster work. Full
redraw does many `create_line`, `create_rectangle`, `create_text` and
`create_image` calls, but bounded tile and PhotoImage caches prevent every
redraw from regenerating waveform pixels. Event and grid object count still
scales with Song length/visible content. Events and sequence clips are viewport
culled in important paths; grid construction and various structure content span
the Song and deserve measurement on very long Songs.

Existing opt-in `REAPCASE_TIMELINE_PERF=1` instrumentation records full redraw,
grid, semantic events, ghost/normal waveform preparation and PhotoImage
creation, then emits debug aggregates. This is appropriately silent by
default. Before layering the renderer, collect representative fixture traces
and Canvas item counts; object persistence adds bookkeeping and invalidation
risk and should target measured hot layers only.

## Playback hot path

The steady `_transport_tick()` path reads `current_time`, converts time to a
musical position, updates the transport string, converts to units, moves the
existing playhead with `canvas.coords`, computes follow-scroll, and moves fixed
headers. It does not read JSON/audio files, analyze waveforms, sort events or
perform a full redraw.

When follow-scroll exits ghost coverage, the tick schedules
`_refresh_ghost_waveform` with `after_idle`; raster mapping/compression therefore
does not execute inside the tick, but it still runs later on Tk's thread and can
delay the next input or STOP/PAUSE callback. This is the principal verified
responsiveness risk in playback. Normal waveform extraction is a single worker
and accepts a playback pause predicate, avoiding direct competition in the
audio callback. STOP and return-to-start currently request full redraws; they
are user actions rather than steady-state work, but a dynamic playhead update
would be cheaper.

The audio callback is independent of Tk and guarded by the engine lock. Its
Python per-frame/per-track mixing is the likely audio-side scaling bottleneck;
benchmark underruns before vectorizing or changing backends.

## Song-open pipeline

`_begin_song_open()` submits `EditorModel.open_phased()` to a single loading
worker and polls a queue from Tk. Worker stages are:

1. read and parse Song JSON;
2. read MIDI decoder and optional Reapcase sidecar;
3. construct timeline and TimingMap, sequence projection and normalized
   STRUCTURE labels;
4. discover/probe audio files;
5. force immediately needed derived Song extent.

The candidate is transactionally committed on Tk only after success. Tk then
closes/reopens the audio engine, resets view state, starts waveform jobs and
performs the first redraw. JSON/model/timing/probing are correctly off the UI
thread. Audio engine construction and first UI render remain synchronous because
they own native/Tk objects; waveform scanning is deferred and cacheable.

Approximate costs cannot be responsibly ranked without user Songs. WAV probing
is header-only; waveform analysis is proportional to total PCM frames; initial
Canvas work scales with grid/content; JSON/model/projections scale with event
count. Add phase timers around the existing progress boundaries before changing
the pipeline.

The waveform boundary is ready for future external `.reapwave` persistence:
`WaveformPyramid`, `PeakLevel` and `DisplayLevel` are immutable and toolkit-free,
and rendering only consumes their envelopes. A future cache should key source
content identity (canonical path plus size/mtime initially, preferably a format
version and robust fingerprint), validate metadata, deserialize off Tk, and
store outside Song directories. It should replace only extraction, not expose
Tk images or presentation geometry. Persistent storage is intentionally not
implemented here.

## Cache inventory

| Cache | Key | Bounds/lifecycle | Invalidation and ownership |
| --- | --- | --- | --- |
| Analysis `waveforms` | resolved path string | at most current Song tracks in normal use; cleared on audio reconfigure/Song | changed path or Song; worker publishes via Tk; playhead independent |
| Pending analyses | path | current requested paths | removed on completion; executor shutdown cancels queued work |
| Display levels | source + frames/bucket | not explicitly LRU; logarithmic levels per active source | source invalidation/clear; Tk renderer ownership |
| Render tiles | source, pyramid/map identities, PPQN, zoom, origin, tile | LRU, 96 default | changed source or full clear; playhead absent |
| Waveform PhotoImages | tile key + visual variant | LRU bounded to twice tile limit | source/full clear; Tk-only and retained to satisfy Tk lifetime |
| Ghost raster compatibility | viewport/render tuple | one entry | clear on changed key/ghost reset; playhead deliberately absent |
| Lane backgrounds | color, width, height | 32 default | LRU lifetime of editor/theme; Tk-only |
| Style gradient | process theme lookup | `lru_cache(maxsize=1)` | process lifetime |
| Dialog positions/recent config | logical family/path | small session state | persisted on changes; Tk-only |

No playhead movement invalidates waveform caches. The tile and PhotoImage bounds
are healthy. Display-level entries lack an independent cap, but Song-level
clear, limited sources and logarithmic resolutions constrain normal growth;
folding them into source invalidation/LRU is P2, not urgent. Python analysis
pyramids can be large (roughly two float32 arrays at each pyramid level, under
about 16 bytes per base bucket across all levels); maximum simultaneous stems
and long recordings should be measured before imposing a memory policy.

## Threading and lifecycle

Tk widgets and variables are created/mutated only by the main thread in the
reviewed paths. The loading worker constructs an unpublished model and sends
strings through `Queue`; the UI commits only `future.result()`. Waveform workers
produce toolkit-free pyramids and marshal completion back through `after`/poll.
Audio callback state is lock protected. Both executors use one worker and are
shut down with `cancel_futures=True`; the audio engine and show preloader are
also closed during `destroy()`.

Risks: running executor tasks are not cancelled by `shutdown(wait=False)` and
may briefly outlive destroyed Tk; completion callbacks must continue to guard
widget existence. Loading has no generation token/cancellation because the UI
permits only one load; preserve that invariant if drag/drop or setlist loading
is added. Waveform results keyed only by path can become stale if a file is
replaced during analysis; `refresh_audio()` identity detection and source
invalidation mitigate subsequent refresh, but a request generation/content
identity would make publication robust. Do not move any `PhotoImage` creation
or Canvas work into an executor.

## Model, serialization and semantic projections

Stadium-native edits live in the timeline and serialize through the lossless
adapter. Reapcase-only LIGHTS/sequence/click state is namespaced in the
`.reapcase.json` sidecar. Selection, cursor, undo, lane/ghost/manager visibility,
viewport and recent files are not written to Stadium. Audio track edits are
Stadium significant and remain model-owned. Existing round-trip, model,
structure-label, timing and sidecar tests provide good pure-boundary coverage.

Canonical semantic owners are strong: STRUCTURE in `structure.py`; navigation
rows in `navigation.py`; looper, lighting and sequence in their modules; lane
classification in `EditorModel`; timing in `TimingMap`. END never starts a
region, pause markers never become ordinary regions, suffix normalization uses
canonical region geometry, and previous/next region navigation excludes END.
These pure seams should be extended rather than replaced.

One redundant normalization pass existed in `open_phased()`: construction
already normalizes labels, then the phased loader called normalization again.
This audit removes the second exact pass. It changes neither output nor edit
counts and avoids duplicated policy invocation.

## Keyboard routing and manager lifecycle

Bindings are visibly grouped in `_build`: canvas gestures/local copy-paste;
timeline editor shortcuts through `_editor_shortcut`; global editor/file
shortcuts through `_global_editor_shortcut`; and native-widget/modal exceptions
inside those gates. Platform wheel variants are intentional, not duplicate
commands. Risk comes from a long imperative binding table with lambdas and case
variants: conflicts are hard to inspect and menu accelerators can drift from
bindings. A declarative command registry is worthwhile only with tests for
focus classes, modal dialogs and return value `"break"`.

Floating lane/track managers reuse `_manager_windows` and raise existing
instances; stale references are checked with `winfo_exists`. Embedded inspector,
Marker Manager and Event List refresh from shared model state. The broad
`_redraw_after_model_change()` refreshes hidden views too; Treeview rebuild cost
should be measured and hidden panels skipped if significant. Geometry policy is
centralized by `_prepare_dialog`, which is healthy.

## Test-suite assessment

The suite strongly favors deterministic pure helpers: Stadium round-trip,
TimingMap, projections, navigation, layout/style, waveform pyramids/tiles,
audio engine and editing semantics. That is appropriate and has enabled the
domain seams above. Critical missing integration coverage includes:

* fake-Canvas assertions that selection/seek/scroll do not cause unintended
  full redraws after renderer invalidation is introduced;
* transport-tick budgets and STOP delivery while ghost refresh is pending;
* transactional Song-load failure/supersession and destroy-during-worker tests;
* analysis publication after file replacement and cache invalidation;
* save/reopen integration covering Stadium plus sidecar together;
* shortcut dispatch under Entry/Treeview/modal focus and accelerator parity;
* manager singleton, hide/show and hidden-refresh behavior;
* viewport culling and Canvas item-count regression tests using a fake Canvas.

Avoid screenshots: instrumentation counters, fake engines, fake Canvas objects
and pure command-policy tables will be faster and less brittle.

## Maintainability risks

| Severity | Location | Failure mode | Treatment |
| --- | --- | --- | --- |
| High | `app.py` orchestration/rendering | unrelated changes break refresh, lifecycle or commands; hard headless tests | extract only renderer/load/command seams with characterization tests |
| High | monolithic `redraw()` | long Songs cause input latency and broad invalidation hides stale-state bugs | measure item counts/times, introduce reasoned invalidation, then persist dynamic layer |
| Medium-high | ghost refresh on Tk idle | raster/PNG work can delay transport controls during follow-scroll | prepare toolkit-free pixels on worker; publish Tk image on UI thread |
| Medium | async path-only waveform identity | stale result after replacement or Song transition | generation/content identity and publication tests |
| Medium | config read/modify/write duplication | future concurrent writer loses unrelated settings | one atomic config repository |
| Medium | command table in `app.py` | shortcut conflicts/native focus regressions | declarative registry plus dispatch tests |
| Low | unbounded display-level dictionary | unusual repeated sources/zoom sessions retain small metadata | tie eviction to tile/source LRU after measuring |
| Low | pure projection modules | temptation to merge healthy boundaries creates coupling | leave alone; keep canonical tests |

## Prioritized roadmap

### P0 — correctness/responsiveness

1. **Move ghost pixel preparation off Tk.** Benefit: predictable STOP/PAUSE and
   scroll responsiveness. Risk: medium (generation races/Tk ownership).
   Suggested size: 150–250 lines plus fake scheduler tests. Dependencies: add
   request generation and cancellation semantics first.
2. **Harden waveform publication identity.** Benefit: prevents stale images
   after file replacement/Song changes. Risk: low-medium. Size: 80–150 lines.
   Dependencies: define source fingerprint and test refresh races.

### P1 — debt worth addressing soon

1. **Add redraw instrumentation and invalidation reasons.** Benefit: evidence
   and fewer broad refreshes. Risk: low. Size: 100–200 lines. Dependencies: none.
2. **Split dynamic Canvas updates from full redraw.** Benefit: cheap selection,
   seek and stop. Risk: medium. Size: 200–400 lines per layer, not one rewrite.
   Dependencies: fake-Canvas characterization and invalidation counters.
3. **Extract Song-load/audio coordinator.** Benefit: testable cancellation and
   smaller god object. Risk: medium. Size: 250–400 lines. Dependencies: loader
   phase timing and transactional lifecycle tests.
4. **Centralize application config persistence.** Benefit: atomic updates and
   one failure policy. Risk: low. Size: 100–180 lines. Dependencies: migration-
   free preservation tests for unknown keys.

### P2 — future optimization/cleanup

1. Persistent versioned `.reapwave` analysis storage outside Song folders;
   high startup benefit, medium risk, 300–600 lines, dependent on fingerprints.
2. Declarative command registry and accelerator parity checks; maintenance
   benefit, medium risk, 200–350 lines, dependent on focus-policy tests.
3. Bound display-level metadata and retire the legacy ghost raster path; small
   memory/simplicity benefit, low risk, under 150 lines, dependent on telemetry
   and compatibility removal.
4. Benchmark/vectorize audio mixing only if underrun evidence warrants it;
   potentially high benefit, high behavioral risk, isolated benchmark PR.

## Recommended next five PRs

1. Instrument redraw reasons, stage timings, Canvas item counts and Song-load
   stages; publish a representative performance baseline.
2. Add waveform request generations/content fingerprints and race/lifecycle
   tests.
3. Move ghost raster preparation to the waveform worker while keeping
   `PhotoImage` publication on Tk.
4. Introduce a minimal dynamic render layer for playhead and selection, backed
   by fake-Canvas redraw-count tests.
5. Consolidate config persistence into one atomic, unknown-key-preserving
   repository.

The healthy Stadium codec, TimingMap, immutable waveform pyramid, audio-engine
boundary and pure semantic projection modules should be left alone except for
focused correctness work. The bottlenecks are Canvas reconstruction and Tk-side
ghost raster work; the dangerous debt is centralized orchestration and unclear
refresh granularity, not the choice of Tk itself.
