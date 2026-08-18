"""Semantic, GUI-independent projections for Inspector and hover help."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class InspectorProjection:
    heading: str
    position: str = ""
    fields: tuple[tuple[str, str], ...] = ()
    count: int = 1


def inspector_projection(model, indices) -> Optional[InspectorProjection]:
    """Describe canonical selection without parsing display labels or payloads."""
    valid = sorted({i for i in indices if 0 <= i < len(model.timeline.events)},
                   key=lambda i: model._units(model.timeline.events[i].position))
    if not valid:
        return None
    events = [model.timeline.events[i] for i in valid]
    if len(events) > 1:
        lanes = sorted({model.lane(event) for event in events})
        kinds = sorted({event.source.type for event in events})
        return InspectorProjection(
            f"{len(events)} events selected",
            f"{events[0].position.render()} — {events[-1].position.render()}",
            (("Lanes", ", ".join(lanes)), ("Types", ", ".join(kinds))), len(events))
    event = events[0]
    capability = model.edit_capability(valid[0])
    fields = capability.values.items() if capability else event.data.items()
    useful = tuple((key.replace("_", " ").title(), str(value)) for key, value in fields
                   if value is not None and not isinstance(value, (dict, list, tuple)))
    semantic_kind = {"PRESETSNAP": "SNAPSHOT", "MIDI_BANK_PROGRAM": "PRESET",
                     "MIDI_CC": "COMMAND"}.get(event.source.type, event.source.type)
    heading = capability.title.removeprefix("EDIT ") if capability else (
        f"{model.lane(event)} {semantic_kind}")
    return InspectorProjection(heading, event.position.render(), useful)


def semantic_tooltip(model, index: int) -> str:
    projection = inspector_projection(model, (index,))
    if projection is None:
        return ""
    main = [f"{label}: {value}" for label, value in projection.fields[:2]]
    return "\n".join((projection.heading, *main, projection.position))
