from datetime import datetime, timezone
import json
import socket
from threading import Event, current_thread

import pytest

from stadium_reaper_bridge.editor.background_operations import BackgroundOperations
from stadium_reaper_bridge.stadium_network.discovery import coalesce, parse_mdns, parse_ssdp
from stadium_reaper_bridge.stadium_network.models import (
    Confidence, DiscoveredDevice, StadiumEndpoint,
)
from stadium_reaper_bridge.stadium_network.probe import SafeProbe, validate_address
from stadium_reaper_bridge.stadium_network.session import NetworkResearchSession


def test_device_model_and_duplicate_coalescing():
    first = DiscoveredDevice("192.168.1.4", services=[StadiumEndpoint("192.168.1.4", 80, "tcp", "_http._tcp")])
    second = DiscoveredDevice("192.168.1.4", hostname="unit.local", services=[StadiumEndpoint("192.168.1.4", 443, "tcp", "_https._tcp")])
    result = coalesce([first, second])
    assert len(result) == 1
    assert result[0].hostname == "unit.local"
    assert [item.port for item in result[0].services] == [80, 443]
    assert result[0].display_name == "Unidentified network service"


@pytest.mark.parametrize("value", ["192.168.1.42", "100.64.0.2", "stadium.local", "vpn-host"])
def test_manual_endpoint_validation(value):
    assert validate_address(value) == value


@pytest.mark.parametrize("value", ["", "http://host", "host/path", "bad host", "-bad.local"])
def test_manual_endpoint_rejects_urls_and_invalid_names(value):
    with pytest.raises(ValueError):
        validate_address(value)


def test_probe_timeout_and_unreachable_endpoint():
    endpoint = StadiumEndpoint("example.local", 1234, "tcp")
    resolver = lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 1234))]
    result = SafeProbe().probe("example.local", endpoint, resolver=resolver,
                               connector=lambda *args, **kwargs: (_ for _ in ()).throw(socket.timeout()))
    assert not result.reachable
    assert result.detail == "advertised endpoint timed out"


def test_unreachable_name_and_cancel_do_not_connect():
    def failed(*args, **kwargs):
        raise socket.gaierror()
    assert not SafeProbe().probe("missing.local", resolver=failed).reachable
    cancel = Event(); cancel.set()
    assert SafeProbe().probe("host.local", cancel=cancel).detail == "cancelled"


def test_manual_probe_resolves_but_does_not_guess_or_scan_ports():
    calls = []
    resolver = lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.2", 0))]
    result = SafeProbe().probe("100.64.0.2", resolver=resolver,
                               connector=lambda *args, **kwargs: calls.append(args))
    assert result.reachable and not calls
    assert "no advertised endpoint" in result.detail


def test_malformed_discovery_responses_are_rejected():
    with pytest.raises(ValueError):
        parse_mdns(b"short", "192.0.2.1")
    with pytest.raises(ValueError):
        parse_ssdp(b"garbage", "192.0.2.1")


def test_worker_isolation_from_caller_thread():
    worker = BackgroundOperations("network-test")
    caller = current_thread().ident
    assert worker.start("thread", lambda: current_thread().ident)
    result = None
    for _ in range(10000):
        result = worker.poll()
        if result:
            break
    worker.close()
    assert result and result.value != caller


def test_marker_timestamps_statuses_and_diagnostic_privacy(tmp_path):
    now = datetime(2026, 8, 28, 14, 32, 18, tzinfo=timezone.utc)
    session = NetworkResearchSession("1.2.3", clock=lambda: now)
    unrelated = DiscoveredDevice("192.168.1.99")
    selected = DiscoveredDevice("192.168.1.42", services=[
        StadiumEndpoint("192.168.1.42", 123, "tcp", evidence="advertisement",
                        confidence=Confidence.OBSERVED)])
    session.record_discovery(unrelated); session.record_discovery(selected)
    session.select_address(selected.address)
    marker = session.add_marker("Official app connected")
    assert marker.timestamp == now
    assert {item.value for item in Confidence} == {"OBSERVED", "INFERRED", "CONFIRMED"}
    path = tmp_path / "diagnostic.json"; session.export(path)
    data = json.loads(path.read_text())
    assert data["session_started"] == now.isoformat()
    assert data["markers"][0]["annotation"] == "Official app connected"
    assert [device["address"] for device in data["devices"]] == ["192.168.1.42"]
    assert "192.168.1.99" not in path.read_text()
