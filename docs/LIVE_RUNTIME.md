# Live Show runtime foundation

## Ownership and schema

`*.reapcase-show.json` is a versioned Reapcase document. Its
`reapcase_show` object contains the display name, ordered `{id, title,
song_json}` references, `live` options, routing under `midi`, an empty/future
`lights.mappings` namespace, notes, console profile, and panic metadata. Song
paths are made relative to the show file when possible. Stadium Song JSON is
never changed by setlist operations.

Each MIDI destination (`stadium`, `second_helix`, and `lights`) independently
stores `enabled`, textual `port`, and channel 1–16. The route is configuration,
not an open device, and does not introduce MIDI I/O or OS port discovery.

## Preparation and readiness

`SongPreparer` builds an immutable `PreparedSong` containing parsed Stadium
data, the canonical `TimingMap`, timeline and semantic runtime events, LIGHTS
sidecar events, resolved audio paths and WAV headers, duration, diagnostics,
and file identities. It reads WAV headers but never PCM and never generates
waveforms.

Readiness is **ERROR** for missing/invalid Song JSON or required audio, and
**WARNING** for a non-blocking issue such as an invalid optional LIGHTS sidecar
or mixed sample rates. A valid no-audio Song is **READY**. The show preflight
also rejects duplicate IDs and invalid routes. The UI aggregates all three
states and retains the original missing path for relocation.

## Cache, preload, and transitions

`ShowPreloader` owns one low-duty worker so preparation does not run on Tk's
thread or an audio callback. Restarting it cancels queued work and invalidates
the cache. `PreparedSongCache` is a three-item LRU by default, sufficient for
current/next/previous without retaining a whole large show. It checks
`(resolved path, size, mtime_ns)` identities for Song, sidecar, and every
resolved WAV. Refresh Show explicitly rebuilds metadata; there is no continuous
disk polling during playback.

`LiveRuntime.next()` only accepts a cached, non-stale next item. Otherwise it
returns immediately and the UI says `NEXT NOT READY`. A successful transition
enters `TRANSITIONING`, stops the sole current audio owner, swaps the prepared
object, resets seconds/units, enters `STOPPED`, and queues the following item.
No parse, directory search, waveform extraction, or PCM read occurs in that
transition. Audio files remain disk-streamed and no second hardware output
stream is opened by preflight.

## Semantic output boundary

The Song says **what** to do, the Show says **where** to send it, and a future
runtime translator will say **how**. `RuntimeCommand` therefore holds a
destination, semantic action, payload, and musical position—not invented raw
MIDI bytes. Native Stadium snapshots remain `PRESETSNAP`/snapshot values in
the source and additionally prepare a `stadium / snapshot / {snapshot: N}`
intent. Existing decoded Second Helix semantics prepare `second_helix` intents;
their source channel remains untouched while the show route can override the
future output channel.

Song LIGHTS sidecars similarly remain semantic `STATE` and `HIT` cues and
prepare `lights` intentions. Note/CC mappings are intentionally deferred:
inventing them would conflate artistic cue meaning with a console-specific
protocol. The reserved show mapping namespace can supply that later.

The next live MIDI milestone can use `LiveRuntime.current_time_seconds`,
`current_units`, `current_song`, `runtime_events`, and `midi_routing`. A
scheduler driven by the existing audio master clock can select due semantic
commands, pass them to destination protocol translators, then apply the
separate show port/channel route. This foundation performs none of those sends.

## Editor LIVE clock and dispatch

Editor LIVE playback uses `AudioEngine.current_frame` as its authoritative
position. The output callback advances that locked frame counter; the Tk
transport callback only samples it. Both the Second Helix crossing dispatcher
and the canvas playhead consume that same sample, so neither wall time nor
waveform rendering advances playback. MIDI is intentionally not timestamped or
sent early because the current `mido` backend has no portable future-timestamp
contract. Instead, every crossed event is claimed in source order at the
lightweight 33 ms clock sample, including all events crossed by a delayed UI
sample.

PLAY has a readiness barrier rather than a sleep. Smart Recall messages are
queued on the serial MIDI worker first, and the audio stream starts only after
the final recall completion is reported back to Tk. A zero-position start and
an ordinary pause/resume have no recall work and cross the barrier immediately.
This ordering also leaves a clean location for a future device-specific
Program Change settling policy without ever blocking Tk.

`PAUSE AT MARKER` is armed in the audio engine as an exact sample-frame
boundary. The callback renders only through that frame and pads the rest of
the hardware block with silence. The LIVE dispatcher independently consumes
the same boundary with source ordering, preventing later MIDI from entering
the worker queue. Its explicit boundary cursor permits resume at the exact
position without an epsilon seek or duplicate event. Stop and Song reload
advance the dispatch generation, so stale queued worker jobs are rejected.

Normal DEBUG logs report PLAY request, preroll, recall, audio start, dispatch
position and lateness. Setting `REAPCASE_LOAD_PERF=1` additionally reports
aggregate MIDI average/max lateness when the dispatcher is stopped or reloaded.
