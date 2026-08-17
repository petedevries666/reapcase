# Desktop Editor event creation

The lane context menus deliberately expose only payloads backed by the current
fixture and MIDI capability inventories. Structure markers, the five observed
native Stadium looper variants, configured Second Helix commands, video
commands, Second Helix bank/program changes, generic MIDI CC flags, and native
Stadium snapshot changes can be created. Snapshot changes copy the last proven
setlist/preset context (including context established by START); if that context
cannot be resolved, creation stops rather than writing placeholder identifiers.

Structure creation includes the exact observed `CYCLE_START;;2;Infinite;Off`
and `CYCLE_END;;0` pair. Cycle End requires an unmatched earlier Cycle Start.
The Marker dialog exposes the fixture-confirmed Pause at Marker `Off`/`On`
field while retaining the remaining safe ten-field template. Native arbitrary
Stadium preset changes and Structure time creation remain unavailable: the
fixtures do not establish a meaningful identifier picker or safe defaults.
Existing instances remain preserved and can still be moved.

Second Helix's combined `Undo/Redo` capability is presented as one action
because the configured CC mapping does not safely distinguish two commands.
Likewise, the block states are derived from the configured CC 67 low/high
mapping rather than duplicated as protocol constants in the GUI.
