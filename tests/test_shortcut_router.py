from types import SimpleNamespace

import pytest

from stadium_reaper_bridge.editor.shortcuts import (
    COMMAND_POLICY, EditorCommand, KeyboardContext, KeyStroke, ShortcutRouter,
)


class Widget:
    def __init__(self, widget_class, application, master=None, *, state="normal", top=None):
        self.widget_class = widget_class
        self.application = application
        self.master = master
        self.state = state
        self.top = application if top is None else top
        self.children = []
        self.tags = ("widget", "class", "toplevel", "all")
        if master:
            master.children.append(self)

    def winfo_class(self): return self.widget_class
    def winfo_toplevel(self): return self.top
    def winfo_children(self): return self.children
    def cget(self, key): return self.state if key == "state" else ""
    def bindtags(self, value=None):
        if value is not None: self.tags = value
        return self.tags


class Application(Widget):
    def __init__(self):
        self.class_binding = None
        super().__init__("Tk", self, top=self)

    def bind_class(self, tag, sequence, callback):
        self.class_binding = tag, sequence, callback


def event(widget, key, *, control=False, shift=False, caps_lock=False):
    state = (0x4 if control else 0) | (0x1 if shift else 0) | (0x2 if caps_lock else 0)
    return SimpleNamespace(widget=widget, keysym=key, state=state)


@pytest.fixture
def routed_editor():
    app = Application()
    timeline = Widget("Canvas", app, app)
    event_list = Widget("Treeview", app, app)
    manager = Widget("TFrame", app, app)
    toolbar = Widget("TButton", app, app)
    roadmap = Widget("Canvas", app, app)
    calls = []
    callbacks = {command: lambda command=command: calls.append(command)
                 for command in EditorCommand}
    router = ShortcutRouter(app, callbacks, debug=False)
    router.register(timeline, KeyboardContext.TIMELINE)
    router.register(event_list, KeyboardContext.EVENT_LIST)
    router.register(manager, KeyboardContext.MANAGER)
    router.register(roadmap, KeyboardContext.ROADMAP)
    router.install()
    return app, router, calls, timeline, event_list, manager, toolbar, roadmap


@pytest.mark.parametrize("target_index", [3, 4, 5, 6, 7])
def test_space_is_transport_throughout_editor_workspace(routed_editor, target_index):
    values = routed_editor
    router, calls, widget = values[1], values[2], values[target_index]
    assert router.route(event(widget, "space")) == "break"
    assert calls == [EditorCommand.PLAY_PAUSE]


@pytest.mark.parametrize("widget_class", ["Entry", "Text", "TSpinbox"])
def test_text_inputs_retain_native_space_copy_and_paste(routed_editor, widget_class):
    app, router, calls = routed_editor[:3]
    widget = Widget(widget_class, app, app)
    for key, control in (("space", False), ("c", True), ("v", True)):
        assert router.route(event(widget, key, control=control)) is None
    assert calls == []


def test_only_editable_combobox_is_text_input(routed_editor):
    app, router, calls = routed_editor[:3]
    editable = Widget("TCombobox", app, app)
    readonly = Widget("TCombobox", app, app, state="readonly")
    assert router.route(event(editable, "space")) is None
    assert router.route(event(readonly, "space")) == "break"
    assert calls == [EditorCommand.PLAY_PAUSE]


def test_modal_never_dispatches_behind_dialog(routed_editor):
    app, router, calls = routed_editor[:3]
    modal = object()
    entry = Widget("Entry", app, top=modal)
    button = Widget("TButton", app, top=modal)
    assert router.route(event(entry, "v", control=True)) is None
    assert router.route(event(button, "space")) is None
    assert calls == []


@pytest.mark.parametrize("key,command", [("c", EditorCommand.COPY_EVENTS),
                                          ("v", EditorCommand.PASTE_EVENTS)])
@pytest.mark.parametrize("target_index", [3, 4, 5, 6])
def test_event_clipboard_uses_selection_context_not_focus(
        routed_editor, key, command, target_index):
    router, calls, widget = routed_editor[1], routed_editor[2], routed_editor[target_index]
    assert router.route(event(widget, key.upper(), control=True, caps_lock=True)) == "break"
    assert calls == [command]


def test_focus_history_sequence_keeps_keyboard_contract(routed_editor):
    _, router, calls, timeline, event_list, manager, toolbar, _ = routed_editor
    sequence = ((timeline, "c", True), (event_list, "space", False),
                (manager, "c", True), (toolbar, "v", True))
    for widget, key, control in sequence:
        assert router.route(event(widget, key, control=control)) == "break"
    assert calls == [EditorCommand.COPY_EVENTS, EditorCommand.PLAY_PAUSE,
                     EditorCommand.COPY_EVENTS, EditorCommand.PASTE_EVENTS]


def test_shortcut_smoke_contract_and_timeline_navigation_policy(routed_editor):
    _, router, calls, timeline, event_list, *_ = routed_editor
    essential = (("space", False, EditorCommand.PLAY_PAUSE),
                 ("c", True, EditorCommand.COPY_EVENTS),
                 ("v", True, EditorCommand.PASTE_EVENTS),
                 ("z", True, EditorCommand.UNDO),
                 ("s", True, EditorCommand.SAVE),
                 ("delete", False, EditorCommand.DELETE),
                 ("escape", False, EditorCommand.ESCAPE),
                 ("left", False, EditorCommand.PREVIOUS_REGION),
                 ("m", True, EditorCommand.STRUCTURE_MANAGER),
                 ("f", True, EditorCommand.MARKER_MANAGER))
    for key, control, command in essential:
        assert router.route(event(timeline, key, control=control)) == "break"
        assert calls.pop() is command
    assert router.route(event(event_list, "left")) is None
    assert calls == []


def test_router_owns_one_pre_class_binding_and_normalizes_windows_variants(routed_editor):
    app, router = routed_editor[:2]
    assert app.class_binding[:2] == (router.binding_tag, "<KeyPress>")
    assert router.binding_tag == app.tags[0]
    assert KeyStroke.from_event(event(app, "C", control=True, caps_lock=True)).key == "c"
    assert KeyboardContext.TEXT_INPUT not in COMMAND_POLICY[EditorCommand.COPY_EVENTS]
