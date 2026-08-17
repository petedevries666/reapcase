"""Timeline geometry helpers shared by the Tk view."""

LANE_HEIGHT = 72
HEADER_WIDTH = 140


def x_for_position(position, ppqn: int, beats_per_bar: int, pixels_per_beat: float) -> float:
    beats = (position.bar - 1) * beats_per_bar + position.beat - 1 + (position.tick - 1) / ppqn
    return HEADER_WIDTH + beats * pixels_per_beat

