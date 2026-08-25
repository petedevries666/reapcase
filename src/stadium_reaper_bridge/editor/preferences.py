"""User-local editor preferences which are deliberately outside Song data."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional


def application_config_path() -> Path:
    """Return the platform-appropriate, user-local Reapcase preference file."""
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Reapcase" / "ui.json"
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "reapcase" / "ui.json"


def load_preferences(path: Optional[Path] = None) -> dict[str, Any]:
    """Load the shared preference document, tolerating a missing/corrupt file."""
    target = path or application_config_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def update_preferences(update: Callable[[dict[str, Any]], None],
                       path: Optional[Path] = None) -> bool:
    """Merge a preference change without overwriting unrelated editor settings."""
    target = path or application_config_path()
    data = load_preferences(target)
    update(data)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False
