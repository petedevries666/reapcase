"""Timeline geometry helpers shared by the Tk view.

The functions in this module deliberately know nothing about Tk.  Keeping drag
and zoom arithmetic here makes interaction changes testable without a display.
"""

from __future__ import annotations

from dataclasses import dataclass

LANE_HEIGHT = 72
HEADER_WIDTH = 140
RULER_HEIGHT = 26
DEFAULT_PIXELS_PER_BEAT = 90.0
# CLOCKSICK reaches beat 993; a roughly one-pixel minimum allows its complete
# timeline (plus breathing room) to fit in the editor's standard 1180px window.
MIN_PIXELS_PER_BEAT = 1.0
MAX_PIXELS_PER_BEAT = 360.0
GRID_MAJOR_MIN_SPACING = 56.0
GRID_BEAT_MIN_SPACING = 28.0


@dataclass(frozen=True)
class TimelineGridDensity:
    """Visual grid resolution, independent of the editor's snap setting."""

    show_beats: bool
    bar_stride: int


def viewport_units(viewport_left: float, viewport_width: float, ppqn: int,
                   pixels_per_beat: float, song_end_units: int,
                   margin_pixels: float = 256.0) -> tuple[int, int]:
    """Return the bounded timeline interval worth materializing on a Canvas.

    The fixed header is included in canvas coordinates, so :func:`units_at_x`
    is deliberately used rather than duplicating that origin adjustment.
    ``margin_pixels`` gives scrolling a small ready-to-display buffer without
    turning off-screen Song detail into thousands of Tk objects.
    """
    left = units_at_x(max(HEADER_WIDTH, viewport_left - margin_pixels),
                      ppqn, pixels_per_beat)
    right = units_at_x(viewport_left + max(1.0, viewport_width) + margin_pixels,
                       ppqn, pixels_per_beat)
    return max(0, left), min(song_end_units, max(left, right))


def timeline_grid_density(pixels_per_beat: float, beats_per_bar: int,
                          minimum_spacing: float = GRID_MAJOR_MIN_SPACING) -> TimelineGridDensity:
    """Choose a musically aligned display bucket for the current screen scale.

    ``beats_per_bar`` should be the shortest signature visible in the viewport,
    so even the closest major lines retain approximately ``minimum_spacing``.
    """
    bar_pixels = pixels_per_beat * beats_per_bar
    stride = 16
    for candidate in (1, 2, 4, 8, 16):
        if bar_pixels * candidate >= minimum_spacing:
            stride = candidate
            break
    return TimelineGridDensity(pixels_per_beat >= GRID_BEAT_MIN_SPACING and stride == 1,
                               stride)


def is_major_display_bar(bar: int, density: TimelineGridDensity) -> bool:
    """Keep coarse lines anchored at measure 1 (1, 5, 9 ... for stride 4)."""
    return (bar - 1) % density.bar_stride == 0


def x_for_position(position, ppqn: int, beats_per_bar: int, pixels_per_beat: float) -> float:
    beats = (position.bar - 1) * beats_per_bar + position.beat - 1 + (position.tick - 1) / ppqn
    return HEADER_WIDTH + beats * pixels_per_beat


def timeline_x(units: int, ppqn: int, pixels_per_beat: float) -> float:
    """Map timeline units onto the one canonical editor origin."""
    return HEADER_WIDTH + units / ppqn * pixels_per_beat


def units_at_x(x: float, ppqn: int, pixels_per_beat: float) -> int:
    """Map a canvas coordinate back to non-negative timeline units."""
    return max(0, round((x - HEADER_WIDTH) / pixels_per_beat * ppqn))


def snapped_units_at_x(x: float, ppqn: int, pixels_per_beat: float, mode: str,
                       beats_per_bar: int, timing_map=None) -> int:
    """Convert a canvas coordinate to a grid-aligned timeline unit.

    This is the canonical pointer-to-timeline path used by creation.  Canvas
    scrolling is intentionally handled by the caller via ``canvasx``.
    """
    units = units_at_x(x, ppqn, pixels_per_beat)
    return snap_drag_delta(0, units, mode, ppqn, beats_per_bar, timing_map)


