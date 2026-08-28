"""Replaceable protocol boundary; no speculative device commands are defined."""

from __future__ import annotations

from abc import ABC, abstractmethod
from threading import Event
from typing import Optional


class StadiumTransport(ABC):
    @abstractmethod
    def discover(self, timeout: float, cancel: Optional[Event] = None):
        """Return passively observed services."""

    @abstractmethod
    def probe(self, address: str, endpoint=None, timeout: float = 2.0,
              cancel: Optional[Event] = None):
        """Perform a bounded, non-destructive observation."""

    @abstractmethod
    def connect(self, address: str):
        """Connect only when a future, confirmed protocol adapter supplies semantics."""

    @abstractmethod
    def disconnect(self):
        """Close a future confirmed-protocol session."""


class ObservationOnlyTransport(StadiumTransport):
    def __init__(self, discovery, probe):
        self.discovery = discovery
        self.prober = probe

    def discover(self, timeout, cancel=None):
        return self.discovery.discover(timeout, cancel)

    def probe(self, address, endpoint=None, timeout=2.0, cancel=None):
        return self.prober.probe(address, endpoint, timeout, cancel)

    def connect(self, address):
        raise NotImplementedError("no confirmed Stadium connection protocol is known")

    def disconnect(self):
        return None
