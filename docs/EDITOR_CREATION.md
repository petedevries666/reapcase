# Desktop Editor event creation

The lane context menus deliberately expose only payloads backed by the current
fixture and MIDI capability inventories. Structure markers, the five observed
native Stadium looper variants, configured Second Helix commands, video
commands, Second Helix bank/program changes, and generic MIDI CC flags can be
created.

The first release does **not** offer Structure cycle/time creation or native
Stadium snapshot/preset creation. Existing files prove how to parse and retain
those flags, but do not establish enough context-independent defaults for their
setlist, preset, tempo, time-signature, and cycle fields. Guessing those fields
would violate lossless authoring. Existing instances remain preserved and can
still be moved.

Second Helix's combined `Undo/Redo` capability is presented as one action
because the configured CC mapping does not safely distinguish two commands.
Likewise, the block states are derived from the configured CC 67 low/high
mapping rather than duplicated as protocol constants in the GUI.
