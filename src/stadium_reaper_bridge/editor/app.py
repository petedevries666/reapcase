"""Tkinter Reapcase Desktop Editor MVP.

Launch with ``PYTHONPATH=src python -m stadium_reaper_bridge.editor.app``.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .layout import (DEFAULT_PIXELS_PER_BEAT, HEADER_WIDTH, LANE_HEIGHT,
                     drag_units, fit_song_scale, snap_drag_delta,
                     x_for_position, zoom_about_cursor)
from .model import EditorModel, LANES, MovePreview


class ReapcaseEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Reapcase Desktop Editor")
        self.geometry("1180x680")
        self.model: EditorModel | None = None
        self.pixels_per_beat = DEFAULT_PIXELS_PER_BEAT
        self.drag_x: float | None = None
        self.drag_preview: MovePreview | None = None
        self.grid_choice = tk.StringVar(value="1 beat")
        self.info = tk.StringVar(value="Open a Stadium Song JSON to begin")
        self.status = tk.StringVar(value="No file loaded")
        self.zoom_label = tk.StringVar()
        self._build()

    def _build(self):
        toolbar = ttk.Frame(self, padding=6); toolbar.pack(fill="x")
        for text, command in (("Open JSON", self.open_json), ("Save As JSON", self.save_as),
                              ("Select All", self.select_all), ("Select All After Cursor", self.select_after),
                              ("Select Lane", self.select_lane), ("Shift Selected", self.shift_dialog),
                              ("Undo", self.undo), ("Zoom Out", lambda: self.zoom_step(1 / 1.25)),
                              ("Zoom In", lambda: self.zoom_step(1.25)), ("Fit Song", self.fit_song)):
            ttk.Button(toolbar, text=text, command=command).pack(side="left", padx=2)
        ttk.Label(toolbar, text=" Grid:").pack(side="left")
        ttk.Combobox(toolbar, textvariable=self.grid_choice, state="readonly", width=12,
                     values=("1 bar", "1 beat", "quarter beat", "no snap")).pack(side="left")
        ttk.Label(toolbar, textvariable=self.zoom_label, width=8, anchor="e").pack(side="left", padx=5)
        ttk.Label(self, textvariable=self.info, padding=(8, 3)).pack(fill="x")
        frame = ttk.Frame(self); frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(frame, background="#171b22", highlightthickness=0)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
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
        self.canvas.bind("<MouseWheel>", self.vertical_wheel)
        self.canvas.bind("<Button-4>", self.vertical_wheel)
        self.canvas.bind("<Button-5>", self.vertical_wheel)
        self.canvas.bind("<Button-2>", lambda event: self.canvas.scan_mark(event.x, event.y))
        self.canvas.bind("<B2-Motion>", lambda event: self.canvas.scan_dragto(event.x, event.y, gain=1))
        self._update_zoom_label()
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=5).pack(fill="x")

    def open_json(self):
        path = filedialog.askopenfilename(filetypes=(("JSON", "*.json"), ("All files", "*")))
        if not path: return
        try: self.model = EditorModel.open(path)
        except Exception as exc: messagebox.showerror("Cannot open Song", str(exc)); return
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

    def redraw(self):
        self.canvas.delete("all")
        if not self.model: return
        m = self.model
        max_bar = max((e.position.bar for e in m.timeline.events), default=1) + 2
        width = HEADER_WIDTH + max_bar * m.numerator * self.pixels_per_beat
        height = len(LANES) * LANE_HEIGHT
        for lane_index, lane in enumerate(LANES):
            y = lane_index * LANE_HEIGHT
            self.canvas.create_rectangle(0, y, width, y + LANE_HEIGHT, fill="#202631" if lane_index % 2 else "#1c222c", outline="#394250")
            self.canvas.create_text(8, y + 12, text=lane, anchor="nw", fill="#9ec8ff", font=("TkDefaultFont", 9, "bold"))
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
        for i, event in enumerate(m.timeline.events):
            lane = LANES.index(m.lane(event)); x = x_for_position(event.position, m.song.ppqn, m.numerator, self.pixels_per_beat)
            y = lane * LANE_HEIGHT + 27; selected = i in m.selected
            text = f"{m.label(event)}  {event.position.render()}"
            item = self.canvas.create_text(x + 5, y + 10, text=text, anchor="w", fill="#101318", tags=(f"event:{i}",))
            box = self.canvas.bbox(item)
            rect = self.canvas.create_rectangle(box[0]-4, box[1]-3, box[2]+4, box[3]+3,
                                                fill="#ffd166" if selected else "#8fd3c7", outline="#ffffff" if selected else "#4d8f88",
                                                tags=(f"event:{i}",))
            self.canvas.tag_raise(item)
        if self.drag_preview:
            self._draw_drag_preview(self.drag_preview)
        unsupported = ", ".join(m.unsupported_types) or "none"
        self.info.set(f"{m.song.name}  |  PPQN {m.song.ppqn}  |  {m.tempo:g} BPM  |  {m.numerator}/{m.denominator}  |  {len(m.timeline.events)} flags  |  {m.path}")
        self.status.set(f"{m.path.name}  |  flags {len(m.timeline.events)}  |  selected {len(m.selected)}  |  cursor {m.cursor.render()}  |  {'MODIFIED' if m.modified else 'unmodified'}  |  unsupported: {unsupported}")
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

    def click(self, event):
        if not self.model: return
        index = self._event_index(event); x = self.canvas.canvasx(event.x)
        units = max(0, round((x - HEADER_WIDTH) / self.pixels_per_beat * self.model.song.ppqn))
        self.model.cursor = self.model._position(units)
        if index is not None:
            if event.state & 0x4: self.model.selected.symmetric_difference_update({index})
            else: self.model.selected = {index}
            self.drag_x = x
            self.drag_preview = self.model.preview_shift(0)
        self.redraw()

    def drag(self, event):
        if not self.model or self.drag_x is None or not self.model.selected:
            return
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
        if not self.model or self.drag_x is None: return
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

    def fit_song(self):
        if not self.model:
            return
        end = max((self.model._units(e.position) for e in self.model.timeline.events), default=0)
        self.pixels_per_beat = fit_song_scale(end, self.model.song.ppqn,
                                              self.canvas.winfo_width())
        self.redraw()
        self.canvas.xview_moveto(0)

    def _update_zoom_label(self):
        self.zoom_label.set(f"{self.pixels_per_beat / DEFAULT_PIXELS_PER_BEAT:.0%}")

    def horizontal_wheel(self, event):
        self.canvas.xview_scroll(-self._wheel_direction(event) * 3, "units")
        return "break"

    def vertical_wheel(self, event):
        self.canvas.yview_scroll(-self._wheel_direction(event) * 3, "units")
        return "break"

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


def main():
    ReapcaseEditor().mainloop()


if __name__ == "__main__":
    main()
