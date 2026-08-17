"""Tkinter Reapcase Desktop Editor MVP.

Launch with ``PYTHONPATH=src python -m stadium_reaper_bridge.editor.app``.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .layout import HEADER_WIDTH, LANE_HEIGHT, x_for_position
from .model import EditorModel, LANES


class ReapcaseEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Reapcase Desktop Editor")
        self.geometry("1180x680")
        self.model: EditorModel | None = None
        self.pixels_per_beat = 90
        self.drag_x: float | None = None
        self.grid_choice = tk.StringVar(value="1 beat")
        self.info = tk.StringVar(value="Open a Stadium Song JSON to begin")
        self.status = tk.StringVar(value="No file loaded")
        self._build()

    def _build(self):
        toolbar = ttk.Frame(self, padding=6); toolbar.pack(fill="x")
        for text, command in (("Open JSON", self.open_json), ("Save As JSON", self.save_as),
                              ("Select All", self.select_all), ("Select All After Cursor", self.select_after),
                              ("Select Lane", self.select_lane), ("Shift Selected", self.shift_dialog),
                              ("Undo", self.undo)):
            ttk.Button(toolbar, text=text, command=command).pack(side="left", padx=2)
        ttk.Label(toolbar, text=" Grid:").pack(side="left")
        ttk.Combobox(toolbar, textvariable=self.grid_choice, state="readonly", width=12,
                     values=("1 bar", "1 beat", "quarter beat", "no snap")).pack(side="left")
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
                self.canvas.create_line(x, 0, x, height, fill="#708096" if prominent else "#343f4d", width=2 if prominent else 1)
                if prominent: self.canvas.create_text(x + 4, 3, text=f"{bar:03d}-01.001", anchor="nw", fill="#b8c2ce")
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
        unsupported = ", ".join(m.unsupported_types) or "none"
        self.info.set(f"{m.song.name}  |  PPQN {m.song.ppqn}  |  {m.tempo:g} BPM  |  {m.numerator}/{m.denominator}  |  {len(m.timeline.events)} flags  |  {m.path}")
        self.status.set(f"{m.path.name}  |  flags {len(m.timeline.events)}  |  selected {len(m.selected)}  |  cursor {m.cursor.render()}  |  {'MODIFIED' if m.modified else 'unmodified'}  |  unsupported: {unsupported}")
        self.canvas.configure(scrollregion=(0, 0, width, height))

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
        self.redraw()

    def drag(self, event):
        pass  # Movement is committed atomically on release.

    def drop(self, event):
        if not self.model or self.drag_x is None: return
        dx = self.canvas.canvasx(event.x) - self.drag_x
        raw = round(dx / self.pixels_per_beat * self.model.song.ppqn)
        grids = {"1 bar": self.model.song.ppqn*self.model.numerator, "1 beat": self.model.song.ppqn,
                 "quarter beat": self.model.song.ppqn//4, "no snap": 1}
        grid = grids[self.grid_choice.get()]
        anchor = min(self.model._units(self.model.timeline.events[i].position) for i in self.model.selected)
        # Snap the destination, not merely the delta: quarter-beat boundaries
        # are therefore exactly ticks 001/061/121/181 at 240 PPQN.
        delta = round((anchor + raw) / grid) * grid - anchor
        try:
            if delta: self.model.shift_selected(ticks=delta)
        except ValueError as exc: messagebox.showerror("Invalid movement", str(exc))
        self.drag_x = None; self.redraw()

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
