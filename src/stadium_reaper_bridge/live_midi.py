"""Audio-clock-driven LIVE MIDI scheduling for SECOND HELIX.

Native Stadium Showcase flags are internal Stadium commands.  They deliberately
do not enter this module; a future StadiumLiveProtocol/StadiumLiveTranslator
will define the separate, documented external protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
from typing import Callable, Iterable, Optional

from .timeline import TimelineEvent
from .editor.creation import SECOND_HELIX_LOOPER_ACTIONS

LOG = logging.getLogger(__name__)


class LiveEventClass(str, Enum):
    RECALLABLE_STATE = "recallable_state"
    ACTION = "action"


@dataclass(frozen=True)
class LiveMidiEvent:
    units: int
    source_order: int
    message: dict[str, int]
    event_class: LiveEventClass
    family: tuple[str, Optional[int]]


def second_helix_events(events: Iterable[TimelineEvent], decoder, units_for) -> tuple[LiveMidiEvent, ...]:
    """Translate established Second Helix messages using the rig decoder.

    The semantic decoder is the authority for channel, CC, value ranges, and
    configured actions.  In particular, do not trust the editor-only
    ``rig_alias`` projection here: it is reconstructed rather than serialized.
    """
    result = []
    for fallback_order, event in enumerate(events):
        data, source = event.data, event.source
        order = event.source_index if event.source_index is not None else fallback_order
        message = None
        classification = LiveEventClass.RECALLABLE_STATE
        family: tuple[str, Optional[int]] = ("", None)
        if source.type == "MIDI_BANK_PROGRAM":
            program = decoder.second_helix_program_change(data)
            if program is not None:
                message, family = {"type": "program_change", "program": program}, ("program", None)
        elif source.type == "MIDI_CC":
            alias = None
            reason = "not decoded as Second Helix"
            try:
                decoded = decoder.decode(data)
            except (TypeError, ValueError) as error:
                decoded = None
                reason = str(error)
            if decoded and decoded.get("system") == "second_helix":
                alias = decoded
                action = decoded.get("action")
                cc, value = data["cc"], data["value"]
                if action == "snapshot":
                    message, family = {"type": "control_change", "cc": cc, "value": value}, ("snapshot", None)
                elif action == "expression":
                    # Expression is continuous state even where the semantic
                    # decoder currently identifies its authorable endpoints.
                    message, family = {"type": "control_change", "cc": cc, "value": value}, ("cc", cc)
                elif action in SECOND_HELIX_LOOPER_ACTIONS:
                    message = {"type": "control_change", "cc": cc, "value": value}
                    classification, family = LiveEventClass.ACTION, ("action", cc)
                else:
                    reason = f"decoded action is not supported by LIVE: {action!r}"
            if message is None and _looks_like_second_helix(data, decoder):
                LOG.debug(
                    "LIVE MIDI SKIP SECOND HELIX: type=%s channel=%r cc=%r value=%r alias=%r reason=%s",
                    source.type, data.get("channel"), data.get("cc"), data.get("value"),
                    alias if alias is not None else data.get("rig_alias"), reason)
        if message is not None:
            result.append(LiveMidiEvent(units_for(event.position), order, message,
                                        classification, family))
    return tuple(sorted(result, key=lambda item: (item.units, item.source_order)))


def build_live_event_set(events: Iterable[TimelineEvent], decoder,
                         units_for) -> tuple[LiveMidiEvent, ...]:
    """Build and diagnose the event snapshot installed in the LIVE dispatcher."""
    source_events = tuple(events)
    midi_events = [event for event in source_events
                   if event.source.type.startswith("MIDI") or
                   {"channel", "cc", "value"} <= event.data.keys()]
    for event in midi_events:
        LOG.debug("LIVE SOURCE\nposition=%s\nsource.type=%s\nsource_index=%s\ndata=%r",
                  event.position.render(), event.source.type, event.source_index, event.data)
    translated = second_helix_events(source_events, decoder, units_for)
    LOG.debug("LIVE BUILD\nsource events: %d\nMIDI events: %d\n"
              "translated Second Helix events: %d",
              len(source_events), len(midi_events), len(translated))
    for event in translated:
        LOG.debug("LIVE EVENT\nposition=%s\nfamily=%s\nclass=%s\nmessage=%r",
                  event.units, event.family, event.event_class.value, event.message)
    return translated


def _looks_like_second_helix(data: dict, decoder) -> bool:
    alias = data.get("rig_alias")
    return (data.get("channel") == decoder.second_helix_channel or
            isinstance(alias, dict) and alias.get("system") == "second_helix")


class LiveMidiDispatcher:
    """Deterministic crossing/recall state machine; its clock is supplied by audio."""
    def __init__(self, send: Callable[[dict[str, int], bool, int], None]):
        self._send = send
        self.events: tuple[LiveMidiEvent, ...] = ()
        self.generation = 0
        self.enabled = False
        self.playing = False
        self._cursor = 0
        self._last_units = 0
        self._resume_units: Optional[int] = None

    def load(self, events: Iterable[LiveMidiEvent]) -> int:
        self.generation += 1
        # The dispatcher owns ordering.  Do not rely on every future inventory
        # producer to preserve the translator's sort contract.
        self.events = tuple(sorted(events, key=lambda event: (
            event.units, event.source_order)))
        self.stop()
        self._last_units = 0
        return self.generation

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self.playing = False

    def start(self, units: int) -> None:
        generation = self.generation
        unchanged_resume = self._resume_units == units
        self._cursor = next((i for i, event in enumerate(self.events)
                             if event.units > units or (event.units == units and not unchanged_resume)),
                            len(self.events))
        if self.enabled and units > 0 and not unchanged_resume:
            latest: dict[tuple[str, Optional[int]], LiveMidiEvent] = {}
            for event in self.events:
                if event.units >= units:
                    break
                if event.event_class is LiveEventClass.RECALLABLE_STATE:
                    latest[event.family] = event
            ordered = sorted(latest.values(), key=lambda event: (
                {"program": 0, "snapshot": 1, "cc": 2}.get(event.family[0], 3),
                event.family[1] if event.family[1] is not None else -1))
            for event in ordered:
                self._send(event.message, True, generation)
        self._last_units, self._resume_units, self.playing = units, None, True

    def poll(self, units: int) -> None:
        if not self.playing:
            return
        next_units = (self.events[self._cursor].units
                      if self._cursor < len(self.events) else None)
        LOG.debug("LIVE ADVANCE\nprevious_units=%s\ncurrent_units=%s\n"
                  "next_index=%s\nnext_event_units=%s",
                  self._last_units, units, self._cursor, next_units)
        if units < self._last_units:  # seek while playing: skip the interval and recall state
            LOG.debug("LIVE SKIP interval=%s..%s reason=backward clock seek",
                      self._last_units, units)
            self.playing = False
            self.start(units)
            return
        while self._cursor < len(self.events) and self.events[self._cursor].units <= units:
            event_index = self._cursor
            event = self.events[event_index]
            # Claim this exact event before invoking arbitrary callback code.
            # Advancing by one event (rather than searching for the next
            # position) is what preserves every same-position sibling.
            self._cursor += 1
            if self.enabled:
                LOG.debug("LIVE DISPATCH index=%s units=%s source_order=%s message=%r",
                          event_index, event.units, event.source_order, event.message)
                self._send(event.message, False, self.generation)
            else:
                LOG.debug("LIVE SKIP index=%s units=%s source_order=%s message=%r "
                          "reason=LIVE disabled", event_index, event.units,
                          event.source_order, event.message)
        self._last_units = units

    def pause(self, units: int) -> None:
        self.playing = False
        self._resume_units = units

    def seek(self, units: int) -> None:
        was_playing = self.playing
        self.playing = False
        self._resume_units = None
        if was_playing:
            self.start(units)

    def stop(self) -> None:
        self.playing = False
        self._resume_units = None
