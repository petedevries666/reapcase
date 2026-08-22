"""Tkinter Reapcase Desktop Editor MVP.

Launch with ``PYTHONPATH=src python -m stadium_reaper_bridge.editor.app``.
"""

from __future__ import annotations
from typing import Optional

import tkinter as tk
import time
import json
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, simpledialog, ttk
from concurrent.futures import ThreadPoolExecutor
from ..show import MidiRoute, ReapcaseShow, SHOW_SUFFIX
from ..runtime import LiveRuntime, Readiness, ShowPreloader

from .layout import (DEFAULT_PIXELS_PER_BEAT, HEADER_WIDTH, LANE_HEIGHT, RULER_HEIGHT,
                     drag_units, fit_range_scale, fit_song_scale, horizontal_wheel_units,
                     marquee_candidates, normalized_rectangle, snap_drag_delta,
                     snapped_units_at_x, timeline_x, units_at_x,
                     zoom_about_cursor)
from .looper import derive_looper_regions, looper_display_label
from .lighting import (HIT_PRESETS, STATE_PRESETS, LightingKind,
                       create_lighting_event, derive_lighting_regions)
from .model import EditorModel, LANES, MovePreview
from .style import (AUDIO, LOOPER_STATE_FILLS, REAPCASE_TREEVIEW_STYLE, THEME,
                    TIMELINE, LaneBackgroundCache, apply_ttk_theme, lane_colors,
                    structure_region_fill)
from .sequence import SequenceClickKind
from .structure import (CYCLES_HEIGHT, MARKERS_HEIGHT, PAUSES_HEIGHT,
                        derive_structure_layout, sticky_label_x,
                        structure_sublane)
from .composite import (COMMANDS_HEIGHT, COMPOSITE_LANES, event_sublane,
                        looper_item_bounds,
                        lane_height, lane_top as composite_lane_top,
                        sublane_bounds, sublane_content_bounds)
from .creation import (MarkerOptions, create_cycle_end, create_cycle_start,
                       create_generic_midi_cc, create_second_helix_expression, create_second_helix_looper,
                       create_second_helix_preset, create_second_helix_snapshot,
                       create_stadium_looper, create_stadium_snapshot, create_structure_marker,
                       create_video_command)
from .audio_engine import AudioEngine, PlaybackError, PlaybackState, PlaybackTrack
from .waveform import (analyze_grid_sync, extract_waveform, format_grid_sync,
                       raster_ppm, timeline_units_to_x, viewport_columns)
from .ergonomics import (BackupError, DialogPositions, editor_shortcuts_allowed,
                         follow_scroll)
from .navigation import (ViewState, event_list_rows, jump_viewport_left,
                         adjacent_event_index, adjacent_marker_index, default_lane_visibility,
                         focused_lane_visibility,
                         marker_region_rows, move_visible_lane,
                         normalized_lane_order, visible_lane_layout)
from .inspector import inspector_projection
from .display import song_header_metadata


class Tooltip:
    """Small reusable delayed tooltip for compact controls."""

    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self.pending = self.window = None
        widget.bind("<Enter>", self._schedule, add=True)
        widget.bind("<Leave>", self._hide, add=True)
        widget.bind("<ButtonPress>", self._hide, add=True)

    def _schedule(self, _event=None):
        self.pending = self.widget.after(self.delay, self._show)

    def _show(self):
        self.pending = None
        if self.window or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(self.window, text=self.text, background=THEME.tooltip, foreground=THEME.text,
                 relief="solid", borderwidth=1, padx=5, pady=2).pack()

    def _hide(self, _event=None):
        if self.pending:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window:
            self.window.destroy()
            self.window = None


def lane_top(lane_index):
    return composite_lane_top(LANES, lane_index)


class ReapcaseEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        apply_ttk_theme(self)
        self.title("Reapcase Desktop Editor")
        self.geometry("1180x680")
        self.model: Optional[EditorModel] = None
        self.pixels_per_beat = DEFAULT_PIXELS_PER_BEAT
        self.drag_x: Optional[float] = None
        self.drag_preview: Optional[MovePreview] = None
        self.drag_copy = False
        self.playhead_drag = False
        self._follow_suspended_until = 0.0
        self._dialog_positions = DialogPositions({})
        self.view_state = ViewState()
        self.current_view = tk.StringVar(value="timeline")
        self.inspector_visible = tk.BooleanVar(value=False)
        self._lane_focus: Optional[str] = None
        self._focus_visibility: Optional[dict[str, bool]] = None
        defaults = default_lane_visibility(LANES + ("AUDIO",))
        self.lane_visibility = {lane: tk.BooleanVar(value=defaults[lane])
                                for lane in LANES + ("AUDIO",)}
        self.lane_order = self._load_lane_order()
        self.loading = False
        self._loading_window = None
        self._loading_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="song-open")
        self._manager_windows = {}
        self._event_rows = {}
        self.audio_drag: Optional[tuple[int, int]] = None
        self.marquee_anchor: Optional[tuple[float, float]] = None
        self.marquee_point: Optional[tuple[float, float]] = None
        self.marquee_base: set[int] = set()
        self.marquee_mode = "replace"
        self.event_bounds: dict[int, tuple[float, float, float, float]] = {}
        self.sequence_bounds: dict[str, tuple[float, float, float, float]] = {}
        self.sequence_drag: Optional[tuple[float, tuple[str, ...]]] = None
        self.sequence_drag_delta = 0
        self.semantic_sources: dict[int, tuple[int, ...]] = {}
        self.grid_choice = tk.StringVar(value="1 beat")
        self.song_title = tk.StringVar(value="NO SONG LOADED")
        self.song_metadata = tk.StringVar(value="Open a Stadium Song JSON to begin")
        self.status = tk.StringVar(value="No file loaded")
        self.zoom_label = tk.StringVar()
        self.transport_position = tk.StringVar(value="00:00.000   |   001-01.001")
        self.audio_engine = AudioEngine()
        self.show: Optional[ReapcaseShow] = None
        self.show_preloader = ShowPreloader()
        self.live_runtime: Optional[LiveRuntime] = None
        self.app_mode = tk.StringVar(value="EDIT")
        self.auto_advance = tk.BooleanVar(value=False)
        self.show_name = tk.StringVar(value="SHOW: No show open")
        self.show_summary = tk.StringVar(value="0 Songs")
        self.current_live = tk.StringVar(value="CURRENT  —")
        self.next_live = tk.StringVar(value="NEXT  —")
        self.monitor_muted: list[bool] = []
        self.monitor_solo: list[bool] = []
        self.waveforms = {}
        self._waveform_images = []
        self._lane_backgrounds = LaneBackgroundCache(self)
        self.manual_audio_root = None
        self.audio_grid_overlay = tk.BooleanVar(value=False)
        self._waveform_pending = set()
        # A single low-duty analyzer avoids concurrent WAV scans competing with
        # the playback stream for disk and CPU.
        self._waveform_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="waveform")
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(33, self._transport_tick)

    def _build(self):
        self._build_menu()
        toolbar = ttk.Frame(self, padding=6); toolbar.pack(fill="x")
        for icon, tip, command in (("▣", "Open Song JSON", self.open_json),
                                   ("▥", "Save Song JSON", self.save),
                                   ("▤", "Save Song JSON As...", self.save_as)):
            button = ttk.Button(toolbar, text=icon, width=3, command=command)
            button.pack(side="left", padx=2)
            Tooltip(button, tip)
        for icon, tip, command in (("⌕−", "Zoom Out", lambda: self.zoom_step(1 / 1.25)),
                                   ("Fit Song", "Fit Song", self.fit_song),
                                   ("⌕+", "Zoom In", lambda: self.zoom_step(1.25)),
                                   ("Undo", "Undo", self.undo)):
            button = ttk.Button(toolbar, text=icon, command=command,
                                width=3 if icon.startswith("⌕") else None)
            button.pack(side="left", padx=2)
            Tooltip(button, tip)
        for text, command in (("Locate Audio Folder", self.locate_audio),
                              ("Refresh Audio", self.refresh_audio),
                              ("Add Audio Track...", self.add_audio_track_dialog),
                              ("Analyze Grid Sync", self.analyze_sync)):
            ttk.Button(toolbar, text=text, command=command).pack(side="left", padx=2)
        ttk.Label(toolbar, text=" Grid:").pack(side="left")
        ttk.Combobox(toolbar, textvariable=self.grid_choice, state="readonly", width=12,
                     values=("1 bar", "1 beat", "half beat", "quarter beat", "no snap")).pack(side="left")
        ttk.Checkbutton(toolbar, text="Audio grid", variable=self.audio_grid_overlay,
                        command=self.redraw).pack(side="left")
        ttk.Label(toolbar, textvariable=self.zoom_label, width=8, anchor="e").pack(side="left", padx=5)
        ttk.Combobox(toolbar, textvariable=self.app_mode, values=("EDIT", "LIVE"), state="readonly",
                     width=6).pack(side="right", padx=4)
        song_header = ttk.Frame(self, style="SongHeader.TFrame", padding=(8, 3, 8, 4))
        song_header.pack(fill="x")
        title_label = ttk.Label(song_header, textvariable=self.song_title,
                                style="SongTitle.TLabel", anchor="w")
        title_label.pack(fill="x")
        metadata_label = ttk.Label(song_header, textvariable=self.song_metadata,
                                   style="SongMetadata.TLabel", anchor="w")
        metadata_label.pack(fill="x")
        self.song_header_labels = (title_label, metadata_label)
        self.song_path_tooltips = tuple(Tooltip(label, "") for label in self.song_header_labels)
        showbar = ttk.LabelFrame(self, text="SHOW / SETLIST", padding=5); showbar.pack(fill="x", padx=6)
        ttk.Label(showbar, textvariable=self.show_name, width=25).pack(side="left", padx=8)
        self.setlist = tk.Listbox(
            showbar, height=4, width=52, exportselection=False,
            background=THEME.surface, foreground=THEME.text,
            selectbackground=THEME.surface_hover, selectforeground=THEME.text,
            highlightbackground=THEME.border, highlightcolor=THEME.border_strong,
            highlightthickness=1, borderwidth=0, relief="flat")
        self.setlist.pack(side="left", fill="x", expand=True); self.setlist.bind("<<ListboxSelect>>", self.select_show_song)
        ttk.Label(showbar, textvariable=self.show_summary, width=30).pack(side="right")
        ttk.Checkbutton(showbar, text="Auto Advance", variable=self.auto_advance,
                        command=self._set_auto_advance).pack(side="right", padx=4)
        live = ttk.Frame(self, padding=(8, 3)); live.pack(fill="x")
        ttk.Button(live, text="Previous Song", command=self.previous_song).pack(side="left")
        ttk.Button(live, text="Next Song", command=self.next_song).pack(side="left", padx=3)
        ttk.Label(live, textvariable=self.current_live, font=("TkDefaultFont", 10, "bold")).pack(side="left", padx=14)
        ttk.Label(live, textvariable=self.next_live).pack(side="left", padx=14)
        transport = ttk.Frame(self, padding=(8, 3)); transport.pack(fill="x")
        ttk.Button(transport, text="|<<", width=5, command=self.return_to_start).pack(side="left")
        ttk.Button(transport, text="Play / Pause", command=self.play_pause).pack(side="left", padx=3)
        ttk.Button(transport, text="Stop", command=self.stop_playback).pack(side="left")
        ttk.Label(transport, textvariable=self.transport_position, font=("TkFixedFont", 10)).pack(side="left", padx=14)
        self.main_content = ttk.Frame(self); self.main_content.pack(fill="both", expand=True)
        frame = ttk.Frame(self.main_content); frame.grid(row=0, column=0, sticky="nsew")
        self.timeline_frame = frame
        self.main_content.rowconfigure(0, weight=1); self.main_content.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(frame, background=TIMELINE.base, highlightthickness=0)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self._scroll_horizontal)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew"); yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew"); frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        self.canvas.bind("<Button-1>", self.click); self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.drop)
        self.canvas.bind("<Button-3>", self.context_menu)
        self.canvas.bind("<Double-Button-1>", self.edit_at_pointer)
        self.canvas.bind("<Control-MouseWheel>", self.mouse_zoom)
        self.canvas.bind("<Control-Button-4>", self.mouse_zoom)
        self.canvas.bind("<Control-Button-5>", self.mouse_zoom)
        self.canvas.bind("<Shift-MouseWheel>", self.horizontal_wheel)
        self.canvas.bind("<Shift-Button-4>", self.horizontal_wheel)
        self.canvas.bind("<Shift-Button-5>", self.horizontal_wheel)
        self.canvas.bind("<MouseWheel>", self.horizontal_wheel)
        self.canvas.bind("<Button-4>", self.horizontal_wheel)
        self.canvas.bind("<Button-5>", self.horizontal_wheel)
        self.canvas.bind("<Button-2>", lambda event: self.canvas.scan_mark(event.x, event.y))
        self.canvas.bind("<B2-Motion>", lambda event: self.canvas.scan_dragto(event.x, event.y, gain=1))
        self.canvas.bind("<Motion>", self.timeline_hover)
        self.canvas.bind("<Control-c>", self.copy_events)
        self.canvas.bind("<Control-v>", self.paste_events)
        self.event_list_frame = ttk.Frame(self.main_content)
        columns = ("position", "lane", "type", "name", "details")
        self.event_tree = ttk.Treeview(self.event_list_frame, columns=columns, show="headings",
                                       selectmode="extended", style=REAPCASE_TREEVIEW_STYLE)
        for column, title, width in (("position", "Position", 110), ("lane", "Lane", 130),
                                     ("type", "Type", 120), ("name", "Name / Action", 220),
                                     ("details", "Details", 260)):
            self.event_tree.heading(column, text=title, command=lambda c=column: self._sort_event_list(c))
            self.event_tree.column(column, width=width, anchor="w")
        event_scroll = ttk.Scrollbar(self.event_list_frame, orient="vertical", command=self.event_tree.yview)
        self.event_tree.configure(yscrollcommand=event_scroll.set)
        self.event_tree.pack(side="left", fill="both", expand=True); event_scroll.pack(side="right", fill="y")
        self.event_tree.bind("<<TreeviewSelect>>", self._event_list_selected)
        self.event_tree.bind("<Double-Button-1>", self._event_list_edit)
        self.event_tree.bind("<Return>", self._event_list_go_to)
        self.inspector = ttk.LabelFrame(self.main_content, text="INSPECTOR", padding=10)
        self.inspector_heading = tk.StringVar(value="No selection")
        self.inspector_position = tk.StringVar(value="")
        ttk.Label(self.inspector, textvariable=self.inspector_heading,
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        ttk.Label(self.inspector, textvariable=self.inspector_position).pack(anchor="w", pady=(4, 10))
        self.inspector_fields = ttk.Frame(self.inspector); self.inspector_fields.pack(fill="both", expand=True)
        ttk.Button(self.inspector, text="Edit…", command=self._inspector_edit).pack(anchor="e", pady=(8, 0))
        self._update_zoom_label()
        ttk.Label(self, textvariable=self.status, style="Status.TLabel", anchor="w", padding=5).pack(fill="x")

    def _build_menu(self):
        bar = tk.Menu(self)
        file_menu = tk.Menu(bar, tearoff=False)
        file_menu.add_command(label="Open JSON...", command=self.open_json)
        file_menu.add_command(label="Save", command=self.save)
        file_menu.add_command(label="Save As...", command=self.save_as)
        bar.add_cascade(label="File", menu=file_menu)
        edit = tk.Menu(bar, tearoff=False)
        for label, command, shortcut in (("Undo", self.undo, "Ctrl+Z"), ("Copy", self.copy_events, "Ctrl+C"),
                                          ("Paste", self.paste_events, "Ctrl+V"), ("Duplicate", self.duplicate_selected, ""),
                                          ("Delete", self.delete_selected, "Delete")):
            edit.add_command(label=label, command=command, accelerator=shortcut)
        bar.add_cascade(label="Edit", menu=edit)
        select = tk.Menu(bar, tearoff=False)
        for label, command in (("Select All", self.select_all),
                               ("Select All After Cursor", self.select_after),
                               ("Select Lane", self.select_lane),
                               ("Shift Selected...", self.shift_dialog)):
            select.add_command(label=label, command=command)
        bar.add_cascade(label="Select", menu=select)
        view = tk.Menu(bar, tearoff=False)
        view.add_radiobutton(label="Timeline", variable=self.current_view, value="timeline",
                             command=lambda: self.switch_view("timeline"), accelerator="Ctrl+1")
        view.add_radiobutton(label="Event List", variable=self.current_view, value="event_list",
                             command=lambda: self.switch_view("event_list"), accelerator="Ctrl+2")
        view.add_separator()
        view.add_checkbutton(label="Inspector", variable=self.inspector_visible,
                             command=self.toggle_inspector)
        view.add_command(label="Lane Manager...", command=self.open_lane_manager)
        view.add_command(label="Marker / Region Manager...", command=self.open_marker_manager)
        view.add_separator()
        view.add_command(label="Zoom Entire Song", command=self.fit_song, accelerator="F")
        view.add_command(label="Zoom to Selection", command=self.fit_selection, accelerator="Shift+F")
        view.add_command(label="Exit Lane Focus", command=self.exit_lane_focus)
        bar.add_cascade(label="View", menu=view)
        show = tk.Menu(bar, tearoff=False)
        for label, command in (("New Show", self.new_show), ("Open Show...", self.open_show),
                               ("Save Show", self.save_show)):
            show.add_command(label=label, command=command)
        show.add_separator()
        for label, command in (("Add Song", self.add_show_song),
                               ("Remove Song", self.remove_show_song),
                               ("Move Song Up", lambda: self.move_show_song(-1)),
                               ("Move Song Down", lambda: self.move_show_song(1)),
                               ("Relocate Song...", self.relocate_show_song)):
            show.add_command(label=label, command=command)
        show.add_separator()
        for label, command in (("Preflight Show", self.preflight_show),
                               ("Refresh Show", self.refresh_show),
                               ("MIDI Settings...", self.midi_settings)):
            show.add_command(label=label, command=command)
        bar.add_cascade(label="Show", menu=show)
        self.configure(menu=bar)
        self.bind_all("<Control-Key-1>", lambda _e: self.switch_view("timeline"))
        self.bind_all("<Control-Key-2>", lambda _e: self.switch_view("event_list"))
        # bind_all is needed because transient dialogs share the Tk binding
        # table, but every DAW command is centrally gated to canvas focus.
        self.bind_all("<Control-d>", lambda e: self._editor_shortcut(e, self.duplicate_selected))
        self.bind_all("<Key-f>", lambda e: self._editor_shortcut(e, self.fit_song))
        self.bind_all("<Shift-Key-F>", lambda e: self._editor_shortcut(e, self.fit_selection))
        self.bind_all("<Tab>", lambda e: self._editor_shortcut(e, self.navigate_event, 1))
        self.bind_all("<Shift-Tab>", lambda e: self._editor_shortcut(e, self.navigate_event, -1))
        self.bind_all("<bracketright>", lambda e: self._editor_shortcut(e, self.navigate_marker, 1))
        self.bind_all("<bracketleft>", lambda e: self._editor_shortcut(e, self.navigate_marker, -1))
        self.bind_all("<Home>", lambda e: self._editor_shortcut(e, self.go_song_edge, False))
        self.bind_all("<End>", lambda e: self._editor_shortcut(e, self.go_song_edge, True))

    def _editor_shortcut(self, event, command, *args):
        """Run and consume a DAW shortcut only in the timeline canvas."""
        if not editor_shortcuts_allowed(getattr(event, "widget", None), self.canvas):
            return None
        command(*args)
        return "break"

    def switch_view(self, view):
        self.view_state.switch(view); self.current_view.set(view)
        if view == "timeline":
            self.event_list_frame.grid_forget(); self.timeline_frame.grid(row=0, column=0, sticky="nsew")
            self.redraw()
        else:
            self.timeline_frame.grid_forget(); self.event_list_frame.grid(row=0, column=0, sticky="nsew")
            self._refresh_event_list()

    def toggle_inspector(self):
        if self.inspector_visible.get():
            self.inspector.grid(row=0, column=1, sticky="nsew")
            self.main_content.columnconfigure(1, minsize=240)
            self._refresh_inspector()
        else:
            self.inspector.grid_forget()
            self.main_content.columnconfigure(1, minsize=0)

    def _refresh_inspector(self):
        if not hasattr(self, "inspector_fields") or not self.inspector_visible.get():
            return
        for child in self.inspector_fields.winfo_children():
            child.destroy()
        projection = inspector_projection(self.model, self.model.selected) if self.model else None
        self.inspector_heading.set(projection.heading if projection else "No selection")
        self.inspector_position.set(projection.position if projection else "")
        if projection:
            for row, (label, value) in enumerate(projection.fields):
                ttk.Label(self.inspector_fields, text=label).grid(row=row, column=0, sticky="nw", pady=2)
                ttk.Label(self.inspector_fields, text=value, wraplength=150).grid(
                    row=row, column=1, sticky="nw", padx=(10, 0), pady=2)

    def _inspector_edit(self):
        if self.model and len(self.model.selected) == 1:
            self.edit_event(next(iter(self.model.selected)))

    def _effective_lane_visibility(self):
        normal = {lane: var.get() for lane, var in self.lane_visibility.items()}
        return focused_lane_visibility(normal, self._lane_focus) if self._lane_focus else normal

    def _reset_lane_visibility(self):
        """Reset presentation state for every successfully loaded Song."""
        self._lane_focus = None
        self._focus_visibility = None
        for lane, shown in default_lane_visibility(self.lane_visibility).items():
            self.lane_visibility[lane].set(shown)

    def focus_lane(self, lane):
        if self._lane_focus == lane:
            self.exit_lane_focus(); return
        if self._lane_focus is None:
            self._focus_visibility = {name: var.get() for name, var in self.lane_visibility.items()}
        self._lane_focus = lane
        self.status.set(f"Focus: {lane}"); self.redraw()

    def exit_lane_focus(self):
        self._lane_focus = None
        # Normal BooleanVars were never changed; this snapshot documents and
        # enforces that focus remains a temporary presentation overlay.
        if self._focus_visibility:
            for name, value in self._focus_visibility.items():
                self.lane_visibility[name].set(value)
        self._focus_visibility = None; self.redraw()

    def _navigate_to_index(self, index):
        if index is None or not self.model:
            return "break"
        self.model.selected = {index}
        units = self.model._units(self.model.timeline.events[index].position)
        self.seek_units(units); self.jump_to_units(units); self._refresh_inspector(); self.redraw()
        return "break"

    def navigate_event(self, direction):
        if not self.model: return "break"
        current = self.model._units(self.model.cursor)
        visible = [lane for lane, shown in self._effective_lane_visibility().items() if shown]
        return self._navigate_to_index(adjacent_event_index(self.model, current, direction, visible))

    def navigate_marker(self, direction):
        if not self.model: return "break"
        return self._navigate_to_index(adjacent_marker_index(
            self.model, self.model._units(self.model.cursor), direction))

    def go_song_edge(self, end):
        if not self.model: return "break"
        units = self.model.song_end_units if end else 0
        self.seek_units(units); self.jump_to_units(units); self.redraw(); return "break"

    def _refresh_event_list(self):
        if not hasattr(self, "event_tree"): return
        self.event_tree.delete(*self.event_tree.get_children()); self._event_rows = {}
        if not self.model: return
        for row in event_list_rows(self.model):
            item = self.event_tree.insert("", "end", iid=str(row.index),
                values=(row.position, row.lane, row.kind, row.name, row.details))
            self._event_rows[item] = row
        self.event_tree.selection_set([str(i) for i in self.model.selected if str(i) in self._event_rows])

    def _sort_event_list(self, column):
        values = [(self.event_tree.set(item, column), item) for item in self.event_tree.get_children("")]
        values.sort(key=lambda pair: pair[0].casefold())
        for index, (_, item) in enumerate(values): self.event_tree.move(item, "", index)

    def _event_list_selected(self, _event=None):
        if self.model:
            self.model.selected = {int(item) for item in self.event_tree.selection()}
            self._refresh_inspector()

    def _event_list_edit(self, _event=None):
        selected = self.event_tree.selection()
        if selected: self.edit_event(int(selected[0]))

    def _event_list_go_to(self, _event=None):
        selected = self.event_tree.selection()
        if selected and self.model: self.jump_to_units(self._event_rows[selected[0]].units)
        return "break"

    def jump_to_units(self, units):
        if not self.model: return
        self.seek_units(units); self.redraw()
        region = self.canvas.cget("scrollregion").split()
        total = float(region[2]) if len(region) == 4 else 1.0
        left = jump_viewport_left(units, self.model.song.ppqn, self.pixels_per_beat,
                                  self.canvas.winfo_width())
        self.canvas.xview_moveto(left / max(1.0, total)); self.redraw()

    def _manager(self, family, title, build):
        existing = self._manager_windows.get(family)
        if existing and existing.winfo_exists(): existing.deiconify(); existing.lift(); existing.focus_force(); return existing
        win = tk.Toplevel(self); win.title(title); self._manager_windows[family] = win
        build(win)
        self._prepare_dialog(win, family)
        return win

    def open_lane_manager(self):
        def build(win):
            body = ttk.Frame(win, padding=12); body.pack(fill="both", expand=True)
            rows = ttk.Frame(body); rows.pack(fill="both", expand=True)
            def refresh():
                for child in rows.winfo_children(): child.destroy()
                shown = [lane for lane in self.lane_order if self.lane_visibility[lane].get()]
                for row, lane in enumerate(self.lane_order):
                    ttk.Checkbutton(rows, text=lane, variable=self.lane_visibility[lane],
                                    command=lambda: (refresh(), self.redraw())).grid(row=row, column=0, sticky="w")
                    ttk.Button(rows, text="↑", width=3, command=lambda x=lane: move(x, -1),
                               state="normal" if lane in shown and shown.index(lane) > 0 else "disabled").grid(row=row, column=1)
                    ttk.Button(rows, text="↓", width=3, command=lambda x=lane: move(x, 1),
                               state="normal" if lane in shown and shown.index(lane) < len(shown)-1 else "disabled").grid(row=row, column=2)
                row = len(self.lane_order)
                ttk.Checkbutton(rows, text="AUDIO", variable=self.lane_visibility["AUDIO"],
                                command=self.redraw).grid(row=row, column=0, sticky="w")
            def move(lane, direction):
                visible = {name: var.get() for name, var in self.lane_visibility.items()}
                self.lane_order = move_visible_lane(self.lane_order, visible, lane, direction)
                self._save_lane_order(); refresh(); self.redraw()
            refresh()
            controls = ttk.Frame(body); controls.pack(fill="x", pady=(10, 0))
            def set_all(value):
                for variable in self.lane_visibility.values(): variable.set(value)
                self.redraw()
            ttk.Button(controls, text="All", command=lambda: set_all(True)).pack(side="left")
            ttk.Button(controls, text="None", command=lambda: set_all(False)).pack(side="left", padx=5)
        self._manager("lane_manager", "Lane Manager", build)

    def open_marker_manager(self):
        def build(win):
            tree = ttk.Treeview(win, columns=("position", "kind", "name", "end"),
                                show="headings", style=REAPCASE_TREEVIEW_STYLE)
            for col, title, width in (("position", "Position", 110), ("kind", "Kind", 100),
                                      ("name", "Name", 220), ("end", "End / Duration", 130)):
                tree.heading(col, text=title); tree.column(col, width=width)
            tree.pack(fill="both", expand=True)
            def refresh():
                tree.delete(*tree.get_children())
                if self.model:
                    for n, row in enumerate(marker_region_rows(self.model)):
                        tree.insert("", "end", iid=str(n), values=(row.position, row.kind, row.name, row.end), tags=(str(row.units),))
            def jump(_event=None):
                selected=tree.selection()
                if selected: self.jump_to_units(int(tree.item(selected[0], "tags")[0]))
            tree.bind("<Double-Button-1>", jump); ttk.Button(win, text="Jump", command=jump).pack(pady=6)
            win._refresh_rows = refresh; refresh()
        self._manager("marker_region_manager", "Marker / Region Manager", build)

    def _refresh_navigation(self):
        """Rebuild model-derived utility rows after a structural model change.

        This deliberately is not part of :meth:`redraw`: transport animation,
        scrolling, and playhead motion repaint the canvas without touching Tk
        Treeviews.
        """
        if self.current_view.get() == "event_list": self._refresh_event_list()
        win = self._manager_windows.get("marker_region_manager")
        if win and win.winfo_exists() and hasattr(win, "_refresh_rows"): win._refresh_rows()

    def _redraw_after_model_change(self):
        self._refresh_navigation()
        self.redraw()

    def _update_song_header(self):
        """Refresh Song identity only when a newly loaded model is committed."""
        if not self.model:
            return
        metadata = song_header_metadata(self.model.song, self.model.path)
        self.song_title.set(metadata.title)
        self.song_metadata.set(metadata.detail)
        for tooltip in self.song_path_tooltips:
            tooltip.text = str(self.model.path)

    def _show_changed(self):
        self.setlist.delete(0, "end")
        if not self.show:
            self.show_name.set("SHOW: No show open"); return
        self.show_name.set(f"SHOW: {self.show.name}")
        self.auto_advance.set(self.show.auto_advance)
        for index, song in enumerate(self.show.songs):
            self.setlist.insert("end", f"{index + 1:02d}  {song.title}   … NOT PREFLIGHTED")
        self.show_summary.set(f"{len(self.show.songs)} Songs")
        self.live_runtime = LiveRuntime(self.show, self.show_preloader, self.stop_playback)

    def _set_auto_advance(self):
        if self.show: self.show.auto_advance = self.auto_advance.get()

    def new_show(self):
        name = simpledialog.askstring("New Show", "Show name:", initialvalue="Untitled Show")
        if name:
            self.show_preloader.restart(); self.show = ReapcaseShow(name=name); self._show_changed()

    def open_show(self):
        path = filedialog.askopenfilename(filetypes=(("Reapcase Show", f"*{SHOW_SUFFIX}"), ("JSON", "*.json")))
        if not path: return
        try:
            self.show_preloader.restart(); self.show = ReapcaseShow.open(path); self._show_changed(); self.preflight_show()
        except Exception as exc: messagebox.showerror("Cannot open Show", str(exc))

    def save_show(self):
        if not self.show: return
        path = self.show.path or filedialog.asksaveasfilename(defaultextension=SHOW_SUFFIX,
                                                               filetypes=(("Reapcase Show", f"*{SHOW_SUFFIX}"),))
        if path:
            try: self.show.save(path); self._show_changed()
            except Exception as exc: messagebox.showerror("Cannot save Show", str(exc))

    def add_show_song(self):
        if not self.show: self.new_show()
        if not self.show: return
        paths = filedialog.askopenfilenames(filetypes=(("Stadium Song JSON", "*.json"),))
        for path in paths: self.show.add_song(path)
        self.show_preloader.restart(); self._show_changed()

    def _show_index(self):
        selected = self.setlist.curselection()
        return selected[0] if selected else None

    def remove_show_song(self):
        index = self._show_index()
        if self.show and index is not None: self.show.remove_song(index); self.show_preloader.restart(); self._show_changed()

    def move_show_song(self, delta):
        index = self._show_index()
        if not self.show or index is None or not 0 <= index + delta < len(self.show.songs): return
        self.show.move_song(index, index + delta); self.show_preloader.restart(); self._show_changed()
        self.setlist.selection_set(index + delta)

    def relocate_show_song(self):
        index = self._show_index()
        if not self.show or index is None: return
        path = filedialog.askopenfilename(filetypes=(("Stadium Song JSON", "*.json"),))
        if path: self.show.relocate_song(index, path); self.show_preloader.restart(); self._show_changed()

    def preflight_show(self):
        if not self.show: return
        self.show_preloader.restart(); results = [None] * len(self.show.songs)
        def update(index, prepared):
            results[index] = prepared
            def paint():
                marker = {Readiness.READY: "✓", Readiness.WARNING: "⚠", Readiness.ERROR: "✕"}[prepared.readiness]
                detail = next((d.message for d in prepared.diagnostics if d.readiness is not Readiness.READY),
                              f"Audio {prepared.audio_resolved}/{prepared.audio_total}")
                self.setlist.delete(index); self.setlist.insert(index, f"{index + 1:02d}  {prepared.title}   {marker} {prepared.readiness.value}   {detail}")
                done = [item for item in results if item]
                counts = {status: sum(i.readiness is status for i in done) for status in Readiness}
                self.show_summary.set(f"{len(self.show.songs)} Songs  {counts[Readiness.READY]} Ready  {counts[Readiness.WARNING]} Warning  {counts[Readiness.ERROR]} Errors")
            self.after(0, paint)
        for index, song in enumerate(self.show.songs):
            self.show_preloader.prepare(self.show, song, lambda item, i=index: update(i, item))

    def refresh_show(self): self.preflight_show()

    def select_show_song(self, _event=None):
        index = self._show_index()
        if self.live_runtime and index is not None:
            prepared = self.live_runtime.select(index); self._update_live_header(prepared)

    def _update_live_header(self, prepared=None):
        if not self.live_runtime: return
        current = prepared or self.live_runtime.current_song; nxt = self.live_runtime.next_song
        if current: self.current_live.set(f"CURRENT  {current.title}  {current.duration_seconds:.1f}s  {current.readiness.value}  Audio {current.audio_resolved}/{current.audio_total}")
        self.next_live.set(f"NEXT  {nxt.title}  {nxt.readiness.value}" if nxt else "NEXT  — / NOT READY")

    def next_song(self):
        if not self.live_runtime: return
        prepared = self.live_runtime.next()
        if prepared is None: self.status.set("NEXT NOT READY"); return
        self.setlist.selection_clear(0, "end"); self.setlist.selection_set(self.live_runtime.current_index)
        self._update_live_header(prepared)

    def previous_song(self):
        if not self.live_runtime: return
        prepared = self.live_runtime.previous()
        if prepared is None: self.status.set("PREVIOUS NOT READY"); return
        self.setlist.selection_clear(0, "end"); self.setlist.selection_set(self.live_runtime.current_index)
        self._update_live_header(prepared)

    def midi_settings(self):
        if not self.show: return
        window = tk.Toplevel(self); window.title("Show MIDI Routing (configuration only)")
        values = {}
        for row, (name, label) in enumerate((("stadium", "Stadium"), ("second_helix", "Second Helix"), ("lights", "Lights"))):
            route = self.show.midi[name]; enabled = tk.BooleanVar(value=route.enabled)
            port = tk.StringVar(value=route.port or ""); channel = tk.StringVar(value=str(route.channel)); values[name] = (enabled, port, channel)
            ttk.Checkbutton(window, text=label, variable=enabled).grid(row=row, column=0, padx=8, pady=5, sticky="w")
            ttk.Label(window, text="Port:").grid(row=row, column=1); ttk.Entry(window, textvariable=port, width=24).grid(row=row, column=2)
            ttk.Label(window, text="Channel:").grid(row=row, column=3); ttk.Entry(window, textvariable=channel, width=4).grid(row=row, column=4)
        def apply():
            try:
                self.show.midi = {name: MidiRoute(enabled.get(), port.get().strip() or None, int(channel.get()))
                                  for name, (enabled, port, channel) in values.items()}
                self.show.validate(); window.destroy()
            except Exception as exc: messagebox.showerror("Invalid MIDI routing", str(exc), parent=window)
        ttk.Button(window, text="Save Settings", command=apply).grid(row=4, column=0, columnspan=5, pady=8)
        self._prepare_dialog(window, "midi_settings", apply)

    def open_json(self):
        if self.loading: return
        path = filedialog.askopenfilename(filetypes=(("JSON", "*.json"), ("All files", "*")))
        if not path: return
        self._begin_song_open(path)

    def _begin_song_open(self, path):
        """Start transactional Song loading; all Tk work stays in this thread."""
        if self.loading: return False
        self.loading = True
        win = tk.Toplevel(self); self._loading_window = win
        win.title("Opening Song"); win.resizable(False, False)
        title = ttk.Label(win, text=f"Opening {Path(path).stem.upper()}…", padding=(18, 14, 18, 6))
        title.pack(fill="x")
        bar = ttk.Progressbar(win, mode="indeterminate", length=330); bar.pack(padx=18, pady=6)
        phase = tk.StringVar(value="Starting…")
        ttk.Label(win, textvariable=phase, padding=(18, 6, 18, 14)).pack(fill="x")
        win.protocol("WM_DELETE_WINDOW", lambda: None)
        self._prepare_dialog(win, "song_loading", cancel=lambda: None); bar.start(12)
        updates = Queue()
        future = self._loading_pool.submit(
            EditorModel.open_phased, path, updates.put,
            audio_root=self.manual_audio_root)
        def poll():
            if not self.loading: return
            try:
                while True: phase.set(updates.get_nowait())
            except Empty:
                pass
            if not future.done(): self.after(35, poll); return
            error = None
            try:
                candidate = future.result()
                phase.set("Finalizing UI…")
                self.audio_engine.close()
                self.model = candidate
                self._reset_lane_visibility()
                self._update_song_header()
                self._configure_audio()
                self._redraw_after_model_change()
            except Exception as exc:
                error = exc
            finally:
                self._finish_song_open()
            if error is not None:
                messagebox.showerror("Cannot open Song", str(error), parent=self)
        self.after(0, poll)
        return True

    def _finish_song_open(self):
        if self._loading_window and self._loading_window.winfo_exists():
            try: self._loading_window.grab_release()
            except tk.TclError: pass
            self._loading_window.destroy()
        self._loading_window = None; self.loading = False

    @staticmethod
    def _lane_preferences_path():
        return Path.home() / ".config" / "reapcase" / "ui.json"

    def _load_lane_order(self):
        try:
            data = json.loads(self._lane_preferences_path().read_text(encoding="utf-8"))
            return normalized_lane_order(data.get("lane_order", ()), LANES)
        except (OSError, ValueError, TypeError):
            return list(LANES)

    def _save_lane_order(self):
        path = self._lane_preferences_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"lane_order": self.lane_order}, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass  # Session persistence remains available on read-only homes.

    def _save_to(self, path):
        try:
            summary = self.model.save_as(path)
        except BackupError as exc:
            messagebox.showerror("Cannot save Song", str(exc)); return
        except Exception as exc:
            messagebox.showerror("Cannot save Song", str(exc)); return
        messagebox.showinfo("Save complete", f"{summary.events_moved} events moved\n"
                            f"{summary.payloads_changed} payloads changed\n"
                            f"{summary.tracks_changed} track operations")
        self.redraw()

    def save(self):
        if self.model and not self.loading:
            self._save_to(self.model.path)

    def save_as(self):
        if not self.model or self.loading: return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("JSON", "*.json"),))
        if path:
            self._save_to(path)

    def _prepare_dialog(self, dialog, family, primary=None, cancel=None):
        """Apply one modal geometry and keyboard policy to an editor form."""
        dialog.transient(self); dialog.grab_set()
        dialog.update_idletasks()
        size = (dialog.winfo_reqwidth(), dialog.winfo_reqheight())
        parent = (self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height())
        screen = (0, 0, dialog.winfo_screenwidth(), dialog.winfo_screenheight())
        x, y = self._dialog_positions.position(family, parent, size, screen)
        dialog.geometry(f"+{x}+{y}")
        dialog.bind("<Configure>", lambda _event: self._dialog_positions.remember(
            family, (dialog.winfo_x(), dialog.winfo_y())), add=True)
        if primary:
            dialog.bind("<Return>", lambda _event: (primary(), "break")[1])
        cancel = cancel or dialog.destroy
        dialog.bind("<Escape>", lambda _event: (cancel(), "break")[1])

    def locate_audio(self):
        if not self.model: return
        root = filedialog.askdirectory(title="Locate Audio Folder")
        if root:
            self.manual_audio_root = root
            self.model.resolve_audio(root)
            self._configure_audio()
            self.redraw()

    def analyze_sync(self):
        if not self.model or not self.model.tempo_map:
            return
        track = next((item for item in self.model.audio_tracks if item.resolved_path), None)
        if not track:
            messagebox.showinfo("Grid Sync", "No resolved audio track to analyze.")
            return
        try:
            results = analyze_grid_sync(track.resolved_path, self.model.tempo_map,
                                        self.model.song.ppqn, self.model._position)
        except Exception as exc:
            messagebox.showerror("Grid Sync", str(exc)); return
        messagebox.showinfo(f"Grid Sync — {track.name}",
                            format_grid_sync(results) if results else "CLICK SYNC\n\nNo strong transients found.")

    def _configure_audio(self, preserve_waveforms=False):
        self.audio_engine.close()
        if not preserve_waveforms:
            self.waveforms.clear(); self._waveform_pending.clear()
        tracks = list(self.model.audio_tracks) if self.model else []
        self.monitor_muted = [False] * len(tracks); self.monitor_solo = [False] * len(tracks)
        resolved = [PlaybackTrack(t.resolved_path, t.name, t.offset) for t in tracks if t.resolved_path]
        try:
            self.audio_engine.open(resolved)
        except Exception as exc:
            self.audio_engine.diagnostic = str(exc)
        for track in tracks:
            if track.resolved_path: self._request_waveform(track.resolved_path)

    def refresh_audio(self):
        if not self.model: return
        changed = self.model.refresh_audio()
        for path in changed:
            self.waveforms.pop(str(path), None)
        self._configure_audio(preserve_waveforms=True)
        self.redraw()

    def add_audio_track_dialog(self):
        if not self.model: return
        if len(self.model.song.tracks) >= 8:
            messagebox.showerror("Cannot add track", "A Song can contain at most 8 audio tracks")
            return
        source = filedialog.askopenfilename(title="Add Audio Track",
                                            filetypes=(("WAV audio", "*.wav"),))
        if not source: return
        from pathlib import Path
        name = simpledialog.askstring("Add Audio Track", "Track display name:", parent=self,
                                      initialvalue=Path(source).stem)
        if name is None: return
        destination = None
        from .audio import stadium_backup_audio_paths
        if stadium_backup_audio_paths(self.model.path) is None:
            destination = filedialog.askdirectory(title="Choose this Song's audio destination")
            if not destination: return
        try:
            self.model.add_audio_track(source, name.strip() or Path(source).stem, destination)
        except Exception as exc:
            messagebox.showerror("Cannot add audio track", str(exc)); return
        self._configure_audio(); self.redraw()

    def _request_waveform(self, path):
        path = str(path)
        if path in self.waveforms or path in self._waveform_pending: return
        self._waveform_pending.add(path)
        future = self._waveform_pool.submit(
            extract_waveform, path,
            pause_requested=lambda: self.audio_engine.state is PlaybackState.PLAYING)
        def poll():
            if not future.done(): self.after(40, poll); return
            self._waveform_pending.discard(path)
            try: self.waveforms[path] = future.result()
            except Exception: pass
            if self.winfo_exists(): self.redraw()
        self.after(40, poll)

    def redraw(self):
        self.canvas.delete("all")
        self._waveform_images = []
        if not self.model: return
        m = self.model
        total_beats = m.song_end_units / m.song.ppqn + 2
        width = HEADER_WIDTH + total_beats * self.pixels_per_beat
        visibility = self._effective_lane_visibility()
        layout = visible_lane_layout(self.lane_order, visibility)
        visible_lanes = layout.lanes
        audio_visible = visibility.get("AUDIO", True)
        all_lanes = list(visible_lanes) + ([f"AUDIO {track.number}" for track in m.audio_tracks] if audio_visible else [])
        editor_height = layout.event_bottom - RULER_HEIGHT
        height = layout.event_bottom + (len(m.audio_tracks) * LANE_HEIGHT if audio_visible else 0)
        # The ruler is a dedicated seek surface, deliberately separate from
        # both the transport above and the editable lanes below.
        self.canvas.create_rectangle(HEADER_WIDTH, 0, width, RULER_HEIGHT,
                                     fill=TIMELINE.ruler, outline=TIMELINE.ruler_edge, tags=("ruler",))
        lane_tops = dict(layout.tops)
        y = RULER_HEIGHT
        for lane_index, lane in enumerate(all_lanes):
            current_height = lane_height(lane) if lane in LANES else LANE_HEIGHT
            if lane in LANES:
                lane_style = lane_colors(lane)
                background = lane_style.background
            else:
                background = AUDIO.background
            lane_image = self._lane_backgrounds.image(background, int(width), current_height)
            self.canvas.create_image(0, y, image=lane_image, anchor="nw")
            self.canvas.create_line(0, y + current_height, width, y + current_height,
                                    fill=TIMELINE.separator)
            if lane == "STRUCTURE":
                for boundary in (y + MARKERS_HEIGHT, y + MARKERS_HEIGHT + PAUSES_HEIGHT):
                    self.canvas.create_line(0, boundary, width, boundary, fill=TIMELINE.sublane_separator)
            elif lane in COMPOSITE_LANES:
                self.canvas.create_line(0, y + COMMANDS_HEIGHT, width, y + COMMANDS_HEIGHT,
                                        fill=TIMELINE.sublane_separator)
            y += current_height
        grid_end = m.song_end_units + 2 * m.song.ppqn
        for point in m.timing_map.iter_beats(0, grid_end):
            bar, beat = point.position.bar, point.position.beat
            x = timeline_x(point.units, m.song.ppqn, self.pixels_per_beat)
            prominent = beat == 1
            if not prominent and self.pixels_per_beat < 28:
                continue
            self.canvas.create_line(x, RULER_HEIGHT, x, height, fill=TIMELINE.grid_bar if prominent else TIMELINE.grid_beat, width=2 if prominent else 1)
            self.canvas.create_line(x, 17 if prominent else 21, x, RULER_HEIGHT,
                                    fill=TIMELINE.ruler_bar if prominent else TIMELINE.ruler_beat)
            if prominent: self.canvas.create_text(x + 4, 2, text=f"{bar:03d}", anchor="nw", fill=TIMELINE.ruler_text)
            if self.pixels_per_beat >= 100:
                for quarter in range(1, 4):
                    qx = x + quarter * self.pixels_per_beat / 4
                    self.canvas.create_line(qx, 0, qx, height, fill=TIMELINE.grid_subdivision, dash=(1, 3))
        preview_selection = self._marquee_selection()
        self.event_bounds = {}
        self.sequence_bounds = {}
        self.semantic_sources = {}
        structure_layout = derive_structure_layout(m.timeline.events, m._units, m.song_end_units)
        region_sources = {i for region in structure_layout.regions for i in region.source_event_indices}
        view_left = self.canvas.canvasx(0) + HEADER_WIDTH
        colors = lane_colors("STRUCTURE")
        marker_region_number = 0
        for region in structure_layout.regions if "STRUCTURE" in visible_lanes else ():
            x1 = timeline_x(region.start_units, m.song.ppqn, self.pixels_per_beat)
            x2 = timeline_x(region.end_units, m.song.ppqn, self.pixels_per_beat)
            sub_y = lane_tops["STRUCTURE"] if region.kind == "marker" else lane_tops["STRUCTURE"] + MARKERS_HEIGHT + PAUSES_HEIGHT
            source = region.source_event_indices[0]
            selected = any(index in m.selected for index in region.source_event_indices)
            if selected:
                fill = colors.selected
            elif region.kind == "marker":
                fill = structure_region_fill(marker_region_number)
            else:
                fill = colors.normal
            if region.kind == "marker":
                marker_region_number += 1
            tags = (f"event:{source}",)
            self.semantic_sources[source] = region.source_event_indices
            self.canvas.create_rectangle(x1, sub_y + 3, max(x1 + 1, x2),
                                         sub_y + (MARKERS_HEIGHT if region.kind == "marker" else CYCLES_HEIGHT) - 3,
                                         fill=fill, outline=colors.outline, width=2 if selected else 1,
                                         tags=tags)
            bounds = (x1, sub_y + 3, max(x1 + 1, x2),
                      sub_y + (MARKERS_HEIGHT if region.kind == "marker" else CYCLES_HEIGHT) - 3)
            for region_index in region.source_event_indices:
                self.event_bounds[region_index] = bounds
            label_width = max(1, len(region.label) * 7)
            label_x = sticky_label_x(x1, x2, view_left, label_width)
            if label_x is not None:
                self.canvas.create_text(label_x, sub_y + (MARKERS_HEIGHT if region.kind == "marker" else CYCLES_HEIGHT) / 2,
                                        text=region.label, anchor="w", fill=colors.text, tags=tags)
        looper_regions = tuple(region for system in ("STADIUM", "SECOND HELIX")
                               for region in derive_looper_regions(
                                   m.timeline.events, m._units, system, m.song_end_units))
        looper_sources = {region.source_event_indices[0] for region in looper_regions}
        state_fill = LOOPER_STATE_FILLS
        for region in looper_regions:
            if region.system not in visible_lanes: continue
            x1 = timeline_x(region.start_units, m.song.ppqn, self.pixels_per_beat)
            x2 = timeline_x(region.end_units, m.song.ppqn, self.pixels_per_beat)
            bounds = looper_item_bounds(visible_lanes, region.system, x1, x2)
            _, y1, _, y2 = bounds
            source = region.source_event_indices[0]
            selected = source in m.selected
            palette = lane_colors(region.system)
            tags = (f"event:{source}",)
            fill = palette.selected if selected else state_fill[region.system][region.state]
            self.canvas.create_rectangle(*bounds, fill=fill,
                                         outline=palette.outline, width=2 if selected else 1,
                                         tags=tags)
            self.event_bounds[source] = bounds
            self.semantic_sources[source] = region.source_event_indices
            label_x = sticky_label_x(x1, x2, view_left, len(region.state) * 7)
            if label_x is not None:
                self.canvas.create_text(label_x, (y1 + y2) / 2, text=region.state,
                                        anchor="w", fill=palette.text, tags=tags)
        lighting_regions = derive_lighting_regions(
            m.timeline.events, m._units, m.song_end_units)
        lighting_sources = {region.source_event_index for region in lighting_regions}
        palette = lane_colors("LIGHTS")
        for region in lighting_regions if "LIGHTS" in visible_lanes else ():
            x1 = timeline_x(region.start_units, m.song.ppqn, self.pixels_per_beat)
            x2 = timeline_x(region.end_units, m.song.ppqn, self.pixels_per_beat)
            y1, y2 = lane_tops["LIGHTS"] + 5, lane_tops["LIGHTS"] + LANE_HEIGHT - 5
            source = region.source_event_index
            selected = source in m.selected
            tags = (f"event:{source}",)
            self.canvas.create_rectangle(x1, y1, max(x1 + 1, x2), y2,
                fill=palette.selected if selected else palette.normal,
                outline=palette.outline, width=2 if selected else 1, tags=tags)
            self.event_bounds[source] = (x1, y1, max(x1 + 1, x2), y2)
            self.semantic_sources[source] = (source,)
            label_x = sticky_label_x(x1, x2, view_left, len(region.label) * 7)
            if label_x is not None:
                self.canvas.create_text(label_x, (y1 + y2) / 2, text=region.label,
                    anchor="w", fill=palette.text, tags=tags)
        # Clicks are locked beat clips; only their small M control is editable.
        sequence = m.sequence_layout
        visible_left = self.canvas.canvasx(0)
        visible_right = visible_left + max(1, self.canvas.winfo_width())
        click_y = lane_tops.get("SEQCLICK", 0)
        click_palette = lane_colors("SEQCLICK")
        for point in sequence.clicks if "SEQCLICK" in visible_lanes else ():
            x = timeline_x(point.units, m.song.ppqn, self.pixels_per_beat)
            x2 = timeline_x(point.end_units, m.song.ppqn, self.pixels_per_beat)
            if x2 < visible_left or x > visible_right:
                continue
            accent = point.kind is SequenceClickKind.ACCENT
            muted = point.identity in m.click_mutes
            y1, y2 = click_y + 5, click_y + LANE_HEIGHT - 5
            tags = (f"seqclick:{point.identity}",)
            self.canvas.create_rectangle(x + 1, y1, max(x + 2, x2 - 1), y2,
                fill=TIMELINE.muted_fill if muted else click_palette.normal,
                outline=click_palette.outline, stipple="gray50" if muted else "", tags=tags)
            center = (y1 + y2) / 2
            spike = 18 if accent else 10
            self.canvas.create_line(x + 8, center - spike, x + 8, center + spike,
                fill=TIMELINE.muted_text if muted else click_palette.selected, width=3, tags=tags)
            if x2 - x >= 46:
                self.canvas.create_text(x + 14, y1 + 7, text="ACCENT" if accent else "TICK",
                    anchor="nw", fill=TIMELINE.muted_text if muted else click_palette.text,
                    font=("TkDefaultFont", 7, "bold"), tags=tags)
            button_left = max(x + 1, x2 - 21)
            mute_tags = (f"seqmute:{point.identity}",)
            self.canvas.create_rectangle(button_left, y1 + 2, x2 - 2, y1 + 20,
                fill=THEME.danger if muted else TIMELINE.control, outline=click_palette.outline,
                tags=mute_tags)
            self.canvas.create_text((button_left + x2 - 2) / 2, y1 + 11, text="M",
                fill=THEME.text, font=("TkDefaultFont", 7, "bold"), tags=mute_tags)
        instruction_y = lane_tops.get("SEQ INSTRUCTIONS", 0)
        instruction_palette = lane_colors("SEQ INSTRUCTIONS")
        for clip in sequence.instructions if "SEQ INSTRUCTIONS" in visible_lanes else ():
            x = timeline_x(clip.units, m.song.ppqn, self.pixels_per_beat)
            next_position = m.timing_map.shift_position(clip.position, beats=1)
            x2 = timeline_x(m._units(next_position), m.song.ppqn, self.pixels_per_beat)
            if x2 < visible_left or x > visible_right:
                continue
            y1, y2 = instruction_y + 5, instruction_y + LANE_HEIGHT - 5
            selected = clip.id in m.sequence_selected
            tags = (f"seqinstruction:{clip.id}",)
            self.canvas.create_rectangle(x + 1, y1, max(x + 2, x2 - 1), y2,
                fill=TIMELINE.muted_fill if clip.muted else instruction_palette.normal,
                outline=instruction_palette.selected if selected else instruction_palette.outline,
                width=2 if selected else 1, stipple="gray50" if clip.muted else "", tags=tags)
            self.sequence_bounds[clip.id] = (x + 1, y1, max(x + 2, x2 - 1), y2)
            if x2 - x >= 38:
                self.canvas.create_text(x + 7, (y1 + y2) / 2, text=clip.label, anchor="w",
                    fill=TIMELINE.muted_text if clip.muted else instruction_palette.text,
                    font=("TkDefaultFont", 8, "bold"), tags=tags)
            button_left = max(x + 1, x2 - 21)
            mute_tags = (f"instructionmute:{clip.id}",)
            self.canvas.create_rectangle(button_left, y1 + 2, x2 - 2, y1 + 20,
                fill=THEME.danger if clip.muted else TIMELINE.control, outline=instruction_palette.outline,
                tags=mute_tags)
            self.canvas.create_text((button_left + x2 - 2) / 2, y1 + 11, text="M",
                fill=THEME.text, font=("TkDefaultFont", 7, "bold"), tags=mute_tags)
        if self.sequence_drag and self.sequence_drag_delta:
            _, identities = self.sequence_drag
            dx = self.sequence_drag_delta / m.song.ppqn * self.pixels_per_beat
            for identity in identities:
                bounds = self.sequence_bounds.get(identity)
                if bounds:
                    self.canvas.create_rectangle(bounds[0] + dx, bounds[1], bounds[2] + dx,
                        bounds[3], fill=TIMELINE.drag_fill, outline=TIMELINE.drag_outline, stipple="gray50",
                        width=2, tags=("sequence-drag-preview",))
        for i, event in enumerate(m.timeline.events):
            if m.lane(event) not in visible_lanes:
                continue
            if i in region_sources or i in looper_sources or i in lighting_sources:
                continue
            event_lane = m.lane(event); x = timeline_x(m._units(event.position), m.song.ppqn, self.pixels_per_beat)
            y = lane_tops[m.lane(event)] + 27
            if event_lane == "STRUCTURE":
                sublane = structure_sublane(event)
                y = lane_tops["STRUCTURE"] + {"markers": 3, "pauses": MARKERS_HEIGHT + 1,
                                    "cycles": MARKERS_HEIGHT + PAUSES_HEIGHT + 3}[sublane]
            elif m.lane(event) in COMPOSITE_LANES:
                row_top, _ = sublane_bounds(visible_lanes, m.lane(event),
                                             event_sublane(event, m.lane(event)))
                y = row_top + 3
            selected = i in m.selected
            previewed = preview_selection is not None and i in preview_selection
            event_lane = m.lane(event)
            is_looper_point = (event_lane in COMPOSITE_LANES and
                                event_sublane(event, event_lane) == "looper")
            text = (looper_display_label(event, event_lane) if is_looper_point
                    else m.label(event))
            if is_looper_point:
                looper_y1, looper_y2 = sublane_content_bounds(
                    visible_lanes, event_lane, "looper")
                text_y = (looper_y1 + looper_y2) / 2
            else:
                text_y = y + 10
            item = self.canvas.create_text(x + 5, text_y, text=text, anchor="w",
                                           fill=lane_colors(m.lane(event)).text,
                                           tags=(f"event:{i}",))
            box = self.canvas.bbox(item)
            palette = lane_colors(m.lane(event))
            bounds = (looper_item_bounds(visible_lanes, event_lane, x, box[2] + 4)
                      if is_looper_point else
                      (box[0]-4, box[1]-3, box[2]+4, box[3]+3))
            rect = self.canvas.create_rectangle(*bounds,
                                                fill=palette.selected if selected or previewed else palette.normal,
                                                outline=palette.outline,
                                                width=2 if selected or previewed else 1,
                                                tags=(f"event:{i}",))
            self.event_bounds[i] = bounds
            self.canvas.tag_raise(item)
        for lane_offset, track in enumerate(m.audio_tracks if audio_visible else ()):
            y = layout.audio_top + lane_offset * LANE_HEIGHT
            flags = " ".join(word for enabled, word in ((track.source.get("mute"), "MUTE"),
                                                          (track.source.get("solo"), "SOLO")) if enabled)
            levels = f"trim {track.source.get('trim', '?')}  gain {track.source.get('gain', '?')}"
            self.canvas.create_text(8, y + 28, anchor="nw", fill=AUDIO.text,
                                    text=f"{track.name}\n{flags} {levels}".strip())
            for column, (label, enabled) in enumerate((("M", self.monitor_muted[lane_offset]),
                                                        ("S", self.monitor_solo[lane_offset]))):
                self.canvas.create_rectangle(76 + column*27, y + 5, 99 + column*27, y + 23,
                    fill=THEME.danger if enabled and label == "M" else (THEME.warning if enabled else TIMELINE.control),
                    tags=(f"monitor:{lane_offset}:{label}",))
                self.canvas.create_text(87 + column*27, y + 14, text=label, fill=THEME.text,
                    tags=(f"monitor:{lane_offset}:{label}",))
            start_x = HEADER_WIDTH  # Non-zero offset units are not established by fixtures.
            duration_units = (m.tempo_map.seconds_to_units(track.file_info.duration_seconds)
                              if track.file_info and m.tempo_map else m.song.ppqn * 2)
            end_x = start_x + max(12, duration_units / m.song.ppqn * self.pixels_per_beat)
            state = (f"{track.file_info.duration_seconds:.2f}s | {track.file_info.sample_rate} Hz | "
                     f"{track.file_info.channels} ch" if track.file_info else "FILE NOT FOUND / unresolved")
            if track.offset != 0:
                state += f" | raw offset {track.offset} (unit unknown)"
            self.canvas.create_rectangle(start_x, y + 22, end_x, y + 62,
                                         fill=AUDIO.clip if track.file_info else AUDIO.missing_clip,
                                         outline=AUDIO.clip_outline if track.file_info else AUDIO.missing_outline)
            clip_label = self.canvas.create_text(start_x + 6, y + 32, anchor="w",
                                                 fill=AUDIO.text,
                                                 text=f"{track.filename}  |  {state}")
            summary = self.waveforms.get(str(track.resolved_path))
            if summary and m.tempo_map:
                center, amplitude = y + 42, 18
                viewport_left = int(self.canvas.canvasx(0))
                viewport_width = max(1, self.canvas.winfo_width())
                image_left, columns = viewport_columns(
                    summary, m.tempo_map, m.song.ppqn, self.pixels_per_beat,
                    viewport_left, viewport_width, HEADER_WIDTH)
                image = tk.PhotoImage(data=raster_ppm(columns), format="PPM")
                self._waveform_images.append(image)
                self.canvas.create_image(image_left, y + 22, image=image, anchor="nw")
                self.canvas.create_line(start_x, center, end_x, center,
                                        fill=AUDIO.waveform, width=1)
                self.canvas.tag_raise(clip_label)
            if self.audio_grid_overlay and self.pixels_per_beat >= 40:
                bar_starts = {point.units for point in
                              m.timing_map.iter_bars(0, m.song_end_units)}
                for unit in range(0, m.song_end_units + 1, max(1, m.song.ppqn // 4)):
                    x = timeline_units_to_x(unit, m.song.ppqn, self.pixels_per_beat,
                                            HEADER_WIDTH)
                    beat = unit % m.song.ppqn == 0
                    bar = unit in bar_starts
                    self.canvas.create_line(x, y + 22, x, y + 62,
                        fill=AUDIO.grid_bar if bar else (AUDIO.grid_beat if beat else AUDIO.grid_subdivision),
                        width=2 if bar else 1, dash=() if beat else (1, 3))
        if self.marquee_anchor and self.marquee_point:
            bounds = normalized_rectangle(*self.marquee_anchor, *self.marquee_point)
            self.canvas.create_rectangle(*bounds, fill=TIMELINE.marquee_fill, stipple="gray50",
                                         outline=TIMELINE.marquee_outline, width=2, tags=("marquee",))
        if self.drag_preview:
            self._draw_drag_preview(self.drag_preview)
        if m.tempo_map:
            play_units = m.tempo_map.seconds_to_units(self.audio_engine.current_time)
            play_x = timeline_x(play_units, m.song.ppqn, self.pixels_per_beat)
            self.canvas.create_line(play_x, 0, play_x, height, fill=THEME.playhead, width=2,
                                    tags=("playhead",))
        # Draw the fixed header last so scrollable lane content can never show
        # through it. Its right edge is the same origin used by every timeline
        # element above.
        view_left = self.canvas.canvasx(0)
        self.canvas.create_rectangle(view_left, 0, view_left + HEADER_WIDTH, RULER_HEIGHT,
                                     fill=THEME.app, outline=TIMELINE.separator,
                                     tags=("fixed-header",))
        header_y = RULER_HEIGHT
        for lane_index, lane in enumerate(all_lanes):
            y = header_y
            current_height = lane_height(lane) if lane in LANES else LANE_HEIGHT
            header_y += current_height
            if lane in LANES:
                lane_style = lane_colors(lane)
                background = lane_style.background
                title_color = lane_style.header
            else:
                background = AUDIO.background
                title_color = AUDIO.text
            header_image = self._lane_backgrounds.image(background, HEADER_WIDTH, current_height)
            self.canvas.create_image(view_left, y, image=header_image, anchor="nw",
                                     tags=("fixed-header",))
            self.canvas.create_rectangle(view_left, y, view_left + HEADER_WIDTH, y + current_height,
                                         fill="", outline=TIMELINE.separator,
                                         tags=("fixed-header",))
            title_offset = 4 if lane in COMPOSITE_LANES else 12
            self.canvas.create_text(view_left + 8, y + title_offset, text=lane, anchor="nw",
                                    fill=title_color, font=("TkDefaultFont", 9, "bold"),
                                    tags=("fixed-header",))
            if lane == "STRUCTURE":
                for label, offset in (("MARKERS", 17), ("PAUSES", MARKERS_HEIGHT + 5),
                                      ("CYCLES", MARKERS_HEIGHT + PAUSES_HEIGHT + 5)):
                    self.canvas.create_text(view_left + 72, y + offset, text=label, anchor="nw",
                                            fill=TIMELINE.sublane_text, font=("TkDefaultFont", 7),
                                            tags=("fixed-header",))
            elif lane in COMPOSITE_LANES:
                for label, offset in (("COMMANDS", 20),
                                      ("LOOPER", COMMANDS_HEIGHT + 8)):
                    self.canvas.create_text(view_left + 28, y + offset, text=label,
                                            anchor="nw", fill=TIMELINE.sublane_text,
                                            font=("TkDefaultFont", 7),
                                            tags=("fixed-header",))
            if lane not in LANES:
                audio_index = lane_index - len(visible_lanes)
                track = m.audio_tracks[audio_index]
                flags = " ".join(word for enabled, word in
                                 ((track.source.get("mute"), "MUTE"),
                                  (track.source.get("solo"), "SOLO")) if enabled)
                levels = f"trim {track.source.get('trim', '?')}  gain {track.source.get('gain', '?')}"
                self.canvas.create_text(view_left + 8, y + 28, anchor="nw", fill=AUDIO.text,
                                        text=f"{track.name}\n{flags} {levels}".strip(),
                                        tags=("fixed-header",))
                for column, (label, enabled) in enumerate(
                        (("M", self.monitor_muted[audio_index]),
                         ("S", self.monitor_solo[audio_index]))):
                    x0 = view_left + 76 + column * 27
                    tags = ("fixed-header", f"monitor:{audio_index}:{label}")
                    self.canvas.create_rectangle(x0, y + 5, x0 + 23, y + 23,
                        fill=THEME.danger if enabled and label == "M" else
                             (THEME.warning if enabled else TIMELINE.control), tags=tags)
                    self.canvas.create_text(x0 + 11, y + 14, text=label, fill=THEME.text, tags=tags)
        if self.audio_drag:
            source, target = self.audio_drag
            top = RULER_HEIGHT + editor_height
            insert_y = top + target * LANE_HEIGHT
            self.canvas.create_line(view_left, insert_y, view_left + HEADER_WIDTH, insert_y,
                                    fill=AUDIO.track_drag, width=3, tags=("track-drag",))
            track = m.audio_tracks[source]
            ghost_y = top + source * LANE_HEIGHT + 8
            self.canvas.create_rectangle(view_left + 3, ghost_y, view_left + HEADER_WIDTH - 3,
                                         ghost_y + 38, fill=AUDIO.ghost, outline=AUDIO.ghost_outline,
                                         stipple="gray50", tags=("track-drag",))
            self.canvas.create_text(view_left + 10, ghost_y + 19, anchor="w",
                                    text=f"AUDIO {source + 1}  {track.name}", fill=THEME.text,
                                    tags=("track-drag",))
        unsupported = ", ".join(m.unsupported_types) or "none"
        resolved = sum(track.file_info is not None for track in m.audio_tracks)
        ready = sum(str(track.resolved_path) in self.waveforms for track in m.audio_tracks
                    if track.resolved_path)
        waveform_total = sum(track.resolved_path is not None for track in m.audio_tracks)
        analysis_state = "paused for playback" if (self._waveform_pending and
                         self.audio_engine.state is PlaybackState.PLAYING) else "analyzing"
        waveform_progress = (f"Waveforms: {ready}/{waveform_total} ready"
                             + (f" ({analysis_state})" if self._waveform_pending else ""))
        self.status.set(f"{m.path.name}  |  flags {len(m.timeline.events)}  |  selected {len(m.selected)}  |  Audio: {resolved} resolved | {waveform_progress} | {self.audio_engine.diagnostic}  |  cursor {m.cursor.render()}  |  {'MODIFIED' if m.modified else 'unmodified'}  |  unsupported: {unsupported}")
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self._update_zoom_label()

    def _draw_drag_preview(self, preview: MovePreview):
        if not self.model:
            return
        targets = preview.targets if preview.valid else preview.original
        for index, position in zip(preview.indices, targets):
            event = self.model.timeline.events[index]
            x = timeline_x(self.model._units(position), self.model.song.ppqn,
                           self.pixels_per_beat)
            if not preview.valid:
                x += preview.delta_units / self.model.song.ppqn * self.pixels_per_beat
            visibility = self._effective_lane_visibility()
            layout = visible_lane_layout(self.lane_order, visibility)
            event_lane = self.model.lane(event)
            if event_lane not in layout.lanes: continue
            y = layout.tops[event_lane] + 27
            if event_lane == "STRUCTURE":
                y = layout.tops[event_lane] + {"markers": 3, "pauses": MARKERS_HEIGHT + 1,
                    "cycles": MARKERS_HEIGHT + PAUSES_HEIGHT + 3}[structure_sublane(event)]
            text = self.model.label(event)
            item = self.canvas.create_text(x + 5, y + 10, text=text, anchor="w",
                                           fill=TIMELINE.invalid_fill if not preview.valid else THEME.surface_raised,
                                           tags=("drag-preview",))
            box = self.canvas.bbox(item)
            self.canvas.create_rectangle(box[0]-4, box[1]-3, box[2]+4, box[3]+3,
                                         fill=TIMELINE.invalid_text if not preview.valid else TIMELINE.drag_fill,
                                         outline=TIMELINE.invalid_outline if not preview.valid else TIMELINE.drag_outline,
                                         stipple="gray50", tags=("drag-preview",))
            self.canvas.tag_raise(item)

    def _event_index(self, event):
        tags = self.canvas.gettags("current")
        return next((int(t.split(':')[1]) for t in tags if t.startswith("event:")), None)

    def _marquee_hits(self):
        if not self.marquee_anchor or not self.marquee_point:
            return set()
        return marquee_candidates((*self.marquee_anchor, *self.marquee_point), self.event_bounds)

    def _marquee_selection(self):
        if not self.marquee_anchor:
            return None
        hits = self._marquee_hits()
        if self.marquee_mode == "replace": return hits
        if self.marquee_mode == "add": return self.marquee_base | hits
        return self.marquee_base ^ hits

    def click(self, event):
        if not self.model: return
        self.canvas.focus_set()
        index = self._event_index(event); x = self.canvas.canvasx(event.x)
        units = snapped_units_at_x(x, self.model.song.ppqn, self.pixels_per_beat,
                                   self.grid_choice.get(), self.model.numerator,
                                   self.model.timing_map)
        self.model.cursor = self.model._position(units)
        tags = self.canvas.gettags("current")
        click_mute = next((tag.split(":", 1)[1] for tag in tags if tag.startswith("seqmute:")), None)
        instruction_mute = next((tag.split(":", 1)[1] for tag in tags if tag.startswith("instructionmute:")), None)
        instruction = next((tag.split(":", 1)[1] for tag in tags if tag.startswith("seqinstruction:")), None)
        if click_mute:
            self.model.toggle_click_mute(click_mute); self.redraw(); return
        if instruction_mute:
            self.model.toggle_instruction_mute(instruction_mute); self.redraw(); return
        if instruction:
            if event.state & 0x4:
                self.model.sequence_selected.symmetric_difference_update({instruction})
            elif instruction not in self.model.sequence_selected:
                self.model.sequence_selected = {instruction}
            self.model.selected.clear()
            self.sequence_drag = (x, tuple(self.model.sequence_selected))
            self.sequence_drag_delta = 0
            self.redraw(); return
        monitor = next((tag for tag in self.canvas.gettags("current") if tag.startswith("monitor:")), None)
        if monitor:
            _, raw_index, kind = monitor.split(":"); lane_index = int(raw_index)
            values = self.monitor_muted if kind == "M" else self.monitor_solo
            values[lane_index] = not values[lane_index]
            # Engine track indices correspond to resolved tracks only.
            resolved_index = sum(t.resolved_path is not None for t in self.model.audio_tracks[:lane_index])
            if self.model.audio_tracks[lane_index].resolved_path:
                self.audio_engine.set_monitor(resolved_index,
                    muted=self.monitor_muted[lane_index], solo=self.monitor_solo[lane_index])
            self.redraw(); return
        audio_index = self._audio_index_at_y(self.canvas.canvasy(event.y))
        if event.x < HEADER_WIDTH and audio_index is not None:
            self.audio_drag = (audio_index, audio_index)
            self.redraw(); return
        if (self.canvas.canvasy(event.y) < RULER_HEIGHT and
                event.x >= HEADER_WIDTH):
            self.playhead_drag = True
            self.seek_units(units); self.redraw(); return
        if index is not None:
            sources = self.semantic_sources.get(index, (index,))
            if len(sources) > 1 and not event.state & 0x4:
                if not set(sources).issubset(self.model.selected):
                    self.model.selected = set(sources)
            else:
                self.model.select_for_drag(index, toggle=bool(event.state & 0x4))
            self.drag_x = x
            self.drag_copy = bool(event.state & 0x20000)
            self.drag_preview = self.model.preview_shift(0)
        else:
            y = self.canvas.canvasy(event.y)
            self.marquee_anchor = self.marquee_point = (x, y)
            self.marquee_base = set(self.model.selected)
            self.marquee_mode = "toggle" if event.state & 0x4 else ("add" if event.state & 0x1 else "replace")
        self._refresh_inspector(); self.redraw()

    def timeline_hover(self, event):
        """Advertise the ruler seek affordance and update its position readout."""
        if not self.model:
            return
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        over_ruler = y < RULER_HEIGHT and event.x >= HEADER_WIDTH
        self.canvas.configure(cursor="crosshair" if over_ruler else "")
        if over_ruler:
            units_at_x(x, self.model.song.ppqn, self.pixels_per_beat)

    def context_menu(self, event):
        """Open a compact lane-specific creation menu at the clicked position."""
        if self.app_mode.get() == "LIVE":
            self.status.set("Timeline authoring is disabled in LIVE mode")
            return
        if not self.model:
            return
        tags = self.canvas.gettags("current")
        instruction = next((tag.split(":", 1)[1] for tag in tags
                            if tag.startswith("seqinstruction:")), None)
        if instruction:
            menu = tk.Menu(self, tearoff=False)
            menu.add_command(label="Edit...", command=lambda: self.edit_instruction(instruction))
            try: menu.tk_popup(event.x_root, event.y_root)
            finally: menu.grab_release()
            return
        y = self.canvas.canvasy(event.y)
        audio_index = self._audio_index_at_y(y)
        if event.x < HEADER_WIDTH and audio_index is not None:
            menu = tk.Menu(self, tearoff=False)
            menu.add_command(label="Move Track Up", state="normal" if audio_index else "disabled",
                             command=lambda: self.move_audio_track(audio_index, audio_index - 1))
            menu.add_command(label="Move Track Down",
                             state="normal" if audio_index < len(self.model.audio_tracks) - 1 else "disabled",
                             command=lambda: self.move_audio_track(audio_index, audio_index + 1))
            menu.add_separator()
            menu.add_command(label="Delete Track", command=lambda: self.delete_audio_track(audio_index))
            try: menu.tk_popup(event.x_root, event.y_root)
            finally: menu.grab_release()
            return
        if event.x < HEADER_WIDTH:
            return
        visibility = self._effective_lane_visibility()
        layout = visible_lane_layout(self.lane_order, visibility)
        lane = next((lane for lane in layout.lanes
                     if layout.tops[lane] <= y < layout.tops[lane] + lane_height(lane)), None)
        if lane is None:
            return  # Ruler, hidden lanes, and audio have no creation menu.
        x = self.canvas.canvasx(event.x)
        units = snapped_units_at_x(x, self.model.song.ppqn, self.pixels_per_beat,
                                   self.grid_choice.get(), self.model.numerator,
                                   self.model.timing_map)
        position = self.model._position(units)
        menu = tk.Menu(self, tearoff=False)
        add = tk.Menu(menu, tearoff=False)
        clicked = self._event_index(event)
        if clicked is not None:
            sources = set(self.semantic_sources.get(clicked, (clicked,)))
            if not sources.issubset(self.model.selected):
                self.model.selected = sources
                self.redraw()
            editable = self.model.selection_is_editable()
            capability = self.model.edit_capability(clicked)
            menu.add_command(label="Edit...", command=lambda index=clicked: self.edit_event(index),
                             state="normal" if capability else "disabled")
            menu.add_command(label="Duplicate", command=self.duplicate_selected,
                             state="normal" if editable else "disabled")
            menu.add_command(label="Delete", command=self.delete_selected,
                             state="normal" if editable else "disabled")
            menu.add_separator()
        menu.add_cascade(label="ADD NEW", menu=add)
        if lane == "STRUCTURE":
            pause_default = y >= layout.tops["STRUCTURE"] + MARKERS_HEIGHT and y < layout.tops["STRUCTURE"] + MARKERS_HEIGHT + PAUSES_HEIGHT
            add.add_command(label="Marker...", command=lambda: self._marker_dialog(position, pause_default))
            cycle = tk.Menu(add, tearoff=False)
            cycle.add_command(label="Cycle Start", command=lambda: self._create(create_cycle_start, position))
            cycle.add_command(label="Cycle End", command=lambda: self._create(
                create_cycle_end, position, self.model.timeline.events))
            add.add_cascade(label="Cycle", menu=cycle)
        elif lane == "STADIUM":
            snapshots = tk.Menu(add, tearoff=False)
            for snapshot in range(1, 9):
                snapshots.add_command(label=f"Snapshot {snapshot}", command=lambda value=snapshot:
                    self._create(create_stadium_snapshot, position, value,
                                 self.model.timeline.events))
            add.add_cascade(label="Snapshot Change", menu=snapshots)
            looper = tk.Menu(add, tearoff=False)
            for action in ("Clear Loop", "Record", "Stop", "Play", "Play Once"):
                looper.add_command(label=action,
                    command=lambda value=action: self._create(create_stadium_looper, position, value))
            add.add_cascade(label="Looper", menu=looper)
        elif lane == "SECOND HELIX":
            expressions = tk.Menu(add, tearoff=False)
            for expression, _cc in self.model.decoder.second_helix_expressions():
                endpoints = tk.Menu(expressions, tearoff=False)
                for label, value in (("Minimum", 0), ("Maximum", 127)):
                    endpoints.add_command(label=label, command=lambda exp=expression, endpoint=value:
                        self._create(create_second_helix_expression, position, exp, endpoint,
                                     self.model.decoder))
                expressions.add_cascade(label=f"EXP {expression}", menu=endpoints)
            add.add_cascade(label="Expression Pedal", menu=expressions)
            snapshots = tk.Menu(add, tearoff=False)
            for snapshot in self.model.decoder.second_helix_snapshots():
                snapshots.add_command(label=f"Snapshot {snapshot}",
                    command=lambda value=snapshot: self._create(
                        create_second_helix_snapshot, position, value, self.model.decoder))
            add.add_cascade(label="Snapshot Change", menu=snapshots)
            add.add_command(label="Preset Change...", command=lambda: self._preset_dialog(position))
            looper = tk.Menu(add, tearoff=False)
            labels = {"Undo/Redo": "Undo / Redo", "On": "Block On", "Off": "Block Off"}
            allowed = {"Record", "Overdub", "Play", "Stop", "Play Once", "Undo/Redo",
                       "Forward", "Reverse", "Full Speed", "Half Speed", "On", "Off"}
            for action in self.model.decoder.second_helix_actions():
                if action in allowed:
                    looper.add_command(label=labels.get(action, action),
                        command=lambda value=action: self._create(
                            create_second_helix_looper, position, value, self.model.decoder))
            add.add_cascade(label="Looper", menu=looper)
        elif lane == "VIDEO":
            labels = {"preload": "Preload Video...", "play_one_shot": "Play One Shot...",
                      "play_loop": "Play Loop...", "stop": "Stop Video...",
                      "rescan_playlist": "Rescan Playlist"}
            for action in self.model.decoder.video_actions():
                if action in labels:
                    callback = (lambda value=action: self._video_dialog(position, value))
                    add.add_command(label=labels[action], command=callback)
        elif lane == "LIGHTS":
            for kind, presets in ((LightingKind.STATE, STATE_PRESETS),
                                  (LightingKind.HIT, HIT_PRESETS)):
                submenu = tk.Menu(add, tearoff=False)
                for cue_name in presets:
                    submenu.add_command(label=cue_name, command=lambda name=cue_name, cue_kind=kind:
                        self._create(create_lighting_event, position, name, cue_kind))
                submenu.add_command(label="Custom...", command=lambda cue_kind=kind:
                    self._lighting_dialog(position, cue_kind))
                add.add_cascade(label=kind.value, menu=submenu)
        elif lane == "MIDI / OTHER":
            add.add_command(label="MIDI CC...", command=lambda: self._midi_cc_dialog(position))
        if add.index("end") is None:
            return
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def edit_at_pointer(self, event):
        """Edit an event, focus a header, or dispatch lane-aware creation."""
        if self.app_mode.get() == "LIVE" or not self.model:
            return
        tags = self.canvas.gettags("current")
        instruction = next((tag.split(":", 1)[1] for tag in tags
                            if tag.startswith("seqinstruction:")), None)
        if instruction:
            self.edit_instruction(instruction); return
        index = self._event_index(event)
        if index is not None and self.model.edit_capability(index):
            self.edit_event(index)
            return
        y = self.canvas.canvasy(event.y)
        layout = visible_lane_layout(self.lane_order, self._effective_lane_visibility())
        lane = next((name for name in layout.lanes
                     if layout.tops[name] <= y < layout.tops[name] + lane_height(name)), None)
        if lane and event.x < HEADER_WIDTH:
            self.focus_lane(lane)
        elif lane and event.x >= HEADER_WIDTH:
            self.context_menu(event)

    def edit_instruction(self, identity):
        item = next(item for item in self.model.instructions if item.id == identity)
        dialog = tk.Toplevel(self); dialog.title("EDIT INSTRUCTION")
        dialog.transient(self); dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14); frame.pack(fill="both", expand=True)
        label, sample = tk.StringVar(value=item.label), tk.StringVar(value=item.sample_id or "")
        muted = tk.BooleanVar(value=item.muted)
        for row, (text_value, variable) in enumerate((("Label", label), ("Sample ID", sample))):
            ttk.Label(frame, text=text_value).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            ttk.Entry(frame, textvariable=variable, width=28).grid(row=row, column=1, pady=4)
        ttk.Checkbutton(frame, text="Muted", variable=muted).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Button(frame, text="Cancel", command=dialog.destroy).grid(row=3, column=0, pady=(14, 0))
        def save():
            try: self.model.edit_instruction(identity, label=label.get(), sample_id=sample.get(), muted=muted.get())
            except ValueError as exc:
                messagebox.showerror("Cannot edit instruction", str(exc), parent=dialog); return
            dialog.destroy(); self.redraw()
        primary = ttk.Button(frame, text="Save", command=save, default="active")
        primary.grid(row=3, column=1, pady=(14, 0))
        self._prepare_dialog(dialog, "instruction_edit", save)

    def edit_event(self, index):
        """Render a form from the central semantic descriptor (never raw payload)."""
        capability = self.model.edit_capability(index)
        if capability is None:
            return
        values = dict(capability.values)
        family = capability.family
        schemas = {
            "marker": (("name", "Name", "text"), ("pause_at_marker", "Pause at Marker", "bool"),
                       ("cycle_marker", "Cycle Marker", "bool")),
            "stadium_snapshot": (("snapshot", "Snapshot", tuple(range(1, 9))),
                                  ("context", "Active Preset Context", "readonly")),
            "cycle": (("repeat_count", "Cycle Count", ("Infinite",)),
                      ("option", "Retrigger Flags", ("Off",))),
            "stadium_looper": (("action", "Action", ("Clear Loop", "Record", "Stop", "Play", "Play Once")),),
            "helix_snapshot": (("snapshot", "Snapshot", self.model.decoder.second_helix_snapshots()),
                               ("channel", "MIDI Channel", "int")),
            "helix_expression": (("expression", "Expression", (1, 2, 3)),
                                 ("value", "Value", (0, 127)), ("channel", "MIDI Channel", "int")),
            "helix_preset": (("bank_msb", "Bank MSB (blank = Off)", "optional_int"),
                             ("bank_lsb", "Bank LSB (blank = Off)", "optional_int"),
                             ("program", "Program", "int"), ("channel", "MIDI Channel", "int")),
            "midi_cc": (("channel", "Channel", "int"), ("cc", "CC", "int"),
                        ("value", "Value", "int")),
            "lighting": (("name", "Name", "text"),),
        }
        if family == "video":
            schemas[family] = (("video", "Video", "optional_int"),
                               ("action", "Action", self.model.decoder.video_actions()),
                               ("channel", "MIDI Channel", "int"))
        elif family.startswith("helix_") and family not in schemas:
            schemas[family] = (("action", "Action", self.model.decoder.second_helix_actions()),
                               ("channel", "MIDI Channel", "int"))
        schema = schemas.get(family)
        if not schema:
            return
        dialog = tk.Toplevel(self); dialog.title(capability.title)
        dialog.transient(self); dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14); frame.pack(fill="both", expand=True)
        variables = {}
        for row, (key, label, kind) in enumerate(schema):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            if kind == "bool":
                variable = tk.BooleanVar(value=values[key]); widget = ttk.Checkbutton(frame, variable=variable)
            elif isinstance(kind, tuple):
                variable = tk.StringVar(value=str(values.get(key, "")))
                widget = ttk.Combobox(frame, textvariable=variable, state="readonly",
                                      values=tuple(str(item) for item in kind), width=25)
            else:
                current = values.get(key, "")
                variable = tk.StringVar(value="" if current is None else str(current))
                widget = ttk.Entry(frame, textvariable=variable, width=28,
                                   state="readonly" if kind == "readonly" else "normal")
            variables[key] = (variable, kind); widget.grid(row=row, column=1, sticky="ew", pady=4)
        buttons = ttk.Frame(frame); buttons.grid(row=len(schema), column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
        def save():
            edited = dict(values)
            try:
                for key, (variable, kind) in variables.items():
                    if kind == "readonly": continue
                    raw = variable.get()
                    if kind == "bool": edited[key] = bool(raw)
                    elif kind in {"int", "optional_int"} or (isinstance(kind, tuple) and kind and isinstance(kind[0], int)):
                        edited[key] = None if kind == "optional_int" and not raw.strip() else int(raw)
                    else: edited[key] = raw
                self.model.edit_event(index, edited)
            except (ValueError, KeyError) as exc:
                messagebox.showerror("Cannot edit event", str(exc), parent=dialog); return
            dialog.destroy(); self._redraw_after_model_change()
        primary = ttk.Button(buttons, text="Save", command=save, default="active")
        primary.pack(side="left", padx=4)
        self._prepare_dialog(dialog, f"event_edit:{family}", save)

    def _create(self, factory, *args):
        try:
            created = factory(*args)
            self.model.insert_event(created)
        except ValueError as exc:
            messagebox.showerror("Cannot create event", str(exc))
            return
        self._redraw_after_model_change()

    def _marker_dialog(self, position, pause_default=False):
        dialog = tk.Toplevel(self)
        dialog.title("Add Structure Marker"); dialog.transient(self); dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Marker name").grid(row=0, column=0, sticky="w")
        name = tk.StringVar(); entry = ttk.Entry(frame, textvariable=name, width=34)
        entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 10))
        pause = tk.BooleanVar(value=pause_default)
        ttk.Checkbutton(frame, text="Pause at Marker", variable=pause).grid(row=2, column=0,
                                                                            columnspan=2, sticky="w")
        ttk.Button(frame, text="Cancel", command=dialog.destroy).grid(row=3, column=0, pady=(14, 0))
        def create():
            before = len(self.model.timeline.events)
            self._create(create_structure_marker, position, MarkerOptions(name.get(), pause.get()))
            if len(self.model.timeline.events) > before:
                dialog.destroy()
        primary = ttk.Button(frame, text="Create", command=create, default="active")
        primary.grid(row=3, column=1, pady=(14, 0))
        self._prepare_dialog(dialog, "structure_marker", create)
        entry.focus_set()

    def _video_dialog(self, position, action):
        if action == "rescan_playlist":
            self._create(create_video_command, position, None, action, self.model.decoder)
            return
        video = simpledialog.askinteger("Add Video Command", "Video number:", parent=self,
                                        initialvalue=6, minvalue=0, maxvalue=127)
        if video is not None:
            self._create(create_video_command, position, video, action, self.model.decoder)

    def _lighting_dialog(self, position, kind):
        title = f"New Lighting {'State' if kind is LightingKind.STATE else 'Hit'}"
        name = simpledialog.askstring(title, "Name:", parent=self)
        if name is not None:
            cue_id = self.model.unique_lighting_id(name)
            self._create(create_lighting_event, position, name, kind, cue_id)

    def _preset_dialog(self, position):
        values = []
        for label, optional in (("Bank MSB (blank = Off):", True),
                                ("Bank LSB (blank = Off):", True), ("Program:", False)):
            raw = simpledialog.askstring("Add Second Helix Preset", label, parent=self)
            if raw is None:
                return
            raw = raw.strip()
            if not raw and optional:
                values.append(None)
                continue
            try:
                values.append(int(raw))
            except ValueError:
                messagebox.showerror("Cannot create event", f"{label.rstrip(':')} must be an integer")
                return
        self._create(create_second_helix_preset, position, *values, self.model.decoder)

    def _midi_cc_dialog(self, position):
        values = []
        for label, low, high in (("Channel:", 1, 16), ("CC:", 0, 127), ("Value:", 0, 127)):
            value = simpledialog.askinteger("Add MIDI CC", label, parent=self,
                                            minvalue=low, maxvalue=high)
            if value is None:
                return
            values.append(value)
        self._create(create_generic_midi_cc, position, *values, self.model.decoder)

    def drag(self, event):
        if self.app_mode.get() == "LIVE": return
        if not self.model:
            return
        if self.playhead_drag:
            units = snapped_units_at_x(self.canvas.canvasx(event.x), self.model.song.ppqn,
                                       self.pixels_per_beat, self.grid_choice.get(),
                                       self.model.numerator, self.model.timing_map)
            self.seek_units(units); self.redraw(); return
        if self.sequence_drag:
            start_x, identities = self.sequence_drag
            raw = drag_units(self.canvas.canvasx(event.x) - start_x,
                             self.pixels_per_beat, self.model.song.ppqn)
            anchor = min(next(item.units for item in self.model.instructions if item.id == identity)
                         for identity in identities)
            self.sequence_drag_delta = snap_drag_delta(
                anchor, raw, self.grid_choice.get(), self.model.song.ppqn,
                self.model.numerator, self.model.timing_map)
            self.redraw(); return
        if self.audio_drag:
            source, _ = self.audio_drag
            y = self.canvas.canvasy(event.y)
            visibility = self._effective_lane_visibility()
            top = visible_lane_layout(self.lane_order, visibility).audio_top
            target = max(0, min(len(self.model.audio_tracks) - 1,
                                int((y - top + LANE_HEIGHT / 2) // LANE_HEIGHT)))
            self.audio_drag = (source, target)
            self.redraw(); return
        if self.marquee_anchor:
            self.marquee_point = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
            self.redraw()
            return
        if self.drag_x is None or not self.model.selected: return
        dx = self.canvas.canvasx(event.x) - self.drag_x
        raw = drag_units(dx, self.pixels_per_beat, self.model.song.ppqn)
        anchor = min(self.model._units(self.model.timeline.events[i].position) for i in self.model.selected)
        delta = snap_drag_delta(anchor, raw, self.grid_choice.get(), self.model.song.ppqn,
                                self.model.numerator, self.model.timing_map)
        self.drag_preview = self.model.preview_shift(delta)
        self.redraw()
        count = len(self.drag_preview.indices)
        if self.drag_preview.valid:
            noun = "event" if count == 1 else "events"
            self.status.set(f"Move {count} {noun} → {self.drag_preview.destination.render()}  |  valid")
        else:
            self.status.set("Invalid move: before Song start")

    def drop(self, event):
        if self.app_mode.get() == "LIVE": return
        if not self.model: return
        if self.playhead_drag:
            self.playhead_drag = False
            return
        if self.sequence_drag:
            _, identities = self.sequence_drag
            self.model.move_instructions(identities, self.sequence_drag_delta)
            self.sequence_drag = None; self.sequence_drag_delta = 0
            self.redraw(); return
        if self.audio_drag:
            source, target = self.audio_drag; self.audio_drag = None
            if self.model.move_audio_track(source, target): self._configure_audio()
            self.redraw(); return
        if self.marquee_anchor:
            selection = self._marquee_selection()
            self.model.selected = selection or set()
            sequence_selection = marquee_candidates(
                (*self.marquee_anchor, *self.marquee_point), self.sequence_bounds)
            if self.marquee_mode == "replace":
                self.model.sequence_selected = sequence_selection
            elif self.marquee_mode == "add":
                self.model.sequence_selected.update(sequence_selection)
            else:
                self.model.sequence_selected.symmetric_difference_update(sequence_selection)
            self.marquee_anchor = self.marquee_point = None
            self.marquee_base = set()
            self.redraw()
            return
        if self.drag_x is None: return
        preview = self.drag_preview
        if preview and preview.valid:
            if self.drag_copy:
                anchor = min(self.model._units(position) for position in preview.original)
                self.model.duplicate_events(preview.indices, anchor + preview.delta_units,
                                            anchor_units=anchor)
            else:
                self.model.commit_preview(preview)
            self._refresh_navigation()
        self.drag_x = None
        self.drag_copy = False
        self.drag_preview = None
        self._refresh_inspector(); self.redraw()

    @staticmethod
    def _wheel_direction(event):
        return 1 if getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4 else -1

    def mouse_zoom(self, event):
        self._zoom_at(1.12 ** self._wheel_direction(event), event.x)
        return "break"

    def zoom_step(self, factor):
        if self.model and self.model.tempo_map:
            play_units = self.model.tempo_map.seconds_to_units(self.audio_engine.current_time)
            playhead_x = timeline_x(play_units, self.model.song.ppqn,
                                    self.pixels_per_beat)
            cursor_x = playhead_x - self.canvas.canvasx(0)
        else:
            cursor_x = self.canvas.winfo_width() / 2
        self._zoom_at(factor, cursor_x)

    def _zoom_at(self, factor, cursor_x):
        old_scroll = self.canvas.canvasx(0)
        result = zoom_about_cursor(self.pixels_per_beat, self.pixels_per_beat * factor,
                                   old_scroll, cursor_x)
        self.pixels_per_beat = result.pixels_per_beat
        self.redraw()
        region = self.canvas.cget("scrollregion").split()
        width = float(region[2]) if len(region) == 4 else 1
        self.canvas.xview_moveto(result.scroll_x / max(1, width))
        self.redraw()

    def fit_song(self):
        if not self.model:
            return
        end = self.model.song_end_units
        self.pixels_per_beat = fit_song_scale(end, self.model.song.ppqn,
                                              self.canvas.winfo_width())
        self.redraw()
        self.canvas.xview_moveto(0)
        self.redraw()

    def fit_selection(self):
        if not self.model or not self.model.selected:
            return
        units = [self.model._units(self.model.timeline.events[i].position)
                 for i in self.model.selected]
        start, end = min(units), max(units)
        self.pixels_per_beat = fit_range_scale(start, end, self.model.song.ppqn,
                                               self.canvas.winfo_width())
        self.redraw()
        left = jump_viewport_left(start, self.model.song.ppqn, self.pixels_per_beat,
                                  self.canvas.winfo_width(), .08)
        region = self.canvas.cget("scrollregion").split()
        width = float(region[2]) if len(region) == 4 else 1
        self.canvas.xview_moveto(left / max(1, width)); self.redraw()

    def _update_zoom_label(self):
        self.zoom_label.set(f"{self.pixels_per_beat / DEFAULT_PIXELS_PER_BEAT:.0%}")

    def horizontal_wheel(self, event):
        self._follow_suspended_until = time.monotonic() + 1.5
        self.canvas.xview_scroll(horizontal_wheel_units(self._wheel_direction(event)), "units")
        self.redraw()
        return "break"

    def _scroll_horizontal(self, *args):
        self._follow_suspended_until = time.monotonic() + 1.5
        self.canvas.xview(*args)
        self.redraw()

    def select_all(self):
        if self.model: self.model.select_all(); self.redraw()
    def select_after(self):
        if self.model: self.model.select_all_after_cursor(); self.redraw()
    def select_lane(self):
        if not self.model: return
        win = tk.Toplevel(self); win.title("Select lane")
        for lane in LANES:
            ttk.Button(win, text=lane, command=lambda x=lane: (self.model.select_lane(x), win.destroy(), self.redraw())).pack(fill="x", padx=12, pady=3)
        self._prepare_dialog(win, "select_lane")
    def shift_dialog(self):
        if not self.model: return
        win = tk.Toplevel(self); win.title("Shift Selected"); entries = []
        for row, label in enumerate(("Bars", "Beats", "Ticks")):
            ttk.Label(win, text=label).grid(row=row, column=0); entry=ttk.Entry(win); entry.insert(0,"0"); entry.grid(row=row,column=1); entries.append(entry)
        def apply():
            try: self.model.shift_selected(*(int(e.get()) for e in entries))
            except ValueError as exc: messagebox.showerror("Invalid shift", str(exc)); return
            win.destroy(); self._redraw_after_model_change()
        ttk.Button(win, text="Shift", command=apply, default="active").grid(row=3, columnspan=2, pady=8)
        self._prepare_dialog(win, "shift_selected", apply)
    def undo(self):
        if self.model:
            before = tuple(track.source for track in self.model.audio_tracks)
            changed = self.model.undo()
            if changed and before != tuple(track.source for track in self.model.audio_tracks):
                self._configure_audio()
            if changed: self._refresh_navigation()
            self.redraw()

    def copy_events(self, _event=None):
        if self.model and self.app_mode.get() != "LIVE":
            count = self.model.copy_selected()
            self.status.set(f"Copied {count} event{'s' if count != 1 else ''}" if count else
                            "Selection contains no copyable events")
        return "break"

    def paste_events(self, _event=None):
        if self.model and self.app_mode.get() != "LIVE":
            count = self.model.paste_at_cursor()
            if count: self._redraw_after_model_change()
            self.status.set(f"Pasted {count} event{'s' if count != 1 else ''} at playhead" if count else
                            "Event clipboard is empty")
        return "break"

    def _audio_index_at_y(self, y):
        if not self.model: return None
        visibility = self._effective_lane_visibility()
        if not visibility.get("AUDIO", True): return None
        top = visible_lane_layout(self.lane_order, visibility).audio_top
        index = int((y - top) // LANE_HEIGHT)
        return index if top <= y and 0 <= index < len(self.model.audio_tracks) else None

    def move_audio_track(self, old_index, new_index):
        if self.model and self.model.move_audio_track(old_index, new_index):
            self._configure_audio(); self.redraw()

    def delete_audio_track(self, index):
        if not self.model: return
        name = self.model.audio_tracks[index].name
        if not messagebox.askyesno("Delete Audio Track",
                                   f'Remove track "{name}" from this Song?\n\nThe WAV file will remain on disk.'):
            return
        self.model.delete_audio_track(index)
        self._configure_audio(); self.redraw()

    def delete_selected(self):
        if self.app_mode.get() == "LIVE": return
        if self.model:
            self.model.delete_selected()
            self._redraw_after_model_change()

    def duplicate_selected(self, _event=None):
        if self.app_mode.get() == "LIVE": return
        if self.model:
            count = self.model.duplicate_selected()
            if count:
                self.status.set(f"{count} {'event' if count == 1 else 'events'} duplicated")
            self._redraw_after_model_change()

    def seek_units(self, units):
        if self.model and self.model.tempo_map:
            self.audio_engine.seek(self.model.tempo_map.units_to_seconds(units))
            self.model.cursor = self.model._position(units)

    def return_to_start(self): self.audio_engine.return_to_start(); self.redraw()
    def stop_playback(self): self.audio_engine.stop(); self.redraw()
    def play_pause(self):
        if self.loading: return
        try:
            if self.audio_engine.state is PlaybackState.PLAYING: self.audio_engine.pause()
            else: self.audio_engine.play()
        except PlaybackError as exc: messagebox.showwarning("Audio unavailable", str(exc))

    def _transport_tick(self):
        if self.model and self.model.tempo_map:
            seconds = self.audio_engine.current_time
            minutes, remainder = divmod(seconds, 60)
            position = self.model.tempo_map.seconds_to_musical_position(seconds)
            self.transport_position.set(f"{int(minutes):02d}:{remainder:06.3f}   |   {position.render()}")
            if self.audio_engine.state is PlaybackState.PLAYING:
                self.redraw()
                play_units = self.model.tempo_map.seconds_to_units(seconds)
                playhead_x = timeline_x(play_units, self.model.song.ppqn, self.pixels_per_beat)
                left = self.canvas.canvasx(0)
                width = self.canvas.winfo_width()
                destination = follow_scroll(
                    playhead_x, left, width, playing=True,
                    suspended=time.monotonic() < self._follow_suspended_until)
                if destination is not None:
                    region = self.canvas.cget("scrollregion").split()
                    total = float(region[2]) if len(region) == 4 else 1.0
                    self.canvas.xview_moveto(destination / max(1.0, total))
        self.after(33, self._transport_tick)

    def destroy(self):
        self.audio_engine.close(); self._waveform_pool.shutdown(wait=False, cancel_futures=True)
        self._loading_pool.shutdown(wait=False, cancel_futures=True)
        self.show_preloader.shutdown()
        super().destroy()


def main():
    ReapcaseEditor().mainloop()


if __name__ == "__main__":
    main()
