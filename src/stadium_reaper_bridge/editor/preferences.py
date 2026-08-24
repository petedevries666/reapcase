"""User-local editor preferences which are deliberately outside Song data."""

from __future__ import annotations

import json
import os
from pathlib import Path


def application_config_path() -> Path:
    """Return the platform-appropriate, user-local Reapcase preference file."""
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Reapcase" / "ui.json"
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "reapcase" / "ui.json"
