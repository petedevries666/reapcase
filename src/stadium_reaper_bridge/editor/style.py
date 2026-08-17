"""Central visual language for the Reapcase desktop editor.

All colours used by :mod:`app` live here so the editor can be rethemed without
having to audit drawing code.  The timeline uses flat fills and, where useful,
a single highlight band rather than per-pixel gradients.
"""

from __future__ import annotations

from dataclasses import dataclass
from tkinter import ttk


@dataclass(frozen=True)
class ThemeColors:
    app: str = "#0d1520"
    chrome: str = "#101a27"
    surface: str = "#142131"
    surface_raised: str = "#192838"
    surface_hover: str = "#203448"
    border: str = "#2a3b4e"
    border_strong: str = "#3a5168"
    text: str = "#d9e3ec"
    text_muted: str = "#8498aa"
    text_dim: str = "#607589"
    tooltip: str = "#1b2a3a"
    playhead: str = "#ef665f"
    danger: str = "#b85758"
    warning: str = "#c6a24e"


@dataclass(frozen=True)
class TimelineStyle:
    base: str = "#0f1a26"
    ruler: str = "#162433"
    ruler_edge: str = "#33495d"
    grid_bar: str = "#526b82"
    grid_beat: str = "#2b4053"
    grid_subdivision: str = "#1d3041"
    ruler_bar: str = "#71889d"
    ruler_beat: str = "#496075"
    ruler_text: str = "#aebdca"
    separator: str = "#263b4d"
    sublane_separator: str = "#30475b"
    header_text: str = "#dbe8f2"
    sublane_text: str = "#71889d"
    muted_fill: str = "#303b45"
    muted_text: str = "#7c8994"
    control: str = "#253747"
    drag_fill: str = "#b8ccdc"
    drag_outline: str = "#78a7cd"
    marquee_fill: str = "#416784"
    marquee_outline: str = "#91bce0"
    invalid_fill: str = "#321d25"
    invalid_text: str = "#df7778"
    invalid_outline: str = "#e25d61"


@dataclass(frozen=True)
class AudioStyle:
    background: str = "#102333"
    background_highlight: str = "#132a3c"
    clip: str = "#294d69"
    clip_outline: str = "#6590b3"
    missing_clip: str = "#3a303d"
    missing_outline: str = "#a76e7c"
    waveform: str = "#5f809e"
    text: str = "#d6e2ec"
    grid_bar: str = "#a9c3d8"
    grid_beat: str = "#708ba5"
    grid_subdivision: str = "#405a70"
    track_drag: str = "#56a6de"
    ghost: str = "#34546d"
    ghost_outline: str = "#82b7dc"


@dataclass(frozen=True)
class LaneColors:
    background: str
    background_highlight: str
    normal: str
    selected: str
    outline: str
    text: str
    header: str


THEME = ThemeColors()
TIMELINE = TimelineStyle()
AUDIO = AudioStyle()

# Backgrounds are deliberately much darker than events.  The paired
# ``background_highlight`` is used for one broad top band: subtle depth with a
# fixed two-rectangle cost per lane.
LANE_PALETTE = {
    "STRUCTURE": LaneColors("#10273b", "#132e45", "#245b89", "#3477ad", "#5793c2", "#edf6fc", "#67b5ed"),
    "STADIUM": LaneColors("#302014", "#382619", "#9b4d1d", "#c96b2c", "#dc8a4f", "#fff2e6", "#ed8a43"),
    "SECOND HELIX": LaneColors("#291b38", "#312042", "#68428a", "#895caf", "#a77ac7", "#f6effb", "#bd86df"),
    "VIDEO": LaneColors("#182735", "#1d2e3e", "#465d70", "#60798d", "#7f96a9", "#eef3f6", "#80afd0"),
    "LIGHTS": LaneColors("#302b16", "#38321a", "#8b711e", "#ad902b", "#c8aa4c", "#fff7d7", "#d5b94e"),
    "MIDI / OTHER": LaneColors("#122d2c", "#153534", "#286a61", "#37897c", "#58a99c", "#e8faf6", "#57c2b2"),
    "SEQCLICK": LaneColors("#0e2d35", "#113740", "#176977", "#238d9d", "#3aabb8", "#e6fbfc", "#45c7d2"),
    "SEQ INSTRUCTIONS": LaneColors("#30291d", "#393123", "#80663a", "#a4864e", "#c2a66d", "#fff4dc", "#d9b76c"),
}

LOOPER_STATE_FILLS = {
    "STADIUM": {"RECORD": "#713316", "PLAY": "#95491c", "OVERDUB": "#b85e27"},
    "SECOND HELIX": {"RECORD": "#4c3065", "PLAY": "#664184", "OVERDUB": "#7d51a0"},
}


def lane_colors(lane: str) -> LaneColors:
    """Return the stable visual identity for a top-level or audio lane."""
    return LANE_PALETTE.get(lane, LANE_PALETTE["MIDI / OTHER"])


def apply_ttk_theme(root) -> None:
    """Apply the compact dark theme to native Tk/ttk controls."""
    root.configure(background=THEME.app)
    style = ttk.Style(root)
    # ``clam`` consistently honours colour options on Linux, macOS, and
    # Windows while retaining native Tk fonts and keyboard behaviour.
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure(".", background=THEME.chrome, foreground=THEME.text,
                    fieldbackground=THEME.surface, bordercolor=THEME.border,
                    lightcolor=THEME.border, darkcolor=THEME.border,
                    troughcolor=THEME.app, font=("TkDefaultFont", 9))
    style.configure("TFrame", background=THEME.chrome)
    style.configure("TLabel", background=THEME.chrome, foreground=THEME.text)
    style.configure("Muted.TLabel", foreground=THEME.text_muted)
    style.configure("TButton", background=THEME.surface, foreground=THEME.text,
                    borderwidth=1, padding=(7, 4), relief="flat")
    style.map("TButton", background=[("pressed", THEME.surface_hover),
                                     ("active", THEME.surface_raised)],
              bordercolor=[("focus", THEME.border_strong)])
    style.configure("TCheckbutton", background=THEME.chrome,
                    foreground=THEME.text_muted, padding=(3, 2))
    style.map("TCheckbutton", background=[("active", THEME.chrome)],
              foreground=[("active", THEME.text)])
    style.configure("TCombobox", fieldbackground=THEME.surface,
                    background=THEME.surface, foreground=THEME.text,
                    arrowcolor=THEME.text_muted, padding=3)
    style.map("TCombobox", fieldbackground=[("readonly", THEME.surface)],
              foreground=[("readonly", THEME.text)],
              selectbackground=[("readonly", THEME.surface)])
    style.configure("TLabelframe", background=THEME.chrome,
                    bordercolor=THEME.border, borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", background=THEME.chrome,
                    foreground=THEME.text_muted, font=("TkDefaultFont", 8, "bold"))
    style.configure("TScrollbar", background=THEME.surface,
                    troughcolor=THEME.app, bordercolor=THEME.app,
                    arrowcolor=THEME.text_muted)
    style.configure("Status.TLabel", background=THEME.app,
                    foreground=THEME.text_muted, borderwidth=0, relief="flat")
