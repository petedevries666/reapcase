# Keyboard command contract

`ShortcutRouter` is the sole binding owner for application keyboard commands.
It installs one pre-class `<KeyPress>` bindtag, normalizes modifiers and keysyms,
classifies semantic editor context, and then applies `COMMAND_POLICY`. Widget
bindings remain appropriate for mouse gestures and native control behavior.

Focus is only the immediate keyboard recipient. Timeline/event selection remains
model state, so moving focus to editor chrome, Event List, or a docked manager
does not change the target of Copy/Paste.

## Policy

| Commands | Timeline | Event List | Manager | Roadmap | Other main UI | Text input | Modal |
|---|---:|---:|---:|---:|---:|---:|---:|
| Space Play/Pause | yes | yes | yes | yes | yes | native | native |
| Ctrl+C/V event clipboard | yes | yes | yes | yes | yes | native | native |
| Ctrl+Z, Ctrl+S, Ctrl+O, Delete, Escape | yes | yes | yes | yes | yes | native | native |
| Ctrl+1/2/3, Ctrl+E/M/F/R/L/G/D | yes | yes | yes | yes | yes | native | native |
| F/Shift+F, Tab, brackets, Home/End, arrows | yes | native | native | native | native | native | native |

Text inputs are Entry, Text, Spinbox, and editable Combobox controls. Read-only
Comboboxes are editor chrome. Any unregistered child Toplevel is modal/native;
an editor auxiliary Toplevel must explicitly register a semantic context.

## Inventory and accelerator parity

Core commands and their menu accelerators are routed centrally: Open, Save,
Save As, Undo, Copy, Paste, Delete, the three view switches, Inspector, ghost
waveform, lane/structure/marker managers, fit commands, transport, and timeline
navigation. Local bindings retained in the project are control-specific mouse,
selection, resize, Return/double-click activation, and modal Return/Escape
bindings. They do not own editor keyboard commands.

Set `REAPCASE_KEY_DEBUG=1` to print the normalized key, focus widget/class,
semantic context, resolved command, and whether Reapcase consumed the event.
