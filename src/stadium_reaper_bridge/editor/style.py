"""Central visual language for the Reapcase desktop editor.

All colours and image treatment used by :mod:`app` live here so the editor can
be rethemed without having to audit drawing code.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import struct
from tkinter import ttk
import tkinter as tk
import zlib


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

# Preserve the supplied texture's alpha curve while keeping its shading subtle.
LANE_GRADIENT_OPACITY = 0.60

# Backgrounds are deliberately much darker than events.  The paired highlight
# remains useful as a flat fallback and for controls; lane depth comes from the
# neutral, alpha-composited texture below.
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


def lane_gradient_asset_path() -> Path:
    """Resolve the checked-in lane texture independently of the process cwd."""
    path = Path(__file__).resolve().parents[3] / "assets" / "ui" / "verti_gradient.png"
    if not path.is_file():
        raise FileNotFoundError(f"lane gradient asset not found: {path}")
    return path


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
    return (left, above, upper_left)[distances.index(min(distances))]


@lru_cache(maxsize=1)
def _gradient_rows() -> tuple[tuple[int, int, int, int], ...]:
    """Decode the small RGBA PNG without adding a graphics dependency."""
    data = lane_gradient_asset_path().read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("lane gradient asset is not a PNG")
    offset, chunks, header = 8, [], None
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        offset += length + 12
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            chunks.append(payload)
        elif kind == b"IEND":
            break
    if header is None:
        raise ValueError("lane gradient PNG has no header")
    width, height, depth, color_type, compression, filtering, interlace = header
    if (depth, color_type, compression, filtering) != (8, 6, 0, 0) or interlace not in (0, 1):
        raise ValueError("lane gradient must be an 8-bit RGBA PNG")
    raw, cursor = zlib.decompress(b"".join(chunks)), 0
    canvas = [[None] * width for _ in range(height)]
    passes = ((0, 0, 1, 1),) if not interlace else (
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
    for x_start, y_start, x_step, y_step in passes:
        pass_width = max(0, (width - x_start + x_step - 1) // x_step)
        pass_height = max(0, (height - y_start + y_step - 1) // y_step)
        if not pass_width or not pass_height:
            continue
        stride, previous = pass_width * 4, bytearray(pass_width * 4)
        for pass_y in range(pass_height):
            filter_type, cursor = raw[cursor], cursor + 1
            encoded = raw[cursor:cursor + stride]
            cursor += stride
            decoded = bytearray(stride)
            for index, value in enumerate(encoded):
                left = decoded[index - 4] if index >= 4 else 0
                above = previous[index]
                upper_left = previous[index - 4] if index >= 4 else 0
                predictors = (0, left, above, (left + above) // 2,
                              _paeth(left, above, upper_left))
                if filter_type >= len(predictors):
                    raise ValueError("lane gradient uses an invalid PNG filter")
                decoded[index] = (value + predictors[filter_type]) & 0xff
            y = y_start + pass_y * y_step
            for pass_x in range(pass_width):
                start = pass_x * 4
                canvas[y][x_start + pass_x * x_step] = tuple(decoded[start:start + 4])
            previous = decoded
    # Average each source row so a future wider neutral texture remains valid.
    return tuple(tuple(sum(pixel[channel] for pixel in row) // width
                       for channel in range(4)) for row in canvas)


def composite_lane_rgb(base: str, height: int) -> tuple[tuple[int, int, int], ...]:
    """Alpha-composite the neutral texture over ``base`` at lane height."""
    if height < 1 or len(base) != 7 or not base.startswith("#"):
        raise ValueError("a positive height and #rrggbb base colour are required")
    base_rgb = tuple(int(base[index:index + 2], 16) for index in (1, 3, 5))
    source = _gradient_rows()
    result = []
    for y in range(height):
        sample = source[round(y * (len(source) - 1) / max(1, height - 1))]
        alpha = max(0, min(255, round(sample[3] * LANE_GRADIENT_OPACITY)))
        result.append(tuple((sample[channel] * alpha + base_rgb[channel] * (255 - alpha) + 127) // 255
                            for channel in range(3)))
    return tuple(result)


class LaneBackgroundCache:
    """Own cached, opaque Tk images produced from semantic lane colours."""

    def __init__(self, master, image_factory=tk.PhotoImage, max_entries: int = 32):
        self.master = master
        self.image_factory = image_factory
        self.max_entries = max_entries
        self._images: dict[tuple[str, int, int, float], object] = {}

    def image(self, base: str, width: int, height: int):
        key = (base.lower(), max(1, int(width)), max(1, int(height)),
               LANE_GRADIENT_OPACITY)
        image = self._images.get(key)
        if image is not None:
            return image
        rows = composite_lane_rgb(key[0], key[2])
        # PPM is supported by every Tk build and is already opaque after the
        # supplied PNG's alpha has been applied to the semantic base colour.
        pixels = b"".join(bytes(row) * key[1] for row in rows)
        ppm = f"P6\n{key[1]} {key[2]}\n255\n".encode("ascii") + pixels
        image = self.image_factory(master=self.master, data=ppm, format="PPM")
        if len(self._images) >= self.max_entries:
            self._images.pop(next(iter(self._images)))
        self._images[key] = image
        return image


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
