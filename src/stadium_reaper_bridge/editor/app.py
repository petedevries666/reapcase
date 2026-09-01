"""Tkinter Reapcase Desktop Editor MVP.

Launch with ``PYTHONPATH=src python -m stadium_reaper_bridge.editor.app``.
"""

from __future__ import annotations
from typing import Optional

from collections import Counter, OrderedDict
import hashlib
import logging
import os
import threading
import tkinter as tk
import time
import json
import inspect
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, simpledialog, ttk
from concurrent.futures import ThreadPoolExecutor
from ..show import ReapcaseShow, SHOW_SUFFIX
from ..runtime import LiveRuntime, Readiness, ShowPreloader
from ..midi_output import DESTINATION_LABELS, MidiDestination, MidiRouter
from ..live_midi import LiveMidiDispatcher, build_live_event_set

from .layout import (DEFAULT_PIXELS_PER_BEAT, HEADER_WIDTH, LANE_HEIGHT, RULER_HEIGHT,
                     drag_units, fit_range_scale, fit_song_scale, horizontal_wheel_units,
                     marquee_candidates, normalized_rectangle, snap_drag_delta,
                     snapped_units_at_x, timeline_x, units_at_x,
                     is_major_display_bar, timeline_grid_density, viewport_units,
                     zoom_about_cursor)
from .looper import derive_looper_regions, looper_display_label
from .lighting import (HIT_PRESETS, STATE_PRESETS, LightingKind,
                       create_lighting_event, derive_lighting_regions)
from .model import AudioProgress, AudioProgressPhase, EditorModel, LANES, MovePreview
from .style import (AUDIO, LOOPER_STATE_FILLS, REAPCASE_TREEVIEW_STYLE, THEME,
                    TIMELINE, LaneBackgroundCache, apply_ttk_theme, lane_colors,
                    event_list_role, manager_row_palette,
                    structure_region_fill)
from .sequence import SequenceClickKind
from .structure import (CYCLES_HEIGHT, MARKERS_HEIGHT, PAUSES_HEIGHT,
                        derive_structure_layout, is_pause_marker, sticky_label_x,
                        structure_sublane)
from .composite import (COMMANDS_HEIGHT, COMPOSITE_LANES, event_sublane,
                        looper_item_bounds,
                        lane_height, lane_top as composite_lane_top,
                        sublane_bounds, sublane_content_bounds)
from .creation import (MarkerOptions, create_cycle_end, create_cycle_start,
                       SECOND_HELIX_LOOPER_ACTIONS,
                       create_generic_midi_cc, create_second_helix_expression, create_second_helix_looper,
                       create_second_helix_preset, create_second_helix_snapshot,
                       create_stadium_looper, create_stadium_snapshot, create_structure_marker,
                       create_video_command)
from .audio_engine import AudioEngine, PlaybackError, PlaybackState, PlaybackTrack
from .audio import full_song_track, waveform_cache_key
from ..analysis import SongAnalyzer, Severity
from .waveform import (WaveformPerformance, WaveformRenderCache, analyze_grid_sync,
                       buffered_viewport, cached_ghost_raster,
                       format_grid_sync, ghost_raster_cache_key, raster_ppm,
                       timed_extract_waveform, timeline_units_to_x,
                       viewport_exits_coverage)
from .ergonomics import (BackupError, DialogPositions, editor_shortcuts_allowed,
                         follow_scroll, global_editor_shortcuts_allowed)
from .shortcuts import EditorCommand, KeyboardContext, ShortcutRouter
from .navigation import (ViewState, event_list_rows, jump_viewport_left,
                         adjacent_event_index, adjacent_marker_index,
                         adjacent_structure_region_index, default_lane_visibility,
                         focused_lane_visibility,
                         ghost_waveform_lane_bounds,
                         marker_flag_manager_rows, move_visible_lane,
                         structure_manager_rows,
                         normalized_lane_order, visible_lane_layout)
from .inspector import inspector_projection
from .display import song_header_metadata
from .preferences import application_config_path, load_preferences, update_preferences
from .transport import resolve_play_start
from .song_browser import SongBrowser, initial_song_folder, workspace_for_song
from .stadium_workspace import (discover_workspace_songs, import_backup, inspect_import,
                                load_manifest, unique_workspace)
from .stadium_export import analyze_build, build_package
from .stadium_implant import (analyze_audio_update, apply_audio_update,
                              implant_package, validate_sd_root)
from .background_operations import BackgroundOperations
from .roadmap import build_roadmap_document
from .roadmap_view import RoadmapView
from .stadium_network_window import StadiumNetworkWindow


LOG = logging.getLogger(__name__)
LOAD_PERF = os.environ.get("REAPCASE_LOAD_PERF", "").lower() in ("1", "true", "yes")


def audible_playback_time(engine):
    """Use the physical-output clock while remaining compatible with old backends."""
    return getattr(engine, "audible_time", engine.current_time)


WAVEFORM_MAX_WORKERS = 2


def _present_migration_error(error, error_title, parent=None):
    """Log a worker's retained traceback before rendering its friendly error."""
    LOG.exception("Stadium %s", error_title.lower(),
                  exc_info=(type(error), error, error.__traceback__))
    messagebox.showerror(error_title, str(error), parent=parent)


def _new_waveform_executor():
    """Create the deliberately small analyzer pool."""
    return ThreadPoolExecutor(max_workers=WAVEFORM_MAX_WORKERS,
                              thread_name_prefix="waveform")
