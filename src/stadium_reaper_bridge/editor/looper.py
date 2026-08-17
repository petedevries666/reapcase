"""Pure, conservative derivation of sustained looper state regions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from ..timeline import TimelineEvent

SUSTAINED_STATES = frozenset({"RECORD", "PLAY", "OVERDUB"})


@dataclass(frozen=True)
class LooperRegion:
    system: str
    state: str
    start_units: int
    end_units: int
    source_event_indices: tuple[int, ...]
    open_ended: bool = False


def _looper_action(event: TimelineEvent, system: str) -> str | None:
    if system == "STADIUM":
        if event.source.type != "LOOPER":
            return None
        action = event.data.get("action")
    elif system == "SECOND HELIX":
        alias = event.data.get("rig_alias", {})
        if alias.get("system") != "second_helix":
            return None
        action = alias.get("action")
    else:
        raise ValueError(f"Unknown looper system: {system!r}")
    if not isinstance(action, str):
        return None
    return action.strip().replace("_", " ").upper()


def derive_looper_regions(events: Iterable[TimelineEvent], units_for: Callable,
                           system: str, song_end_units: int) -> tuple[LooperRegion, ...]:
    """Derive regions; only sustained actions and STOP affect the state machine."""
    relevant = []
    for index, event in enumerate(events):
        action = _looper_action(event, system)
        if action in SUSTAINED_STATES or action == "STOP":
            relevant.append((units_for(event.position), index, action))
    relevant.sort(key=lambda item: (item[0], item[1]))

    regions: list[LooperRegion] = []
    active: tuple[int, int, str] | None = None
    for units, index, action in relevant:
        if action == "STOP":
            if active is not None:
                start, source, state = active
                regions.append(LooperRegion(system, state, start, max(start, units), (source,)))
                active = None
            continue
        if active is not None:
            start, source, state = active
            regions.append(LooperRegion(system, state, start, max(start, units), (source,)))
        active = (units, index, action)
    if active is not None:
        start, source, state = active
        regions.append(LooperRegion(system, state, start, max(start, song_end_units),
                                    (source,), open_ended=True))
    return tuple(regions)
