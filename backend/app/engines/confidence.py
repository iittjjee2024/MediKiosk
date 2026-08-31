"""Confidence gate -- the boundary between AI perception and the record.

Every AI output is a *candidate*. This gate decides whether a candidate is
admitted as a Clinical Fact, admitted but visibly marked unconfirmed, or
withheld entirely and routed to a human.

Guessing is worse than declaring uncertainty in a clinical record, so the low
band does not admit a fact at all: the raw text is preserved verbatim and the
item enters the verification queue. Documents are held to a higher bar than
speech because a misread drug name is more dangerous than a misheard symptom
the patient can immediately re-confirm.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ..models import (ADMIT_ACCEPTED, ADMIT_UNCONFIRMED, ADMIT_UNREADABLE,
                      ADMIT_WITHHELD)

BAND_HIGH = "high"
BAND_MEDIUM = "medium"
BAND_LOW = "low"
BAND_UNREADABLE = "unreadable"

SOURCE_ANSWER = "answer"
SOURCE_DOCUMENT = "document"


@dataclass(frozen=True)
class Verdict:
    band: str
    admit: bool
    disposition: str
    verification_status: str
    confirm_back_required: bool
    reason: str | None = None

    @property
    def needs_human(self) -> bool:
        return not self.admit


def _thresholds(source_type: str) -> tuple[float, float, float | None]:
    if source_type == SOURCE_DOCUMENT:
        return (settings.conf_high_document,
                settings.conf_medium_document,
                settings.conf_unreadable_document)
    return settings.conf_high, settings.conf_medium, None


def classify(confidence: float, source_type: str = SOURCE_ANSWER) -> Verdict:
    """Map a confidence score to an admission decision."""
    conf = 0.0 if confidence is None else float(confidence)
    high, medium, unreadable = _thresholds(source_type)

    if unreadable is not None and conf < unreadable:
        return Verdict(
            band=BAND_UNREADABLE,
            admit=False,
            disposition=ADMIT_UNREADABLE,
            verification_status="pending_human",
            confirm_back_required=False,
            reason="region_unreadable",
        )

    if conf >= high:
        return Verdict(BAND_HIGH, True, ADMIT_ACCEPTED, "auto", False)

    if conf >= medium:


        return Verdict(BAND_MEDIUM, True, ADMIT_UNCONFIRMED, "unconfirmed",
                       True, reason="medium_confidence")

    return Verdict(
        band=BAND_LOW,
        admit=False,
        disposition=ADMIT_WITHHELD,
        verification_status="pending_human",
        confirm_back_required=source_type == SOURCE_ANSWER,
        reason="low_confidence",
    )
