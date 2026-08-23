"""Verified, non-overwriting Stadium SD deployment services."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile
from typing import Optional, Tuple

from .stadium_archive import inspect_archive, is_reapcase_only
from .stadium_export import AUDIO_ROOT, FileChange, compare_file, sha256
from .stadium_workspace import load_manifest, update_manifest

SD_DIRECTORIES = ("backups", "clips", "screenshots", "songs")


def validate_sd_root(root: Path) -> Path:
    root = Path(root).resolve()
    missing = [name for name in SD_DIRECTORIES if not (root / name).is_dir()]
    if missing:
        raise ValueError("not a recognized Stadium SD root; missing: %s" % ", ".join(missing))
    return root


def implant_package(package: Path, sd_root: Path, workspace: Optional[Path] = None) -> Path:
    """Copy one verified archive into backups; never extract or overwrite."""
    package, sd_root = Path(package).resolve(), validate_sd_root(sd_root)
    inspect_archive(package, require_no_peaks=True, require_clean=True)
    destination = sd_root / "backups" / package.name
    if destination.exists():
        raise FileExistsError("backup destination already exists: %s" % destination)
    temporary = destination.with_name(".%s.reapcase-copying" % destination.name)
    if temporary.exists():
        raise FileExistsError("an incomplete copy already exists: %s" % temporary)
    try:
        shutil.copy2(str(package), str(temporary))
        if temporary.stat().st_size != package.stat().st_size or sha256(temporary) != sha256(package):
            raise OSError("copied package failed size/SHA-256 verification")
        os.replace(str(temporary), str(destination))
        if workspace is not None:
            relative = os.path.relpath(str(package), str(workspace)).replace(os.sep, "/")
            update_manifest(workspace, last_implanted_package=relative)
        return destination
    finally:
        if temporary.exists():
            temporary.unlink()


def sd_audio_root(root: Path) -> Path:
    root = validate_sd_root(root)
    candidates = (root / AUDIO_ROOT, root / "songs" / "Audio")
    found = [path for path in candidates if path.is_dir()]
    if len(found) != 1:
        raise ValueError("Stadium audio workspace was not recognized unambiguously")
    return found[0]


def sd_peak_root(root: Path) -> Path:
    root = validate_sd_root(root)
    candidates = (root / "songs" / "workspace" / "peaks", root / "songs" / "peaks")
    found = [path for path in candidates if path.is_dir()]
    if len(found) != 1:
        raise ValueError("Stadium peak-cache directory was not recognized unambiguously")
    return found[0]


@dataclass(frozen=True)
class AudioUpdatePlan:
    workspace: Path
    sd_root: Path
    audio_root: Path
    peak_root: Path
    files: Tuple[FileChange, ...]
    peak_count: int

    @property
    def changed_count(self) -> int:
        return sum(item.status == "CHANGED" for item in self.files)

    @property
    def added_count(self) -> int:
        return sum(item.status == "ADDED" for item in self.files)


def analyze_audio_update(workspace: Path, root: Path) -> AudioUpdatePlan:
    workspace, root = Path(workspace).resolve(), validate_sd_root(root)
    load_manifest(workspace)
    audio, peaks = sd_audio_root(root), sd_peak_root(root)
    local_root = workspace / AUDIO_ROOT
    files = tuple(FileChange(path.relative_to(local_root).as_posix(),
                             compare_file(path, audio / path.relative_to(local_root)),
                             path.stat().st_size)
                  for path in sorted(local_root.rglob("*"))
                  if path.is_file() and not is_reapcase_only(path.relative_to(local_root).as_posix()))
    return AudioUpdatePlan(workspace, root, audio, peaks, files,
                           sum(p.is_file() and p.suffix.casefold() == ".peak"
                               for p in peaks.rglob("*")))


def apply_audio_update(plan: AudioUpdatePlan) -> Tuple[Path, ...]:
    """Atomically replace changed audio, verify it, then clear only scoped .peak files."""
    copied = []
    for change in plan.files:
        if change.status == "UNCHANGED":
            continue
        source, destination = plan.workspace / AUDIO_ROOT / change.path, plan.audio_root / change.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".%s." % destination.name,
                                         suffix=".reapcase-copying", dir=str(destination.parent))
        os.close(fd)
        temporary = Path(temp_name)
        try:
            shutil.copy2(str(source), str(temporary))
            if temporary.stat().st_size != source.stat().st_size or sha256(temporary) != sha256(source):
                raise OSError("audio copy verification failed: %s" % change.path)
            os.replace(str(temporary), str(destination))
            if destination.stat().st_size != source.stat().st_size or sha256(destination) != sha256(source):
                raise OSError("installed audio verification failed: %s" % change.path)
            copied.append(destination)
        finally:
            if temporary.exists():
                temporary.unlink()
    for peak in plan.peak_root.rglob("*"):
        if peak.is_file() and peak.suffix.casefold() == ".peak":
            peak.unlink()
    if any(p.is_file() and p.suffix.casefold() == ".peak" for p in plan.peak_root.rglob("*")):
        raise OSError("one or more Stadium peak caches could not be removed")
    return tuple(copied)
