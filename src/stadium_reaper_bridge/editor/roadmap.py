"""Printable, GUI-independent song roadmap projection.

The objects in this module deliberately contain musical/document units only.
Screen and future PDF renderers are consumers of the same ``RoadmapDocument``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..stadium import MusicalPosition
from .structure import MANAGED_MEASURE_SUFFIX, derive_structure_layout

ROADMAP_VERSION = 1
VALID_MEASURES_PER_ROW = (2, 4, 8)


@dataclass(frozen=True)
class RoadmapNote:
    text: str


@dataclass(frozen=True)
class RoadmapMeasure:
    number: int
    section: str
    position: MusicalPosition
    label: Optional[str] = None
    note: Optional[RoadmapNote] = None
    begins_region: bool = False
    ends_region: bool = False


@dataclass(frozen=True)
class RoadmapBlock:
    """One displayed cell; ``measure_count`` enables later xN shorthand."""

    start_measure: int
    measure_count: int
    display_mode: str
    measures: tuple[RoadmapMeasure, ...]

    @property
    def source_position(self) -> MusicalPosition:
        return self.measures[0].position


@dataclass(frozen=True)
class RoadmapRow:
    blocks: tuple[RoadmapBlock, ...]


@dataclass(frozen=True)
class RoadmapSection:
    name: str
    rows: tuple[RoadmapRow, ...]


@dataclass(frozen=True)
class RoadmapPageStyle:
    """Renderer-neutral print geometry, expressed in typographic points."""

    page_width: float = 595.0       # A4 portrait
    page_height: float = 842.0
    margin_top: float = 42.0
    margin_right: float = 42.0
    margin_bottom: float = 42.0
    margin_left: float = 42.0
    title_size: float = 16.0
    metadata_size: float = 9.0
    section_size: float = 11.0
    measure_size: float = 8.0
    border_width: float = 0.8


@dataclass(frozen=True)
class RoadmapDocument:
    title: str
    tempo: Optional[float]
    numerator: int
    denominator: int
    measures_per_row: int
    sections: tuple[RoadmapSection, ...]
    page_style: RoadmapPageStyle = RoadmapPageStyle()

    @property
    def blocks(self):
        return tuple(block for section in self.sections for row in section.rows
                     for block in row.blocks)


def default_roadmap_metadata() -> dict:
    return {"version": ROADMAP_VERSION, "measures_per_row": 4,
            "notes": {}, "blocks": []}


def normalize_roadmap_metadata(value) -> dict:
    """Validate optional sidecar data and preserve future keys."""
    if value is None:
        return default_roadmap_metadata()
    if not isinstance(value, dict):
        raise ValueError("Invalid Reapcase roadmap sidecar")
    result = dict(value)
    version = result.get("version", ROADMAP_VERSION)
    if not isinstance(version, int) or version < 1:
        raise ValueError("Invalid roadmap version")
    per_row = result.get("measures_per_row", 4)
    if per_row not in VALID_MEASURES_PER_ROW:
        raise ValueError("Roadmap measures_per_row must be 2, 4, or 8")
    notes = result.get("notes", {})
    if not isinstance(notes, dict) or not all(
            str(key).isdigit() and isinstance(text, str) for key, text in notes.items()):
        raise ValueError("Invalid roadmap measure notes")
    blocks = result.get("blocks", [])
    if not isinstance(blocks, list):
        raise ValueError("Invalid roadmap blocks")
    result.update(version=version, measures_per_row=per_row,
                  notes={str(key): text for key, text in notes.items()}, blocks=blocks)
    return result


def _clean_region_label(label: str) -> str:
    return MANAGED_MEASURE_SUFFIX.sub("", label).strip() or "SECTION"


def build_roadmap_document(model, metadata=None) -> RoadmapDocument:
    """Project canonical STRUCTURE regions into physical-measure blocks."""
    settings = normalize_roadmap_metadata(metadata)
    per_row, notes = settings["measures_per_row"], settings["notes"]
    layout = derive_structure_layout(model.timeline.events, model._units,
                                     model.song_end_units)
    sections = []
    for region in layout.regions:
        if region.kind != "marker" or region.end_units <= region.start_units:
            continue
        first = model._position(region.start_units).bar
        # A boundary may occur within a measure. Include each physical measure
        # touched by the canonical region, without inventing partial bars.
        last = model._position(max(region.start_units, region.end_units - 1)).bar
        name = _clean_region_label(region.label)
        measures = []
        for number in range(first, last + 1):
            note_text = notes.get(str(number), "").strip()
            measures.append(RoadmapMeasure(
                number, name, MusicalPosition(number, 1, 1),
                note=RoadmapNote(note_text) if note_text else None,
                begins_region=number == first, ends_region=number == last))
        blocks = tuple(RoadmapBlock(item.number, 1, "measure", (item,))
                       for item in measures)
        rows = tuple(RoadmapRow(blocks[offset:offset + per_row])
                     for offset in range(0, len(blocks), per_row))
        sections.append(RoadmapSection(name, rows))
    return RoadmapDocument(str(model.song.name), model.tempo, model.numerator,
                           model.denominator, per_row, tuple(sections))
