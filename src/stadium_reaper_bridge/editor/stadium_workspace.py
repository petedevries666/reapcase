"""Transactional Stadium workspace import and manifest management."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .stadium_archive import ArchiveInspection, inspect_archive, safe_extract

MANIFEST_NAME = ".reapcase-workspace.json"
SONGS_DIRECTORY = Path("showcase/songs/workspace")


@dataclass(frozen=True)
class WorkspaceSong:
    """The small amount of Song data needed by workspace navigation."""

    path: Path
    title: str

    @property
    def label(self) -> str:
        return "%s   %s" % (self.path.stem, self.title)


def _natural_filename_key(path: Path):
    """Put numeric Stadium ids in numeric order, with a stable general fallback."""
    stem = path.stem
    return (0, int(stem), stem.casefold()) if stem.isdigit() else (1, stem.casefold(), stem)


def discover_workspace_songs(workspace: Path) -> tuple[WorkspaceSong, ...]:
    """Read only Song headers in a validated imported workspace.

    Broken JSON and unrelated JSON documents are deliberately ignored so one
    bad file cannot make the navigation menu unusable.
    """
    workspace = Path(workspace)
    load_manifest(workspace)
    result = []
    for path in sorted((workspace / SONGS_DIRECTORY).glob("*.json"),
                       key=_natural_filename_key):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if (not isinstance(document, dict) or
                not isinstance(document.get("name"), str) or
                not document["name"].strip() or
                not isinstance(document.get("flags"), list)):
            continue
        result.append(WorkspaceSong(path.resolve(), document["name"].strip()))
    return tuple(result)


def backup_stem(path: Path) -> str:
    name = Path(path).name
    return name[:-7] if name.casefold().endswith(".tar.gz") else Path(name).stem


def unique_workspace(parent: Path, archive: Path) -> Path:
    base = Path(parent) / (backup_stem(archive) + "_wip")
    candidate, number = base, 2
    while candidate.exists():
        candidate = base.with_name(base.name + "_%d" % number)
        number += 1
    return candidate


def load_manifest(workspace: Path) -> Dict[str, Any]:
    path = Path(workspace) / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("not a valid imported Stadium workspace: %s" % exc)
    if not isinstance(data, dict) or data.get("workspace_type") != "stadium_backup":
        raise ValueError("not an imported Stadium workspace")
    return data


def update_manifest(workspace: Path, **changes: Any) -> Dict[str, Any]:
    """Atomically update known values while retaining future/unknown keys."""
    workspace = Path(workspace)
    data = load_manifest(workspace)
    data.update(changes)
    path = workspace / MANIFEST_NAME
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))
    return data


def resolve_manifest_path(workspace: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else (Path(workspace) / path).resolve()


def import_backup(archive: Path, parent: Path, *, destination: Optional[Path] = None) -> Path:
    """Copy the immutable reference and atomically publish a safely extracted WIP."""
    archive, parent = Path(archive).resolve(), Path(parent).resolve()
    inspect_archive(archive)
    parent.mkdir(parents=True, exist_ok=True)
    final = Path(destination) if destination else unique_workspace(parent, archive)
    if final.exists():
        raise FileExistsError("workspace already exists: %s" % final)
    local_archive = parent / archive.name
    if local_archive.exists() and local_archive.resolve() != archive:
        raise FileExistsError("preserved reference already exists: %s" % local_archive)
    temp_root = Path(tempfile.mkdtemp(prefix=".%s-import-" % final.name, dir=str(parent)))
    temp_workspace = temp_root / final.name
    copied_temp = temp_root / archive.name
    try:
        if local_archive.resolve() != archive:
            shutil.copy2(str(archive), str(copied_temp))
            reference = copied_temp
        else:
            reference = archive
        safe_extract(reference, temp_workspace)
        manifest = {
            "version": 1, "workspace_type": "stadium_backup",
            "source_backup": "../" + archive.name,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "last_built_package": None, "last_implanted_package": None,
        }
        (temp_workspace / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if copied_temp.exists():
            os.replace(str(copied_temp), str(local_archive))
        os.replace(str(temp_workspace), str(final))
        return final
    finally:
        shutil.rmtree(str(temp_root), ignore_errors=True)


def inspect_import(archive: Path) -> ArchiveInspection:
    return inspect_archive(archive)
