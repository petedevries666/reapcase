from pathlib import Path

from stadium_reaper_bridge.editor.style import (
    AUDIO,
    LANE_GRADIENT_OPACITY,
    LANE_PALETTE,
    LaneBackgroundCache,
    composite_lane_rgb,
    lane_colors,
    lane_gradient_asset_path,
)
from stadium_reaper_bridge.editor import style


def test_lane_gradient_asset_resolves_outside_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    path = lane_gradient_asset_path()

    assert path == Path(__file__).resolve().parents[1] / "assets/ui/verti_gradient.png"
    assert path.is_file()


def test_lane_background_cache_uses_color_size_key_and_preserves_alpha_composite():
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return object()

    cache = LaneBackgroundCache("tk-master", image_factory=factory)
    first = cache.image("#10273b", 5, 7)
    same = cache.image("#10273B", 5, 7)
    resized = cache.image("#10273b", 6, 7)

    assert first is same
    assert resized is not first
    assert len(calls) == 2
    assert calls[0]["master"] == "tk-master"
    assert calls[0]["format"] == "PPM"
    assert calls[0]["data"].startswith(b"P6\n5 7\n255\n")
    assert len(set(composite_lane_rgb("#10273b", 77))) > 1


def test_lane_gradient_scales_each_source_alpha_to_sixty_percent(monkeypatch):
    monkeypatch.setattr(style, "_gradient_rows", lambda: ((240, 120, 60, 101),))

    result = composite_lane_rgb("#102030", 1)[0]
    alpha = round(101 * 0.60)
    expected = tuple((source * alpha + base * (255 - alpha) + 127) // 255
                     for source, base in zip((240, 120, 60), (16, 32, 48)))

    assert LANE_GRADIENT_OPACITY == 0.60
    assert alpha == 61
    assert result == expected


def test_semantic_lane_color_associations_are_unchanged():
    expected = {
        "STRUCTURE": "#10273b",
        "STADIUM": "#302014",
        "SECOND HELIX": "#291b38",
        "VIDEO": "#182735",
        "LIGHTS": "#302b16",
        "MIDI / OTHER": "#122d2c",
        "SEQCLICK": "#0e2d35",
        "SEQ INSTRUCTIONS": "#30291d",
    }

    assert {lane: lane_colors(lane).background for lane in expected} == expected
    assert set(LANE_PALETTE) == set(expected)
    assert AUDIO.background == "#102333"
