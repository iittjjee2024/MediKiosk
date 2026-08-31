"""Summary assembly with sentence-level citations and grounding validation.

The physician has seconds. A list of two hundred structured fields is not
readable in seconds; a cited narrative is. So the summary is prose -- but prose
under a hard constraint.

Two invariants are enforced here:

1. Every published sentence carries at least one citation to a Clinical Fact
   that exists in this session. `validate_grounding` mechanically removes any
   sentence that cannot, which is what converts hallucination from an invisible
   risk into a detectable defect. An LLM pass is optional and additive; if it
   is unavailable or its output fails grounding wholesale, the deterministic
   template below is served instead. The physician always receives a complete,
   correct summary -- only fluency degrades.

2. A section with no facts says so explicitly. Silently omitting an empty
   section would let the physician read absence of data as absence of a
   finding, which is a clinically dangerous ambiguity.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from ..models import SECTION_LABELS, SECTION_ORDER, ClinicalFact
from .conflict import Conflict
from .timeline import TimelineEntry, describe

NOT_CAPTURED = "Not captured in this intake session."

SECTION_FOR_CATEGORY = {
    "symptom": "hpi",
    "diagnosis": "past_medical",
    "procedure": "past_medical",
    "medication": "drug_allergy",
    "allergy": "drug_allergy",
    "family_history": "family",
    "personal_history": "personal",
    "lab_value": "review_of_systems",
    "ayush_parameter": "ayush_dashavidha",
}


@dataclass
class DraftSentence:
    section: str
    text: str
    fact_ids: tuple[str, ...]
    grounded: bool = True


@dataclass
class GroundingReport:
    kept: list[DraftSentence] = dc_field(default_factory=list)
    dropped: list[DraftSentence] = dc_field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.kept) + len(self.dropped)

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 100.0
        return round(len(self.kept) / self.total * 100, 2)


def _fact_phrase(fact: ClinicalFact) -> str:
    base = fact.label or fact.clinical_concept or fact.category
    value = fact.value_normalized or fact.value_raw
    parts = [str(base).replace("_", " ")]
    if value and str(value) != str(base):
        unit = f" {fact.unit}" if fact.unit else ""
        parts.append(f"{value}{unit}".replace("_", " "))
    text = ": ".join(parts)
    if fact.verification_status == "unconfirmed":
        text += " (unconfirmed)"
    return text


def _section_of(fact: ClinicalFact) -> str:
    if fact.field_code and "." in fact.field_code:
        prefix = fact.field_code.split(".", 1)[0]
        if prefix in SECTION_ORDER:
            return prefix
    return SECTION_FOR_CATEGORY.get(fact.category, "hpi")


def build_template(facts: list[ClinicalFact],
                   *,
                   timeline: list[TimelineEntry] | None = None,
                   conflicts: list[Conflict] | None = None,
                   pending_documents: int = 0,
                   interaction_check_performed: bool = False,
                   ) -> list[DraftSentence]:
    """Deterministic sectioned summary. This artefact alone is usable."""
    admitted = [f for f in facts if f.physician_status != "rejected"]
    by_section: dict[str, list[ClinicalFact]] = {}
    for f in admitted:
        by_section.setdefault(_section_of(f), []).append(f)

    out: list[DraftSentence] = []

    for section in SECTION_ORDER:
        rows = sorted(by_section.get(section, []),
                      key=lambda f: (f.category, f.field_code or "",
                                     f.label or "", f.id))
        if not rows:
            out.append(DraftSentence(section, NOT_CAPTURED, ()))
            continue

        if section == "chief_complaint":
            for f in rows:
                out.append(DraftSentence(
                    section,
                    f"Presenting complaint: {_fact_phrase(f)}.",
                    (f.id,)))
            continue

        grouped: dict[str, list[ClinicalFact]] = {}
        for f in rows:
            grouped.setdefault(f.category, []).append(f)

        for category, items in sorted(grouped.items()):
            phrases = [_fact_phrase(f) for f in items]
            ids = tuple(f.id for f in items)
            noun = category.replace("_", " ")
            out.append(DraftSentence(
                section,
                f"{noun.capitalize()} - " + "; ".join(phrases) + ".",
                ids))


    if timeline:
        for entry in timeline[:12]:
            out.append(DraftSentence(
                "prior_investigations",
                f"{describe(entry)} - {entry.label}"
                + (" [outside reference range]" if entry.is_abnormal else "")
                + ".",
                (entry.fact_id,)))
    else:
        out.append(DraftSentence("prior_investigations", NOT_CAPTURED, ()))


    if conflicts:
        for c in conflicts:
            out.append(DraftSentence("conflicts", c.explanation, c.fact_ids))
    else:
        out.append(DraftSentence(
            "conflicts",
            "No contradictions detected between patient statements and "
            "scanned documents.", ()))

    if not interaction_check_performed:
        out.append(DraftSentence(
            "caveats",
            "Drug interaction checking was NOT performed for this session.",
            ()))
    if pending_documents:
        out.append(DraftSentence(
            "caveats",
            f"{pending_documents} uploaded document(s) are still being "
            f"processed; this summary may be regenerated.", ()))
    out.append(DraftSentence(
        "caveats",
        "Patient-reported history captured before consultation. Draft for "
        "physician review - not a diagnosis.", ()))

    return out


def validate_grounding(sentences: list[DraftSentence],
                       facts: list[ClinicalFact]) -> GroundingReport:
    """Drop any sentence whose citations do not resolve to real facts.

    Structural sentences -- section placeholders and caveats -- legitimately
    carry no citations because they assert nothing about the patient. Every
    other sentence must cite, and every cited id must exist in this session.
    """
    valid_ids = {f.id for f in facts if f.physician_status != "rejected"}
    structural_sections = {"caveats"}
    report = GroundingReport()

    for s in sentences:
        if not s.fact_ids:
            structural = (s.section in structural_sections
                          or s.text == NOT_CAPTURED
                          or s.text.startswith("No contradictions detected"))
            if structural:
                report.kept.append(s)
            else:
                s.grounded = False
                report.dropped.append(s)
            continue

        if all(fid in valid_ids for fid in s.fact_ids):
            report.kept.append(s)
        else:
            s.grounded = False
            report.dropped.append(s)

    return report


def to_structured(sentences: list[DraftSentence]) -> dict:
    """Section-keyed body for storage and client rendering."""
    body: dict[str, list[dict]] = {}
    for s in sentences:
        body.setdefault(s.section, []).append(
            {"text": s.text, "fact_ids": list(s.fact_ids)})
    return {
        "sections": body,
        "labels": {**SECTION_LABELS,
                   "prior_investigations": "Prior Investigations",
                   "conflicts": "Flagged Conflicts",
                   "caveats": "Caveats"},
        "order": SECTION_ORDER + ["prior_investigations", "conflicts",
                                  "caveats"],
    }


def render_text(sentences: list[DraftSentence]) -> str:
    """Plain-text summary, for print and for the FHIR Composition narrative."""
    labels = to_structured(sentences)["labels"]
    order = to_structured(sentences)["order"]
    grouped: dict[str, list[str]] = {}
    for s in sentences:
        grouped.setdefault(s.section, []).append(s.text)

    lines: list[str] = []
    for section in order:
        if section not in grouped:
            continue
        lines.append(labels.get(section, section).upper())
        lines.extend(f"  {t}" for t in grouped[section])
        lines.append("")
    return "\n".join(lines).rstrip()
