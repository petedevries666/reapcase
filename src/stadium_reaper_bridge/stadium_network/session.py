"""Research session state and privacy-scoped diagnostics."""

from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .models import (DiscoveredDevice, NetworkMarker, ProtocolObservation,
                     StadiumEndpoint, json_value, utc_now)
from .experiment import ResearchExperiment

LOG = logging.getLogger(__name__)


class NetworkResearchSession:
    def __init__(self, version: str, clock: Callable[[], datetime] = utc_now):
        self.version = version
        self._clock = clock
        self.started = clock()
        self.devices: Dict[str, DiscoveredDevice] = {}
        self.explicit_addresses = set()
        self.observations: List[ProtocolObservation] = []
        self.markers: List[NetworkMarker] = []
        self.experiments: List[ResearchExperiment] = []

    def start_official_create_song_experiment(self, monotonic=None) -> ResearchExperiment:
        kwargs = {"clock": self._clock}
        if monotonic is not None:
            kwargs["monotonic"] = monotonic
        experiment = ResearchExperiment(self.version, **kwargs)
        self.experiments.append(experiment)
        return experiment

    def record_discovery(self, device: DiscoveredDevice) -> None:
        if device.address in self.devices:
            self.devices[device.address].merge(device)
        else:
            self.devices[device.address] = device
        LOG.debug("STADIUM_NET session recorded discovery address=%s", device.address)

    def select_address(self, address: str) -> None:
        self.explicit_addresses.add(address)
        if address in self.devices:
            self.devices[address].selected_for_research = True

    def add_marker(self, annotation: str) -> NetworkMarker:
        annotation = annotation.strip()
        if not annotation:
            raise ValueError("marker text cannot be empty")
        marker = NetworkMarker(self._clock(), annotation)
        self.markers.append(marker)
        LOG.debug("STADIUM_NET session marker timestamp=%s", marker.timestamp.isoformat())
        return marker

    def diagnostic(self) -> dict:
        # Discovery can observe unrelated multicast traffic.  Export only entries
        # affirmatively selected/probed by the user, preventing passive LAN leaks.
        included = [device for device in self.devices.values()
                    if device.selected_for_research or device.address in self.explicit_addresses]
        endpoints = [endpoint for device in included for endpoint in device.services]
        return json_value({
            "reapcase_version": self.version,
            "session_started": self.started,
            "devices": included,
            "endpoints": endpoints,
            "observations": self.observations,
            "markers": self.markers,
        })

    def export(self, path: Path) -> None:
        path.write_text(json.dumps(self.diagnostic(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
