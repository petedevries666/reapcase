import io
import json
from pathlib import Path
import tarfile

import pytest

from stadium_reaper_bridge.editor.stadium_archive import StadiumArchiveError, inspect_archive
from stadium_reaper_bridge.editor.stadium_export import analyze_build, build_package, sha256
from stadium_reaper_bridge.editor.stadium_implant import (analyze_audio_update, apply_audio_update,
                                                           implant_package, validate_sd_root)
from stadium_reaper_bridge.editor.stadium_workspace import (import_backup, load_manifest,
                                                              unique_workspace, update_manifest)


DIRS = ("db", "proxy", "showcase", "screenshots", "songs", "showcase/songs/workspace",
        "songs/workspace/Audio", "songs/workspace/peaks")


def make_backup(path, extra=None):
    files = {
        "showcase/songs/workspace/1.json": json.dumps({"name": "Demo", "ppqn": 240,
                                                        "params": {"tempo": 120}, "flags": [],
                                                        "tracks": [{"filename": "Audio/shared/FULL.wav"}]}).encode(),
        "songs/workspace/Audio/shared/FULL.wav": b"old audio",
        "songs/workspace/peaks/old.peak": b"cache",
        "db/unknown.sqlite": b"preserve me",
    }
    files.update(extra or {})
    with tarfile.open(path, "w:gz") as tar:
        for directory in DIRS:
            info = tarfile.TarInfo(directory); info.type = tarfile.DIRTYPE; tar.addfile(info)
        for name, content in files.items():
            info = tarfile.TarInfo(name); info.size = len(content); tar.addfile(info, io.BytesIO(content))


def test_import_build_implant_audio_round_trip(tmp_path):
    source_dir = tmp_path / "external"; source_dir.mkdir()
    source = source_dir / "2026-08-18_180859.tar.gz"; make_backup(source)
    parent = tmp_path / "work"; workspace = import_backup(source, parent)
    assert workspace.name == "2026-08-18_180859_wip"
    assert (parent / source.name).read_bytes() == source.read_bytes()
    assert load_manifest(workspace)["source_backup"] == "../" + source.name
    update_manifest(workspace, future_key={"kept": True})
    assert load_manifest(workspace)["future_key"] == {"kept": True}
    song = workspace / "showcase/songs/workspace/1.json"
    data = json.loads(song.read_text()); data["params"]["tempo"] = 124
    song.write_text(json.dumps(data))
    audio = workspace / "songs/workspace/Audio/shared/FULL.wav"; audio.write_bytes(b"new audio")
    (workspace / ".reapcase-backups").mkdir(); (workspace / ".reapcase-backups/x").write_text("x")
    (workspace / "songs/workspace/Audio/bad.reapwave").write_text("x")
    plan = analyze_build(workspace)
    package = build_package(plan, parent / "2026-08-23_181442.tar.gz")
    verified = inspect_archive(package, require_no_peaks=True, require_clean=True)
    assert "db/unknown.sqlite" in verified.members
    assert not any("reap" in name for name in verified.members)
    sd = tmp_path / "sd"
    for directory in ("backups", "clips", "screenshots", "songs/workspace/Audio", "songs/workspace/peaks"):
        (sd / directory).mkdir(parents=True, exist_ok=True)
    destination = implant_package(package, sd, workspace)
    assert destination.parent == sd / "backups" and sha256(destination) == sha256(package)
    assert load_manifest(workspace)["last_implanted_package"]
    (sd / "songs/workspace/Audio/shared").mkdir()
    (sd / "songs/workspace/Audio/shared/FULL.wav").write_bytes(b"sd old")
    (sd / "songs/workspace/peaks/cache.peak").write_bytes(b"peak")
    (sd / "songs/unrelated.txt").write_text("keep")
    update = analyze_audio_update(workspace, sd)
    copied = apply_audio_update(update)
    assert len(copied) == 1 and copied[0].read_bytes() == b"new audio"
    assert not list((sd / "songs/workspace/peaks").glob("*.peak"))
    assert (sd / "songs/unrelated.txt").read_text() == "keep"


@pytest.mark.parametrize("name", ("../escape", "/absolute"))
def test_unsafe_archive_paths_rejected(tmp_path, name):
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(name); info.size = 1; tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(StadiumArchiveError): inspect_archive(path)


def test_symlink_rejected_and_existing_names_are_unique(tmp_path):
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("songs/link"); info.type = tarfile.SYMTYPE; info.linkname = "/tmp"
        tar.addfile(info)
    with pytest.raises(StadiumArchiveError): inspect_archive(path)
    archive = tmp_path / "x.tar.gz"; archive.touch()
    (tmp_path / "x_wip").mkdir()
    assert unique_workspace(tmp_path, archive).name == "x_wip_2"


def test_missing_wip_file_does_not_delete_source_and_existing_sd_copy_refused(tmp_path):
    source = tmp_path / "source.tar.gz"
    make_backup(source, {"songs/workspace/Audio/shared/OTHER.wav": b"preserved"})
    workspace = import_backup(source, tmp_path / "work")
    package = build_package(analyze_build(workspace), tmp_path / "built.tar.gz")
    with tarfile.open(package) as tar:
        assert tar.extractfile("songs/workspace/Audio/shared/OTHER.wav").read() == b"preserved"
    sd = tmp_path / "sd"
    for item in ("backups", "clips", "screenshots", "songs"):
        (sd / item).mkdir(parents=True)
    target = sd / "backups" / package.name; target.write_bytes(b"existing")
    with pytest.raises(FileExistsError): implant_package(package, sd, workspace)
    assert target.read_bytes() == b"existing"
    with pytest.raises(ValueError): validate_sd_root(tmp_path)