def track_header_rect(lane_top: float) -> tuple[float, float, float, float]:
    return (0.0, lane_top, float(HEADER_WIDTH), lane_top + LANE_HEIGHT)


def waveform_clip_rect(lane_top: float, end_x: float) -> tuple[float, float, float, float]:
    """Audio clips start at the origin; their header-side edge is exclusive."""
    return (float(HEADER_WIDTH), lane_top + 22, max(float(HEADER_WIDTH), end_x),
            lane_top + 62)


def drag_units(pixel_delta: float, pixels_per_beat: float, ppqn: int) -> int:
    """Convert a horizontal pointer displacement to the nearest whole tick."""
    return round(pixel_delta * ppqn / pixels_per_beat)


def snap_drag_delta(anchor_units: int, raw_delta: int, mode: str,
                    ppqn: int, beats_per_bar: int, timing_map=None) -> int:
    """Snap the destination of the earliest selected event and return a delta."""
    destination = max(0, anchor_units + raw_delta)
    if timing_map and mode == "1 bar":
        return timing_map.nearest_bar_units(destination) - anchor_units
    if timing_map and mode == "1 beat":
        return timing_map.nearest_beat_units(destination) - anchor_units
    grids = {"1 bar": ppqn * beats_per_bar, "1 beat": ppqn,
             "half beat": max(1, round(ppqn / 2)),
             "quarter beat": max(1, round(ppqn / 4)), "no snap": 1}
    if mode not in grids:
        raise ValueError(f"Unknown snap mode: {mode}")
    grid = grids[mode]
    return round((anchor_units + raw_delta) / grid) * grid - anchor_units


def clamp_zoom(value: float, minimum: float = MIN_PIXELS_PER_BEAT,
               maximum: float = MAX_PIXELS_PER_BEAT) -> float:
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class ZoomResult:
    pixels_per_beat: float
    scroll_x: float


def zoom_about_cursor(old_scale: float, requested_scale: float, scroll_x: float,
                      cursor_x: float, minimum: float = MIN_PIXELS_PER_BEAT,
                      maximum: float = MAX_PIXELS_PER_BEAT) -> ZoomResult:
    """Zoom while leaving the beat underneath a viewport cursor stationary."""
    new_scale = clamp_zoom(requested_scale, minimum, maximum)
    beat = (scroll_x + cursor_x - HEADER_WIDTH) / old_scale
    new_scroll = HEADER_WIDTH + beat * new_scale - cursor_x
    return ZoomResult(new_scale, max(0.0, new_scroll))


def fit_song_scale(song_end_units: int, ppqn: int, viewport_width: float,
                   minimum: float = MIN_PIXELS_PER_BEAT,
                   maximum: float = MAX_PIXELS_PER_BEAT) -> float:
    """Return a bounded scale that fits the song plus one beat of breathing room."""
    usable = max(1.0, viewport_width - HEADER_WIDTH - 16)
    beats = max(1.0, song_end_units / ppqn + 1.0)
    return clamp_zoom(usable / beats, minimum, maximum)


def fit_range_scale(start_units: int, end_units: int, ppqn: int,
                    viewport_width: float, *, single_window_beats: float = 8.0,
                    minimum: float = MIN_PIXELS_PER_BEAT,
                    maximum: float = MAX_PIXELS_PER_BEAT) -> float:
    """Fit a selected musical range, keeping point selections useful and finite."""
    span_beats = max(single_window_beats, abs(end_units - start_units) / ppqn)
    usable = max(1.0, viewport_width - HEADER_WIDTH - 32)
    return clamp_zoom(usable / (span_beats * 1.1), minimum, maximum)


def normalized_rectangle(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float, float, float]:
    """Return direction-independent left, top, right, bottom bounds."""
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def rectangles_intersect(first, second) -> bool:
    a = normalized_rectangle(*first)
    b = normalized_rectangle(*second)
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def marquee_candidates(marquee, event_bounds) -> set[int]:
    """Return event indices whose rendered blocks intersect a marquee."""
    return {index for index, bounds in event_bounds.items()
            if rectangles_intersect(marquee, bounds)}


def horizontal_wheel_units(direction: int, amount: int = 3) -> int:
    """Pure navigation policy: positive wheel direction scrolls left."""
    return -direction * amount
