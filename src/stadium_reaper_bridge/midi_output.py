"""Persistent logical MIDI output routing for the future LIVE transport."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import importlib
from pathlib import Path
from typing import Any, Optional, Protocol, Union

from .editor.preferences import load_preferences, update_preferences


# Optional MIDI providers can fail while importing their native backend, not
# merely while importing mido itself.  Keep the expected provider failures at
# this boundary so they can never take down the editor.
MIDI_BACKEND_ERRORS = (ImportError, OSError, RuntimeError)
MIDI_MESSAGE_ERRORS = (TypeError, ValueError)


class MidiDestination(str, Enum):
    SECOND_HELIX = "second_helix"
    STADIUM = "stadium"
    ZYNTH_PLAYER = "zynth_player"
    LIGHT = "light"


DESTINATION_LABELS = {
    MidiDestination.SECOND_HELIX: "SECOND HELIX",
    MidiDestination.STADIUM: "STADIUM",
    MidiDestination.ZYNTH_PLAYER: "ZYNTH PLAYER",
    MidiDestination.LIGHT: "LIGHT",
}


@dataclass(frozen=True)
class MidiOutputRoute:
    port: Optional[str] = None
    channel: int = 1

    def __post_init__(self) -> None:
        if self.port is not None and (not isinstance(self.port, str) or not self.port):
            raise ValueError("MIDI output port must be non-empty text or None")
        if isinstance(self.channel, bool) or not isinstance(self.channel, int) or not 1 <= self.channel <= 16:
            raise ValueError("MIDI channel must be an integer between 1 and 16")

    def to_dict(self) -> dict[str, Any]:
        return {"port": self.port, "channel": self.channel}


def default_midi_outputs() -> dict[MidiDestination, MidiOutputRoute]:
    return {destination: MidiOutputRoute() for destination in MidiDestination}


def load_midi_outputs(path: Optional[Path] = None) -> dict[MidiDestination, MidiOutputRoute]:
    routes = default_midi_outputs()
    raw = load_preferences(path).get("midi_outputs", {})
    if not isinstance(raw, dict):
        return routes
    for destination in MidiDestination:
        item = raw.get(destination.value)
        if not isinstance(item, dict):
            continue
        try:
            routes[destination] = MidiOutputRoute(item.get("port"), item.get("channel", 1))
        except ValueError:
            pass
    return routes


def save_midi_outputs(routes: dict[MidiDestination, MidiOutputRoute],
                      path: Optional[Path] = None) -> bool:
    complete = {destination: routes.get(destination, MidiOutputRoute()).to_dict()
                for destination in MidiDestination}
    return update_preferences(
        lambda data: data.__setitem__("midi_outputs",
                                      {key.value: value for key, value in complete.items()}), path)


class MidiBackend(Protocol):
    def output_names(self) -> list[str]: ...
    def open_output(self, name: str) -> Any: ...
    def send(self, output: Any, message: Any, channel: int) -> None: ...
    def close_output(self, output: Any) -> None: ...


class UnavailableMidiBackend:
    """Safe backend used when no optional OS MIDI provider is installed."""

    def output_names(self) -> list[str]: return []
    def open_output(self, name: str) -> Any: raise OSError(f"MIDI output unavailable: {name}")
    def send(self, output: Any, message: Any, channel: int) -> None: return None
    def close_output(self, output: Any) -> None: return None


class MidoBackend:
    """Adapter which keeps mido and its zero-based channels at the boundary."""

    def __init__(self) -> None:
        self._mido = importlib.import_module("mido")

    def output_names(self) -> list[str]:
        try:
            return list(self._mido.get_output_names())
        except MIDI_BACKEND_ERRORS:
            return []

    def open_output(self, name: str) -> Any:
        return self._mido.open_output(name)

    def send(self, output: Any, message: Any, channel: int) -> None:
        if isinstance(message, dict):
            # The application's canonical CC representation uses ``cc`` while
            # mido calls the same MIDI data byte ``control``.  Keep that
            # provider-specific spelling at this backend boundary.
            fields = dict(message)
            if fields.get("type") == "control_change" and "cc" in fields:
                fields["control"] = fields.pop("cc")
            message = self._mido.Message(**fields)
        if hasattr(message, "copy"):
            message = message.copy(channel=channel - 1)
        output.send(message)

    def close_output(self, output: Any) -> None:
        output.close()


def system_midi_backend() -> MidiBackend:
    try:
        return MidoBackend()
    except MIDI_BACKEND_ERRORS:
        return UnavailableMidiBackend()


class MidiRouter:
    """Resolve logical lanes and safely own physical MIDI output resources."""

    def __init__(self, backend: Optional[MidiBackend] = None,
                 preferences_path: Optional[Path] = None):
        self.backend = backend or system_midi_backend()
        self.preferences_path = preferences_path
        self.routes = load_midi_outputs(preferences_path)
        self.available_ports: tuple[str, ...] = ()
        self._outputs: dict[str, Any] = {}
        self.refresh()

    @staticmethod
    def _destination(value: Union[MidiDestination, str]) -> MidiDestination:
        if isinstance(value, MidiDestination):
            return value
        try:
            return MidiDestination(value.lower())
        except ValueError:
            return MidiDestination[value.upper()]

    def resolve(self, destination: Union[MidiDestination, str]) -> Optional[MidiOutputRoute]:
        route = self.routes[self._destination(destination)]
        return route if route.port in self.available_ports else None

    def status(self, destination: Union[MidiDestination, str]) -> str:
        route = self.routes[self._destination(destination)]
        if route.port is None:
            return "Not configured"
        return "Connected" if route.port in self.available_ports else "Device unavailable"

    def configure(self, destination: Union[MidiDestination, str], port: Optional[str],
                  channel: int) -> bool:
        key = self._destination(destination)
        route = MidiOutputRoute(port, channel)
        old_port = self.routes[key].port
        self.routes[key] = route
        if old_port and old_port != port and not any(
                item.port == old_port for name, item in self.routes.items() if name != key):
            self._close_port(old_port)
        return save_midi_outputs(self.routes, self.preferences_path)

    def refresh(self) -> tuple[str, ...]:
        self.close()
        try:
            self.available_ports = tuple(dict.fromkeys(self.backend.output_names()))
        except MIDI_BACKEND_ERRORS:
            self.available_ports = ()
        return self.available_ports

    def send(self, destination: Union[MidiDestination, str], message: Any) -> bool:
        route = self.resolve(destination)
        if route is None:
            return False
        try:
            output = self._outputs.get(route.port)
            if output is None:
                output = self.backend.open_output(route.port)
                self._outputs[route.port] = output
            self.backend.send(output, message, route.channel)
            return True
        except MIDI_BACKEND_ERRORS + MIDI_MESSAGE_ERRORS:
            self._close_port(route.port)
            return False

    def _close_port(self, port: str) -> None:
        output = self._outputs.pop(port, None)
        if output is not None:
            try:
                self.backend.close_output(output)
            except MIDI_BACKEND_ERRORS:
                pass

    def close(self) -> None:
        for port in tuple(self._outputs):
            self._close_port(port)
