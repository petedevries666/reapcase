"""Tkinter Reapcase Desktop Editor MVP.

Launch with ``PYTHONPATH=src python -m stadium_reaper_bridge.editor.app``.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from concurrent.futures import ThreadPoolExecutor

from .layout import (DEFAULT_PIXELS_PER_BEAT, HEADER_WIDTH, LANE_HEIGHT,
                     drag_units, fit_song_scale, horizontal_wheel_units,
                     marquee_candidates, normalized_rectangle, snap_drag_delta,
                     x_for_position, zoom_about_cursor)
from .model import EditorModel, LANES, MovePreview
from .audio_engine import AudioEngine, PlaybackError, PlaybackState, PlaybackTrack
from .waveform import (analyze_grid_sync, extract_waveform, format_grid_sync,
                       raster_ppm, timeline_units_to_x, viewport_columns)


class ReapcaseEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Reapcase Desktop Editor")
        self.geometry("1180x680")
        self.model: EditorModel | None = None
        self.pixels_per_beat = DEFAULT_PIXELS_PER_BEAT
        self.drag_x: float | None = None
        self.drag_preview: MovePreview | None = None
        self.marquee_anchor: tuple[float, float] | None = None
        self.marquee_point: tuple[float, float] | None = None
        self.marquee_base: set[int] = set()
        self.marquee_mode = "replace"
        self.event_bounds: dict[int, tuple[float, float, float, float]] = {}
        self.grid_choice = tk.StringVar(value="1 beat")
        self.info = tk.StringVar(value="Open a Stadium Song JSON to begin")
        self.status = tk.StringVar(value="No file loaded")
        self.zoom_label = tk.StringVar()
        self.transport_position = tk.StringVar(value="00:00.000   |   001-01.001")
        self.audio_engine = AudioEngine()
        self.monitor_muted: list[bool] = []
        self.monitor_solo: list[bool] = []
        self.waveforms = {}
        self._waveform_images = []
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
        toolbar = ttk.Frame(self, padding=6); toolbar.pack(fill="x")
        for text, command in (("Open JSON", self.open_json), ("Save As JSON", self.save_as),
                              ("Locate Audio Folder", self.locate_audio),
                              ("Analyze Grid Sync", self.analyze_sync),
                              ("Select All", self.select_all), ("Select All After Cursor", self.select_after),
                              ("Select Lane", self.select_lane), ("Shift Selected", self.shift_dialog),
                              ("Undo", self.undo), ("Zoom Out", lambda: self.zoom_step(1 / 1.25)),
                              ("Zoom In", lambda: self.zoom_step(1.25)), ("Fit Song", self.fit_song)):
            ttk.Button(toolbar, text=text, command=command).pack(side="left", padx=2)
        ttk.Label(toolbar, text=" Grid:").pack(side="left")
        ttk.Combobox(toolbar, textvariable=self.grid_choice, state="readonly", width=12,
                     values=("1 bar", "1 beat", "quarter beat", "no snap")).pack(side="left")
        ttk.Checkbutton(toolbar, text="Audio grid", variable=self.audio_grid_overlay,
                        command=self.redraw).pack(side="left")
        ttk.Label(toolbar, textvariable=self.zoom_label, width=8, anchor="e").pack(side="left", padx=5)
        ttk.Label(self, textvariable=self.info, padding=(8, 3)).pack(fill="x")
        transport = ttk.Frame(self, padding=(8, 3)); transport.pack(fill="x")
        ttk.Button(transport, text="|<<", width=5, command=self.return_to_start).pack(side="left")
        ttk.Button(transport, text="Play / Pause", command=self.play_pause).pack(side="left", padx=3)
        ttk.Button(transport, text="Stop", command=self.stop_playback).pack(side="left")
        ttk.Label(transport, textvariable=self.transport_position, font=("TkFixedFont", 10)).pack(side="left", padx=14)
        frame = ttk.Frame(self); frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(frame, background="#171b22", highlightthickness=0)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self._scroll_horizontal)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew"); yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew"); frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        self.canvas.bind("<Button-1>", self.click); self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.drop)
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
        self._update_zoom_label()
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=5).pack(fill="x")

    def open_json(self):
        path = filedialog.askopenfilename(filetypes=(("JSON", "*.json"), ("All files", "*")))
        if not path: return
        self.audio_engine.close()
        try: self.model = EditorModel.open(path)
        except Exception as exc: messagebox.showerror("Cannot open Song", str(exc)); return
        if self.manual_audio_root:
            self.model.resolve_audio(self.manual_audio_root)
        self._configure_audio()
        self.redraw()

    def save_as(self):
        if not self.model: return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("JSON", "*.json"),))
        if path and str(self.model.path.resolve()) == str(__import__('pathlib').Path(path).resolve()):
            messagebox.showerror("Choose a new file", "Save As never overwrites the original source file."); return
        if path:
            summary = self.model.save_as(path)
            messagebox.showinfo("Export complete", f"{summary.events_moved} events moved\n0 payloads changed\n0 tracks changed")
            self.redraw()

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

    def _configure_audio(self):
        self.audio_engine.close(); self.waveforms.clear(); self._waveform_pending.clear()
        tracks = list(self.model.audio_tracks) if self.model else []
        self.monitor_muted = [False] * len(tracks); self.monitor_solo = [False] * len(tracks)
        resolved = [PlaybackTrack(t.resolved_path, t.name, t.offset) for t in tracks if t.resolved_path]
        try:
            self.audio_engine.open(resolved)
        except Exception as exc:
            self.audio_engine.diagnostic = str(exc)
        for track in tracks:
            if track.resolved_path: self._request_waveform(track.resolved_path)

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
        all_lanes = list(LANES) + [f"AUDIO {track.number}" for track in m.audio_tracks]
        height = len(all_lanes) * LANE_HEIGHT
        for lane_index, lane in enumerate(all_lanes):
            y = lane_index * LANE_HEIGHT
            self.canvas.create_rectangle(0, y, width, y + LANE_HEIGHT, fill="#202631" if lane_index % 2 else "#1c222c", outline="#394250")
            self.canvas.create_text(8, y + 12, text=lane, anchor="nw", fill="#9ec8ff", font=("TkDefaultFont", 9, "bold"))
        max_bar = max(1, int(total_beats / m.numerator) + 2)
        for bar in range(1, max_bar + 1):
            for beat in range(1, m.numerator + 1):
                x = HEADER_WIDTH + ((bar - 1) * m.numerator + beat - 1) * self.pixels_per_beat
                prominent = beat == 1
                if not prominent and self.pixels_per_beat < 28:
                    continue
                self.canvas.create_line(x, 0, x, height, fill="#708096" if prominent else "#343f4d", width=2 if prominent else 1)
                if prominent: self.canvas.create_text(x + 4, 3, text=f"{bar:03d}-01.001", anchor="nw", fill="#b8c2ce")
                if self.pixels_per_beat >= 100:
                    for quarter in range(1, 4):
                        qx = x + quarter * self.pixels_per_beat / 4
                        self.canvas.create_line(qx, 0, qx, height, fill="#29333f", dash=(1, 3))
        preview_selection = self._marquee_selection()
        self.event_bounds = {}
        for i, event in enumerate(m.timeline.events):
            lane = LANES.index(m.lane(event)); x = x_for_position(event.position, m.song.ppqn, m.numerator, self.pixels_per_beat)
            y = lane * LANE_HEIGHT + 27; selected = i in m.selected
            previewed = preview_selection is not None and i in preview_selection
            text = f"{m.label(event)}  {event.position.render()}"
            item = self.canvas.create_text(x + 5, y + 10, text=text, anchor="w", fill="#101318", tags=(f"event:{i}",))
            box = self.canvas.bbox(item)
            rect = self.canvas.create_rectangle(box[0]-4, box[1]-3, box[2]+4, box[3]+3,
                                                fill="#ffe49a" if previewed else ("#ffd166" if selected else "#8fd3c7"),
                                                outline="#ffffff" if selected or previewed else "#4d8f88",
                                                tags=(f"event:{i}",))
            self.event_bounds[i] = (box[0]-4, box[1]-3, box[2]+4, box[3]+3)
            self.canvas.tag_raise(item)
        for lane_offset, track in enumerate(m.audio_tracks):
            y = (len(LANES) + lane_offset) * LANE_HEIGHT
            flags = " ".join(word for enabled, word in ((track.source.get("mute"), "MUTE"),
                                                          (track.source.get("solo"), "SOLO")) if enabled)
            levels = f"trim {track.source.get('trim', '?')}  gain {track.source.get('gain', '?')}"
            self.canvas.create_text(8, y + 28, anchor="nw", fill="#c6d4e5",
                                    text=f"{track.name}\n{flags} {levels}".strip())
            for column, (label, enabled) in enumerate((("M", self.monitor_muted[lane_offset]),
                                                        ("S", self.monitor_solo[lane_offset]))):
                self.canvas.create_rectangle(76 + column*27, y + 5, 99 + column*27, y + 23,
                    fill="#d36b62" if enabled and label == "M" else ("#e3bf58" if enabled else "#394552"),
                    tags=(f"monitor:{lane_offset}:{label}",))
                self.canvas.create_text(87 + column*27, y + 14, text=label, fill="white",
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
                                         fill="#526b8a" if track.file_info else "#463f50",
                                         outline="#8cb8e8" if track.file_info else "#d28b9a")
            clip_label = self.canvas.create_text(start_x + 6, y + 32, anchor="w",
                                                 fill="#f0f5fa",
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
                                        fill="#7891aa", width=1)
                self.canvas.tag_raise(clip_label)
            if self.audio_grid_overlay and self.pixels_per_beat >= 40:
                for unit in range(0, m.song_end_units + 1, max(1, m.song.ppqn // 4)):
                    x = timeline_units_to_x(unit, m.song.ppqn, self.pixels_per_beat,
                                            HEADER_WIDTH)
                    beat = unit % m.song.ppqn == 0
                    bar = unit % (m.song.ppqn * m.numerator) == 0
                    self.canvas.create_line(x, y + 22, x, y + 62,
                        fill="#d5e6fa" if bar else ("#90a9c5" if beat else "#53657a"),
                        width=2 if bar else 1, dash=() if beat else (1, 3))
        if self.marquee_anchor and self.marquee_point:
            bounds = normalized_rectangle(*self.marquee_anchor, *self.marquee_point)
            self.canvas.create_rectangle(*bounds, fill="#527aa3", stipple="gray50",
                                         outline="#b9dcff", width=2, tags=("marquee",))
        if self.drag_preview:
            self._draw_drag_preview(self.drag_preview)
        if m.tempo_map:
            play_units = m.tempo_map.seconds_to_units(self.audio_engine.current_time)
            play_x = HEADER_WIDTH + play_units / m.song.ppqn * self.pixels_per_beat
            self.canvas.create_line(play_x, 0, play_x, height, fill="#ff5b57", width=2,
                                    tags=("playhead",))
        unsupported = ", ".join(m.unsupported_types) or "none"
        overflow = f" | WARNING: {m.audio_overflow} tracks above display limit preserved" if m.audio_overflow else ""
        tempo = f"{m.tempo:g} BPM" if m.tempo is not None else "tempo unavailable"
        self.info.set(f"{m.song.name}  |  PPQN {m.song.ppqn}  |  {tempo}  |  {m.numerator}/{m.denominator}  |  {len(m.timeline.events)} flags  |  {m.path}{overflow}")
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
            x = x_for_position(position, self.model.song.ppqn, self.model.numerator, self.pixels_per_beat)
            if not preview.valid:
                x += preview.delta_units / self.model.song.ppqn * self.pixels_per_beat
            y = LANES.index(self.model.lane(event)) * LANE_HEIGHT + 27
            text = f"{self.model.label(event)}  {position.render()}"
            item = self.canvas.create_text(x + 5, y + 10, text=text, anchor="w",
                                           fill="#381b1b" if not preview.valid else "#263241",
                                           tags=("drag-preview",))
            box = self.canvas.bbox(item)
            self.canvas.create_rectangle(box[0]-4, box[1]-3, box[2]+4, box[3]+3,
                                         fill="#e87777" if not preview.valid else "#d8e7f5",
                                         outline="#ff4f4f" if not preview.valid else "#8fb8dc",
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
        index = self._event_index(event); x = self.canvas.canvasx(event.x)
        units = max(0, round((x - HEADER_WIDTH) / self.pixels_per_beat * self.model.song.ppqn))
        self.model.cursor = self.model._position(units)
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
        if self.canvas.canvasy(event.y) < 24:
            self.seek_units(units); self.redraw(); return
        if index is not None:
            self.model.select_for_drag(index, toggle=bool(event.state & 0x4))
            self.drag_x = x
            self.drag_preview = self.model.preview_shift(0)
        else:
            y = self.canvas.canvasy(event.y)
            self.marquee_anchor = self.marquee_point = (x, y)
            self.marquee_base = set(self.model.selected)
            self.marquee_mode = "toggle" if event.state & 0x4 else ("add" if event.state & 0x1 else "replace")
        self.redraw()

    def drag(self, event):
        if not self.model:
            return
        if self.marquee_anchor:
            self.marquee_point = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
            self.redraw()
            return
        if self.drag_x is None or not self.model.selected: return
        dx = self.canvas.canvasx(event.x) - self.drag_x
        raw = drag_units(dx, self.pixels_per_beat, self.model.song.ppqn)
        anchor = min(self.model._units(self.model.timeline.events[i].position) for i in self.model.selected)
        delta = snap_drag_delta(anchor, raw, self.grid_choice.get(), self.model.song.ppqn,
                                self.model.numerator)
        self.drag_preview = self.model.preview_shift(delta)
        self.redraw()
        count = len(self.drag_preview.indices)
        if self.drag_preview.valid:
            noun = "event" if count == 1 else "events"
            self.status.set(f"Move {count} {noun} → {self.drag_preview.destination.render()}  |  valid")
        else:
            self.status.set("Invalid move: before Song start")

    def drop(self, event):
        if not self.model: return
        if self.marquee_anchor:
            selection = self._marquee_selection()
            self.model.selected = selection or set()
            self.marquee_anchor = self.marquee_point = None
            self.marquee_base = set()
            self.redraw()
            return
        if self.drag_x is None: return
        preview = self.drag_preview
        if preview and preview.valid:
            self.model.commit_preview(preview)
        self.drag_x = None
        self.drag_preview = None
        self.redraw()

    @staticmethod
    def _wheel_direction(event):
        return 1 if getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4 else -1

    def mouse_zoom(self, event):
        self._zoom_at(1.12 ** self._wheel_direction(event), event.x)
        return "break"

    def zoom_step(self, factor):
        self._zoom_at(factor, self.canvas.winfo_width() / 2)

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

    def _update_zoom_label(self):
        self.zoom_label.set(f"{self.pixels_per_beat / DEFAULT_PIXELS_PER_BEAT:.0%}")

    def horizontal_wheel(self, event):
        self.canvas.xview_scroll(horizontal_wheel_units(self._wheel_direction(event)), "units")
        self.redraw()
        return "break"

    def _scroll_horizontal(self, *args):
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
    def shift_dialog(self):
        if not self.model: return
        win = tk.Toplevel(self); win.title("Shift Selected"); entries = []
        for row, label in enumerate(("Bars", "Beats", "Ticks")):
            ttk.Label(win, text=label).grid(row=row, column=0); entry=ttk.Entry(win); entry.insert(0,"0"); entry.grid(row=row,column=1); entries.append(entry)
        def apply():
            try: self.model.shift_selected(*(int(e.get()) for e in entries))
            except ValueError as exc: messagebox.showerror("Invalid shift", str(exc)); return
            win.destroy(); self.redraw()
        ttk.Button(win, text="Shift", command=apply).grid(row=3, columnspan=2, pady=8)
    def undo(self):
        if self.model: self.model.undo(); self.redraw()

    def seek_units(self, units):
        if self.model and self.model.tempo_map:
            self.audio_engine.seek(self.model.tempo_map.units_to_seconds(units))
            self.model.cursor = self.model._position(units)

    def return_to_start(self): self.audio_engine.return_to_start(); self.redraw()
    def stop_playback(self): self.audio_engine.stop(); self.redraw()
    def play_pause(self):
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
            if self.audio_engine.state is PlaybackState.PLAYING: self.redraw()
        self.after(33, self._transport_tick)

    def destroy(self):
        self.audio_engine.close(); self._waveform_pool.shutdown(wait=False, cancel_futures=True)
        super().destroy()


def main():
    ReapcaseEditor().mainloop()


if __name__ == "__main__":
    main()
