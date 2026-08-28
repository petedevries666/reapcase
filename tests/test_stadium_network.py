from datetime import datetime, timezone
import json
import socket
from threading import Event, current_thread
import wave

import pytest

from stadium_reaper_bridge.editor.background_operations import BackgroundOperations
from stadium_reaper_bridge.editor.stadium_capture_wizard import start_research_wav_inspection
from stadium_reaper_bridge.stadium_network.discovery import coalesce, parse_mdns, parse_ssdp
from stadium_reaper_bridge.stadium_network.models import (
    Confidence, DiscoveredDevice, StadiumEndpoint,
)
from stadium_reaper_bridge.stadium_network.probe import SafeProbe, validate_address
from stadium_reaper_bridge.stadium_network.session import NetworkResearchSession
from stadium_reaper_bridge.stadium_network.experiment import inspect_research_wav


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


def test_guided_experiment_uses_utc_and_monotonic_markers(tmp_path):
    now = datetime(2026, 8, 28, 14, 2, 10, 123000, tzinfo=timezone.utc)
    ticks = iter((100.0, 132.319, 197.896, 205.687))
    session = NetworkResearchSession("1.2.3", clock=lambda: now)
    experiment = session.start_official_create_song_experiment(monotonic=lambda: next(ticks))
    capture_started = experiment.mark("CAPTURE_STARTED")
    trigger = experiment.mark("CREATE_SONG_TRIGGER")
    complete = experiment.mark("TRANSFER_COMPLETE_CONFIRMED")
    experiment.user_observed_operation_duration = complete.elapsed_seconds - trigger.elapsed_seconds
    assert capture_started.timestamp == now
    assert capture_started.elapsed_seconds == pytest.approx(32.319)
    assert experiment.user_observed_operation_duration == pytest.approx(7.791)
    assert session.experiments == [experiment]


def test_research_wav_capture_recognition_and_session_export(tmp_path):
    wav_path = tmp_path / "test.wav"
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(2); output.setsampwidth(2); output.setframerate(48000)
        output.writeframes(b"\0" * 48000 * 2 * 2)
    audio = inspect_research_wav(wav_path)
    assert (audio.duration, audio.sample_rate, audio.channels) == (1.0, 48000, 2)
    assert len(audio.sha256) == 64

    capture = tmp_path / "capture.pcapng"
    capture.write_bytes(b"\x0a\x0d\x0d\x0a" + b"research")
    experiment = NetworkResearchSession("1.2.3").start_official_create_song_experiment()
    experiment.audio = [audio]; experiment.result = "SUCCESS"
    experiment.mark("CREATE_SONG_TRIGGER"); experiment.set_capture(capture); experiment.finish()
    folder = experiment.export_folder(tmp_path / "exports")
    diagnostic = json.loads((folder / "session.json").read_text())
    assert diagnostic["experiment"]["audio"][0]["filename"] == "test.wav"
    assert diagnostic["capture"]["type"] == "pcapng"
    assert diagnostic["capture"]["external_path"] == str(capture.resolve())
    assert not (folder / "capture.pcapng").exists()
    assert "Reapcase did not send commands to Stadium" in (folder / "README.txt").read_text()


def test_research_wav_inspection_is_queued_off_the_ui_thread(monkeypatch, tmp_path):
    queued = {}

    class Operations:
        def start(self, name, function):
            queued.update(name=name, function=function)
            return True

    caller = current_thread().ident
    cancel = Event()
    observed = {}

    def inspect(path, supplied_cancel):
        observed.update(path=path, cancel=supplied_cancel, thread=current_thread().ident)

    monkeypatch.setattr(
        "stadium_reaper_bridge.editor.stadium_capture_wizard.inspect_research_wav", inspect)
    assert start_research_wav_inspection(Operations(), tmp_path / "test.wav", cancel)
    assert queued["name"] == "research-wav"
    assert not observed  # The Tk-facing call only queued the expensive work.

    worker = BackgroundOperations("wav-regression")
    assert worker.start(queued["name"], queued["function"])
    for _ in range(10000):
        if worker.poll():
            break
    worker.close()
    assert observed == {"path": tmp_path / "test.wav", "cancel": cancel,
                        "thread": observed["thread"]}
    assert observed["thread"] != caller


def test_aborted_experiment_is_atomically_persisted_with_partial_state(tmp_path):
    now = datetime(2026, 8, 28, 15, 1, 2, 345000, tzinfo=timezone.utc)
    session = NetworkResearchSession("1.2.3", clock=lambda: now)
    experiment = session.start_official_create_song_experiment()
    experiment.song_name = "PARTIAL TEST"
    experiment.tempo = 97
    experiment.mark("CAPTURE_STARTED")
    capture = tmp_path / "partial.pcap"
    capture.write_bytes(b"\xd4\xc3\xb2\xa1" + b"partial")
    experiment.set_capture(capture)
    experiment.mark("SESSION_CANCELLED", abort_step="STEP 8 / 13 — PREPARE TEST SONG")
    experiment.abort("STEP 8 / 13 — PREPARE TEST SONG")

    folder = experiment.persist_partial(tmp_path / "research")
    data = json.loads((folder / "session.json").read_text())
    assert data["session"]["result"] == "ABORTED"
    assert data["session"]["abort_step"] == "STEP 8 / 13 — PREPARE TEST SONG"
    assert data["session"]["aborted_at"] == now.isoformat()
    assert data["experiment"]["song"] == {"name": "PARTIAL TEST", "tempo": 97}
    assert [marker["name"] for marker in data["markers"]] == [
        "CAPTURE_STARTED", "SESSION_CANCELLED"]
    assert data["capture"]["external_path"] == str(capture.resolve())
    assert not (folder / "session.json.tmp").exists()
