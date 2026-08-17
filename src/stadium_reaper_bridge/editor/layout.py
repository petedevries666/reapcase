"""Timeline geometry helpers shared by the Tk view.

The functions in this module deliberately know nothing about Tk.  Keeping drag
and zoom arithmetic here makes interaction changes testable without a display.
"""

from __future__ import annotations

from dataclasses import dataclass

LANE_HEIGHT = 72
HEADER_WIDTH = 140
DEFAULT_PIXELS_PER_BEAT = 90.0
# CLOCKSICK reaches beat 993; a roughly one-pixel minimum allows its complete
# timeline (plus breathing room) to fit in the editor's standard 1180px window.
MIN_PIXELS_PER_BEAT = 1.0
MAX_PIXELS_PER_BEAT = 360.0


def x_for_position(position, ppqn: int, beats_per_bar: int, pixels_per_beat: float) -> float:
    beats = (position.bar - 1) * beats_per_bar + position.beat - 1 + (position.tick - 1) / ppqn
    return HEADER_WIDTH + beats * pixels_per_beat


def drag_units(pixel_delta: float, pixels_per_beat: float, ppqn: int) -> int:
    """Convert a horizontal pointer displacement to the nearest whole tick."""
    return round(pixel_delta * ppqn / pixels_per_beat)


def snap_drag_delta(anchor_units: int, raw_delta: int, mode: str,
                    ppqn: int, beats_per_bar: int) -> int:
    """Snap the destination of the earliest selected event and return a delta."""
    grids = {
        "1 bar": ppqn * beats_per_bar,
        "1 beat": ppqn,
        "quarter beat": max(1, round(ppqn / 4)),
        "no snap": 1,
    }
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
