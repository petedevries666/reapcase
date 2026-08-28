"""Neutral evidence models.  These types intentionally contain no vendor guesses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Confidence(str, Enum):
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    CONFIRMED = "CONFIRMED"


@dataclass(frozen=True)
class StadiumEndpoint:
    address: str
    port: int
    transport: str
    service_name: str = ""
    evidence: str = ""
    confidence: Confidence = Confidence.OBSERVED


@dataclass
class DiscoveredDevice:
    address: str
    hostname: str = ""
    display_name: str = "Unidentified network service"
    services: List[StadiumEndpoint] = field(default_factory=list)
    txt_metadata: Dict[str, str] = field(default_factory=dict)
    discovered_at: datetime = field(default_factory=utc_now)
    selected_for_research: bool = False

    def merge(self, other: "DiscoveredDevice") -> None:
        """Coalesce repeated advertisements without upgrading their confidence."""
        if self.address != other.address:
            raise ValueError("cannot merge discoveries at different addresses")
        if other.hostname:
            self.hostname = other.hostname
        known = set(self.services)
        self.services.extend(item for item in other.services if item not in known)
        self.txt_metadata.update(other.txt_metadata)
        self.discovered_at = min(self.discovered_at, other.discovered_at)
        self.selected_for_research |= other.selected_for_research


@dataclass(frozen=True)
class ProtocolObservation:
    timestamp: datetime
    direction: str
    endpoint: Optional[StadiumEndpoint]
    size: Optional[int]
    protocol_hint: str
    annotation: str
    confidence: Confidence = Confidence.OBSERVED


@dataclass(frozen=True)
class NetworkMarker:
    timestamp: datetime
    annotation: str


def json_value(value):
    """Return a JSON-compatible value while retaining explicit evidence status."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value
