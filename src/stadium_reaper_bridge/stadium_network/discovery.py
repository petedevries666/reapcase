"""Bounded passive listeners for standard service advertisements.

No query packets are emitted: the lab records unsolicited mDNS and SSDP traffic
already present on the LAN.  Unknown advertisements remain generically labelled.
"""

from __future__ import annotations

import logging
import select
import socket
import struct
import time
from threading import Event
from typing import Callable, Iterable, List, Optional

from .models import DiscoveredDevice, StadiumEndpoint

LOG = logging.getLogger(__name__)


def _dns_name(packet: bytes, offset: int, seen=None):
    seen = set() if seen is None else seen
    labels = []
    while True:
        if offset >= len(packet) or offset in seen:
            raise ValueError("malformed DNS name")
        seen.add(offset)
        length = packet[offset]
        if length == 0:
            return ".".join(labels), offset + 1
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("truncated DNS pointer")
            target = ((length & 0x3F) << 8) | packet[offset + 1]
            suffix, _ = _dns_name(packet, target, seen)
            labels.append(suffix)
            return ".".join(labels), offset + 2
        if length > 63 or offset + 1 + length > len(packet):
            raise ValueError("invalid DNS label")
        labels.append(packet[offset + 1:offset + 1 + length].decode("utf-8", "replace"))
        offset += length + 1


def parse_mdns(packet: bytes, sender: str) -> List[DiscoveredDevice]:
    """Parse advertised SRV/A records conservatively; malformed input is ignored."""
    if len(packet) < 12:
        raise ValueError("truncated DNS packet")
    qd, an, ns, ar = struct.unpack("!HHHH", packet[4:12])
    offset = 12
    for _ in range(qd):
        _, offset = _dns_name(packet, offset)
        offset += 4
        if offset > len(packet):
            raise ValueError("truncated DNS question")
    devices = []
    for _ in range(an + ns + ar):
        name, offset = _dns_name(packet, offset)
        if offset + 10 > len(packet):
            raise ValueError("truncated DNS record")
        kind, _cls, _ttl, length = struct.unpack("!HHIH", packet[offset:offset + 10])
        start = offset + 10
        end = start + length
        if end > len(packet):
            raise ValueError("truncated DNS data")
        if kind == 33 and length >= 6:  # SRV
            _priority, _weight, port = struct.unpack("!HHH", packet[start:start + 6])
            host, _ = _dns_name(packet, start + 6)
            endpoint = StadiumEndpoint(sender, port, "tcp", name, "unsolicited mDNS SRV advertisement")
            devices.append(DiscoveredDevice(sender, host, services=[endpoint]))
        offset = end
    return devices


def parse_ssdp(packet: bytes, sender: str) -> Optional[DiscoveredDevice]:
    text = packet.decode("iso-8859-1", "replace")
    first = text.splitlines()[0] if text.splitlines() else ""
    if not (first.startswith("NOTIFY ") or first.startswith("HTTP/1.1 200")):
        raise ValueError("not an SSDP advertisement")
    headers = {}
    for line in text.splitlines()[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    service = headers.get("nt") or headers.get("st", "SSDP")
    return DiscoveredDevice(sender, display_name="Unidentified SSDP service",
                            txt_metadata={"service": service})


def coalesce(devices: Iterable[DiscoveredDevice]) -> List[DiscoveredDevice]:
    result = {}
    for device in devices:
        key = device.address
        if key in result:
            result[key].merge(device)
        else:
            result[key] = device
    return list(result.values())


class PassiveDiscovery:
    """Listen for existing advertisements for a short, cancellable interval."""

    def discover(self, timeout: float = 4.0, cancel: Optional[Event] = None,
                 socket_factory: Callable = socket.socket) -> List[DiscoveredDevice]:
        cancel = cancel or Event()
        sockets = []
        specs = (("224.0.0.251", 5353, parse_mdns), ("239.255.255.250", 1900, parse_ssdp))
        parsers = {}
        try:
            for group, port, parser in specs:
                try:
                    sock = socket_factory(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(("", port))
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                    socket.inet_aton(group) + socket.inet_aton("0.0.0.0"))
                    sock.setblocking(False)
                    sockets.append(sock); parsers[sock] = parser
                except OSError as exc:
                    LOG.debug("STADIUM_NET discovery listener unavailable port=%s error=%s", port, exc)
            found = []
            deadline = time.monotonic() + max(0, timeout)
            while sockets and not cancel.is_set() and time.monotonic() < deadline:
                readable, _, _ = select.select(
                    sockets, [], [], max(0, min(.1, deadline - time.monotonic())))
                for sock in readable:
                    packet, peer = sock.recvfrom(65535)
                    try:
                        values = parsers[sock](packet, peer[0])
                        found.extend(values if isinstance(values, list) else ([values] if values else []))
                    except (ValueError, UnicodeError, struct.error) as exc:
                        LOG.debug("STADIUM_NET discovery malformed advertisement: %s", exc)
            LOG.debug("STADIUM_NET discovery complete devices=%d cancelled=%s", len(found), cancel.is_set())
            return coalesce(found)
        finally:
            for sock in sockets:
                sock.close()
