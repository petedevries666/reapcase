"""Lightweight filesystem browser for Stadium Song JSON files.

This module deliberately keeps discovery separate from Tk so browsing and its
performance guarantees can be tested without constructing an editor model.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .stadium_workspace import SONGS_DIRECTORY, load_manifest


def natural_key(value: str):
    """Return a case-insensitive key with digit runs compared numerically."""
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", value))


@dataclass(frozen=True)
class SongBrowserEntry:
    path: Path
    title: str

    @property
    def file_id(self) -> str:
        return self.path.stem


class SongMetadataCache:
    """Cache validated Song headers by filesystem identity."""

    def __init__(self):
        self._items: dict[Path, tuple[int, int, Optional[SongBrowserEntry]]] = {}

    def inspect(self, path: Path) -> Optional[SongBrowserEntry]:
        path = Path(path).resolve()
        try:
            stat = path.stat()
        except OSError:
            return None
        cached = self._items.get(path)
        signature = (stat.st_size, stat.st_mtime_ns)
        if cached and cached[:2] == signature:
            return cached[2]
        entry = None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(document, dict) and
                    isinstance(document.get("name"), str) and document["name"].strip() and
                    isinstance(document.get("flags"), list)):
                entry = SongBrowserEntry(path, document["name"].strip())
        except (OSError, UnicodeError, ValueError, TypeError):
            pass
        self._items[path] = (*signature, entry)
        return entry


_METADATA_CACHE = SongMetadataCache()


class SongDirectory:
    """A cheap, filterable projection of one real filesystem directory."""

    def __init__(self, path: Path, cache: Optional[SongMetadataCache] = None):
        self.path = Path(path).resolve()
        self.cache = cache or SongMetadataCache()
        self.folders: tuple[Path, ...] = ()
        self.songs: tuple[SongBrowserEntry, ...] = ()

    def scan(self) -> "SongDirectory":
        folders, songs = [], []
        try:
            children = tuple(self.path.iterdir())
        except OSError:
            children = ()
        for child in children:
            try:
                if child.is_dir():
                    folders.append(child.resolve())
                elif child.is_file() and child.suffix.casefold() == ".json":
                    song = self.cache.inspect(child)
                    if song:
                        songs.append(song)
            except OSError:
                continue
        self.folders = tuple(sorted(folders, key=lambda item: natural_key(item.name)))
        self.songs = tuple(sorted(songs, key=lambda item: natural_key(item.file_id)))
        return self

    def filtered_songs(self, query: str = "", sort_by: str = "file"):
        query = query.strip().casefold()
        result = [song for song in self.songs
                  if not query or query in song.file_id.casefold() or query in song.title.casefold()]
        key = ((lambda song: (natural_key(song.title), natural_key(song.file_id)))
               if sort_by == "title" else
               (lambda song: (natural_key(song.file_id), natural_key(song.title))))
        return tuple(sorted(result, key=key))


def workspace_for_song(path: Path) -> Optional[Path]:
    """Recognize a Song inside the canonical directory of an imported workspace."""
    path = Path(path).resolve()
    parts = SONGS_DIRECTORY.parts
    if tuple(path.parent.parts[-len(parts):]) != parts:
        return None
    workspace = path.parent
    for _part in parts:
        workspace = workspace.parent
    try:
        load_manifest(workspace)
    except ValueError:
        return None
    return workspace.resolve()


def initial_song_folder(workspace: Optional[Path], remembered: Optional[Path],
                        fallback: Optional[Path] = None) -> Path:
    """Prefer current workspace Songs, then a remembered usable directory."""
    if workspace:
        candidate = Path(workspace) / SONGS_DIRECTORY
        try:
            load_manifest(workspace)
            if candidate.is_dir():
                return candidate.resolve()
        except ValueError:
            pass
    if remembered and Path(remembered).is_dir():
        return Path(remembered).resolve()
    candidate = Path(fallback or Path.home())
    return candidate.resolve() if candidate.is_dir() else Path.cwd().resolve()


class SongBrowser(tk.Toplevel):
    """Compact Explorer-like modal specialized for choosing Stadium Songs."""

    def __init__(self, parent, initial: Path, on_open: Callable[[Path], object],
                 workspace: Optional[Path] = None, on_folder: Optional[Callable[[Path], None]] = None):
        super().__init__(parent)
        self.title("Open Song")
        self.geometry("720x480")
        self.minsize(560, 360)
        self._on_open, self._on_folder = on_open, on_folder
        # Shared for the process lifetime, so closing and reopening the picker
        # does not reparse unchanged files from the same large workspace.
        self._cache = _METADATA_CACHE
        self._history: list[Path] = []
        self._folder = Path(initial).resolve()
        self._workspace = Path(workspace).resolve() if workspace else None
        self.path_text = tk.StringVar(value=str(self._folder))
        self.search = tk.StringVar()
        self.sort_by = tk.StringVar(value="file")
        self._rows: dict[str, tuple[str, object]] = {}
        self._build()
        self._navigate(self._folder, remember=False)
        self.transient(parent); self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(self):
        nav = ttk.Frame(self, padding=(8, 8, 8, 4)); nav.pack(fill="x")
        ttk.Button(nav, text="Back", command=self._back).pack(side="left")
        ttk.Button(nav, text="Up", command=self._up).pack(side="left", padx=(4, 8))
        ttk.Entry(nav, textvariable=self.path_text).pack(side="left", fill="x", expand=True)
        ttk.Button(nav, text="Go", command=lambda: self._navigate(Path(self.path_text.get()))).pack(side="left", padx=(4, 0))
        if self._workspace:
            ttk.Button(nav, text="Current Workspace Songs", command=self._workspace_songs).pack(side="left", padx=(8, 0))
        search = ttk.Frame(self, padding=(8, 4)); search.pack(fill="x")
        ttk.Label(search, text="Search Songs:").pack(side="left")
        ttk.Entry(search, textvariable=self.search).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(search, text="Sort:").pack(side="left")
        combo = ttk.Combobox(search, textvariable=self.sort_by, state="readonly", width=10,
                             values=("file", "title")); combo.pack(side="left")
        self.tree = ttk.Treeview(self, columns=("file", "song"), show="headings", selectmode="browse")
        self.tree.heading("file", text="File / ID", command=lambda: self._set_sort("file"))
        self.tree.heading("song", text="Song", command=lambda: self._set_sort("title"))
        self.tree.column("file", width=180, stretch=False); self.tree.column("song", width=440)
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)
        buttons = ttk.Frame(self, padding=(8, 4, 8, 8)); buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Open", command=self._open).pack(side="right", padx=6)
        self.search.trace_add("write", lambda *_: self._render())
        self.sort_by.trace_add("write", lambda *_: self._render())
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<Return>", lambda _event: self._open())

    def _navigate(self, path: Path, remember=True):
        path = Path(path).expanduser().resolve()
        if not path.is_dir():
            return
        if remember and path != self._folder:
            self._history.append(self._folder)
        self._folder = path
        self.path_text.set(str(path))
        self._directory = SongDirectory(path, self._cache).scan()
        if self._on_folder:
            self._on_folder(path)
        self._render()

    def _render(self):
        self.tree.delete(*self.tree.get_children()); self._rows.clear()
        for folder in self._directory.folders:
            row = self.tree.insert("", "end", values=("📁 " + folder.name, "Folder"))
            self._rows[row] = ("folder", folder)
        for song in self._directory.filtered_songs(self.search.get(), self.sort_by.get()):
            row = self.tree.insert("", "end", values=(song.file_id, song.title))
            self._rows[row] = ("song", song.path)

    def _selected(self):
        selected = self.tree.selection()
        return self._rows.get(selected[0]) if selected else None

    def _open(self):
        selected = self._selected()
        if not selected:
            return
        kind, path = selected
        if kind == "folder":
            self._navigate(path)
        elif self._on_open(path) is not False:
            self.destroy()

    def _double_click(self, _event=None):
        self._open()

    def _back(self):
        if self._history:
            destination = self._history.pop()
            self._navigate(destination, remember=False)

    def _up(self):
        if self._folder.parent != self._folder:
            self._navigate(self._folder.parent)

    def _workspace_songs(self):
        if self._workspace:
            self._navigate(self._workspace / SONGS_DIRECTORY)

    def _set_sort(self, value):
        self.sort_by.set(value)
