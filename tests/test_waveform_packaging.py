"""Distribution contract for the native waveform extrema dependency."""
from pathlib import Path

from stadium_reaper_bridge.editor import waveform


def test_native_extrema_module_is_importable_at_packaged_startup():
    # Importing waveform is what the Windows entry point does at startup. The
    # backport intentionally exposes the same module name as CPython <= 3.12.
    assert waveform.audioop.__name__ == "audioop"
    assert waveform._sample_extrema(waveform.array("h", (-7, 2, 9))) == (-7, 9)


def test_python_313_distribution_installs_audioop_backport():
    project = (Path(__file__).parents[1] / "pyproject.toml").read_text("utf-8")
    assert 'audioop-lts>=0.2.1; python_version >= \'3.13\'' in project
