"""Security boundary for reading and writing Stadium backup archives."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import tarfile
from typing import Iterable, Tuple


REQUIRED_DIRECTORIES = (
    "db", "proxy", "showcase", "screenshots", "songs",
    "showcase/songs/workspace", "songs/workspace/Audio", "songs/workspace/peaks",
)


class StadiumArchiveError(ValueError):
    """An archive cannot safely be treated as a Stadium backup."""


@dataclass(frozen=True)
class ArchiveInspection:
    path: Path
    members: Tuple[str, ...]
    song_json: Tuple[str, ...]
    peak_count: int


def _safe_name(name: str) -> str:
    """Return a normalized member name, rejecting every extraction ambiguity."""
    if not name or "\\" in name:
        raise StadiumArchiveError("archive member has an empty or backslash path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise StadiumArchiveError("unsafe archive path: %s" % name)
    return path.as_posix().rstrip("/")


def inspect_archive(path: Path, *, require_no_peaks: bool = False,
                    require_clean: bool = False) -> ArchiveInspection:
    path = Path(path)
    try:
        with tarfile.open(str(path), "r:gz") as archive:
            names = []
            seen = set()
            for member in archive.getmembers():
                name = _safe_name(member.name)
                if name in seen:
                    raise StadiumArchiveError("duplicate archive member is ambiguous: %s" % name)
                seen.add(name)
                if member.issym() or member.islnk():
                    raise StadiumArchiveError("links are not permitted: %s" % name)
                if not (member.isfile() or member.isdir()):
                    raise StadiumArchiveError("special archive member is not permitted: %s" % name)
                names.append(name)
            available = set(names)
            missing = [item for item in REQUIRED_DIRECTORIES
                       if item not in available and not any(n.startswith(item + "/") for n in available)]
            if missing:
                raise StadiumArchiveError("Stadium structure missing: %s" % ", ".join(missing))
            songs = tuple(sorted(n for n in names
                                 if n.startswith("showcase/songs/workspace/") and n.endswith(".json")))
            for name in songs:
                member = archive.getmember(name)
                try:
                    json.load(archive.extractfile(member))
                except (ValueError, TypeError, OSError) as exc:
                    raise StadiumArchiveError("invalid Song JSON %s: %s" % (name, exc))
            peaks = sum(n.casefold().endswith(".peak") for n in names)
            if require_no_peaks and peaks:
                raise StadiumArchiveError("generated archive contains Stadium .peak caches")
            if require_clean:
                leaked = [n for n in names if is_reapcase_only(n)]
                if leaked:
                    raise StadiumArchiveError("generated archive contains Reapcase metadata: %s" % leaked[0])
            return ArchiveInspection(path, tuple(names), songs, peaks)
    except (tarfile.TarError, OSError) as exc:
        raise StadiumArchiveError("archive is not a readable tar.gz: %s" % exc)


def safe_extract(path: Path, destination: Path) -> ArchiveInspection:
    """Extract regular files/directories only, after validating the entire archive."""
    inspection = inspect_archive(path)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(path), "r:gz") as archive:
        for member in archive.getmembers():
            name = _safe_name(member.name)
            target = destination.joinpath(*PurePosixPath(name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise StadiumArchiveError("could not read archive member: %s" % name)
                with source, target.open("wb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
    return inspection


def is_reapcase_only(name: str) -> bool:
    """Central deny policy applied to both patched and inherited content."""
    path = PurePosixPath(name.replace("\\", "/"))
    lower_parts = tuple(part.casefold() for part in path.parts)
    basename = path.name.casefold()
    return (basename == ".reapcase-workspace.json" or
            basename.endswith(".json.reapcase.json") or
            basename.endswith(".reapwave") or
            ".reapcase-backups" in lower_parts or
            "__pycache__" in lower_parts or
            basename.endswith((".tmp", ".temp", ".debug", ".perf")) or
            basename in (".ds_store", "thumbs.db"))


def write_archive(tree: Path, output: Path) -> int:
    """Write a deterministic-path archive without following links."""
    count = 0
    with tarfile.open(str(output), "w:gz") as archive:
        for item in sorted(Path(tree).rglob("*")):
            relative = item.relative_to(tree).as_posix()
            if item.is_symlink() or is_reapcase_only(relative):
                continue
            archive.add(str(item), arcname=relative, recursive=False)
            count += 1
    return count
