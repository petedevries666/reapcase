"""Authoritative keyboard command routing for the Reapcase editor.

Tk focus identifies the key's immediate recipient; it does not own the editor
selection.  This module translates one normalized key stream into semantic
commands and applies an explicit context policy before invoking the editor.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
import os


class KeyboardContext(Enum):
    TIMELINE = auto()
    EVENT_LIST = auto()
    TEXT_INPUT = auto()
    MODAL_DIALOG = auto()
    MANAGER = auto()
    ROADMAP = auto()
    OTHER_MAIN_WINDOW = auto()


class EditorCommand(Enum):
    PLAY_PAUSE = auto()
    COPY_EVENTS = auto()
    PASTE_EVENTS = auto()
    UNDO = auto()
    SAVE = auto()
    SAVE_AS = auto()
    OPEN = auto()
    DELETE = auto()
    ESCAPE = auto()
    DUPLICATE = auto()
    FIT_SONG = auto()
    FIT_SELECTION = auto()
    NEXT_EVENT = auto()
    PREVIOUS_EVENT = auto()
    NEXT_MARKER = auto()
    PREVIOUS_MARKER = auto()
    SONG_START = auto()
    SONG_END = auto()
    PREVIOUS_REGION = auto()
    NEXT_REGION = auto()
    ZOOM_IN = auto()
    ZOOM_OUT = auto()
    TIMELINE_VIEW = auto()
    EVENT_LIST_VIEW = auto()
    ROADMAP_VIEW = auto()
    INSPECTOR = auto()
    STRUCTURE_MANAGER = auto()
    MARKER_MANAGER = auto()
    LANE_MANAGER = auto()
    GHOST = auto()


EDITOR_CONTEXTS = frozenset({
    KeyboardContext.TIMELINE, KeyboardContext.EVENT_LIST,
    KeyboardContext.MANAGER, KeyboardContext.ROADMAP,
    KeyboardContext.OTHER_MAIN_WINDOW,
})
TIMELINE_ONLY = frozenset({KeyboardContext.TIMELINE})

# This is the keyboard contract.  Text input and modal dialogs are deliberately
# absent: returning None lets the native Tk binding continue unmolested.
COMMAND_POLICY = {
    command: EDITOR_CONTEXTS for command in (
        EditorCommand.PLAY_PAUSE, EditorCommand.COPY_EVENTS,
        EditorCommand.PASTE_EVENTS, EditorCommand.UNDO, EditorCommand.SAVE,
        EditorCommand.SAVE_AS, EditorCommand.OPEN, EditorCommand.DELETE,
        EditorCommand.ESCAPE, EditorCommand.DUPLICATE,
        EditorCommand.TIMELINE_VIEW, EditorCommand.EVENT_LIST_VIEW,
        EditorCommand.ROADMAP_VIEW, EditorCommand.INSPECTOR,
        EditorCommand.STRUCTURE_MANAGER, EditorCommand.MARKER_MANAGER,
        EditorCommand.LANE_MANAGER, EditorCommand.GHOST,
    )
}
COMMAND_POLICY.update({command: TIMELINE_ONLY for command in (
    EditorCommand.FIT_SONG, EditorCommand.FIT_SELECTION,
    EditorCommand.NEXT_EVENT, EditorCommand.PREVIOUS_EVENT,
    EditorCommand.NEXT_MARKER, EditorCommand.PREVIOUS_MARKER,
    EditorCommand.SONG_START, EditorCommand.SONG_END,
    EditorCommand.PREVIOUS_REGION, EditorCommand.NEXT_REGION,
    EditorCommand.ZOOM_IN, EditorCommand.ZOOM_OUT,
)})


KEY_COMMANDS = {
    (False, False, "space"): EditorCommand.PLAY_PAUSE,
    (True, False, "c"): EditorCommand.COPY_EVENTS,
    (True, False, "v"): EditorCommand.PASTE_EVENTS,
    (True, False, "z"): EditorCommand.UNDO,
    (True, False, "s"): EditorCommand.SAVE,
    (True, True, "s"): EditorCommand.SAVE_AS,
    (True, False, "o"): EditorCommand.OPEN,
    (False, False, "delete"): EditorCommand.DELETE,
    (False, False, "escape"): EditorCommand.ESCAPE,
    (True, False, "d"): EditorCommand.DUPLICATE,
    (False, False, "f"): EditorCommand.FIT_SONG,
    (False, True, "f"): EditorCommand.FIT_SELECTION,
    (False, False, "tab"): EditorCommand.NEXT_EVENT,
    (False, True, "tab"): EditorCommand.PREVIOUS_EVENT,
    (False, False, "bracketright"): EditorCommand.NEXT_MARKER,
    (False, False, "bracketleft"): EditorCommand.PREVIOUS_MARKER,
    (False, False, "home"): EditorCommand.SONG_START,
    (False, False, "end"): EditorCommand.SONG_END,
    (False, False, "left"): EditorCommand.PREVIOUS_REGION,
    (False, False, "right"): EditorCommand.NEXT_REGION,
    (False, False, "up"): EditorCommand.ZOOM_IN,
    (False, False, "down"): EditorCommand.ZOOM_OUT,
    (True, False, "1"): EditorCommand.TIMELINE_VIEW,
    (True, False, "2"): EditorCommand.EVENT_LIST_VIEW,
    (True, False, "3"): EditorCommand.ROADMAP_VIEW,
    (True, False, "e"): EditorCommand.INSPECTOR,
    (True, False, "m"): EditorCommand.STRUCTURE_MANAGER,
    (True, False, "f"): EditorCommand.MARKER_MANAGER,
    (True, False, "r"): EditorCommand.STRUCTURE_MANAGER,
    (True, False, "l"): EditorCommand.LANE_MANAGER,
    (True, False, "g"): EditorCommand.GHOST,
}


@dataclass(frozen=True)
class KeyStroke:
    control: bool
    shift: bool
    key: str

    @classmethod
    def from_event(cls, event):
        # Tk's stable modifier bits avoid separate lower/upper/CapsLock binds.
        state = int(getattr(event, "state", 0))
        key = str(getattr(event, "keysym", "")).casefold()
        if key in {"kp_space"}:
            key = "space"
        return cls(bool(state & 0x4), bool(state & 0x1), key)


class ShortcutRouter:
    """Route editor keys once, independently of incidental widget focus."""

    binding_tag = "ReapcaseKeyboardRouter"
    text_classes = frozenset({
        "entry", "tentry", "text", "spinbox", "tspinbox", "combobox", "tcombobox",
    })

    def __init__(self, application, callbacks, *, debug=None):
        self.application = application
        self.callbacks = callbacks
        self.debug = (os.environ.get("REAPCASE_KEY_DEBUG", "").casefold()
                      in {"1", "true", "yes"}) if debug is None else debug
        self.widget_contexts = {}

    def register(self, widget, context):
        """Assign semantic context to a widget subtree and install one bindtag."""
        self.widget_contexts[widget] = context
        self._tag_tree(widget)

    def install(self):
        self.application.bind_class(self.binding_tag, "<KeyPress>", self.route)
        self._tag_tree(self.application)

    def _tag_tree(self, widget):
        try:
            tags = widget.bindtags()
            if self.binding_tag not in tags:
                # Before widget/class bindings: consumed commands cannot also
                # perform native Treeview/Button behavior.
                widget.bindtags((self.binding_tag,) + tags)
            for child in widget.winfo_children():
                self._tag_tree(child)
        except (AttributeError, TypeError):
            return

    def context_for(self, widget):
        if widget is None:
            return KeyboardContext.MODAL_DIALOG
        widget_class = self._widget_class(widget)
        if self._is_text_input(widget, widget_class):
            return KeyboardContext.TEXT_INPUT
        try:
            if widget.winfo_toplevel() is not self.application:
                return KeyboardContext.MODAL_DIALOG
        except (AttributeError, TypeError):
            return KeyboardContext.MODAL_DIALOG
        current = widget
        while current is not None:
            if current in self.widget_contexts:
                return self.widget_contexts[current]
            current = getattr(current, "master", None)
        return KeyboardContext.OTHER_MAIN_WINDOW

    def _widget_class(self, widget):
        try:
            return str(widget.winfo_class()).casefold()
        except (AttributeError, TypeError):
            return "unknown"

    def _is_text_input(self, widget, widget_class):
        if widget_class not in self.text_classes:
            return False
        if widget_class in {"combobox", "tcombobox"}:
            try:
                return str(widget.cget("state")).casefold() != "readonly"
            except (AttributeError, TypeError):
                pass
        return True

    def route(self, event):
        stroke = KeyStroke.from_event(event)
        command = KEY_COMMANDS.get((stroke.control, stroke.shift, stroke.key))
        context = self.context_for(getattr(event, "widget", None))
        consumed = bool(command and context in COMMAND_POLICY.get(command, ()))
        if consumed:
            self.callbacks[command]()
        if self.debug:
            widget = getattr(event, "widget", None)
            print("KEY %s focus=%s class=%s context=%s command=%s consumed=%s" % (
                stroke.key, widget, self._widget_class(widget), context.name,
                command.name if command else "NATIVE", "yes" if consumed else "no"))
        return "break" if consumed else None
