"""Tk screen renderer for :mod:`.roadmap`; no song interpretation lives here."""

import tkinter as tk
from tkinter import ttk


class RoadmapView(ttk.Frame):
    def __init__(self, parent, *, on_navigate, on_edit_note, on_layout):
        super().__init__(parent)
        controls = ttk.Frame(self, padding=(8, 5))
        controls.pack(fill="x")
        ttk.Label(controls, text="MEASURES / ROW").pack(side="left")
        self.per_row = tk.StringVar(value="4")
        chooser = ttk.Combobox(controls, textvariable=self.per_row,
                               values=("2", "4", "8"), state="readonly", width=4)
        chooser.pack(side="left", padx=6)
        chooser.bind("<<ComboboxSelected>>", lambda _e: on_layout(int(self.per_row.get())))
        self.canvas = tk.Canvas(self, background="#f7f7f4", highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._on_navigate, self._on_edit_note = on_navigate, on_edit_note
        self._hits = {}
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Double-Button-1>", self._double_click)
        self.canvas.bind("<Configure>", lambda _e: self.render(self.document)
                         if hasattr(self, "document") else None)

    def render(self, document):
        self.document = document
        self.per_row.set(str(document.measures_per_row))
        c = self.canvas; c.delete("all"); self._hits = {}
        width = max(c.winfo_width(), 700)
        left, right, gap = 54, 36, 10
        cell_width = (width - left - right - gap * (document.measures_per_row - 1)) / document.measures_per_row
        y = 35
        c.create_text(left, y, text=document.title.upper(), anchor="w",
                      fill="#161616", font=("TkDefaultFont", 17, "bold"))
        y += 25
        tempo = f"{document.tempo:g} BPM" if document.tempo is not None else "— BPM"
        c.create_text(left, y, text=f"{tempo} · {document.numerator}/{document.denominator}",
                      anchor="w", fill="#444444", font=("TkDefaultFont", 10))
        y += 42
        for section in document.sections:
            c.create_text(left, y, text=section.name.upper(), anchor="w",
                          fill="#111111", font=("TkDefaultFont", 11, "bold"))
            y += 24
            for row in section.rows:
                for column, block in enumerate(row.blocks):
                    x = left + column * (cell_width + gap)
                    measure = block.measures[0]
                    c.create_text(x, y, text=str(measure.number), anchor="sw",
                                  fill="#333333", font=("TkDefaultFont", 9, "bold"))
                    top, bottom = y + 5, y + 64
                    item = c.create_rectangle(x, top, x + cell_width, bottom,
                                              outline="#222222", width=1)
                    if measure.note:
                        c.create_text(x + cell_width / 2, (top + bottom) / 2,
                                      text=measure.note.text, fill="#111111",
                                      width=max(20, cell_width - 12), justify="center",
                                      font=("TkDefaultFont", 10, "bold"))
                    self._hits[item] = block
                y += 88
            y += 18
        c.configure(scrollregion=(0, 0, width, max(y, c.winfo_height())))

    def _block_at(self, event):
        items = self.canvas.find_overlapping(event.x, self.canvas.canvasy(event.y),
                                             event.x, self.canvas.canvasy(event.y))
        return next((self._hits[item] for item in reversed(items) if item in self._hits), None)

    def _click(self, event):
        block = self._block_at(event)
        if block:
            self._on_navigate(block)

    def _double_click(self, event):
        block = self._block_at(event)
        if block:
            self._on_edit_note(block)