UI_AUDIO_POLL_BUDGET_SECONDS = 0.012
MARKER_MANAGER_COLUMNS = ("kind", "name")
MARKER_FLAG_FILTER_LANES = ("STADIUM", "SECOND HELIX", "VIDEO", "LIGHTS", "MIDI / OTHER")
# Compatibility/export for integrations that display the established keys.
GLOBAL_MANAGER_SHORTCUTS = (
    ("<Control-m>", "open_marker_manager"),
    ("<Control-f>", "open_marker_flag_manager"),
)
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
        self.marker_manager_visible = tk.BooleanVar(value=False)
        self.marker_flag_manager_visible = tk.BooleanVar(value=False)
        self.marker_flag_filters = {lane: tk.BooleanVar(value=True)
                                    for lane in MARKER_FLAG_FILTER_LANES}
        # This is an application presentation preference, not Song data.  A
        # missing key gets the new visible default; an explicit False remains
        # authoritative on every subsequent launch and Song load.
        preferences = load_preferences()
        self.full_song_ghost_visible = tk.BooleanVar(
            value=bool(preferences.get("full_song_ghost_visible", True)))
        self.pre_roll_enabled = tk.BooleanVar(
            value=bool(preferences.get("pre_roll_enabled", False)))
        measures = preferences.get("pre_roll_measures", 2)
        self.pre_roll_measures = tk.IntVar(
            value=measures if measures in (1, 2, 4, 8) else 2)
        self.pre_roll_target_units: Optional[int] = None
        # Editor-session rehearsal range.  These are canonical musical units,
        # deliberately independent of the Song, cursor, and canvas geometry.
        self.time_selection_start_units: Optional[int] = None
        self.time_selection_end_units: Optional[int] = None
        self._time_selection_drag: Optional[tuple[str, int]] = None
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
        self._workspace_song_inventory = None
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
        self.audio_status = tk.StringVar(value="Audio: —")
        self.status = tk.StringVar(value="No file loaded")
        self.zoom_label = tk.StringVar()
        self.transport_position = tk.StringVar(value="00:00.000   |   001-01.001")
        self.audio_engine = AudioEngine()
        # Optional MIDI failures are contained by MidiRouter and never block editing.
        self.midi_router = MidiRouter()
        self._live_midi_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-midi")
        self.live_midi = LiveMidiDispatcher(self._send_live_midi)
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
        self._waveform_track_images = {}
        self._waveform_render_serial = {}
        self._waveform_render_cache = WaveformRenderCache()
        self._waveform_photo_cache = OrderedDict()
        self._ghost_raster_cache = {}  # compatibility; new renderer never populates it
        self._waveform_perf = WaveformPerformance(LOAD_PERF or
            os.environ.get("REAPCASE_TIMELINE_PERF", "").lower() in ("1", "true", "yes"))
        self._ghost_raster_coverage = None
        self._ghost_refresh_pending = False
        self._ghost_waveform_image = None
        self._redraw_idle_id = None
        self._lane_backgrounds = LaneBackgroundCache(self)
        self.manual_audio_root = None
        self.stadium_workspace = None
        self._migration_operations = BackgroundOperations()
        self._migration_window = None
        self.audio_grid_overlay = tk.BooleanVar(value=False)
        self._waveform_pending = set()
        self._initial_waveform_generation = None
        self._initial_waveform_targets = set()
        self._initial_waveform_terminal = set()
        self._initial_waveform_presentation_scheduled = False
        # Two analyzers overlap decode and I/O without turning a Song open into
        # an unbounded burst that competes with playback or Tk.
        self._waveform_pool = _new_waveform_executor()
        self._audio_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio-resolve")
        self._load_generation = 0
        self._audio_ready = False
        self._audio_error = None
        self._audio_cancel = threading.Event()
        self._waveform_cancel = threading.Event()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close_editor)
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
        self.live_badge = tk.Label(toolbar, text="LIVE OFF", bg="#3a2020", fg="white",
                                   font=("TkDefaultFont", 9, "bold"), padx=8, pady=3)
        self.live_badge.pack(side="right", padx=3)
        self.app_mode.trace_add("write", self._live_mode_changed)
        song_header = ttk.Frame(self, style="SongHeader.TFrame", padding=(8, 3, 8, 4))
        song_header.pack(fill="x")
        title_label = ttk.Label(song_header, textvariable=self.song_title,
                                style="SongTitle.TLabel", anchor="w")
        title_label.pack(fill="x")
        metadata_label = ttk.Label(song_header, textvariable=self.song_metadata,
                                   style="SongMetadata.TLabel", anchor="w")
        metadata_label.pack(fill="x")
        audio_row = ttk.Frame(song_header, style="SongHeader.TFrame")
        audio_row.pack(fill="x")
        ttk.Label(audio_row, textvariable=self.audio_status,
                  style="SongMetadata.TLabel", anchor="w").pack(side="left")
        self.audio_progress = ttk.Progressbar(audio_row, mode="determinate", length=150)
        self.audio_progress.pack(side="left", padx=8)
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
        self.play_button = ttk.Button(transport, text="Play / Pause", command=self.play_pause)
        self.play_button.pack(side="left", padx=3)
        self.stop_button = ttk.Button(transport, text="Stop", command=self.stop_playback)
        self.stop_button.pack(side="left")
        ttk.Button(transport, text="<○", width=3, command=lambda: self.navigate_region(-1)).pack(side="left", padx=(5, 1))
        ttk.Button(transport, text="○>", width=3, command=lambda: self.navigate_region(1)).pack(side="left")
        self.pre_roll_toggle = ttk.Checkbutton(
            transport, text="PRE-ROLL", variable=self.pre_roll_enabled,
            command=self._persist_pre_roll)
        self.pre_roll_toggle.pack(side="left", padx=(12, 2))
        ttk.Combobox(transport, textvariable=self.pre_roll_measures, state="readonly",
                     width=2, values=(1, 2, 4, 8)).pack(side="left")
        ttk.Label(transport, text="BARS").pack(side="left", padx=(3, 0))
        self.pre_roll_measures.trace_add("write", self._persist_pre_roll)
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
        self.canvas.bind("<Configure>", self._timeline_resized)
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
        self.event_tree.bind("<Double-Button-1>", self._event_list_go_to)
        self.event_tree.bind("<Return>", self._event_list_go_to)
        self.roadmap_view = RoadmapView(
            self.main_content, on_navigate=self._roadmap_navigate,
            on_edit_note=self._roadmap_edit_note, on_layout=self._roadmap_layout)
        self.right_sidebar = ttk.Frame(self.main_content)
        self.right_sidebar.columnconfigure(0, weight=1)
        self.inspector = ttk.LabelFrame(self.right_sidebar, text="INSPECTOR", padding=10)
        self.inspector_heading = tk.StringVar(value="No selection")
        self.inspector_position = tk.StringVar(value="")
        ttk.Label(self.inspector, textvariable=self.inspector_heading,
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        ttk.Label(self.inspector, textvariable=self.inspector_position).pack(anchor="w", pady=(4, 10))
        self.inspector_fields = ttk.Frame(self.inspector); self.inspector_fields.pack(fill="both", expand=True)
        ttk.Button(self.inspector, text="Edit…", command=self._inspector_edit).pack(anchor="e", pady=(8, 0))
        self.marker_manager = ttk.LabelFrame(
            self.right_sidebar, text="STRUCTURE MANAGER", padding=6)
        self.marker_tree = ttk.Treeview(
            self.marker_manager, columns=MARKER_MANAGER_COLUMNS, show="headings",
            selectmode="browse", style=REAPCASE_TREEVIEW_STYLE)
        for column, title, width in (("kind", "Kind", 100), ("name", "Name", 160)):
            self.marker_tree.heading(column, text=title)
            self.marker_tree.column(column, width=width, minwidth=70, anchor="w",
                                    stretch=column == "name")
        marker_scroll = ttk.Scrollbar(self.marker_manager, orient="vertical",
                                      command=self.marker_tree.yview)
        self.marker_tree.configure(yscrollcommand=marker_scroll.set)
        self.marker_tree.grid(row=0, column=0, sticky="nsew")
        marker_scroll.grid(row=0, column=1, sticky="ns")
        self.marker_manager.rowconfigure(0, weight=1)
        self.marker_manager.columnconfigure(0, weight=1)
        self._marker_rows = {}
        self.marker_tree.bind("<<TreeviewSelect>>", self._marker_manager_selected)
        self.marker_tree.bind("<Control-Button-1>", self._marker_manager_ctrl_click)
        ttk.Button(self.marker_manager, text="Jump", command=self._marker_manager_selected).grid(
            row=1, column=0, columnspan=2, pady=(6, 0))
        self.marker_flag_manager = ttk.LabelFrame(
            self.right_sidebar, text="MARKER & FLAG MANAGER", padding=6)
        filters = ttk.Frame(self.marker_flag_manager)
        filters.grid(row=0, column=0, columnspan=2, sticky="ew")
        filter_labels = ("Stadium", "Second Helix", "Video", "Lights", "Midi / Others")
        for number, (lane, label) in enumerate(zip(MARKER_FLAG_FILTER_LANES, filter_labels)):
            ttk.Checkbutton(filters, text=label, variable=self.marker_flag_filters[lane],
                            command=self._refresh_marker_flag_manager).grid(
                                row=number // 3, column=number % 3, sticky="w", padx=(0, 5))
        self.marker_flag_tree = ttk.Treeview(
            self.marker_flag_manager, columns=MARKER_MANAGER_COLUMNS, show="headings",
            selectmode="browse", style=REAPCASE_TREEVIEW_STYLE)
        for column, title, width in (("kind", "Lane", 110), ("name", "Name", 160)):
            self.marker_flag_tree.heading(column, text=title)
            self.marker_flag_tree.column(column, width=width, minwidth=70, anchor="w",
                                         stretch=column == "name")
        flag_scroll = ttk.Scrollbar(self.marker_flag_manager, orient="vertical",
                                    command=self.marker_flag_tree.yview)
        self.marker_flag_tree.configure(yscrollcommand=flag_scroll.set)
        self.marker_flag_tree.grid(row=1, column=0, sticky="nsew")
        flag_scroll.grid(row=1, column=1, sticky="ns")
        self.marker_flag_manager.rowconfigure(1, weight=1)
        self.marker_flag_manager.columnconfigure(0, weight=1)
        self._marker_flag_rows = {}
        self.marker_flag_tree.bind("<<TreeviewSelect>>", self._marker_flag_manager_selected)
        self._update_zoom_label()
        ttk.Label(self, textvariable=self.status, style="Status.TLabel", anchor="w", padding=5).pack(fill="x")
        self._install_shortcut_router()

    def open_stadium_network(self):
        """Open the isolated observation lab without changing editor state."""
        StadiumNetworkWindow(self)

    def _build_menu(self):
        bar = tk.Menu(self)
        file_menu = tk.Menu(bar, tearoff=False, postcommand=self._refresh_file_menu_state)
        self.file_menu = file_menu
        file_menu.add_command(label="Open Song...", command=self.open_json, accelerator="Ctrl+O")
        self.workspace_song_menu = tk.Menu(
            file_menu, tearoff=False, postcommand=self._rebuild_workspace_song_menu)
        file_menu.add_cascade(label="Open Song from Current Workspace",
                              menu=self.workspace_song_menu)
        self._workspace_song_cascade = file_menu.index("end")
        file_menu.add_command(label="Save", command=self.save, accelerator="Ctrl+S")
        file_menu.add_command(label="Save As...", command=self.save_as,
                              accelerator="Ctrl+Shift+S")
        file_menu.add_separator()
        import_menu = tk.Menu(file_menu, tearoff=False)
        import_menu.add_command(label="Import Backup from Stadium...",
                                command=self.import_stadium_backup)
        import_menu.add_command(label="Open Reapcase Workspace...",
                                command=self.open_stadium_workspace)
        file_menu.add_cascade(label="Import", menu=import_menu)
        export_menu = tk.Menu(file_menu, tearoff=False)
        export_menu.add_command(label="Build Stadium Backup...", command=self.build_stadium_backup)
        export_menu.add_command(label="Implant Stadium Backup...", command=self.implant_stadium_backup)
        export_menu.add_command(label="Update Audio on Stadium SD...", command=self.update_stadium_audio)
        file_menu.add_cascade(label="Export", menu=export_menu)
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
        view.add_radiobutton(label="Roadmap", variable=self.current_view, value="roadmap",
                             command=lambda: self.switch_view("roadmap"), accelerator="Ctrl+3")
        view.add_separator()
        view.add_checkbutton(label="Inspector", variable=self.inspector_visible,
                             command=self.apply_inspector_visibility, accelerator="Ctrl+E")
        view.add_checkbutton(label="Show FULL-SONG Ghost Waveform",
                             variable=self.full_song_ghost_visible,
                             command=self.toggle_full_song_ghost, accelerator="Ctrl+G")
        view.add_command(label="Lane Manager...", command=self.toggle_lane_manager, accelerator="Ctrl+L")
        # Ctrl+M used to be advertised for Track Manager, while the established
        # Structure Manager command was Ctrl+R.  Structure navigation owns
        # Ctrl+M now; Track Manager remains available here without a conflicting
        # accelerator.
        view.add_command(label="Track Manager...", command=self.toggle_track_manager)
        view.add_checkbutton(label="Structure Manager",
                             variable=self.marker_manager_visible,
                             command=self.apply_sidebar_visibility, accelerator="Ctrl+M")
        view.add_checkbutton(label="Marker & Flag Manager",
                             variable=self.marker_flag_manager_visible,
                             command=self.apply_sidebar_visibility, accelerator="Ctrl+F")
        view.add_separator()
        view.add_command(label="Zoom Entire Song", command=self.fit_song, accelerator="F")
        view.add_command(label="Zoom to Selection", command=self.fit_selection, accelerator="Shift+F")
        view.add_command(label="Exit Lane Focus", command=self.exit_lane_focus)
        bar.add_cascade(label="View", menu=view)
        tools = tk.Menu(bar, tearoff=False)
        tools.add_command(label="Analyze Song", command=self.analyze_song)
        tools.add_separator()
        tools.add_command(label="Stadium Network... (EXPERIMENTAL)",
                          command=self.open_stadium_network)
        bar.add_cascade(label="Tools", menu=tools)
        self.midi_menu = tk.Menu(bar, tearoff=False,
                                 postcommand=self._refresh_midi_menu_labels)
        for destination in MidiDestination:
            self.midi_menu.add_command(
                label=DESTINATION_LABELS[destination],
                command=lambda value=destination: self.configure_midi_destination(value))
        self.midi_menu.add_separator()
        self.midi_menu.add_command(label="Refresh MIDI Devices",
                                   command=self.refresh_midi_devices)
        bar.add_cascade(label="MIDI", menu=self.midi_menu)
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

    def _install_shortcut_router(self):
        """Install the editor's single semantic keyboard command layer."""
        callbacks = {
            EditorCommand.PLAY_PAUSE: self.play_pause,
            EditorCommand.COPY_EVENTS: self.copy_events,
            EditorCommand.PASTE_EVENTS: self.paste_events,
            EditorCommand.UNDO: self.undo,
            EditorCommand.SAVE: self.save,
            EditorCommand.SAVE_AS: self.save_as,
            EditorCommand.OPEN: self.open_json,
            EditorCommand.DELETE: self.delete_selected,
            EditorCommand.ESCAPE: self.clear_time_selection,
            EditorCommand.DUPLICATE: self.duplicate_selected,
            EditorCommand.FIT_SONG: self.fit_song,
            EditorCommand.FIT_SELECTION: self.fit_selection,
            EditorCommand.NEXT_EVENT: lambda: self.navigate_event(1),
            EditorCommand.PREVIOUS_EVENT: lambda: self.navigate_event(-1),
            EditorCommand.NEXT_MARKER: lambda: self.navigate_marker(1),
            EditorCommand.PREVIOUS_MARKER: lambda: self.navigate_marker(-1),
            EditorCommand.SONG_START: lambda: self.go_song_edge(False),
            EditorCommand.SONG_END: lambda: self.go_song_edge(True),
            EditorCommand.PREVIOUS_REGION: lambda: self.navigate_region(-1),
            EditorCommand.NEXT_REGION: lambda: self.navigate_region(1),
            EditorCommand.ZOOM_IN: lambda: self.zoom_step(1.25),
            EditorCommand.ZOOM_OUT: lambda: self.zoom_step(1 / 1.25),
            EditorCommand.TIMELINE_VIEW: lambda: self.switch_view("timeline"),
            EditorCommand.EVENT_LIST_VIEW: lambda: self.switch_view("event_list"),
            EditorCommand.ROADMAP_VIEW: lambda: self.switch_view("roadmap"),
            EditorCommand.INSPECTOR: self.toggle_inspector,
            EditorCommand.STRUCTURE_MANAGER: self.open_marker_manager,
            EditorCommand.MARKER_MANAGER: self.open_marker_flag_manager,
            EditorCommand.LANE_MANAGER: self.toggle_lane_manager,
            EditorCommand.GHOST: self.toggle_ghost_preference,
        }
        self.shortcut_router = ShortcutRouter(self, callbacks)
        self.shortcut_router.register(self.timeline_frame, KeyboardContext.TIMELINE)
        self.shortcut_router.register(self.event_list_frame, KeyboardContext.EVENT_LIST)
        self.shortcut_router.register(self.marker_manager, KeyboardContext.MANAGER)
        self.shortcut_router.register(self.marker_flag_manager, KeyboardContext.MANAGER)
        self.shortcut_router.register(self.roadmap_view, KeyboardContext.ROADMAP)
        self.shortcut_router.install()

    def _refresh_midi_menu_labels(self):
        for index, destination in enumerate(MidiDestination):
            label = DESTINATION_LABELS[destination]
            self.midi_menu.entryconfigure(index,
                label=f"{label.title()}...     {self.midi_router.status(destination)}")

    def refresh_midi_devices(self):
        ports = self.midi_router.refresh()
        self._refresh_midi_menu_labels()
        self.status.set(f"MIDI devices refreshed ({len(ports)} output port(s))")

    def configure_midi_destination(self, destination):
        route = self.midi_router.routes[destination]
        window = tk.Toplevel(self)
        window.title(f"{DESTINATION_LABELS[destination]} MIDI")
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=12)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="Output Port").grid(row=0, column=0, sticky="w")
        missing = route.port and route.port not in self.midi_router.available_ports
        configured_label = f"{route.port} (not connected)" if missing else route.port
        options = ["Not configured"] + list(self.midi_router.available_ports)
        if missing:
            options.append(configured_label)
        port = tk.StringVar(value=configured_label or "Not configured")
        ttk.Combobox(frame, textvariable=port, values=options, state="readonly", width=38).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(2, 10))
        ttk.Label(frame, text="Channel").grid(row=2, column=0, sticky="w")
        channel = tk.IntVar(value=route.channel)
        ttk.Combobox(frame, textvariable=channel, values=tuple(range(1, 17)),
                     state="readonly", width=5).grid(row=3, column=0, sticky="w", pady=(2, 10))
        ttk.Label(frame, text=self.midi_router.status(destination)).grid(
            row=3, column=1, sticky="e", padx=(20, 0))

        def save():
            selected = port.get()
            if missing and selected == configured_label:
                selected = route.port
            elif selected == "Not configured":
                selected = None
            self.midi_router.configure(destination, selected, channel.get())
            window.destroy()
            self._refresh_midi_menu_labels()

        ttk.Button(frame, text="Cancel", command=window.destroy).grid(row=4, column=0, sticky="e")
        ttk.Button(frame, text="Save", command=save).grid(row=4, column=1, sticky="e", padx=(8, 0))
        self._prepare_dialog(window, f"midi_{destination.value}", save)

    def midi_settings(self):
        """Open the app-level Stadium route (legacy API entry point)."""
        self.configure_midi_destination(MidiDestination.STADIUM)

    def invalidate_workspace_song_inventory(self):
        self._workspace_song_inventory = None

    def _refresh_file_menu_state(self):
        state = "normal" if self.stadium_workspace else "disabled"
        self.file_menu.entryconfigure(self._workspace_song_cascade, state=state)

    def workspace_songs(self):
        if not self.stadium_workspace:
            return ()
        if self._workspace_song_inventory is None:
            try:
                self._workspace_song_inventory = discover_workspace_songs(self.stadium_workspace)
            except ValueError:
                self.stadium_workspace = None
                return ()
        return self._workspace_song_inventory

    def _rebuild_workspace_song_menu(self):
        self.workspace_song_menu.delete(0, "end")
        songs = self.workspace_songs()
        current = self.model.path.resolve() if self.model else None
        for song in songs:
            prefix = "✓ " if current == song.path else ""
            self.workspace_song_menu.add_command(
                label=prefix + song.label,
                command=lambda path=song.path: self.open_workspace_song(path))
        if not songs:
            self.workspace_song_menu.add_command(
                label="No workspace Songs", state="disabled")

    def open_workspace_song(self, path):
        allowed = {song.path for song in self.workspace_songs()}
        candidate = Path(path).resolve()
        return self._begin_song_open(candidate) if candidate in allowed else False

    # Kept as small compatibility entry points for third-party extensions.
    # Reapcase itself does not bind through these legacy focus policies.
    def _editor_shortcut(self, event, command, *args):
        if not editor_shortcuts_allowed(getattr(event, "widget", None), self.canvas):
            return None
        command(*args)
        return "break"

    def _global_editor_shortcut(self, event, command, *args,
                                allow_native_navigation=True, allow_text_input=False):
        if not global_editor_shortcuts_allowed(
                getattr(event, "widget", None), self,
                allow_native_navigation=allow_native_navigation,
                allow_text_input=allow_text_input):
            return None
        command(*args)
        return "break"

    def set_stadium_workspace(self, workspace):
        """Activate only an explicitly validated imported workspace."""
        workspace = Path(workspace).resolve()
        load_manifest(workspace)
        self.stadium_workspace = workspace
        self.invalidate_workspace_song_inventory()
        return self.workspace_songs()

    def open_stadium_workspace(self):
        path = filedialog.askdirectory(title="Open Reapcase Workspace")
        if not path:
            return False
        try:
            self.set_stadium_workspace(path)
        except ValueError as exc:
            messagebox.showerror("Cannot open workspace", str(exc), parent=self)
            return False
        self.status.set("Opened Stadium workspace: %s" % Path(path).name)
        return True

    def switch_view(self, view):
        self.view_state.switch(view); self.current_view.set(view)
        self.timeline_frame.grid_forget(); self.event_list_frame.grid_forget()
        self.roadmap_view.grid_forget()
        if view == "timeline":
            self.timeline_frame.grid(row=0, column=0, sticky="nsew")
            if self.model:
                self.jump_to_units(self.model._units(self.model.cursor))
            else:
                self.redraw()
        elif view == "event_list":
            self.event_list_frame.grid(row=0, column=0, sticky="nsew")
            self._refresh_event_list()
        else:
            self.roadmap_view.grid(row=0, column=0, sticky="nsew")
            self._refresh_roadmap()

    def _refresh_roadmap(self):
        if self.model:
            self.roadmap_view.render(build_roadmap_document(
                self.model, self.model.roadmap_metadata()))

    def _roadmap_navigate(self, block):
        if not self.model:
            return
        self.model.cursor = block.source_position
        self.transport_position.set(
            f"{self.model.timing_map.position_to_seconds(block.source_position):07.3f}   |   "
            f"{block.source_position.render()}")

    def _roadmap_edit_note(self, block):
        if not self.model:
            return
        measure = block.start_measure
        current = self.model.roadmap_metadata()["notes"].get(str(measure), "")
        text = simpledialog.askstring("Roadmap Note", f"Note for measure {measure}:",
                                      initialvalue=current, parent=self)
        if text is not None and self.model.set_roadmap_note(measure, text):
            self._refresh_roadmap()

    def _roadmap_layout(self, value):
        if self.model and self.model.set_roadmap_measures_per_row(value):
            self._refresh_roadmap()

    def apply_inspector_visibility(self):
        self.apply_sidebar_visibility()

    def apply_sidebar_visibility(self):
        inspector = self.inspector_visible.get()
        manager = self.marker_manager_visible.get()
        flag_manager = (self.marker_flag_manager_visible.get()
                        if hasattr(self, "marker_flag_manager_visible") else False)
        if inspector or manager or flag_manager:
            self.right_sidebar.grid(row=0, column=1, sticky="nsew")
            self.main_content.columnconfigure(1, minsize=270)
            self._refresh_inspector()
        else:
            self.right_sidebar.grid_forget()
            self.main_content.columnconfigure(1, minsize=0)
        if inspector:
            self.inspector.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        else:
            self.inspector.grid_forget()
        if manager:
            self.marker_manager.grid(row=1, column=0, sticky="nsew")
            self._refresh_marker_manager()
        else:
            self.marker_manager.grid_forget()
        if flag_manager:
            self.marker_flag_manager.grid(row=2, column=0, sticky="nsew")
            self._refresh_marker_flag_manager()
        elif hasattr(self, "marker_flag_manager"):
            self.marker_flag_manager.grid_forget()
        visible = sum((inspector, manager, flag_manager))
        for row, shown in enumerate((inspector, manager, flag_manager)):
            self.right_sidebar.rowconfigure(row, weight=1 if shown else 0)

    def toggle_inspector(self):
        self.inspector_visible.set(not self.inspector_visible.get())
        self.apply_inspector_visibility()

    def toggle_marker_manager(self):
        self.marker_manager_visible.set(not self.marker_manager_visible.get())
        self.apply_sidebar_visibility()

    def open_marker_manager(self):
        """Show the canonical docked manager (legacy menu/API entry point)."""
        self.marker_manager_visible.set(True)
        self.apply_sidebar_visibility()
        self.marker_tree.focus_set()
        return self.marker_manager

    def open_marker_flag_manager(self):
        """Expose and focus the canonical docked Marker & Flag Manager."""
        self.marker_flag_manager_visible.set(True)
        self.apply_sidebar_visibility()
        self.marker_flag_tree.focus_set()
        return self.marker_flag_manager

    def _refresh_marker_manager(self):
        if not hasattr(self, "marker_tree"):
            return
        self.marker_tree.delete(*self.marker_tree.get_children())
        self._marker_rows = {}
        if not self.model:
            return
        configured = set()
        for number, row in enumerate(structure_manager_rows(self.model)):
            role = row.kind
            tag = "role_" + role.casefold().replace(" ", "_").replace("/", "_")
            if tag not in configured:
                background, foreground = manager_row_palette(role)
                options = {"background": background, "foreground": foreground}
                if role == "PAUSE":
                    options["font"] = ("TkDefaultFont", 9, "bold")
                self.marker_tree.tag_configure(tag, **options)
                configured.add(tag)
            item = self.marker_tree.insert("", "end", iid=str(number),
                                           values=(row.kind, row.name), tags=(tag,))
            self._marker_rows[item] = row

    def _marker_manager_selected(self, _event=None):
        selected = self.marker_tree.selection()
        if selected and selected[0] in self._marker_rows:
            row = self._marker_rows[selected[0]]
            self.navigate_to_event(
                row.units, select_index=row.indices[0] if len(row.indices) == 1 else None,
                vertical=False, reveal_lane=row.lane)
        return "break"

    def _marker_manager_ctrl_click(self, event):
        """Turn actual REGION rows into a continuous rehearsal range."""
        item = self.marker_tree.identify_row(event.y)
        row = self._marker_rows.get(item)
        if not row or row.kind != "REGION" or row.end_units is None:
            return "break"
        if self.has_time_selection():
            self.extend_time_selection(row.units, row.end_units)
        else:
            self.set_time_selection(row.units, row.end_units)
        # Reveal horizontally without the vertical scrolling performed by
        # ordinary manager navigation.
        self.jump_to_units(row.units, vertical=False)
        self.redraw("structure manager time selection")
        return "break"

    def set_time_selection(self, start_units, end_units):
        """Set an ordered, non-empty editor-session range in musical units."""
        start, end = sorted((max(0, int(start_units)), max(0, int(end_units))))
        if start == end:
            return False
        self.time_selection_start_units = start
        self.time_selection_end_units = end
        return True

    def extend_time_selection(self, start_units, end_units):
        if not ReapcaseEditor.has_time_selection(self):
            return ReapcaseEditor.set_time_selection(self, start_units, end_units)
        return ReapcaseEditor.set_time_selection(self,
            min(self.time_selection_start_units, int(start_units)),
            max(self.time_selection_end_units, int(end_units)))

    def clear_time_selection(self):
        if not ReapcaseEditor.has_time_selection(self):
            return False
        self.time_selection_start_units = None
        self.time_selection_end_units = None
        self._time_selection_drag = None
        self.redraw("clear time selection")
        return True

    def has_time_selection(self):
        start = getattr(self, "time_selection_start_units", None)
        end = getattr(self, "time_selection_end_units", None)
        return start is not None and end is not None and start < end

    def time_selection_range(self):
        return ((self.time_selection_start_units, self.time_selection_end_units)
                if ReapcaseEditor.has_time_selection(self) else None)

    def _refresh_marker_flag_manager(self):
        if not hasattr(self, "marker_flag_tree"):
            return
        self.marker_flag_tree.delete(*self.marker_flag_tree.get_children())
        self._marker_flag_rows = {}
        if not self.model:
            return
        enabled = {lane for lane, variable in self.marker_flag_filters.items() if variable.get()}
        for number, row in enumerate(marker_flag_manager_rows(self.model, enabled)):
            tag = "lane_" + row.lane.casefold().replace(" ", "_").replace("/", "_")
            background, foreground = manager_row_palette(row.lane)
            self.marker_flag_tree.tag_configure(tag, background=background,
                                                foreground=foreground)
            item = self.marker_flag_tree.insert("", "end", iid=str(number),
                                                values=(row.kind, row.name), tags=(tag,))
            self._marker_flag_rows[item] = row

    def _marker_flag_manager_selected(self, _event=None):
        selected = self.marker_flag_tree.selection()
        if selected and selected[0] in self._marker_flag_rows:
            row = self._marker_flag_rows[selected[0]]
            self.navigate_to_event(row.units, select_index=row.indices[0],
                                   vertical=False, reveal_lane=row.lane)
        return "break"

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

    def _clear_ghost_waveform(self):
        """Discard every rendered or cached ghost-waveform artifact."""
        self.canvas.delete("ghost-waveform")
        if (self._ghost_waveform_image is not None and
                self._ghost_waveform_image in self._waveform_images):
            self._waveform_images.remove(self._ghost_waveform_image)
        self._ghost_raster_cache.clear()
        self._ghost_raster_coverage = None
        self._ghost_refresh_pending = False
        self._ghost_waveform_image = None

    def _reset_full_song_ghost(self):
        """Clear Song-specific rendering while retaining the user preference."""
        self._clear_ghost_waveform()

    def toggle_full_song_ghost(self):
        """Apply and persist the user-local View preference outside Song data."""
        visible = bool(self.full_song_ghost_visible.get())
        update_preferences(lambda data: data.update(full_song_ghost_visible=visible))
        if not visible:
            self._clear_ghost_waveform()
        self.redraw()

    def toggle_ghost_preference(self):
        self.full_song_ghost_visible.set(not self.full_song_ghost_visible.get())
        self.toggle_full_song_ghost()

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
        units = self.model._units(self.model.timeline.events[index].position)
        self.jump_to_units(units, select_index=index)
        return "break"

    def analyze_song(self):
        """Open a non-modal report based on the current in-memory Timeline."""
        if not self.model:
            self.status.set("Open a Song before running analysis"); return
        win = getattr(self, "_analysis_window", None)
        if win is None or not win.winfo_exists():
            win = tk.Toplevel(self); win.title("Analyze Song"); win.geometry("920x680")
            self._analysis_window = win
            outer = ttk.Frame(win, padding=12); outer.pack(fill="both", expand=True)
            win.summary = tk.StringVar(); ttk.Label(outer, textvariable=win.summary,
                justify="left", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
            config = ttk.LabelFrame(outer, text="START / END CHECKLIST", padding=8)
            config.pack(fill="x", pady=8); win.config_vars = {}
            cfg = self.model.analysis_config()
            fields = (("initialization_before_bar","Initialization before bar"),
                      ("bass_snapshot","Bass snapshot"),
                      ("max_hold_bars","Maximum pedal hold (bars)"),("close_tolerance_ticks","Close tolerance (ticks)"))
            for col,(name,label) in enumerate(fields):
                ttk.Label(config,text=label).grid(row=0,column=col,sticky="w",padx=3)
                var=tk.StringVar(value=str(getattr(cfg,name))); win.config_vars[name]=var
                ttk.Entry(config,textvariable=var,width=10).grid(row=1,column=col,padx=3)
            checks=(("require_bass_program_zero","BASS PRG 0"),("require_bass_program_nonzero","BASS PRG != 0"),("require_target_bass_snapshot","BASS SNAP"),("require_exp_1","EXP 1 = 0"),("require_exp_2","EXP 2 = 0"),("enforce_start_order","Enforce order"),("require_clear_loop","CLEAR LOOP"),("reject_current","Reject CURRENT"),("check_exp_end","Expression rest at END"))
            for n,(name,label) in enumerate(checks):
                var=tk.BooleanVar(value=getattr(cfg,name)); win.config_vars[name]=var
                ttk.Checkbutton(config,text=label,variable=var).grid(row=2+n//5,column=n%5,sticky="w",padx=3)
            buttons=ttk.Frame(outer); buttons.pack(fill="x")
            ttk.Button(buttons,text="Analyze / Re-run Analysis",command=self._rerun_analysis).pack(side="left")
            ttk.Button(buttons,text="Save configuration",command=self._save_analysis_config).pack(side="left",padx=6)
            columns=("severity","category","position","message")
            win.results=ttk.Treeview(outer,columns=columns,show="headings")
            for name,width in zip(columns,(90,120,90,570)): win.results.heading(name,text=name.upper()); win.results.column(name,width=width)
            win.results.pack(fill="both",expand=True,pady=(8,0)); win.results.bind("<Double-1>",self._analysis_activate)
        win.lift(); self._rerun_analysis()

    def _analysis_config_from_window(self):
        cfg=self.model.analysis_config(); vars=self._analysis_window.config_vars
        for name,var in vars.items():
            old=getattr(cfg,name); raw=var.get()
            try: value=bool(raw) if isinstance(old,bool) else type(old)(raw)
            except ValueError: value=old
            setattr(cfg,name,value)
        return cfg

    def _save_analysis_config(self):
        self.model.set_analysis_config(self._analysis_config_from_window())
        self.status.set("Analysis configuration staged; Save writes only the Reapcase sidecar")

    def _rerun_analysis(self):
        win=self._analysis_window; report=SongAnalyzer().analyze(self.model,self._analysis_config_from_window()); win.report=report
        s=report.summary; counts=Counter(r.severity for r in report.results)
        loop=[]
        for device,actions in s.looper_actions.items(): loop.append(f"{device} Looper: " + (" / ".join(f"{k} {v}" for k,v in actions.items()) if actions else "NONE"))
        bpm = f"{s.bpm:g}" if s.bpm is not None else "—"
        positions = f"First {s.first_position.render() if s.first_position else '—'}   Last {s.last_position.render() if s.last_position else '—'}   END {s.end_position.render() if s.end_position else '—'}"
        if s.end_behavior:
            positions += f"   End behavior: {s.end_behavior}"
        win.summary.set(f"SONG SUMMARY — {s.name}\nDuration {int(s.duration_seconds//60):02d}:{s.duration_seconds%60:06.3f}   Bars {s.bars}   Regions {s.regions}   BPM {bpm}   Time signature {s.time_signature}\n{positions}\n" + "   ".join(f"{k} {v}" for k,v in s.inventory.items()) + "\n" + "   ".join(loop) + f"\n\nANALYSIS   {counts[Severity.ERROR]} ERRORS   {counts[Severity.WARNING]} WARNINGS   {counts[Severity.INFO]} INFO" + ("\n✓ No critical issue detected" if not counts[Severity.ERROR] else ""))
        win.results.delete(*win.results.get_children())
        for n,r in enumerate(report.results): win.results.insert("", "end",iid=str(n),values=(r.severity.value,r.category,r.position.render() if r.position else "—",r.message))

    def _analysis_activate(self, _event=None):
        win=self._analysis_window; selected=win.results.selection()
        if selected:
            result=win.report.results[int(selected[0])]
            if result.event_index is not None and result.event_index < len(self.model.timeline.events): self._navigate_to_index(result.event_index)

    def navigate_event(self, direction):
        if not self.model: return "break"
        current = self.model._units(self.model.cursor)
        visible = [lane for lane, shown in self._effective_lane_visibility().items() if shown]
        return self._navigate_to_index(adjacent_event_index(self.model, current, direction, visible))

    def navigate_marker(self, direction):
        if not self.model: return "break"
        return self._navigate_to_index(adjacent_marker_index(
            self.model, self.model._units(self.model.cursor), direction))

    def navigate_region(self, direction):
        if not self.model: return "break"
        return self._navigate_to_index(adjacent_structure_region_index(
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
            role = event_list_role(row.lane, row.kind, pause=row.role == "PAUSE")
            tag = "role_" + role.casefold().replace(" ", "_").replace("/", "_")
            background, foreground = manager_row_palette(role)
            options = {"background": background, "foreground": foreground}
            if role == "PAUSE":
                options["font"] = ("TkDefaultFont", 9, "bold")
            self.event_tree.tag_configure(tag, **options)
            item = self.event_tree.insert("", "end", iid=str(row.index),
                values=(row.position, row.lane, row.kind, row.name, row.details), tags=(tag,))
            self._event_rows[item] = row
        self.event_tree.selection_set([str(i) for i in self.model.selected if str(i) in self._event_rows])

    def _sort_event_list(self, column):
        values = [(self.event_tree.set(item, column), item) for item in self.event_tree.get_children("")]
        values.sort(key=lambda pair: pair[0].casefold())
        for index, (_, item) in enumerate(values): self.event_tree.move(item, "", index)

    def _event_list_selected(self, _event=None):
        if self.model:
            selected = self.event_tree.selection()
            self.model.selected = {int(item) for item in selected}
            if selected:
                row = self._event_rows[selected[0]]
                self.navigate_to_event(row.units, select_index=row.index,
                                       vertical=False, reveal_lane=row.lane)
            else:
                self._refresh_inspector()

    def _event_list_edit(self, _event=None):
        selected = self.event_tree.selection()
        if selected: self.edit_event(int(selected[0]))

    def _event_list_go_to(self, _event=None):
        selected = self.event_tree.selection()
        if selected and self.model:
            row = self._event_rows[selected[0]]
            self.navigate_to_event(row.units, select_index=row.index,
                                   vertical=False, reveal_lane=row.lane)
        return "break"

    def navigate_to_event(self, units, *, select_index=None, horizontal=True,
                          vertical=False, reveal_lane=None):
        """Navigate to an event, with independent horizontal and lane reveal."""
        if not self.model: return
        if select_index is not None:
            self.model.selected = {select_index}
        self.seek_units(units)
        self._follow_suspended_until = time.monotonic() + .8
        if horizontal or vertical:
            region = self.canvas.cget("scrollregion").split()
            total = float(region[2]) if len(region) == 4 else 1.0
            previous = self.canvas.canvasx(0)
            if horizontal:
                left = jump_viewport_left(units, self.model.song.ppqn, self.pixels_per_beat,
                                          self.canvas.winfo_width())
                self.canvas.xview_moveto(left / max(1.0, total))
            if vertical and reveal_lane:
                layout = visible_lane_layout(self.lane_order, {
                    lane: variable.get() for lane, variable in self.lane_visibility.items()})
                if reveal_lane in layout.tops:
                    height = max(1.0, float(region[3]) if len(region) == 4 else 1.0)
                    self.canvas.yview_moveto(
                        max(0.0, layout.tops[reveal_lane] - RULER_HEIGHT) / height)
            self._update_fixed_headers_for_scroll(previous)
        self._refresh_inspector()
        # Tk scroll mutations above are queued before this idle callback.  This
        # both paints the final viewport and coalesces rapid Treeview selections.
        self.request_redraw("event navigation")

    def jump_to_units(self, units, *, select_index=None, horizontal=True,
                      vertical=False, reveal_lane=None):
        """Compatibility entry point for callers migrating to navigate_to_event."""
        return self.navigate_to_event(units, select_index=select_index,
                                      horizontal=horizontal, vertical=vertical,
                                      reveal_lane=reveal_lane)

    def _manager(self, family, title, build):
        existing = self._manager_windows.get(family)
        if existing and existing.winfo_exists(): existing.deiconify(); existing.lift(); existing.focus_force(); return existing
        win = tk.Toplevel(self); win.title(title); self._manager_windows[family] = win
        build(win)
        self._prepare_dialog(win, family, modal=False)
        return win

    def _toggle_manager(self, family, opener):
        existing = self._manager_windows.get(family)
        if existing and existing.winfo_exists() and existing.winfo_viewable():
            existing.withdraw(); return existing
        return opener()

    def toggle_lane_manager(self):
        return self._toggle_manager("lane_manager", self.open_lane_manager)

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

    def open_track_manager(self):
        """Expose the existing audio-track operations without duplicating their logic."""
        def build(win):
            body = ttk.Frame(win, padding=10); body.pack(fill="both", expand=True)
            ttk.Label(body, text="Audio tracks are managed in the Timeline AUDIO lane.").pack(anchor="w")
            ttk.Button(body, text="Add Audio Track…", command=self.add_audio_track_dialog).pack(anchor="w", pady=6)
            ttk.Button(body, text="Show AUDIO Lane", command=lambda: (
                self.lane_visibility["AUDIO"].set(True), self.redraw())).pack(anchor="w")
        return self._manager("track_manager", "Track Manager", build)

    def toggle_track_manager(self):
        return self._toggle_manager("track_manager", self.open_track_manager)

    def _refresh_navigation(self):
        """Rebuild model-derived utility rows after a structural model change.

        This deliberately is not part of :meth:`redraw`: transport animation,
        scrolling, and playhead motion repaint the canvas without touching Tk
        Treeviews.
        """
        if self.current_view.get() == "event_list": self._refresh_event_list()
        self._refresh_marker_manager()
        self._refresh_marker_flag_manager()

    def _redraw_after_model_change(self):
        self._refresh_navigation()
        self.redraw()

    def _rebuild_live_event_set(self):
        """Synchronize LIVE with canonical editor state, independently of redraw/save."""
        if self.model:
            self.live_midi.load(build_live_event_set(
                self.model.timeline.events, self.model.decoder, self.model._units))
            self.live_midi.set_timing_map(self.model.tempo_map.units_to_seconds)
            self.live_midi.set_pause_boundaries(
                (self.model._units(event.position),
                 event.source_index if event.source_index is not None else order)
                for order, event in enumerate(self.model.timeline.events)
                if event.source.type == "MARKER" and is_pause_marker(event))

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

    def open_json(self):
        if self.loading: return
        remembered = self._load_last_song_folder()
        initial = initial_song_folder(self.stadium_workspace, remembered)
        SongBrowser(self, initial, self._begin_song_open, self.stadium_workspace,
                    self._remember_song_folder)

    def _load_last_song_folder(self):
        try:
            data = json.loads(application_config_path().read_text(encoding="utf-8"))
            value = data.get("last_song_folder") if isinstance(data, dict) else None
            return Path(value) if value else None
        except (OSError, ValueError, TypeError):
            return None

    def _remember_song_folder(self, folder):
        path = application_config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict): data = {}
            except (OSError, ValueError, TypeError):
                data = {}
            data["last_song_folder"] = str(Path(folder).resolve())
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _require_stadium_workspace(self):
        if self.stadium_workspace:
            try:
                load_manifest(self.stadium_workspace)
                return Path(self.stadium_workspace)
            except ValueError:
                self.stadium_workspace = None
        messagebox.showinfo("Imported workspace required",
                            "This operation requires an imported Stadium workspace.\n\n"
                            "Use File → Import → Import Backup from Stadium first.")
        return None

    def _close_editor(self):
        """Stop UI dispatch before destroying Tk; in-flight atomic work may finish safely."""
        self._migration_operations.close()
        self.live_midi.stop()
        self.live_midi.load(())
        self._live_midi_pool.shutdown(wait=False, cancel_futures=True)
        self.midi_router.close()
        self.destroy()

    def _run_migration(self, name, phase, function, success, error_title, progress_queue=None):
        """Run migration I/O on the dedicated worker and dispatch results on Tk."""
        if self._migration_operations.active:
            messagebox.showinfo("Migration already in progress",
                                "Wait for the current Stadium operation to finish.")
            return False
        window = tk.Toplevel(self)
        self._migration_window = window
        window.title("Stadium operation")
        window.resizable(False, False)
        phase_label = ttk.Label(window, text=phase, padding=(22, 18, 22, 8))
        phase_label.pack(fill="x")
        progress = ttk.Progressbar(window,
                                   mode="determinate" if progress_queue else "indeterminate",
                                   length=380, maximum=100)
        progress.pack(padx=22, pady=(4, 18)); progress.start(12)
        ttk.Label(window, text="This stage will finish at a safe boundary. Closing is disabled.",
                  padding=(22, 0, 22, 16)).pack(fill="x")
        window.protocol("WM_DELETE_WINDOW", lambda: None)
        self._prepare_dialog(window, "stadium_progress", cancel=lambda: None)
        if not self._migration_operations.start(name, function):
            window.destroy(); self._migration_window = None
            return False

        def poll():
            if self._migration_operations.closed or not self.winfo_exists():
                return
            if progress_queue is not None:
                latest = None
                try:
                    while True: latest = progress_queue.get_nowait()
                except Empty:
                    pass
                if latest is not None:
                    detail = (" — %s" % Path(latest.current_file).name
                              if latest.current_file else "")
                    phase_label.configure(text="%s%s — %d%%" %
                                          (latest.phase, detail, latest.percent))
                    progress.configure(value=latest.percent)
            result = self._migration_operations.poll()
            if result is None:
                self.after(50, poll)
                return
            if window.winfo_exists():
                try: window.grab_release()
                except tk.TclError: pass
                window.destroy()
            self._migration_window = None
            if result.error is not None:
                _present_migration_error(result.error, error_title, self)
            else:
                success(result.value)
        self.after(50, poll)
        return True

    def import_stadium_backup(self):
        archive = filedialog.askopenfilename(title="Import Backup from Stadium",
                                             filetypes=(("Stadium backup", "*.tar.gz"),))
        if not archive:
            return
        parent = filedialog.askdirectory(title="Choose Reapcase workspace parent",
                                         initialdir=str(Path(archive).parent))
        if not parent:
            return
        def inspected(_inspection):
            destination = unique_workspace(Path(parent), Path(archive))
            review = ("IMPORT STADIUM BACKUP\n\nArchive:\n%s\n\n"
                      "✓ archive readable\n✓ Stadium structure recognized\n"
                      "✓ Song workspace found\n✓ Audio workspace found\n\nDestination:\n%s\n\n"
                      "Original backup will be preserved." % (Path(archive).name, destination))
            if not messagebox.askokcancel("Import Stadium Backup", review, parent=self,
                                          icon=messagebox.INFO, default=messagebox.CANCEL):
                return
            self._run_migration("import", "Importing backup...",
                                lambda: import_backup(Path(archive), Path(parent), destination=destination),
                                imported, "Import failed")

        def imported(workspace):
            songs = self.set_stadium_workspace(workspace)
            self.status.set("Imported Stadium workspace: %s" % workspace.name)
            if songs:
                self._begin_song_open(str(songs[0].path))
        self._run_migration("inspect-import", "Scanning archive...",
                            lambda: inspect_import(Path(archive)), inspected, "Import preflight failed")

    def build_stadium_backup(self):
        workspace = self._require_stadium_workspace()
        if not workspace:
            return
        self._run_migration("analyze-build", "Comparing Songs and audio...",
                            lambda: analyze_build(workspace), self._show_build_review,
                            "Build analysis failed")

    def _show_build_review(self, plan):
        """Scrollable, readable confirmation; no package exists until its primary action."""
        window = tk.Toplevel(self); window.title("Build Stadium Backup")
        window.geometry("760x620"); window.minsize(620, 420)
        header = ttk.Frame(window, padding=12); header.pack(fill="x")
        ttk.Label(header, text="BUILD STADIUM BACKUP", font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        for label, value in (("Source backup", plan.source.name), ("Workspace", plan.workspace.name),
                             ("Reference used for comparison", plan.reference.name)):
            ttk.Label(header, text="%s:  %s" % (label, value)).pack(anchor="w", pady=(3, 0))
        body = tk.Text(window, wrap="word", padx=12, pady=10, relief="flat")
        scroll = ttk.Scrollbar(window, command=body.yview); body.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y"); body.pack(fill="both", expand=True)
        body.tag_configure("heading", font=("TkDefaultFont", 11, "bold"), spacing1=10)
        body.tag_configure("quiet", foreground="#777777")
        body.insert("end", "SONGS\n", "heading")
        for song in plan.songs:
            status = {"CHANGED": "MODIFIED", "ADDED": "ADDED", "UNCHANGED": "UNCHANGED"}[song.status]
            tag = "quiet" if song.status == "UNCHANGED" else None
            body.insert("end", "%s — %s\nstatus: %s\n" % (song.name, song.path, status), tag)
            if song.details:
                body.insert("end", "  " + "\n  ".join(song.details) + "\n")
            body.insert("end", "\n")
        added = sum(a.status == "ADDED" for a in plan.audio)
        changed = sum(a.status == "CHANGED" for a in plan.audio)
        unchanged = sum(a.status == "UNCHANGED" for a in plan.audio)
        body.insert("end", "AUDIO\n", "heading")
        body.insert("end", "%d added  •  %d changed  •  %d unchanged\n" % (added, changed, unchanged))
        body.insert("end", "PEAKS\n", "heading")
        body.insert("end", "%d .peak files will be removed; Stadium will rebuild them.\n" % plan.peak_count)
        body.insert("end", "REAPCASE FILES\n", "heading")
        body.insert("end", "%d files will be excluded.\n" % plan.excluded_count)
        body.insert("end", "BUILD ACTIONS\n", "heading")
        body.insert("end", ("%d Song replacements, %d audio replacements, %d audio additions.\n"
                            "%d source-backup files will be preserved conservatively; missing WIP files "
                            "will not be deleted.\n") %
                    (plan.song_replacements, changed, added, plan.source_file_count))
        body.configure(state="disabled")
        buttons = ttk.Frame(window, padding=12); buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="right", padx=(8, 0))
        def confirm():
            window.destroy()
            updates = Queue()
            self._run_migration("build", "Building and verifying package...",
                                lambda: build_package(plan, progress=updates.put),
                                self._build_complete, "Build failed", updates)
        ttk.Button(buttons, text="Build Package", command=confirm).pack(side="right")
        self._prepare_dialog(window, "stadium_build_review", primary=confirm, cancel=window.destroy)

    def _build_complete(self, package):
        self.status.set("Verified Stadium package built: %s" % package.name)
        if messagebox.askyesno("Build complete ✓",
                               "%s\n\n✓ Archive verified\n✓ Stadium structure valid\n"
                               "✓ Song JSON valid\n✓ Reapcase-only files excluded\n"
                               "✓ .peak caches removed\n\nImplant on Stadium SD now?" % package.name):
            self.implant_stadium_backup(package)

    def implant_stadium_backup(self, package=None):
        workspace = self._require_stadium_workspace()
        if not workspace:
            return
        if package is None:
            package = filedialog.askopenfilename(title="Choose verified Stadium package",
                                                  filetypes=(("Stadium backup", "*.tar.gz"),))
        if not package:
            return
        root = filedialog.askdirectory(title="Choose Stadium SD root")
        if not root:
            return
        def preflight(_root):
            destination = Path(root) / "backups" / Path(package).name
            if not messagebox.askokcancel("Implant Stadium Backup",
                    "IMPLANT STADIUM BACKUP\n\nStadium drive:\n%s\n\n"
                    "✓ backups/ detected\n✓ songs/ detected\n✓ clips/ detected\n"
                    "✓ Stadium SD recognized\n\nPackage:\n%s\n\nDestination:\n%s" %
                    (root, Path(package).name, destination), default=messagebox.CANCEL):
                return
            self._run_migration("implant", "Copying to Stadium SD and verifying copy...",
                                lambda: implant_package(Path(package), Path(root), workspace),
                                complete, "Implant failed")
        def complete(copied):
            messagebox.showinfo("Implant complete ✓",
                                "Package copied and verified.\n\n%s\n\n"
                                "✓ size verified\n✓ SHA-256 verified\n\nSafe to eject the Stadium drive." % copied)
        self._run_migration("implant-preflight", "Scanning Stadium SD...",
                            lambda: validate_sd_root(Path(root)), preflight, "Implant preflight failed")

    def update_stadium_audio(self):
        workspace = self._require_stadium_workspace()
        if not workspace:
            return
        root = filedialog.askdirectory(title="Choose Stadium SD root")
        if not root:
            return
        def analyzed(value):
            plan, build_plan = value
            song_changes = [s for s in build_plan.songs if s.status != "UNCHANGED"]
            warning = ""
            if song_changes:
                warning = ("\n\n⚠ SONG CHANGES ALSO DETECTED\n%d Songs differ from the last Reapcase reference.\n"
                           "Audio Update will NOT deploy these Song changes.\n"
                           "Use Build Stadium Backup to deploy them." % len(song_changes))
            review = ("UPDATE AUDIO ON STADIUM SD\n\n%d audio files will be replaced\n"
                      "%d audio files will be added\n%d unchanged\n\nPeaks:\n"
                      "%d Stadium .peak caches will be removed\nStadium will rebuild them%s" %
                      (plan.changed_count, plan.added_count,
                       sum(f.status == "UNCHANGED" for f in plan.files), plan.peak_count, warning))
            if not messagebox.askokcancel("Review Audio Update", review, default=messagebox.CANCEL):
                return
            self._run_migration("audio-update", "Copying audio and verifying files...",
                                lambda: apply_audio_update(plan), complete, "Audio Update failed")
        def complete(copied):
            messagebox.showinfo("Audio Update complete ✓",
                                "%d audio files copied and SHA-256 verified.\n"
                                "Stadium peak caches were cleared." % len(copied))
        self._run_migration("analyze-audio", "Comparing audio and Songs...",
                            lambda: (analyze_audio_update(workspace, Path(root)), analyze_build(workspace)),
                            analyzed, "Audio Update analysis failed")

    def _begin_song_open(self, path):
        """Start transactional Song loading; all Tk work stays in this thread."""
        if self.loading: return False
        if self.model and self.model.modified:
            choice = messagebox.askyesnocancel(
                "Unsaved Song changes",
                "Save changes before opening another Song?", parent=self)
            if choice is None:
                return False
            if choice:
                if not self._save_to(self.model.path):
                    return False
        self._audio_cancel.set()
        self._waveform_cancel.set()
        self._audio_cancel = threading.Event()
        self._waveform_cancel = threading.Event()
        audio_cancel = self._audio_cancel
        self.waveforms.clear(); self._waveform_pending.clear()
        self._initial_waveform_generation = generation = self._load_generation + 1
        self._initial_waveform_targets.clear(); self._initial_waveform_terminal.clear()
        self._initial_waveform_presentation_scheduled = False
        self._waveform_render_cache.clear(); self._waveform_photo_cache.clear()
        self._load_generation = generation
        self._audio_ready = False; self._audio_error = None
        if hasattr(self, "play_button"): self.play_button.state(["disabled"])
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
                candidate.set_timeline_change_listener(self._rebuild_live_event_set)
                self._rebuild_live_event_set()
                self._activate_workspace_for_song(candidate.path)
                self.monitor_muted = [False] * len(candidate.audio_tracks)
                self.monitor_solo = [False] * len(candidate.audio_tracks)
                self._reset_lane_visibility()
                self._reset_full_song_ghost()
                self._update_song_header()
                redraw_started = time.perf_counter()
                self._redraw_after_model_change()
                if os.environ.get("REAPCASE_LOAD_PERF", "").lower() in ("1", "true", "yes"):
                    LOG.debug("Song load timing: initial redraw %.1f ms",
                              (time.perf_counter() - redraw_started) * 1000)
            except Exception as exc:
                error = exc
            finally:
                self._finish_song_open()
            if error is not None:
                messagebox.showerror("Cannot open Song", str(error), parent=self)
            elif generation == self._load_generation:
                self._start_audio_resolution(candidate, generation, audio_cancel)
        self.after(0, poll)
        return True

    def _activate_workspace_for_song(self, path):
        """Update context only once the normal loading pipeline has succeeded."""
        workspace = workspace_for_song(path)
        if workspace:
            self.set_stadium_workspace(workspace)
        else:
            self.stadium_workspace = None
            self.invalidate_workspace_song_inventory()

    def _start_audio_resolution(self, model, generation, cancel):
        """Progressively apply immutable worker output on Tk's event loop."""
        total = len(model.audio_tracks)
        self._show_audio_progress(AudioProgress(AudioProgressPhase.RESOLVING_PATH,
                                               1 if total else None, total))
        updates = Queue()

        def work():
            try:
                resolved = []
                for result in model.audio_resolution_results(
                        self.manual_audio_root, cancel.is_set,
                        lambda message: updates.put(("progress", message))):
                    if cancel.is_set(): return
                    resolved.append(result.track)
                    updates.put(("track", result))
                if cancel.is_set(): return
                updates.put(("resolved", None))
                tracks = [PlaybackTrack(t.resolved_path, t.name, t.offset, t.file_info)
                          for t in resolved if t.resolved_path and t.file_info]
                updates.put(("progress", AudioProgress(
                    AudioProgressPhase.PREPARING_ENGINE, total, total)))
                started = time.perf_counter()
                prepared = self.audio_engine.prepare(tracks)
                if os.environ.get("REAPCASE_LOAD_PERF", "").lower() in ("1", "true", "yes"):
                    LOG.debug("Song load timing: audio engine prepare %.1f ms",
                              (time.perf_counter() - started) * 1000)
                if cancel.is_set():
                    prepared.close(); return
                queued_at = time.perf_counter()
                updates.put(("prepared", (prepared, queued_at)))
                if LOAD_PERF:
                    LOG.debug("UI audio: prepared result queued")
            except BaseException as exc:
                if not cancel.is_set(): updates.put(("error", exc))
        self._audio_pool.submit(work)
        completed = 0
        if LOAD_PERF:
            self._start_load_heartbeat(model, generation, cancel)

        def poll():
            nonlocal completed
            callback_started = time.perf_counter()
            if LOAD_PERF:
                LOG.debug("UI audio: queue callback start")
            if not self._audio_load_current(model, generation, cancel) or not self.winfo_exists():
                try:
                    while True:
                        kind, value = updates.get_nowait()
                        if kind == "prepared": value[0].close()
                except Empty:
                    pass
                return
            redraw_needed = False
            continuation = False
            try:
                while True:
                    kind, value = updates.get_nowait()
                    if kind == "track":
                        apply_started = time.perf_counter()
                        model.apply_audio_resolution(value)
                        if LOAD_PERF:
                            LOG.debug("UI audio: apply track %d %.1f ms", value.track.number,
                                      (time.perf_counter() - apply_started) * 1000)
                        completed += 1
                        self._show_audio_progress(AudioProgress(
                            AudioProgressPhase.TRACK_COMPLETE, completed, total,
                            value.track.filename))
                        redraw_needed = True
                    elif kind == "progress":
                        self._show_audio_progress(value, completed)
                    elif kind == "resolved":
                        self.audio_status.set("Audio: preparing engine…")
                    elif kind == "prepared":
                        prepared, queued_at = value
                        received_at = time.perf_counter()
                        if LOAD_PERF:
                            LOG.debug("UI audio: prepared result received by Tk %.1f ms after queue",
                                      (received_at - queued_at) * 1000)
                        if redraw_needed:
                            self._timed_loading_redraw(completed)
                            redraw_needed = False
                        self._commit_prepared_audio(prepared, model, generation, cancel,
                                                    received_at=received_at)
                        continuation = False
                        break
                    elif kind == "error":
                        self._set_audio_error(str(value))
                        continuation = False
                        break
                    if (redraw_needed and
                            time.perf_counter() - callback_started >= UI_AUDIO_POLL_BUDGET_SECONDS):
                        continuation = True
                        break
            except Empty:
                continuation = True
            if redraw_needed:
                self._timed_loading_redraw(completed)
            if LOAD_PERF:
                LOG.debug("UI audio: queue callback total %.1f ms",
                          (time.perf_counter() - callback_started) * 1000)
            if continuation and self._audio_load_current(model, generation, cancel):
                self.after(0 if not updates.empty() else 35, poll)
        self.after(0, poll)

    def _timed_loading_redraw(self, completed):
        started = time.perf_counter()
        self.redraw()
        if LOAD_PERF:
            LOG.debug("UI audio: redraw through track %d %.1f ms", completed,
                      (time.perf_counter() - started) * 1000)

    def _start_load_heartbeat(self, model, generation, cancel, interval_ms=100):
        """Report Tk starvation while the critical audio startup path is active."""
        expected = time.perf_counter() + interval_ms / 1000
        worst = 0.0
        def beat():
            nonlocal expected, worst
            delay = max(0.0, time.perf_counter() - expected)
            worst = max(worst, delay)
            LOG.debug("Tk heartbeat max delay: %.1f ms", worst * 1000)
            if self._audio_load_current(model, generation, cancel) and not self._audio_ready:
                expected = time.perf_counter() + interval_ms / 1000
                self.after(interval_ms, beat)
        self.after(interval_ms, beat)

    def _show_audio_progress(self, message, completed=0):
        """Update only header widgets; timeline redraws are track-result driven."""
        phase, index, total, filename = (message.phase, message.track_index,
                                         message.total_tracks or 0, message.filename)
        suffix = f" {index}/{total}" if index and total else ""
        suffix += f" — {filename}" if filename else ""
        if phase in (AudioProgressPhase.INDEXING_AUDIO,
                     AudioProgressPhase.PREPARING_ENGINE):
            self.audio_progress.stop()
            self.audio_progress.configure(mode="indeterminate", maximum=max(1, total), value=0)
            self.audio_progress.start(12)
            label = ("indexing Audio library…" if phase == AudioProgressPhase.INDEXING_AUDIO
                     else "preparing engine…")
            self.audio_status.set(f"Audio: {label}")
        elif phase in (AudioProgressPhase.RESOLVING_PATH, AudioProgressPhase.READING_HEADER):
            self.audio_progress.stop()
            self.audio_progress.configure(mode="indeterminate", maximum=max(1, total), value=0)
            self.audio_progress.start(12)
            verb = "locating" if phase == AudioProgressPhase.RESOLVING_PATH else "reading header"
            self.audio_status.set(f"Audio: {verb}{suffix}")
        elif phase == AudioProgressPhase.TRACK_COMPLETE:
            self.audio_progress.stop()
            self.audio_progress.configure(mode="determinate", maximum=max(1, total), value=completed)
            self.audio_status.set(f"Audio: {completed}/{total} ready")

    def _audio_load_current(self, model, generation, cancel):
        """Single guard for progress, engine readiness, and playback enablement."""
        return (generation == self._load_generation and model is self.model
                and not cancel.is_set())

    def _commit_prepared_audio(self, prepared, model, generation, cancel, received_at=None):
        """Perform the small, I/O-free engine state swap on Tk's thread."""
        if not self._audio_load_current(model, generation, cancel):
            prepared.close()
            return
        received_at = received_at or time.perf_counter()
        commit_started = time.perf_counter()
        self.audio_engine.commit(prepared)
        if LOAD_PERF:
            LOG.debug("UI audio: AudioEngine.commit() %.1f ms",
                      (time.perf_counter() - commit_started) * 1000)
        self.monitor_muted = [False] * len(model.audio_tracks)
        self.monitor_solo = [False] * len(model.audio_tracks)
        ready_started = time.perf_counter()
        self._set_audio_ready(model)
        if LOAD_PERF:
            LOG.debug("UI audio: _set_audio_ready() %.1f ms",
                      (time.perf_counter() - ready_started) * 1000)
            LOG.debug("UI audio: prepared-received to READY total %.1f ms",
                      (time.perf_counter() - received_at) * 1000)
        self.after_idle(lambda: self._request_initial_waveforms(model, generation, cancel))

    def _request_initial_waveforms(self, model, generation, cancel):
        """Queue eligible tracks in deterministic presentation priority.

        This is submission-order priority, not lazy scheduling: all eligible
        tracks may be submitted immediately.  The bounded executor ensures the
        visible tracks at the front of the queue begin before ordinary
        offscreen tracks.
        """
        if not self._audio_load_current(model, generation, cancel) or not self._audio_ready:
            return
        tracks = [track for track in model.audio_tracks
                  if track.resolved_path and track.file_info]
        targets = {str(track.resolved_path) for track in tracks}
        if getattr(self, "_initial_waveform_generation", None) != generation:
            self._initial_waveform_generation = generation
            self._initial_waveform_terminal = set()
        self._initial_waveform_targets = targets
        self._initial_waveform_terminal.intersection_update(targets)
        self._initial_waveform_terminal.update(
            targets.intersection(getattr(self, "waveforms", {})))
        self._initial_waveform_presentation_scheduled = False
        ReapcaseEditor._update_waveform_header(self, generation)
        # Submission order is executor priority. Prefer vertically visible
        # audio lanes; an optional FULL-SONG ghost is next, and offscreen lanes
        # cannot occupy both workers before visible material is queued.
        visible = set()
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            try:
                visibility = self._effective_lane_visibility()
                top = visible_lane_layout(self.lane_order, visibility).audio_top
                view_top = canvas.canvasy(0)
                view_bottom = view_top + canvas.winfo_height()
                visible = {index for index in range(len(model.audio_tracks))
                           if top + (index + 1) * LANE_HEIGHT >= view_top and
                           top + index * LANE_HEIGHT <= view_bottom}
            except (AttributeError, tk.TclError):
                pass
        ghost = (full_song_track(model.audio_tracks)
                 if all(hasattr(track, "name") for track in model.audio_tracks) else None)
        ghost_needed = bool(getattr(self, "full_song_ghost_visible", None) and
                            self.full_song_ghost_visible.get())
        indexed = list(enumerate(model.audio_tracks))
        priority = {id(track): (0 if index in visible else
                                1 if ghost_needed and track is ghost else 2, index)
                    for index, track in indexed}
        tracks.sort(key=lambda track: priority[id(track)])
        for track in tracks:
            self._request_waveform(track.resolved_path, generation)
        # Targeted invalidation schedules the first visible tiles as each result
        # arrives. Submission of every eligible source completes the initial
        # presentation plan; later viewport tile work is intentionally absent.
        self._initial_waveform_presentation_scheduled = True
        ReapcaseEditor._update_waveform_header(self, generation)

    def _update_waveform_header(self, generation=None):
        """Publish initial analysis progress without touching the Timeline."""
        current_generation = getattr(self, "_load_generation", generation)
        generation = current_generation if generation is None else generation
        if (generation != current_generation or not self._audio_ready or
                getattr(self, "_initial_waveform_generation", None) != generation):
            return
        if not hasattr(self, "audio_status") or not hasattr(self, "model"):
            return
        targets = self._initial_waveform_targets
        complete = len(targets.intersection(self._initial_waveform_terminal))
        waveforms = ("WAVEFORMS READY" if
                     complete == len(targets) and
                     self._initial_waveform_presentation_scheduled else
                     f"WAVEFORMS {complete}/{len(targets)}")
        self.audio_status.set(ReapcaseEditor._audio_ready_text(self.model) +
                              f"  •  {waveforms}")

    @staticmethod
    def _audio_ready_text(model):
        ready = sum(t.status == "ready" for t in model.audio_tracks)
        missing = sum(t.status == "missing" for t in model.audio_tracks)
        invalid = sum(t.status == "invalid" for t in model.audio_tracks)
        suffix = []
        if missing: suffix.append(f"{missing} missing")
        if invalid: suffix.append(f"{invalid} invalid")
        return ("Audio: READY" +
                (f" — {ready}/{len(model.audio_tracks)} resolved, " +
                 ", ".join(suffix) if suffix else ""))

    def _set_audio_ready(self, model):
        self._audio_ready = True
        self._audio_error = None
        self.play_button.state(["!disabled"])
        if hasattr(self, "audio_progress"):
            self.audio_progress.stop()
            self.audio_progress.configure(mode="determinate", maximum=max(1, len(model.audio_tracks)),
                                          value=len(model.audio_tracks))
        targets = {str(t.resolved_path) for t in model.audio_tracks
                   if getattr(t, "resolved_path", None) and
                   getattr(t, "file_info", None)}
        self._initial_waveform_generation = self._load_generation
        self._initial_waveform_targets = targets
        self._initial_waveform_terminal = targets.intersection(
            getattr(self, "waveforms", {}))
        self._initial_waveform_presentation_scheduled = False
        self.audio_status.set(ReapcaseEditor._audio_ready_text(model))
        ReapcaseEditor._update_waveform_header(self)

    def _set_audio_error(self, diagnostic):
        self._audio_ready = False; self._audio_error = diagnostic
        self.play_button.state(["disabled"])
        self.audio_status.set("Audio: ERROR")
        self.status.set(diagnostic)

    def _finish_song_open(self):
        if self._loading_window and self._loading_window.winfo_exists():
            try: self._loading_window.grab_release()
            except tk.TclError: pass
            self._loading_window.destroy()
        self._loading_window = None; self.loading = False

    @staticmethod
    def _lane_preferences_path():
        return application_config_path()

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
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict): data = {}
            except (OSError, ValueError, TypeError):
                data = {}
            data["lane_order"] = self.lane_order
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass  # Session persistence remains available on read-only homes.

    def _save_to(self, path):
        try:
            summary = self.model.save_as(path)
        except BackupError as exc:
            messagebox.showerror("Cannot save Song", str(exc)); return False
        except Exception as exc:
            messagebox.showerror("Cannot save Song", str(exc)); return False
        messagebox.showinfo("Save complete", f"{summary.events_moved} events moved\n"
                            f"{summary.payloads_changed} payloads changed\n"
                            f"{summary.tracks_changed} track operations")
        self.redraw()
        self.invalidate_workspace_song_inventory()
        return True

    def save(self):
        if self.model and not self.loading:
            self._save_to(self.model.path)

    def save_as(self):
        if not self.model or self.loading: return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("JSON", "*.json"),))
        if path:
            self._save_to(path)

    def _prepare_dialog(self, dialog, family, primary=None, cancel=None, modal=True):
        """Apply remembered geometry and the requested dialog keyboard policy."""
        dialog.transient(self)
        if modal:
            dialog.grab_set()
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

    def _configure_audio(self, preserve_waveforms=False, request_waveforms=True):
        self.audio_engine.close()
        if not preserve_waveforms:
            self.waveforms.clear(); self._waveform_pending.clear()
            self._waveform_render_cache.clear(); self._waveform_photo_cache.clear()
        tracks = list(self.model.audio_tracks) if self.model else []
        self.monitor_muted = [False] * len(tracks); self.monitor_solo = [False] * len(tracks)
        resolved = [PlaybackTrack(t.resolved_path, t.name, t.offset, t.file_info)
                    for t in tracks if t.resolved_path and t.file_info]
        try:
            self.audio_engine.open(resolved)
        except Exception as exc:
            self.audio_engine.diagnostic = str(exc)
            self._set_audio_error(str(exc))
        else:
            self._set_audio_ready(self.model)
        if request_waveforms:
            self._request_initial_waveforms(self.model, self._load_generation,
                                            self._waveform_cancel)

    def refresh_audio(self):
        if not self.model: return
        changed = self.model.refresh_audio()
        for path in changed:
            self.waveforms.pop(str(path), None)
            self._waveform_render_cache.invalidate_source(str(path))
            for key in tuple(self._waveform_photo_cache):
                if key[0][0] == str(path):
                    del self._waveform_photo_cache[key]
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

    def _request_waveform(self, path, generation=None):
        generation = self._load_generation if generation is None else generation
        cancel = self._waveform_cancel
        path = str(path)
        if path in self.waveforms or path in self._waveform_pending: return
        self._waveform_pending.add(path)
        submitted = time.perf_counter()
        future = self._waveform_pool.submit(
            timed_extract_waveform, path,
            pause_requested=lambda: self.audio_engine.state is PlaybackState.PLAYING,
            cancel_requested=cancel.is_set)
        def poll():
            if not future.done(): self.after(40, poll); return
            self._waveform_pending.discard(path)
            if generation != self._load_generation or cancel.is_set(): return
            try: result = future.result()
            except Exception:
                result = None
            else: self.waveforms[path] = result.summary
            if LOAD_PERF:
                delivered = time.perf_counter()
                if result is not None:
                    LOG.debug("UI audio: waveform timing %s cache=%s full_wav_scan=yes wav_bytes=%d wav_frames=%d queue=%.1f ms extraction=%.1f ms delivery=%.1f ms",
                        path, result.cache_status, result.wav_bytes, result.wav_frames,
                        (result.worker_started - submitted) * 1000,
                        (result.worker_completed - result.worker_started) * 1000,
                        (delivered - result.worker_completed) * 1000)
            if result is not None and self.winfo_exists():
                self._invalidate_waveform_track(path, generation)
            if (generation == getattr(self, "_initial_waveform_generation", None) and
                    path in getattr(self, "_initial_waveform_targets", ())):
                self._initial_waveform_terminal.add(path)
                ReapcaseEditor._update_waveform_header(self, generation)
        self.after(40, poll)

    def _tile_photo(self, tile_key, tile, height, foreground, background=None,
                    stride=1):
        """Create a Tk image once per tile variant, never per redraw/tick."""
        lookup_started = time.perf_counter() if self._waveform_perf.enabled else 0.0
        variant = (tile_key, int(height), foreground, background, int(stride))
        image = self._waveform_photo_cache.get(variant)
        if lookup_started:
            self._waveform_perf.record("PhotoImage creation/cache lookup",
                                       time.perf_counter() - lookup_started)
        if image is not None:
            self._waveform_photo_cache.move_to_end(variant)
            return image
        tile_started = time.perf_counter()
        if background is not None:
            with self._waveform_perf.measure("waveform tile raster generation"):
                raster = raster_ppm(list(tile.columns), height, foreground, background)
            with self._waveform_perf.measure("PhotoImage creation"):
                image = tk.PhotoImage(data=raster, format="PPM")
        else:
            with self._waveform_perf.measure("waveform tile raster generation"):
                # A blank PhotoImage is transparent. Bulk rectangular puts draw
                # only extrema spans, avoiding RGBA allocation and PNG/zlib.
                with self._waveform_perf.measure("PhotoImage creation"):
                    image = tk.PhotoImage(width=max(1, len(tile.columns)), height=height)
                center, amplitude = height / 2, max(1, height / 2 - 10)
                for x in range(0, len(tile.columns), max(1, stride)):
                    low, high = tile.columns[x]
                    if low == high == 0:
                        continue
                    y0 = max(0, min(height - 1, round(center - high * amplitude)))
                    y1 = max(0, min(height - 1, round(center - low * amplitude)))
                    image.put(foreground, to=(x, min(y0, y1),
                                               min(len(tile.columns), x + stride),
                                               max(y0, y1) + 1))
        if LOAD_PERF:
            LOG.debug("UI audio: waveform tile/photo generation %.1f ms",
                      (time.perf_counter() - tile_started) * 1000)
        self._waveform_photo_cache[variant] = image
        while len(self._waveform_photo_cache) > self._waveform_render_cache.max_tiles * 2:
            self._waveform_photo_cache.popitem(last=False)
        return image

    def _prepared_tiles(self, source, summary, *, prefetch=1):
        left, width = self.canvas.canvasx(0), max(1, self.canvas.winfo_width())
        return self._waveform_render_cache.visible_tiles(
            source, summary, self.model.tempo_map, self.model.song.ppqn,
            self.pixels_per_beat, left, width, HEADER_WIDTH, prefetch)

    @staticmethod
    def _waveform_tag(source):
        """Stable, Canvas-safe tag for one source's ordinary waveform layer."""
        return "waveform-track-" + hashlib.sha1(str(source).encode()).hexdigest()[:16]

    def _waveform_lane(self, source):
        if not self.model or not self._effective_lane_visibility().get("AUDIO", True):
            return None
        layout = visible_lane_layout(self.lane_order, self._effective_lane_visibility())
        for offset, track in enumerate(self.model.audio_tracks):
            if waveform_cache_key(track) == source:
                return track, layout.audio_top + offset * LANE_HEIGHT
        return None

    def _invalidate_waveform_track(self, source, generation=None):
        """Replace only one visible waveform layer; never rebuild the timeline."""
        generation = self._load_generation if generation is None else generation
        if generation != self._load_generation or not self.model:
            return
        lane = self._waveform_lane(source)
        summary = self.waveforms.get(source)
        if not lane or not summary or not self.model.tempo_map:
            return
        tag = self._waveform_tag(source)
        serial = self._waveform_render_serial.get(source, 0) + 1
        self._waveform_render_serial[source] = serial
        self.canvas.delete(tag)
        self._waveform_track_images[source] = []
        stats = self._waveform_render_cache.stats.copy()
        with self._waveform_perf.measure("waveform tile selection"):
            prepared = self._prepared_tiles(source, summary)
        if LOAD_PERF:
            LOG.debug("UI audio: targeted waveform source=%s viewport=%.0f+%d zoom=%.3f tiles=%d hits=%d misses=%d",
                source, self.canvas.canvasx(0), max(1, self.canvas.winfo_width()),
                self.pixels_per_beat, len(prepared),
                self._waveform_render_cache.stats["tile_hit"] - stats["tile_hit"],
                self._waveform_render_cache.stats["tile_miss"] - stats["tile_miss"])
        foreground = tuple(bytes.fromhex(AUDIO.waveform.removeprefix("#")))
        background = tuple(bytes.fromhex(AUDIO.clip.removeprefix("#")))
        y = lane[1]

        def render_next(index=0):
            if (generation != self._load_generation or
                    serial != self._waveform_render_serial.get(source)
                    or not self.winfo_exists()):
                return
            if index >= len(prepared):
                return
            key, tile = prepared[index]
            image = self._tile_photo(key, tile, 40, foreground, background)
            self._waveform_track_images[source].append(image)
            with self._waveform_perf.measure("canvas.create_image calls"):
                self.canvas.create_image(tile.left, y + 22, image=image, anchor="nw",
                                         tags=(tag, "waveform-layer"))
            self.after_idle(lambda: render_next(index + 1))
        render_next()

    def _draw_ghost_waveform(self, layout, visible_lanes):
        """Draw bounded, reusable transparent FULL-SONG tiles."""
        if not self.full_song_ghost_visible.get():
            self._clear_ghost_waveform()
            return
        m = self.model
        ghost_track = full_song_track(m.audio_tracks)
        ghost_bounds = ghost_waveform_lane_bounds(layout)
        ghost_summary = (self.waveforms.get(waveform_cache_key(ghost_track))
                         if ghost_track else None)
        if not (ghost_summary and ghost_bounds and m.tempo_map):
            self._ghost_raster_coverage = None
            self._ghost_waveform_image = None
            return
        top, bottom = ghost_bounds
        # Keep light-weight test doubles and third-party embedders using the
        # pre-tile private surface functional during the migration.
        if not hasattr(self, "_waveform_render_cache"):
            left, width = buffered_viewport(int(self.canvas.canvasx(0)),
                                            max(1, self.canvas.winfo_width()))
            key = ghost_raster_cache_key((waveform_cache_key(ghost_track), id(ghost_summary)),
                left, width, self.pixels_per_beat, ghost_bounds, visible_lanes,
                ppqn=m.song.ppqn, tempo_identity=id(m.tempo_map))
            image_left, image = cached_ghost_raster(
                self._ghost_raster_cache, key, lambda: (left, None))
            self._ghost_waveform_image = image
            self._waveform_images.append(image)
            self.canvas.create_image(image_left, top, image=image, anchor="nw",
                                     tags=("ghost-waveform",))
            self._ghost_raster_coverage = (image_left, image_left + image.width())
            return
        with self._waveform_perf.measure("ghost waveform preparation"):
            prepared = self._prepared_tiles(waveform_cache_key(ghost_track), ghost_summary)
        images = []
        for key, tile in prepared:
            image = self._tile_photo(key, tile, bottom - top,
                                     AUDIO.ghost_waveform, None,
                                     AUDIO.ghost_waveform_stride)
            images.append(image)
            self.canvas.create_image(tile.left, top, image=image, anchor="nw",
                                     tags=("ghost-waveform",))
        self._waveform_images.extend(images)
        self._ghost_waveform_image = images
        if prepared:
            self._ghost_raster_coverage = (prepared[0][1].left,
                prepared[-1][1].left + self._waveform_render_cache.tile_width)

    def _refresh_ghost_waveform(self):
        """Refresh only the buffered ghost after the transport callback yields."""
        self._ghost_refresh_pending = False
        if not self.model or not self.full_song_ghost_visible.get():
            self._clear_ghost_waveform()
            return
        visibility = self._effective_lane_visibility()
        layout = visible_lane_layout(self.lane_order, visibility)
        self.canvas.delete("ghost-waveform")
        self._draw_ghost_waveform(layout, layout.lanes)
        self.canvas.tag_lower("ghost-waveform", "ghost-foreground-anchor")

    def request_redraw(self, reason=None):
        """Coalesce invalidations into one deterministic idle repaint."""
        if self._redraw_idle_id is not None:
            return

        def paint():
            self._redraw_idle_id = None
            if self.winfo_exists():
                self.redraw(reason)

        self._redraw_idle_id = self.after_idle(paint)

    def _timeline_resized(self, _event=None):
        # Configure can fire several times while panes settle.  Repainting once
        # at idle also regenerates viewport-sized cached lane backgrounds.
        self.request_redraw("timeline resize")

    def redraw(self, reason=None):
        pending = getattr(self, "_redraw_idle_id", None)
        if pending is not None:
            self.after_cancel(pending)
            self._redraw_idle_id = None
        if LOAD_PERF:
            caller = inspect.currentframe().f_back
            LOG.debug("timeline full redraw reason=%s caller=%s:%d (%s)",
                      reason or "unspecified", caller.f_code.co_filename,
                      caller.f_lineno, caller.f_code.co_name)
        redraw_started = time.perf_counter() if self._waveform_perf.enabled else 0.0
        phase_started = redraw_started
        self.canvas.delete("all")
        self._waveform_images = []
        self._waveform_track_images = {}
        self._waveform_render_serial = {key: value + 1
                                        for key, value in self._waveform_render_serial.items()}
        if phase_started:
            self._waveform_perf.record("canvas delete/reset", time.perf_counter() - phase_started)
        if not self.model: return
        layer_counts = Counter()
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
        phase_started = time.perf_counter() if self._waveform_perf.enabled else 0.0
        y = RULER_HEIGHT
        for lane_index, lane in enumerate(all_lanes):
            current_height = lane_height(lane) if lane in LANES else LANE_HEIGHT
            if lane in LANES:
                lane_style = lane_colors(lane)
                background = lane_style.background
            else:
                audio_index = lane_index - len(visible_lanes)
                background = (AUDIO.background_alternate if audio_index % 2
                              else AUDIO.background)
            lane_image = self._lane_backgrounds.image(background, int(width), current_height)
            self.canvas.create_image(0, y, image=lane_image, anchor="nw")
            self.canvas.create_line(0, y + current_height, width, y + current_height,
                                    fill=TIMELINE.separator)
            if lane in {"STRUCTURE", "STADIUM", "SECOND HELIX"}:
                self.canvas.create_rectangle(1, y + 1, width - 1,
                                             y + current_height - 1,
                                             outline=lane_colors(lane).outline,
                                             width=1)
            if lane == "STRUCTURE":
                for boundary in (y + MARKERS_HEIGHT, y + MARKERS_HEIGHT + PAUSES_HEIGHT):
                    self.canvas.create_line(0, boundary, width, boundary, fill=TIMELINE.sublane_separator)
            elif lane in COMPOSITE_LANES:
                self.canvas.create_line(0, y + COMMANDS_HEIGHT, width, y + COMMANDS_HEIGHT,
                                        fill=TIMELINE.sublane_separator)
            y += current_height
        if phase_started:
            self._waveform_perf.record("lane backgrounds", time.perf_counter() - phase_started)
            layer_counts["audio backgrounds"] = len(m.audio_tracks) * 2
        # The FULL-SONG overview consumes the same cached peak pyramid and the
        # same tempo-aware viewport mapping as its ordinary audio track.  Draw
        # it directly over lane backgrounds so every semantic layer stays on
        # top, without an opaque raster background covering lane identity.
        self._draw_ghost_waveform(layout, visible_lanes)
        # A stable stacking anchor keeps later ghost-only refreshes behind all
        # timeline foreground primitives without reconstructing the canvas.
        self.canvas.create_line(0, 0, 0, 0, tags=("ghost-foreground-anchor",))
        grid_started = time.perf_counter() if self._waveform_perf.enabled else 0.0
        grid_item_start = len(self.canvas.find_all())
        grid_end = m.song_end_units + 2 * m.song.ppqn
        visible_left = self.canvas.canvasx(0)
        visible_right = visible_left + max(1, self.canvas.winfo_width())
        grid_start_units = units_at_x(visible_left, m.song.ppqn, self.pixels_per_beat)
        grid_visible_end = min(grid_end, units_at_x(visible_right, m.song.ppqn,
                                                    self.pixels_per_beat))
        shortest_bar = m.timing_map.minimum_beats_per_bar(grid_start_units,
                                                          grid_visible_end)
        density = timeline_grid_density(self.pixels_per_beat, shortest_bar)
        points = (m.timing_map.iter_beats(grid_start_units, grid_visible_end)
                  if density.show_beats else
                  m.timing_map.iter_bars(grid_start_units, grid_visible_end))
        for point in points:
            bar, beat = point.position.bar, point.position.beat
            prominent = point.is_bar
            if prominent and not is_major_display_bar(bar, density):
                continue
            x = timeline_x(point.units, m.song.ppqn, self.pixels_per_beat)
            self.canvas.create_line(x, RULER_HEIGHT, x, height, fill=TIMELINE.grid_bar if prominent else TIMELINE.grid_beat, width=2 if prominent else 1)
            self.canvas.create_line(x, 17 if prominent else 21, x, RULER_HEIGHT,
                                    fill=TIMELINE.ruler_bar if prominent else TIMELINE.ruler_beat)
            if prominent: self.canvas.create_text(x + 4, 2, text=f"{bar:03d}", anchor="nw", fill=TIMELINE.ruler_text)
        if grid_started:
            self._waveform_perf.record("grid", time.perf_counter() - grid_started)
            layer_counts["timeline grid"] = len(self.canvas.find_all()) - grid_item_start
        semantic_started = time.perf_counter() if self._waveform_perf.enabled else 0.0
        semantic_item_start = len(self.canvas.find_all())
        preview_selection = self._marquee_selection()
        self.event_bounds = {}
        self.sequence_bounds = {}
        self.semantic_sources = {}
        structure_layout = derive_structure_layout(m.timeline.events, m._units, m.song_end_units)
        region_sources = {i for region in structure_layout.regions for i in region.source_event_indices}
        view_left = self.canvas.canvasx(0) + HEADER_WIDTH
        material_left = self.canvas.canvasx(0) - 256
        material_right = (self.canvas.canvasx(0)
                          + max(1, self.canvas.winfo_width()) + 256)
        colors = lane_colors("STRUCTURE")
        marker_region_number = 0
        for region in structure_layout.regions if "STRUCTURE" in visible_lanes else ():
            x1 = timeline_x(region.start_units, m.song.ppqn, self.pixels_per_beat)
            x2 = timeline_x(region.end_units, m.song.ppqn, self.pixels_per_beat)
            marker_number = marker_region_number
            if region.kind == "marker":
                marker_region_number += 1
            if x2 < material_left or x1 > material_right:
                continue
            sub_y = lane_tops["STRUCTURE"] if region.kind == "marker" else lane_tops["STRUCTURE"] + MARKERS_HEIGHT + PAUSES_HEIGHT
            source = region.source_event_indices[0]
            selected = any(index in m.selected for index in region.source_event_indices)
            if selected:
                fill = colors.selected
            elif region.kind == "marker":
                fill = structure_region_fill(marker_number)
            else:
                fill = colors.normal
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
            if x2 < material_left or x1 > material_right:
                continue
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
            if x2 < material_left or x1 > material_right:
                continue
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
            if x < material_left or x > material_right:
                continue
            y = lane_tops[m.lane(event)] + 27
            if event_lane == "STRUCTURE":
                sublane = structure_sublane(event)
                y = lane_tops["STRUCTURE"] + {"markers": 3, "pauses": MARKERS_HEIGHT + 1,
                                    "cycles": MARKERS_HEIGHT + PAUSES_HEIGHT + 3}[sublane]
            elif m.lane(event) in COMPOSITE_LANES:
                row_top, _ = sublane_bounds(visible_lanes, m.lane(event),
                                             event_sublane(event, m.lane(event)))
                y = row_top + 1
            selected = i in m.selected
            previewed = preview_selection is not None and i in preview_selection
            event_lane = m.lane(event)
            is_looper_point = (event_lane in COMPOSITE_LANES and
                                event_sublane(event, event_lane) == "looper")
            text = (looper_display_label(event, event_lane) if is_looper_point
                    else m.label(event))
            if event_lane in COMPOSITE_LANES:
                event_y1, event_y2 = sublane_content_bounds(
                    visible_lanes, event_lane, event_sublane(event, event_lane))
                text_y = (event_y1 + event_y2) / 2
            else:
                text_y = y + 10
            item = self.canvas.create_text(x + 5, text_y, text=text, anchor="w",
                                           fill=lane_colors(m.lane(event)).text,
                                           tags=(f"event:{i}",))
            box = self.canvas.bbox(item)
            palette = lane_colors(m.lane(event))
            bounds = ((x, event_y1, max(x + 1, box[2] + 4), event_y2)
                      if event_lane in COMPOSITE_LANES else
                      (box[0]-4, box[1]-3, box[2]+4, box[3]+3))
            rect = self.canvas.create_rectangle(*bounds,
                                                fill=palette.selected if selected or previewed else palette.normal,
                                                outline=palette.outline,
                                                width=2 if selected or previewed else 1,
                                                tags=(f"event:{i}",))
            self.event_bounds[i] = bounds
            self.canvas.tag_raise(item)
        if semantic_started:
            elapsed = time.perf_counter() - semantic_started
            self._waveform_perf.record("semantic events", elapsed)
            self._waveform_perf.record("structure/events drawing", elapsed)
            layer_counts["structure/events"] = (
                len(self.canvas.find_all()) - semantic_item_start)
        audio_started = time.perf_counter() if self._waveform_perf.enabled else 0.0
        tile_stats_before = self._waveform_render_cache.stats.copy()
        tiles_requested = 0
        audio_grid_item_total = 0
        for lane_offset, track in enumerate(m.audio_tracks if audio_visible else ()):
            track_started = time.perf_counter()
            track_items_started = len(self.canvas.find_all())
            category = {name: (0.0, 0) for name in (
                "audio clip/body drawing", "audio labels",
                "waveform preparation", "waveform image placement",
                "audio grid calculation", "audio grid Canvas creation")}
            y = layout.audio_top + lane_offset * LANE_HEIGHT
            body_started = time.perf_counter()
            body_items = len(self.canvas.find_all())
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
                     f"{track.file_info.channels} ch" if track.file_info else
                     {"resolving": "Resolving…", "missing": "Missing",
                      "invalid": "Invalid"}.get(track.status, "Unavailable"))
            if track.offset != 0:
                state += f" | raw offset {track.offset} (unit unknown)"
            clip_body_started = time.perf_counter()
            clip_body_items = len(self.canvas.find_all())
            self.canvas.create_rectangle(start_x, y + 22, end_x, y + 62,
                                         fill=AUDIO.clip if track.file_info else AUDIO.missing_clip,
                                         outline=AUDIO.clip_outline if track.file_info else AUDIO.missing_outline)
            category["audio clip/body drawing"] = (
                time.perf_counter() - clip_body_started,
                len(self.canvas.find_all()) - clip_body_items)
            clip_label = self.canvas.create_text(start_x + 6, y + 32, anchor="w",
                                                 fill=AUDIO.text,
                                                 text=f"{track.filename}  |  {state}")
            total_body_items = len(self.canvas.find_all()) - body_items
            category["audio labels"] = (
                max(0.0, time.perf_counter() - body_started
                    - category["audio clip/body drawing"][0]),
                total_body_items - category["audio clip/body drawing"][1])
            summary = self.waveforms.get(waveform_cache_key(track))
            if summary and m.tempo_map:
                preparation_started = time.perf_counter()
                center, amplitude = y + 42, 18
                with self._waveform_perf.measure("normal waveform preparation"):
                    with self._waveform_perf.measure("waveform tile selection"):
                        prepared = self._prepared_tiles(waveform_cache_key(track), summary)
                category["waveform preparation"] = (
                    time.perf_counter() - preparation_started, 0)
                tiles_requested += len(prepared)
                foreground = tuple(bytes.fromhex(AUDIO.waveform.removeprefix("#")))
                background = tuple(bytes.fromhex(AUDIO.clip.removeprefix("#")))
                placement_started = time.perf_counter()
                waveform_items = len(self.canvas.find_all())
                for key, tile in prepared:
                    image = self._tile_photo(key, tile, 40, foreground, background)
                    self._waveform_images.append(image)
                    source = waveform_cache_key(track)
                    self._waveform_track_images.setdefault(source, []).append(image)
                    with self._waveform_perf.measure("canvas.create_image calls"):
                        self.canvas.create_image(tile.left, y + 22, image=image, anchor="nw",
                            tags=(self._waveform_tag(source), "waveform-layer"))
                self.canvas.create_line(start_x, center, end_x, center,
                                        fill=AUDIO.waveform, width=1)
                self.canvas.tag_raise(clip_label)
                category["waveform image placement"] = (
                    time.perf_counter() - placement_started,
                    len(self.canvas.find_all()) - waveform_items)
            if self.audio_grid_overlay:
                calculation_started = time.perf_counter()
                grid_start, grid_stop = viewport_units(
                    self.canvas.canvasx(0), max(1, self.canvas.winfo_width()),
                    m.song.ppqn, self.pixels_per_beat, m.song_end_units)
                shortest = m.timing_map.minimum_beats_per_bar(grid_start, grid_stop)
                audio_density = timeline_grid_density(self.pixels_per_beat, shortest)
                bars = tuple(m.timing_map.iter_bars(grid_start, grid_stop))
                bar_starts = {point.units for point in bars
                              if is_major_display_bar(point.position.bar, audio_density)}
                if self.pixels_per_beat >= 40:
                    step = max(1, m.song.ppqn // 4)
                    first = grid_start - grid_start % step
                    units = range(first, grid_stop + 1, step)
                elif audio_density.show_beats:
                    units = (point.units for point in
                             m.timing_map.iter_beats(grid_start, grid_stop))
                else:
                    units = iter(sorted(bar_starts))
                calculation_elapsed = time.perf_counter() - calculation_started
                creation_started = time.perf_counter()
                grid_items = len(self.canvas.find_all())
                for unit in units:
                    x = timeline_units_to_x(unit, m.song.ppqn, self.pixels_per_beat,
                                            HEADER_WIDTH)
                    beat = unit % m.song.ppqn == 0
                    bar = unit in bar_starts
                    self.canvas.create_line(x, y + 22, x, y + 62,
                        fill=AUDIO.grid_bar if bar else (AUDIO.grid_beat if beat else AUDIO.grid_subdivision),
                        width=2 if bar else 1, dash=() if beat else (1, 3))
                category["audio grid calculation"] = (calculation_elapsed, 0)
                category["audio grid Canvas creation"] = (
                    time.perf_counter() - creation_started,
                    len(self.canvas.find_all()) - grid_items)
                audio_grid_item_total += category["audio grid Canvas creation"][1]
            if LOAD_PERF:
                LOG.debug("AUDIO %s: %s total=%.1f ms/%d items", track.name,
                    ", ".join("%s %.1f ms/%d items" % (name, seconds * 1000, count)
                              for name, (seconds, count) in category.items()),
                    (time.perf_counter() - track_started) * 1000,
                    len(self.canvas.find_all()) - track_items_started)
        if audio_started:
            self._waveform_perf.record("audio lane drawing", time.perf_counter() - audio_started)
            LOG.debug("timeline waveform viewport width=%d zoom=%.3f tiles=%d hits=%d misses=%d",
                max(1, self.canvas.winfo_width()), self.pixels_per_beat, tiles_requested,
                self._waveform_render_cache.stats["tile_hit"] - tile_stats_before["tile_hit"],
                self._waveform_render_cache.stats["tile_miss"] - tile_stats_before["tile_miss"])
            layer_counts["audio grids"] = audio_grid_item_total
            layer_counts["waveform images"] = len(self.canvas.find_withtag("waveform-layer"))
        if self.marquee_anchor and self.marquee_point:
            bounds = normalized_rectangle(*self.marquee_anchor, *self.marquee_point)
            self.canvas.create_rectangle(*bounds, fill=TIMELINE.marquee_fill, stipple="gray50",
                                         outline=TIMELINE.marquee_outline, width=2, tags=("marquee",))
        if self.drag_preview:
            self._draw_drag_preview(self.drag_preview)
        self._draw_time_selection(height)
        if m.tempo_map:
            play_units = m.tempo_map.seconds_to_units(audible_playback_time(self.audio_engine))
            play_x = timeline_x(play_units, m.song.ppqn, self.pixels_per_beat)
            self.canvas.create_line(play_x, 0, play_x, height, fill=THEME.playhead, width=2,
                                    tags=("playhead",))
        # Draw the fixed header last so scrollable lane content can never show
        # through it. Its right edge is the same origin used by every timeline
        # element above.
        headers_started = time.perf_counter() if self._waveform_perf.enabled else 0.0
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
        if headers_started:
            self._waveform_perf.record("fixed headers", time.perf_counter() - headers_started)
            layer_counts["headers"] = len(self.canvas.find_withtag("fixed-header"))
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
        final_started = time.perf_counter() if self._waveform_perf.enabled else 0.0
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self._update_zoom_label()
        if final_started:
            self._waveform_perf.record("scrollregion/finalization", time.perf_counter() - final_started)
            LOG.debug("timeline canvas items=%d visible_width=%d zoom=%.3f",
                      len(self.canvas.find_all()), max(1, self.canvas.winfo_width()),
                      self.pixels_per_beat)
            LOG.debug("timeline canvas layers: %s", dict(layer_counts))
        if redraw_started:
            self._waveform_perf.record("full redraw", time.perf_counter() - redraw_started)
            self._waveform_perf.log(LOG)

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

    def _draw_time_selection(self, height):
        """Rebuild the complete range layer from canonical units each paint."""
        if not self.model or not self.has_time_selection():
            return
        start, end = self.time_selection_range()
        x1 = timeline_x(start, self.model.song.ppqn, self.pixels_per_beat)
        x2 = timeline_x(end, self.model.song.ppqn, self.pixels_per_beat)
        # Tk stipple provides a transparent-looking wash without hiding events
        # or waveforms.  Strong edges and ruler handles carry the precision.
        self.canvas.create_rectangle(x1, RULER_HEIGHT, x2, height,
            fill="#52708a", stipple="gray50", outline="", tags=("time-selection",))
        for x, side in ((x1, "start"), (x2, "end")):
            self.canvas.create_line(x, 0, x, height, fill="#74c7ec", width=3,
                                    tags=("time-selection", f"time-{side}"))
            self.canvas.create_polygon(x - 7, 1, x + 7, 1, x, 12,
                fill="#74c7ec", outline="#d7f3ff",
                tags=("time-selection", f"time-{side}"))

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

    def _move_playhead_item(self):
        """Move the transport overlay without rebuilding static timeline layers."""
        if not self.model:
            return
        items = self.canvas.find_withtag("playhead")
        if not items:
            return
        units = self.model._units(self.model.cursor)
        x = timeline_x(units, self.model.song.ppqn, self.pixels_per_beat)
        coordinates = self.canvas.coords(items[0])
        if len(coordinates) == 4:
            self.canvas.coords(items[0], x, coordinates[1], x, coordinates[3])

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
            side = "anchor"
            if self.has_time_selection():
                sx = timeline_x(self.time_selection_start_units, self.model.song.ppqn,
                                self.pixels_per_beat)
                ex = timeline_x(self.time_selection_end_units, self.model.song.ppqn,
                                self.pixels_per_beat)
                if abs(x - sx) <= 9: side = "start"
                elif abs(x - ex) <= 9: side = "end"
            self._time_selection_drag = (side, units)
            return
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
        cursor = "crosshair" if over_ruler else ""
        if over_ruler and self.has_time_selection():
            for boundary in self.time_selection_range():
                if abs(x - timeline_x(boundary, self.model.song.ppqn,
                                      self.pixels_per_beat)) <= 9:
                    cursor = "sb_h_double_arrow"
        self.canvas.configure(cursor=cursor)
        if over_ruler:
            units_at_x(x, self.model.song.ppqn, self.pixels_per_beat)

    def context_menu(self, event):
        """Open a compact lane-specific creation menu at the clicked position."""
        if self.app_mode.get() == "LIVE":
            self.status.set("Timeline authoring is disabled in LIVE mode")
            return
        if not self.model:
            return
        if (self.canvas.canvasy(event.y) < RULER_HEIGHT and
                event.x >= HEADER_WIDTH and self.has_time_selection()):
            units = units_at_x(self.canvas.canvasx(event.x), self.model.song.ppqn,
                               self.pixels_per_beat)
            if self.time_selection_start_units <= units <= self.time_selection_end_units:
                menu = tk.Menu(self, tearoff=False)
                menu.add_command(label="Remove Time Selection",
                                 command=self.clear_time_selection)
                try: menu.tk_popup(event.x_root, event.y_root)
                finally: menu.grab_release()
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
            for action in self.model.decoder.second_helix_actions():
                if action in SECOND_HELIX_LOOPER_ACTIONS:
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
            "helix_snapshot": (("label", "Label", "text"),
                               ("snapshot", "Snapshot", self.model.decoder.second_helix_snapshots()),
                               ("channel", "MIDI Channel", "int")),
            "helix_expression": (("label", "Label", "text"),
                                 ("expression", "Expression", (1, 2, 3)),
                                 ("value", "Value", (0, 127)), ("channel", "MIDI Channel", "int")),
            "helix_preset": (("label", "Label", "text"),
                             ("bank_msb", "Bank MSB (blank = Off)", "optional_int"),
                             ("bank_lsb", "Bank LSB (blank = Off)", "optional_int"),
                             ("program", "Program", "int"), ("channel", "MIDI Channel", "int")),
            "midi_program": (("label", "Label", "text"),
                             ("channel", "Channel", "int"),
                             ("bank_msb", "Bank MSB (blank = Off)", "optional_int"),
                             ("bank_lsb", "Bank LSB (blank = Off)", "optional_int"),
                             ("program", "Program", "int")),
            "midi_cc": (("label", "Label", "text"), ("channel", "Channel", "int"), ("cc", "CC", "int"),
                        ("value", "Value", "int")),
            "lighting": (("label", "Label", "text"),),
            "end": (("end_behavior", "End of Song", ("Pause", "Play Next", "Cue Next", "Repeat", "Cue Same")),
                    ("fade_out", "Fade Out", "bool"),
                    ("fade_length", "Fade Length", "float"),
                    ("gap_before_next_song", "Gap Before Next Song", "bool"),
                    ("gap_length", "Gap Length", "float")),
        }
        if family == "video":
            schemas[family] = (("label", "Label", "text"), ("video", "Video", "optional_int"),
                               ("action", "Action", self.model.decoder.video_actions()),
                               ("channel", "MIDI Channel", "int"))
        elif family.startswith("helix_") and family not in schemas:
            schemas[family] = (("label", "Label", "text"),
                               ("action", "Action", self.model.decoder.second_helix_actions()),
                               ("channel", "MIDI Channel", "int"))
        schema = schemas.get(family)
        if not schema:
            return
        dialog = tk.Toplevel(self); dialog.title(capability.title)
        dialog.transient(self); dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14); frame.pack(fill="both", expand=True)
        variables = {}
        widgets = {}
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
            variables[key] = (variable, kind); widgets[key] = widget
            widget.grid(row=row, column=1, sticky="ew", pady=4)
        if family == "end":
            ttk.Label(frame, text="Final behavior may also depend on Stadium Global Settings > Songs.",
                      wraplength=360).grid(row=len(schema), column=0, columnspan=2,
                                           sticky="w", pady=(8, 0))
            def refresh_end_states(*_args):
                widgets["fade_length"].configure(
                    state="normal" if variables["fade_out"][0].get() else "disabled")
                gap_allowed = variables["end_behavior"][0].get() != "Pause"
                widgets["gap_before_next_song"].configure(
                    state="normal" if gap_allowed else "disabled")
                widgets["gap_length"].configure(state="normal" if gap_allowed and
                    variables["gap_before_next_song"][0].get() else "disabled")
            variables["fade_out"][0].trace_add("write", refresh_end_states)
            variables["gap_before_next_song"][0].trace_add("write", refresh_end_states)
            variables["end_behavior"][0].trace_add("write", refresh_end_states)
            refresh_end_states()
        button_row = len(schema) + (1 if family == "end" else 0)
        buttons = ttk.Frame(frame); buttons.grid(row=button_row, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
        def save():
            edited = dict(values)
            try:
                for key, (variable, kind) in variables.items():
                    if kind == "readonly": continue
                    raw = variable.get()
                    if kind == "bool": edited[key] = bool(raw)
                    elif kind == "float": edited[key] = float(raw)
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
        if self._time_selection_drag:
            side, anchor = self._time_selection_drag
            units = snapped_units_at_x(self.canvas.canvasx(event.x), self.model.song.ppqn,
                self.pixels_per_beat, self.grid_choice.get(), self.model.numerator,
                self.model.timing_map)
            if side == "start":
                self.set_time_selection(units, self.time_selection_end_units)
            elif side == "end":
                self.set_time_selection(self.time_selection_start_units, units)
            else:
                self.set_time_selection(anchor, units)
            self.redraw("time selection drag")
            return
        if self.playhead_drag:
            units = snapped_units_at_x(self.canvas.canvasx(event.x), self.model.song.ppqn,
                                       self.pixels_per_beat, self.grid_choice.get(),
                                       self.model.numerator, self.model.timing_map)
            self.seek_units(units); self._move_playhead_item(); return
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
        if self._time_selection_drag:
            side, anchor = self._time_selection_drag
            self._time_selection_drag = None
            units = snapped_units_at_x(self.canvas.canvasx(event.x), self.model.song.ppqn,
                self.pixels_per_beat, self.grid_choice.get(), self.model.numerator,
                self.model.timing_map)
            if side == "anchor" and units == anchor:
                # Preserve the old ruler click-to-seek behavior.
                self.seek_units(units)
            elif side == "start":
                self.set_time_selection(units, self.time_selection_end_units)
            elif side == "end":
                self.set_time_selection(self.time_selection_start_units, units)
            else:
                self.set_time_selection(anchor, units)
            self.redraw("commit time selection")
            return
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
            play_units = self.model.tempo_map.seconds_to_units(audible_playback_time(self.audio_engine))
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
            if hasattr(self, "live_midi"):
                self.live_midi.seek(units)
            self.model.cursor = self.model._position(units)
            seconds = self.model.tempo_map.units_to_seconds(units)
            minutes, remainder = divmod(seconds, 60)
            self.transport_position.set(
                f"{int(minutes):02d}:{remainder:06.3f}   |   {self.model.cursor.render()}")

    def return_to_start(self):
        self.audio_engine.return_to_start()
        if hasattr(self, "live_midi"): self.live_midi.stop()
        self.redraw()
    def stop_playback(self):
        self.audio_engine.stop()
        if hasattr(self, "live_midi"): self.live_midi.stop()
        self.redraw()

    def _persist_pre_roll(self, *_args):
        """Persist rehearsal controls as user-local state, never Song data."""
        enabled = bool(self.pre_roll_enabled.get())
        try:
            measures = int(self.pre_roll_measures.get())
        except (TypeError, ValueError, tk.TclError):
            return
        update_preferences(lambda data: data.update(
            pre_roll_enabled=enabled, pre_roll_measures=measures))

    def play_pause(self):
        if self.loading or not self._audio_ready:
            self.status.set("Audio is still loading…" if not self._audio_error
                            else self._audio_error)
            return
        try:
            if self.audio_engine.state is PlaybackState.PLAYING:
                units = (self.model.tempo_map.seconds_to_units(audible_playback_time(self.audio_engine))
                         if self.model and self.model.tempo_map else 0)
                self.audio_engine.pause()
                if hasattr(self, "live_midi"): self.live_midi.pause(units)
            else:
                requested_units = (self.model.tempo_map.seconds_to_units(audible_playback_time(self.audio_engine))
                         if self.model and self.model.tempo_map else 0)
                current_units = requested_units
                resuming = self.audio_engine.state is PlaybackState.PAUSED
                selection = ReapcaseEditor.time_selection_range(self)
                if selection and not resuming:
                    requested_units = selection[0]
                enabled = bool(getattr(self, "pre_roll_enabled", None) and
                               self.pre_roll_enabled.get())
                measures_var = getattr(self, "pre_roll_measures", None)
                measures = int(measures_var.get()) if measures_var else 2
                timing_map = self.model.tempo_map if self.model else None
                units = (requested_units if resuming or not timing_map else resolve_play_start(
                    timing_map, requested_units, pre_roll_enabled=enabled,
                    pre_roll_measures=measures))
                self.pre_roll_target_units = requested_units if units != requested_units else None
                target_position = (timing_map.units_to_position(requested_units).render()
                                   if timing_map else str(requested_units))
                effective_position = (timing_map.units_to_position(units).render()
                                      if timing_map else str(units))
                LOG.debug("LIVE PLAY REQUEST requested_position=%s pre_roll_enabled=%s "
                          "pre_roll_measures=%s effective_position=%s resume=%s",
                          target_position, str(enabled).lower(), measures,
                          effective_position, str(resuming).lower())
                if timing_map:
                    LOG.debug("PRE-ROLL RESOLVE target_units=%s effective_units=%s "
                              "target_time=%.6f effective_time=%.6f",
                              requested_units, units,
                              timing_map.units_to_seconds(requested_units),
                              timing_map.units_to_seconds(units))
                if units != current_units:
                    # Feed the transformed position into the existing seek/start
                    # path so audio, MIDI recall, graphics and pause boundaries
                    # continue to share one canonical clock.
                    self.seek_units(units)
                live_midi = getattr(self, "live_midi", None)
                completions = live_midi.start(units) if live_midi else ()
                generation = live_midi.generation if live_midi else None
                def go():
                    if (live_midi and
                            (generation != live_midi.generation or not live_midi.playing)):
                        return
                    pause_units = live_midi.next_pause_units if live_midi else None
                    if selection:
                        pause_units = (selection[1] if pause_units is None else
                                       min(pause_units, selection[1]))
                    pause_seconds = (self.model.tempo_map.units_to_seconds(pause_units)
                                     if pause_units is not None else None)
                    if hasattr(self.audio_engine, "pause_at"):
                        self.audio_engine.pause_at(pause_seconds)
                    self.audio_engine.play()
                    LOG.debug("LIVE AUDIO START audio_position=%s",
                              getattr(self.audio_engine, "current_time", 0.0))
                if completions:
                    # Poll readiness on Tk; never call Tk from the worker and
                    # never sleep either thread. The serial worker makes the
                    # final Future a barrier for every preceding recall.
                    def await_recall():
                        if completions[-1].done():
                            go()
                        else:
                            self.after(1, await_recall)
                    await_recall()
                else:
                    go()
        except PlaybackError as exc: messagebox.showwarning("Audio unavailable", str(exc))

    def _live_mode_changed(self, *_args):
        enabled = self.app_mode.get() == "LIVE"
        self.live_midi.set_enabled(enabled)
        self.live_badge.configure(text="● LIVE ON" if enabled else "LIVE OFF",
                                  bg="#b51f2e" if enabled else "#3a2020")
        if enabled and self.midi_router.status(MidiDestination.SECOND_HELIX) != "Connected":
            self.status.set("LIVE: SECOND HELIX unavailable")

    @staticmethod
    def _live_message_rejection_reason(message):
        if not isinstance(message, dict):
            return "message is not a dictionary"
        message_type = message.get("type")
        if message_type == "program_change":
            value = message.get("program")
            return (None if isinstance(value, int) and not isinstance(value, bool)
                    and 0 <= value <= 127 else "invalid program_change")
        if message_type == "control_change":
            cc, value = message.get("cc"), message.get("value")
            valid = all(isinstance(item, int) and not isinstance(item, bool)
                        and 0 <= item <= 127 for item in (cc, value))
            return None if valid else "invalid control_change"
        return f"unsupported message type {message_type!r}"

    def _send_live_midi(self, message, recall, generation):
        """Serialize potentially blocking device I/O away from Tk."""
        LOG.debug("LIVE CALLBACK message=%r", message)
        position = audible_playback_time(self.audio_engine)
        def send():
            if generation != self.live_midi.generation:
                LOG.debug("LIVE ROUTER SKIP message=%r reason=stale generation", message)
                return
            if not self.live_midi.enabled:
                LOG.debug("LIVE ROUTER SKIP message=%r reason=LIVE disabled", message)
                return
            reason = self._live_message_rejection_reason(message)
            if reason is not None:
                LOG.debug("LIVE ROUTER SKIP message=%r reason=%s", message, reason)
                return
            LOG.debug("LIVE ROUTER SEND destination=SECOND_HELIX message=%r", message)
            sent = self.midi_router.send(MidiDestination.SECOND_HELIX, message)
            LOG.debug("LIVE ROUTER RESULT success=%s", str(sent).lower())
            kind = "LIVE RECALL" if recall else "LIVE MIDI"
            LOG.debug("%s  %02d:%06.3f  SECOND HELIX  %s%s", kind,
                      int(position // 60), position % 60, message,
                      "" if sent else " (unavailable)")
        return self._live_midi_pool.submit(send)

    def _update_fixed_headers_for_scroll(self, previous_left):
        """Keep viewport overlays fixed without reconstructing the timeline.

        Canvas items use scrollregion coordinates, including the lane headers.
        Moving those already-created items by the viewport delta is cheap and
        leaves waveform rasters and all other timeline primitives untouched.
        """
        current_left = self.canvas.canvasx(0)
        delta = current_left - previous_left
        if delta:
            self.canvas.move("fixed-header", delta, 0)
            if self.canvas.find_withtag("track-drag"):
                self.canvas.move("track-drag", delta, 0)

    def _transport_tick(self):
        if self.model and self.model.tempo_map:
            seconds = audible_playback_time(self.audio_engine)
            minutes, remainder = divmod(seconds, 60)
            position = self.model.tempo_map.seconds_to_musical_position(seconds)
            self.transport_position.set(f"{int(minutes):02d}:{remainder:06.3f}   |   {position.render()}")
            if (self.audio_engine.state is PlaybackState.PLAYING or
                    hasattr(self, "live_midi") and self.live_midi.playing):
                play_units = self.model.tempo_map.seconds_to_units(seconds)
                if hasattr(self, "live_midi"):
                    pause_units = self.live_midi.poll(play_units)
                    if pause_units is not None:
                        # Audio callback already stopped at this exact frame.
                        self.model.cursor = self.model._position(pause_units)
                playhead_x = timeline_x(play_units, self.model.song.ppqn, self.pixels_per_beat)
                playhead = self.canvas.find_withtag("playhead")
                if playhead:
                    coordinates = self.canvas.coords(playhead[0])
                    if len(coordinates) == 4:
                        self.canvas.coords(playhead[0], playhead_x, coordinates[1],
                                           playhead_x, coordinates[3])
                left = self.canvas.canvasx(0)
                width = self.canvas.winfo_width()
                destination = follow_scroll(
                    playhead_x, left, width, playing=True,
                    suspended=time.monotonic() < self._follow_suspended_until)
                if destination is not None:
                    region = self.canvas.cget("scrollregion").split()
                    total = float(region[2]) if len(region) == 4 else 1.0
                    self.canvas.xview_moveto(destination / max(1.0, total))
                    # Scrolling reuses the existing canvas and ghost raster.
                    # Only viewport-fixed overlays need a lightweight move.
                    self._update_fixed_headers_for_scroll(left)
                    current_left = self.canvas.canvasx(0)
                    if (self.full_song_ghost_visible.get() and
                            not self._ghost_refresh_pending and
                            viewport_exits_coverage(
                                current_left, width, self._ghost_raster_coverage)):
                        self._ghost_refresh_pending = True
                        # Raster mapping/compression runs only after this 33 ms
                        # transport callback returns to Tk's event loop.
                        self.after_idle(self._refresh_ghost_waveform)
        self.after(33, self._transport_tick)

    def destroy(self):
        self._load_generation += 1
        self._audio_cancel.set(); self._waveform_cancel.set()
        self.audio_engine.close(); self._waveform_pool.shutdown(wait=False, cancel_futures=True)
        self._audio_pool.shutdown(wait=False, cancel_futures=True)
        self._loading_pool.shutdown(wait=False, cancel_futures=True)
        self.show_preloader.shutdown()
        self.midi_router.close()
        super().destroy()


def main():
    ReapcaseEditor().mainloop()


if __name__ == "__main__":
    main()
