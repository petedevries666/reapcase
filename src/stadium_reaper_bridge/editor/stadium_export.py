"""Reviewable change planning and transactional Stadium package building."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tarfile
import time
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .stadium_archive import (ArchiveInspection, StadiumArchiveError, _safe_name,
                              inspect_archive, is_reapcase_only)
from .stadium_workspace import load_manifest, resolve_manifest_path, update_manifest

SONG_ROOT = Path("showcase/songs/workspace")
AUDIO_ROOT = Path("songs/workspace/Audio")
LOGGER = logging.getLogger(__name__)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str
    size: int


@dataclass(frozen=True)
class SongChange:
    path: str
    name: str
    status: str
    details: Tuple[str, ...]


@dataclass(frozen=True)
class BuildPlan:
    source: Path
    workspace: Path
    reference: Path
    songs: Tuple[SongChange, ...]
    audio: Tuple[FileChange, ...]
    peak_count: int
    excluded_count: int
    source_file_count: int
    source_members: Tuple[str, ...]

    @property
    def song_replacements(self) -> int:
        return sum(item.status == "CHANGED" for item in self.songs)


@dataclass(frozen=True)
class BuildProgress:
    """A throttled, display-independent snapshot of end-to-end build work."""
    phase: str
    processed_bytes: int
    total_bytes: int
    files_processed: int
    total_files: int
    current_file: str = ""

    @property
    def percent(self) -> int:
        if self.phase == "Complete":
            return 100
        return min(99, int(100 * self.processed_bytes / max(1, self.total_bytes)))


@dataclass
class _Perf:
    started: float
    files: int = 0
    bytes_read: int = 0
    bytes_written: int = 0
    audio_bytes: int = 0

    def log(self, stage: str) -> None:
        LOGGER.debug("PERF stadium stage=%s elapsed=%.3fs files=%d bytes_read=%d "
                     "bytes_written=%d audio_bytes=%d non_audio_bytes=%d",
                     stage, time.monotonic() - self.started, self.files, self.bytes_read,
                     self.bytes_written, self.audio_bytes,
                     max(0, self.bytes_read - self.audio_bytes))


def _json_from_archive(archive: Path) -> Dict[str, Dict[str, Any]]:
    import tarfile
    result = {}
    with tarfile.open(str(archive), "r:gz") as source:
        for member in source.getmembers():
            if member.isfile() and member.name.startswith(SONG_ROOT.as_posix() + "/") and member.name.endswith(".json"):
                result[member.name] = json.load(source.extractfile(member))
    return result


def _flag_parts(value: Any) -> Tuple[str, str]:
    text = str(value)
    position, _, payload = text.partition("|")
    return position, payload


def semantic_song_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Tuple[str, ...]:
    """Produce stable semantic summaries without depending on JSON key ordering."""
    details: List[str] = []
    old_flags = [_flag_parts(v) for v in before.get("flags", [])]
    new_flags = [_flag_parts(v) for v in after.get("flags", [])]
    old_by_payload = {payload: pos for pos, payload in old_flags}
    new_by_payload = {payload: pos for pos, payload in new_flags}
    for payload in sorted(old_by_payload.keys() & new_by_payload.keys()):
        if old_by_payload[payload] != new_by_payload[payload]:
            kind = payload.split(";", 1)[0] or "flag"
            details.append("~ %s moved %s → %s" % (kind, old_by_payload[payload], new_by_payload[payload]))
    added = [p for _, p in new_flags if p not in old_by_payload]
    removed = [p for _, p in old_flags if p not in new_by_payload]
    if added:
        details.append("+ %d flags added" % len(added))
    if removed:
        details.append("- %d flags removed" % len(removed))
    old_params, new_params = before.get("params"), after.get("params")
    valid_params = (str, dict, type(None))
    if not isinstance(old_params, valid_params) or not isinstance(new_params, valid_params):
        raise ValueError("Invalid Stadium Song params: expected a string, object, or null")
    if isinstance(old_params, dict) and isinstance(new_params, dict):
        for key in sorted(set(old_params) | set(new_params)):
            if old_params.get(key) != new_params.get(key):
                label = "tempo" if "tempo" in key.casefold() or "bpm" in key.casefold() else key
                details.append("~ %s: %s → %s" % (label, old_params.get(key), new_params.get(key)))
    elif old_params != new_params:
        # Stadium's native/older representation is an opaque semicolon-delimited
        # string.  Compare it without projecting mapping methods onto it.
        details.append("~ Song parameters changed")
    if before.get("tracks") != after.get("tracks"):
        details.append("~ audio references changed")
    known = {"flags", "params", "tracks"}
    if any(before.get(k) != after.get(k) for k in set(before) | set(after) if k not in known):
        details.append("~ Song metadata changed")
    return tuple(details or ("~ Song JSON changed",))


def _files(root: Path) -> Dict[str, Path]:
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}


def compare_file(local: Path, other: Optional[Path]) -> str:
    if other is None or not other.is_file():
        return "ADDED"
    left, right = local.stat(), other.stat()
    if left.st_size != right.st_size:
        return "CHANGED"
    # Equal size and mtime is a cheap identity hint; hash resolves all ambiguity.
    return "UNCHANGED" if sha256(local) == sha256(other) else "CHANGED"


def reference_package(workspace: Path, manifest: Dict[str, Any]) -> Path:
    implanted = resolve_manifest_path(workspace, manifest.get("last_implanted_package"))
    if implanted and implanted.is_file():
        try:
            inspect_archive(implanted, require_no_peaks=True, require_clean=True)
            return implanted
        except ValueError:
            pass
    source = resolve_manifest_path(workspace, manifest.get("source_backup"))
    if source is None:
        raise ValueError("workspace manifest has no source backup")
    inspect_archive(source)
    return source


def analyze_build(workspace: Path) -> BuildPlan:
    perf = _Perf(time.monotonic())
    workspace = Path(workspace).resolve()
    manifest = load_manifest(workspace)
    source = resolve_manifest_path(workspace, manifest.get("source_backup"))
    if source is None:
        raise ValueError("workspace manifest has no source backup")
    source_info = inspect_archive(source)
    reference = reference_package(workspace, manifest)
    baseline = _json_from_archive(reference)
    songs = []
    for relative, path in sorted(_files(workspace / SONG_ROOT).items()):
        if not relative.endswith(".json") or is_reapcase_only(relative):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid Stadium Song %s: expected a JSON object" % relative)
        key = (SONG_ROOT / relative).as_posix()
        old = baseline.get(key)
        songs.append(SongChange(relative, str(data.get("name") or Path(relative).stem),
                                "ADDED" if old is None else
                                ("UNCHANGED" if old == data else "CHANGED"),
                                () if old == data else semantic_song_diff(old or {}, data)))
    # Import provenance is a safe cheap identity check: extraction created the
    # workspace copy and recorded its size/mtime.  This avoids hashing multi-GB WAVs.
    cache = manifest.get("build_cache", {}).get("files", {})
    audio_items = []
    for name, path in sorted(_files(workspace / AUDIO_ROOT).items()):
        if is_reapcase_only(name):
            continue
        stat = path.stat(); key = (AUDIO_ROOT / name).as_posix(); old = cache.get(key)
        unchanged = bool(old and old.get("size") == stat.st_size and
                         old.get("mtime_ns") == stat.st_mtime_ns)
        audio_items.append(FileChange(name, "UNCHANGED" if unchanged else
                                      ("CHANGED" if key in source_info.members else "ADDED"),
                                      stat.st_size))
        perf.files += 1
    audio = tuple(audio_items)
    excluded = sum(is_reapcase_only(p.relative_to(workspace).as_posix())
                   for p in workspace.rglob("*") if p.is_file())
    perf.log("analyze")
    return BuildPlan(source, workspace, reference, tuple(songs), audio, source_info.peak_count,
                     excluded, sum(1 for n in source_info.members if "." in Path(n).name),
                     source_info.members)


def unique_package_path(parent: Path, now: Optional[datetime] = None) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.now()
    stem = moment.strftime("%Y-%m-%d_%H%M%S")
    candidate, suffix = parent / (stem + ".tar.gz"), 2
    while candidate.exists():
        candidate = parent / (stem + "_%d.tar.gz" % suffix)
        suffix += 1
    return candidate


def build_package(plan: BuildPlan, output: Optional[Path] = None, *, progress=None) -> Path:
    """Stream source/workspace files into a transactional, verified tar.gz.

    gzip streams are not random-access, so every output archive must be rewritten.
    Unchanged compressed ranges cannot safely be spliced with Python's gzip/tarfile.
    This path nevertheless eliminates the former full extraction, staging copies,
    and second reads of workspace audio.
    """
    output = Path(output) if output else unique_package_path(plan.workspace.parent)
    if output.exists():
        raise FileExistsError("package already exists: %s" % output)
    temporary_archive = output.with_name(".%s.tmp" % output.name)
    perf = _Perf(time.monotonic())
    local = {p.relative_to(plan.workspace).as_posix(): p
             for p in plan.workspace.rglob("*") if p.is_file() and
             not p.name.casefold().endswith(".peak") and
             not is_reapcase_only(p.relative_to(plan.workspace).as_posix())}
    source_members = set(plan.source_members)
    eligible_source = {n for n in source_members if not n.casefold().endswith(".peak") and
                       not is_reapcase_only(n)}
    names = eligible_source | set(local)
    total_bytes = sum(local[n].stat().st_size if n in local else 0 for n in names)
    last_report = [0.0]
    def report(phase, name=""):
        if progress and (phase in ("Verifying structure", "Finalizing", "Complete") or
                         time.monotonic() - last_report[0] >= .1):
            progress(BuildProgress(phase, perf.bytes_read, total_bytes, perf.files,
                                   len(names), name)); last_report[0] = time.monotonic()
    class CountingReader:
        def __init__(self, stream, name): self.stream, self.name = stream, name
        def read(self, size=-1):
            chunk = self.stream.read(size)
            perf.bytes_read += len(chunk); perf.bytes_written += len(chunk)
            if self.name.startswith(AUDIO_ROOT.as_posix() + "/"):
                perf.audio_bytes += len(chunk)
            report("Adding audio" if self.name.startswith(AUDIO_ROOT.as_posix())
                   else "Adding files", self.name)
            return chunk
    try:
        report("Preparing build plan")
        temporary_archive.unlink(missing_ok=True)
        count = 0
        with tarfile.open(str(plan.source), "r:gz") as source:
            members = source.getmembers()
            sizes = {m.name: m.size for m in members if m.isfile()}
            total_bytes += sum(sizes.get(n, 0) for n in names if n not in local)
            destination = tarfile.open(str(temporary_archive), "w:gz")
            try:
                emitted = set()
                for member in members:
                    name = _safe_name(member.name)
                    if name not in names or name in emitted:
                        continue
                    if name in local:
                        path = local[name]
                        info = destination.gettarinfo(str(path), arcname=name)
                        if info.isfile():
                            with path.open("rb") as stream:
                                destination.addfile(info, CountingReader(stream, name))
                        else: destination.addfile(info)
                    elif member.isdir():
                        destination.addfile(member)
                    else:
                        stream = source.extractfile(member)
                        if stream is None: raise StadiumArchiveError("could not read %s" % name)
                        with stream: destination.addfile(member, CountingReader(stream, name))
                    emitted.add(name); count += 1; perf.files += 1
                    report("Adding audio" if name.startswith(AUDIO_ROOT.as_posix()) else "Adding files", name)
                for name in sorted(set(local) - emitted):
                    info = destination.gettarinfo(str(local[name]), arcname=name)
                    if info.isfile():
                        with local[name].open("rb") as stream:
                            destination.addfile(info, CountingReader(stream, name))
                    else: destination.addfile(info)
                    count += 1; perf.files += 1
                    report("Adding audio" if name.startswith(AUDIO_ROOT.as_posix()) else "Adding files", name)
            finally:
                destination.close()
        perf.log("archive-write")
        report("Verifying structure")
        verified = inspect_archive(temporary_archive, require_no_peaks=True, require_clean=True)
        if len(verified.members) != count:
            raise ValueError("generated archive file count does not match build staging plan")
        perf.log("structural-verify")
        report("Finalizing")
        os.replace(str(temporary_archive), str(output))
        relative = os.path.relpath(str(output), str(plan.workspace)).replace(os.sep, "/")
        update_manifest(plan.workspace, last_built_package=relative)
        report("Complete")
        return output
    finally:
        temporary_archive.unlink(missing_ok=True)
