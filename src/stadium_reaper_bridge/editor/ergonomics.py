"""Pure quality-of-life policies shared by the desktop editor.

The geometry and viewport functions deliberately avoid Tk so their behavior is
stable and testable on headless build machines.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil


def centered_position(parent: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int]:
    """Center a child of *size* in parent ``(x, y, width, height)``."""
    x, y, width, height = parent
    child_width, child_height = size
    return x + (width - child_width) // 2, y + (height - child_height) // 2


def clamp_dialog_position(position: tuple[int, int], size: tuple[int, int],
                          screen: tuple[int, int, int, int], visible: int = 48) -> tuple[int, int]:
    """Keep a useful title-bar-sized portion of a dialog on the current screen."""
    x, y = position
    width, height = size
    left, top, screen_width, screen_height = screen
    x = min(left + screen_width - visible, max(left - width + visible, x))
    y = min(top + screen_height - visible, max(top, y))
    return x, y


@dataclass
class DialogPositions:
    """Session-owned dialog-family position store (never Song metadata)."""

    positions: dict[str, tuple[int, int]]

    def position(self, family: str, parent, size, screen) -> tuple[int, int]:
        desired = self.positions.get(family, centered_position(parent, size))
        return clamp_dialog_position(desired, size, screen)

    def remember(self, family: str, position: tuple[int, int]) -> None:
        self.positions[family] = position


def follow_scroll(playhead_x: float, viewport_left: float, viewport_width: float,
                  *, playing: bool, suspended: bool = False,
                  trigger: float = .78, target: float = .30) -> float | None:
    """Return a new viewport left edge only when threshold following is needed."""
    if not playing or viewport_width <= 0:
        return None
    relative = (playhead_x - viewport_left) / viewport_width
    # A suspended follow still rescues a playhead that has actually left view.
    threshold = 1.0 if suspended else trigger
    if relative < threshold and relative >= 0:
        return None
    return max(0.0, playhead_x - viewport_width * target)


class BackupError(OSError):
    """Raised before writing when an overwrite backup cannot be secured."""


def backup_existing(paths: list[Path], *, now: datetime | None = None,
                    copy=shutil.copy2) -> tuple[Path, ...]:
    """Back up every existing file as one timestamp generation before overwrite."""
    existing = [Path(path) for path in paths if Path(path).exists()]
    if not existing:
        return ()
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    backup_dir = existing[0].parent / ".reapcase-backups"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        suffix = 0
        while True:
            token = stamp if suffix == 0 else f"{stamp}_{suffix:03d}"
            destinations = [backup_dir / f"{path.stem}_{token}{path.suffix}" for path in existing]
            if not any(path.exists() for path in destinations):
                break
            suffix += 1
        copied = []
        for source, destination in zip(existing, destinations):
            copy(source, destination)
            copied.append(destination)
        return tuple(copied)
    except OSError as exc:
        for destination in locals().get("copied", ()):
            try: destination.unlink()
            except OSError: pass
        raise BackupError("Backup could not be created. Original file was not overwritten.") from exc
