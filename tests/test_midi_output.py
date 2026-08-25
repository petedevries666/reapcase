from pathlib import Path
import json
import types

import pytest

from stadium_reaper_bridge.midi_output import (
    MidiDestination, MidiOutputRoute, MidiRouter, MidoBackend, load_midi_outputs,
    system_midi_backend,
)


class FakeBackend:
    def __init__(self, names=()):
        self.names = list(names)
        self.opened = []
        self.closed = []
        self.sent = []
        self.fail_enumeration = False

    def output_names(self):
        if self.fail_enumeration:
            raise OSError("backend unavailable")
        return list(self.names)

    def open_output(self, name):
        if name not in self.names:
            raise OSError("disconnected")
        output = {"name": name}
        self.opened.append(output)
        return output

    def send(self, output, message, channel):
        self.sent.append((output["name"], message, channel))

    def close_output(self, output):
        self.closed.append(output["name"])


def test_four_logical_destinations_exist():
    assert tuple(MidiDestination) == (
        MidiDestination.SECOND_HELIX, MidiDestination.STADIUM,
        MidiDestination.ZYNTH_PLAYER, MidiDestination.LIGHT,
    )


@pytest.mark.parametrize("channel", (1, 16))
def test_human_channels_accept_full_range(channel):
    assert MidiOutputRoute("Port", channel).channel == channel


@pytest.mark.parametrize("channel", (0, 17, True))
def test_invalid_channels_are_rejected(channel):
    with pytest.raises(ValueError):
        MidiOutputRoute("Port", channel)


def test_ports_enumerate_refresh_and_preserve_configuration(tmp_path):
    backend = FakeBackend(["A"])
    router = MidiRouter(backend, tmp_path / "ui.json")
    router.configure("SECOND_HELIX", "A", 3)
    backend.names.append("B")
    assert router.refresh() == ("A", "B")
    assert router.routes[MidiDestination.SECOND_HELIX] == MidiOutputRoute("A", 3)


def test_configuration_persists_and_preserves_other_preferences(tmp_path):
    path = tmp_path / "ui.json"
    path.write_text('{"lane_order": ["LIGHTS"]}', encoding="utf-8")
    first = MidiRouter(FakeBackend(["USB MIDI"]), path)
    assert first.configure("stadium", "USB MIDI", 12)
    second = MidiRouter(FakeBackend(["USB MIDI"]), path)
    assert second.routes[MidiDestination.STADIUM] == MidiOutputRoute("USB MIDI", 12)
    assert json.loads(path.read_text())["lane_order"] == ["LIGHTS"]


def test_missing_device_is_nonfatal_and_name_survives_reload(tmp_path):
    path = tmp_path / "ui.json"
    router = MidiRouter(FakeBackend(), path)
    router.configure("light", "Disconnected USB", 7)
    restarted = MidiRouter(FakeBackend(), path)
    assert restarted.status("light") == "Device unavailable"
    assert restarted.routes[MidiDestination.LIGHT].port == "Disconnected USB"
    assert restarted.send("light", {"type": "cc"}) is False


def test_no_configuration_or_backend_failure_does_not_block_startup(tmp_path):
    backend = FakeBackend()
    backend.fail_enumeration = True
    router = MidiRouter(backend, tmp_path / "ui.json")
    assert router.available_ports == ()
    assert all(router.status(destination) == "Not configured" for destination in MidiDestination)


@pytest.mark.parametrize("error", (ImportError("provider failed"),
                                   ModuleNotFoundError("No module named 'rtmidi'")))
def test_mido_enumeration_import_failure_does_not_block_router_startup(tmp_path, monkeypatch,
                                                                      error):
    mido = types.SimpleNamespace(get_output_names=lambda: (_ for _ in ()).throw(error))
    monkeypatch.setattr("stadium_reaper_bridge.midi_output.importlib.import_module",
                        lambda name: mido)

    router = MidiRouter(preferences_path=tmp_path / "ui.json")

    assert isinstance(router.backend, MidoBackend)
    assert router.available_ports == ()
    assert router.send("stadium", object()) is False


def test_missing_mido_and_rtmidi_uses_unavailable_backend(tmp_path, monkeypatch):
    def without_midi(name):
        raise ModuleNotFoundError(f"No module named '{name}'")

    monkeypatch.setattr("stadium_reaper_bridge.midi_output.importlib.import_module", without_midi)
    router = MidiRouter(system_midi_backend(), tmp_path / "ui.json")

    assert router.available_ports == ()
    assert router.send("light", object()) is False


@pytest.mark.parametrize("operation", ("open_output", "send"))
@pytest.mark.parametrize("error", (ImportError("optional provider missing"),
                                   ModuleNotFoundError("No module named 'rtmidi'"),
                                   OSError("native MIDI failure"), RuntimeError("backend failure")))
def test_optional_backend_failures_during_send_are_safe(tmp_path, operation, error):
    backend = FakeBackend(["Port"])
    router = MidiRouter(backend, tmp_path / "ui.json")
    router.configure("stadium", "Port", 1)

    def fail(*args, **kwargs):
        raise error

    setattr(backend, operation, fail)
    assert router.send("stadium", object()) is False


def test_destinations_may_share_port_and_route_independent_channels(tmp_path):
    backend = FakeBackend(["Shared"])
    router = MidiRouter(backend, tmp_path / "ui.json")
    router.configure("stadium", "Shared", 1)
    router.configure("zynth_player", "Shared", 9)
    assert router.resolve("stadium") == MidiOutputRoute("Shared", 1)
    assert router.resolve("ZYNTH_PLAYER") == MidiOutputRoute("Shared", 9)
    assert router.send("stadium", "one")
    assert router.send("zynth_player", "two")
    assert backend.sent == [("Shared", "one", 1), ("Shared", "two", 9)]
    assert len(backend.opened) == 1


def test_refresh_and_close_release_resources(tmp_path):
    backend = FakeBackend(["Port"])
    router = MidiRouter(backend, tmp_path / "ui.json")
    router.configure("stadium", "Port", 1)
    assert router.send("stadium", "message")
    router.refresh()
    assert backend.closed == ["Port"]
    assert router.send("stadium", "again")
    router.close()
    assert backend.closed == ["Port", "Port"]


def test_preferences_are_not_song_or_workspace_data(tmp_path):
    preference_path = tmp_path / "config" / "ui.json"
    song = tmp_path / "workspace" / "Song.json"
    song.parent.mkdir()
    song.write_text('{"song": "unchanged"}', encoding="utf-8")
    before = song.read_bytes()
    MidiRouter(FakeBackend(), preference_path).configure("second_helix", "Rig", 3)
    assert song.read_bytes() == before
    assert load_midi_outputs(preference_path)[MidiDestination.SECOND_HELIX].port == "Rig"
    assert not (song.parent / "ui.json").exists()
