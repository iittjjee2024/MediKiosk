"""Deterministic timeline construction.

Dates arrive with varying precision: a printed collection date is exact,
"last Diwali" is not. Every entry therefore carries a precision marker and the
engine orders using it, so the physician can see that an event is
approximately dated instead of being shown a false exact date.

Undated documents are placed in a clearly labelled undated group. They are
never silently assigned the upload date, because that would fabricate
chronology -- the one thing a clinical timeline must not do.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..models import ClinicalFact

PRECISION_RANK = {"day": 0, "month": 1, "year": 2, "inferred": 3, "undated": 4}

TIMELINE_CATEGORIES = {"diagnosis", "medication", "lab_value", "procedure",
                       "symptom"}


@dataclass(frozen=True)
class TimelineEntry:
    fact_id: str
    event_date: date | None
    date_precision: str
    category: str
    label: str
    source_type: str
    is_abnormal: bool
    is_conflicting: bool

    @property
    def is_undated(self) -> bool:
        return self.event_date is None


def _label_for(fact: ClinicalFact) -> str:
    base = fact.label or fact.clinical_concept or fact.category
    value = fact.value_normalized or fact.value_raw
    if value and value != base:
        unit = f" {fact.unit}" if fact.unit else ""
        return f"{base}: {value}{unit}"
    return str(base)


def build(facts: list[ClinicalFact]) -> tuple[list[TimelineEntry],
                                              list[TimelineEntry]]:
    """Return (dated entries newest-first, undated entries).

    Sorting is total: date, then precision, then category, then fact id. The
    final tiebreaker guarantees the same input always yields the same order,
    so the timeline is reproducible rather than dependent on row order.
    """
    entries = [
        TimelineEntry(
            fact_id=f.id,
            event_date=f.effective_date,
            date_precision=(f.date_precision or "day") if f.effective_date
            else "undated",
            category=f.category,
            label=_label_for(f),
            source_type=f.source_type,
            is_abnormal=(f.value_normalized or "").lower() == "abnormal",
            is_conflicting=bool(f.is_conflicting),
        )
        for f in facts
        if f.category in TIMELINE_CATEGORIES
    ]

    dated = sorted(
        (e for e in entries if not e.is_undated),
        key=lambda e: (e.event_date, PRECISION_RANK.get(e.date_precision, 9),
                       e.category, e.fact_id),
        reverse=True,
    )
    undated = sorted(
        (e for e in entries if e.is_undated),
        key=lambda e: (e.category, e.label, e.fact_id),
    )
    return dated, undated


def describe(entry: TimelineEntry) -> str:
    """Human-readable date that never overstates precision."""
    if entry.event_date is None:
        return "date not established"
    iso = entry.event_date.isoformat()
    match entry.date_precision:
        case "day":
            return iso
        case "month":
            return iso[:7] + " (month only)"
        case "year":
            return iso[:4] + " (year only)"
        case "inferred":
            return f"{iso} (approximate)"
    return iso
