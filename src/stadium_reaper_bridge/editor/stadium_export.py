"""Reviewable change planning and transactional Stadium package building."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .stadium_archive import (ArchiveInspection, inspect_archive, is_reapcase_only,
                              safe_extract, write_archive)
from .stadium_workspace import load_manifest, resolve_manifest_path, update_manifest

SONG_ROOT = Path("showcase/songs/workspace")
AUDIO_ROOT = Path("songs/workspace/Audio")


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

    @property
    def song_replacements(self) -> int:
        return sum(item.status == "CHANGED" for item in self.songs)


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
    old_params, new_params = before.get("params", {}), after.get("params", {})
    for key in sorted(set(old_params) | set(new_params)):
        if old_params.get(key) != new_params.get(key):
            label = "tempo" if "tempo" in key.casefold() or "bpm" in key.casefold() else key
            details.append("~ %s: %s → %s" % (label, old_params.get(key), new_params.get(key)))
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
        key = (SONG_ROOT / relative).as_posix()
        old = baseline.get(key)
        songs.append(SongChange(relative, str(data.get("name") or Path(relative).stem),
                                "ADDED" if old is None else
                                ("UNCHANGED" if old == data else "CHANGED"),
                                () if old == data else semantic_song_diff(old or {}, data)))
    # Audio delta is against the canonical extracted source during build; absent WIP files remain preserved.
    temp = Path(tempfile.mkdtemp(prefix="reapcase-analysis-"))
    try:
        safe_extract(source, temp)
        audio = tuple(FileChange(name, compare_file(path, temp / AUDIO_ROOT / name), path.stat().st_size)
                      for name, path in sorted(_files(workspace / AUDIO_ROOT).items())
                      if not is_reapcase_only(name))
        excluded = sum(is_reapcase_only(p.relative_to(workspace).as_posix())
                       for p in workspace.rglob("*") if p.is_file())
    finally:
        shutil.rmtree(str(temp), ignore_errors=True)
    return BuildPlan(source, workspace, reference, tuple(songs), audio, source_info.peak_count,
                     excluded, sum(1 for n in source_info.members if "." in Path(n).name))


def unique_package_path(parent: Path, now: Optional[datetime] = None) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.now()
    stem = moment.strftime("%Y-%m-%d_%H%M%S")
    candidate, suffix = parent / (stem + ".tar.gz"), 2
    while candidate.exists():
        candidate = parent / (stem + "_%d.tar.gz" % suffix)
        suffix += 1
    return candidate


def build_package(plan: BuildPlan, output: Optional[Path] = None) -> Path:
    """Patch a fresh source extraction and publish only a reopened, verified archive."""
    output = Path(output) if output else unique_package_path(plan.workspace.parent)
    if output.exists():
        raise FileExistsError("package already exists: %s" % output)
    staging_root = Path(tempfile.mkdtemp(prefix=".reapcase-build-", dir=str(output.parent)))
    tree, temporary_archive = staging_root / "tree", staging_root / output.name
    try:
        safe_extract(plan.source, tree)
        for base in (SONG_ROOT, AUDIO_ROOT):
            for relative, local in _files(plan.workspace / base).items():
                if is_reapcase_only(relative):
                    continue
                target = tree / base / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(local), str(target))
        for item in list(tree.rglob("*")):
            relative = item.relative_to(tree).as_posix()
            if item.is_file() and (item.suffix.casefold() == ".peak" or is_reapcase_only(relative)):
                item.unlink()
        expected_count = write_archive(tree, temporary_archive)
        verified = inspect_archive(temporary_archive, require_no_peaks=True, require_clean=True)
        if len(verified.members) != expected_count:
            raise ValueError("generated archive file count does not match build staging plan")
        os.replace(str(temporary_archive), str(output))
        relative = os.path.relpath(str(output), str(plan.workspace)).replace(os.sep, "/")
        update_manifest(plan.workspace, last_built_package=relative)
        return output
    finally:
        shutil.rmtree(str(staging_root), ignore_errors=True)
