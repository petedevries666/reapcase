"""Conservative endpoint validation and reachability observations."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import logging
import re
import socket
from threading import Event
from typing import Callable, Optional, Sequence

from .models import StadiumEndpoint

LOG = logging.getLogger(__name__)
HOST = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.?$")


def validate_address(value: str) -> str:
    value = value.strip()
    if not value or "://" in value or "/" in value or any(char.isspace() for char in value):
        raise ValueError("enter an IP address or hostname, without a URL or path")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if not HOST.fullmatch(value):
            raise ValueError("invalid IP address or hostname")
    return value.rstrip(".")


@dataclass(frozen=True)
class ProbeResult:
    address: str
    reachable: bool
    resolved_addresses: Sequence[str]
    endpoint: Optional[StadiumEndpoint] = None
    detail: str = ""


class SafeProbe:
    """Resolve an address and optionally connect only to an advertised endpoint."""

    def probe(self, address: str, endpoint: Optional[StadiumEndpoint] = None,
              timeout: float = 2.0, cancel: Optional[Event] = None,
              resolver: Callable = socket.getaddrinfo,
              connector: Callable = socket.create_connection) -> ProbeResult:
        address = validate_address(address)
        cancel = cancel or Event()
        if cancel.is_set():
            return ProbeResult(address, False, (), endpoint, "cancelled")
        try:
            infos = resolver(address, endpoint.port if endpoint else None, type=socket.SOCK_STREAM)
            resolved = tuple(dict.fromkeys(item[4][0] for item in infos))
        except (socket.gaierror, TimeoutError, OSError) as exc:
            LOG.debug("STADIUM_NET probe resolution failed address=%s error=%s", address, exc)
            return ProbeResult(address, False, (), endpoint, "unreachable: name resolution failed")
        if cancel.is_set():
            return ProbeResult(address, False, resolved, endpoint, "cancelled")
        if endpoint is None:
            return ProbeResult(address, True, resolved, detail="address resolves; no advertised endpoint was contacted")
        try:
            connection = connector((address, endpoint.port), timeout=timeout)
            connection.close()
            return ProbeResult(address, True, resolved, endpoint, "advertised TCP endpoint accepted a connection")
        except (TimeoutError, socket.timeout):
            return ProbeResult(address, False, resolved, endpoint, "advertised endpoint timed out")
        except OSError as exc:
            LOG.debug("STADIUM_NET probe unreachable address=%s port=%s error=%s", address, endpoint.port, exc)
            return ProbeResult(address, False, resolved, endpoint, "advertised endpoint unreachable")
