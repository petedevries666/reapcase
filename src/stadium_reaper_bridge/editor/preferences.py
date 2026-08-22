"""User-local editor preferences which are deliberately outside Song data."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Optional


def application_config_path() -> Path:
    """Return the platform-appropriate, user-local Reapcase preference file."""
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Reapcase" / "ui.json"
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "reapcase" / "ui.json"


@dataclass(frozen=True)
class RecentFile:
    path: str
    title: str

    @property
    def display(self) -> str:
        filename = Path(self.path).name
        return f"{self.title} — {filename}" if self.title else filename


class RecentFiles:
    """Small MRU store; paths stay absolute while menus expose only filenames."""

    def __init__(self, preference_path: Optional[Path] = None, limit: int = 10):
        self.preference_path = preference_path or application_config_path()
        self.limit = limit
        self.entries = self._load()

    def _document(self) -> dict:
        try:
            data = json.loads(self.preference_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _load(self) -> list[RecentFile]:
        result = []
        for item in self._document().get("recent_files", ()):
            if isinstance(item, dict) and item.get("path"):
                result.append(RecentFile(str(item["path"]), str(item.get("title", ""))))
        return result[:self.limit]

    def _save(self) -> None:
        data = self._document()
        data["recent_files"] = [entry.__dict__ for entry in self.entries]
        try:
            self.preference_path.parent.mkdir(parents=True, exist_ok=True)
            self.preference_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def add(self, path, title: str = "") -> None:
        absolute = str(Path(path).expanduser().resolve())
        key = os.path.normcase(absolute)
        self.entries = [entry for entry in self.entries
                        if os.path.normcase(entry.path) != key]
        self.entries.insert(0, RecentFile(absolute, title.strip()))
        self.entries = self.entries[:self.limit]
        self._save()

    def remove(self, path) -> None:
        key = os.path.normcase(str(Path(path).expanduser().resolve()))
        self.entries = [entry for entry in self.entries
                        if os.path.normcase(entry.path) != key]
        self._save()

    def clear(self) -> None:
        self.entries = []
        self._save()
