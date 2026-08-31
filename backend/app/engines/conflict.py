"""Deterministic conflict detection.

Compares what the patient said against what their documents show. This is a
capability oral history taking structurally cannot have, because the two
sources are never represented in the same system at the same time.

The engine's contract is to *surface*, never to decide. Conflicts are never
auto-resolved: resolution requires clinical judgement, which is outside the
platform's declared scope. Both facts are retained, linked by a shared
conflict group, and shown to the physician with their dates and provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ..models import ClinicalFact

# Concepts where a patient denial is contradicted by documentary evidence.
# Keyed by the negation field, valued by the fact category that contradicts it.
DENIAL_FIELDS: dict[str, tuple[str, str]] = {
    "drug_allergy.current_medication": ("medication", "denies_medication"),
    "drug_allergy.known_allergy": ("allergy", "denies_allergy"),
    "past_medical.diagnosed_conditions": ("diagnosis", "denies_diagnosis"),
}

NEGATIVE_VALUES = {"no", "none", "nil", "never", "denies", "no_medication",
                   "no_allergy", "no_condition", "false"}

CONFLICT_NEGATION = "negation_vs_evidence"
CONFLICT_VALUE = "value_contradiction"
CONFLICT_TEMPORAL = "temporal_impossibility"
CONFLICT_DUPLICATE = "duplicate_with_divergence"


@dataclass(frozen=True)
class Conflict:
    kind: str
    fact_ids: tuple[str, ...]
    explanation: str

    @property
    def group_key(self) -> str:
        return "|".join(sorted(self.fact_ids))


def _is_negative(fact: ClinicalFact) -> bool:
    val = (fact.value_normalized or fact.value_raw or "").strip().lower()
    return val in NEGATIVE_VALUES


def _label(fact: ClinicalFact) -> str:
    return (fact.label or fact.value_normalized or fact.value_raw
            or fact.clinical_concept or fact.category)


def _detect_negation(facts: list[ClinicalFact]) -> list[Conflict]:
    out: list[Conflict] = []
    doc_facts = [f for f in facts if f.source_type == "document"]

    for denial_field, (contradicting_cat, _) in DENIAL_FIELDS.items():
        denials = [f for f in facts
                   if f.field_code == denial_field and _is_negative(f)]
        if not denials:
            continue
        evidence = [f for f in doc_facts if f.category == contradicting_cat]
        for denial in denials:
            for ev in evidence:
                out.append(Conflict(
                    kind=CONFLICT_NEGATION,
                    fact_ids=(denial.id, ev.id),
                    explanation=(
                        f"Patient reported '{_label(denial)}' for "
                        f"{denial_field.split('.')[-1].replace('_', ' ')}, but a "
                        f"scanned document shows {contradicting_cat} "
                        f"'{_label(ev)}'."),
                ))
    return out


def _detect_duplicate_divergence(facts: list[ClinicalFact]) -> list[Conflict]:
    """Same medication concept recorded at two different normalised values."""
    meds = [f for f in facts
            if f.category == "medication" and f.clinical_concept]
    out: list[Conflict] = []
    for a, b in combinations(meds, 2):
        if a.clinical_concept != b.clinical_concept:
            continue
        if not a.value_normalized or not b.value_normalized:
            continue
        if a.value_normalized == b.value_normalized:
            continue
        out.append(Conflict(
            kind=CONFLICT_DUPLICATE,
            fact_ids=(a.id, b.id),
            explanation=(
                f"'{a.clinical_concept}' appears at two different values: "
                f"'{a.value_normalized}' and '{b.value_normalized}'. The "
                f"timeline shows the sequence."),
        ))
    return out


def _detect_temporal(facts: list[ClinicalFact]) -> list[Conflict]:
    """A document predating a stated symptom onset it refers to."""
    onsets = [f for f in facts
              if f.category == "symptom" and f.effective_date
              and f.source_type == "answer"]
    docs = [f for f in facts
            if f.source_type == "document" and f.effective_date
            and f.category in {"diagnosis", "lab_value"}]
    out: list[Conflict] = []
    for onset in onsets:
        for doc in docs:
            if doc.clinical_concept and onset.clinical_concept \
                    and doc.clinical_concept == onset.clinical_concept \
                    and doc.effective_date < onset.effective_date:
                out.append(Conflict(
                    kind=CONFLICT_TEMPORAL,
                    fact_ids=(onset.id, doc.id),
                    explanation=(
                        f"Document dated {doc.effective_date.isoformat()} "
                        f"references '{doc.clinical_concept}' but the patient "
                        f"stated onset as "
                        f"{onset.effective_date.isoformat()}."),
                ))
    return out


def detect(facts: list[ClinicalFact]) -> list[Conflict]:
    """All conflicts across the admitted fact set, in stable order."""
    found = (_detect_negation(facts)
             + _detect_value_contradiction(facts)
             + _detect_duplicate_divergence(facts)
             + _detect_temporal(facts))
    return sorted(found, key=lambda c: (c.kind, c.group_key))


def _detect_value_contradiction(facts: list[ClinicalFact]) -> list[Conflict]:
    """Patient claims a condition is controlled while a lab value is abnormal."""
    controlled = [f for f in facts
                  if f.source_type == "answer"
                  and (f.value_normalized or "").lower() in
                  {"controlled", "well_controlled", "normal"}]
    abnormal = [f for f in facts
                if f.category == "lab_value" and f.source_type == "document"
                and (f.value_normalized or "").lower() == "abnormal"]
    out: list[Conflict] = []
    for c in controlled:
        for ab in abnormal:
            if c.clinical_concept and ab.clinical_concept \
                    and c.clinical_concept != ab.clinical_concept:
                continue
            out.append(Conflict(
                kind=CONFLICT_VALUE,
                fact_ids=(c.id, ab.id),
                explanation=(
                    f"Patient reported '{_label(c)}' but lab value "
                    f"'{_label(ab)}' is outside its reference range."),
            ))
    return out


def apply(facts: list[ClinicalFact],
          conflicts: list[Conflict]) -> dict[str, list[Conflict]]:
    """Mark facts as conflicting and assign shared group ids.

    Mutates the passed facts (they are session-attached ORM rows) and returns a
    fact-id -> conflicts index for the physician's conflict panel.
    """
    index: dict[str, list[Conflict]] = {}
    by_id = {f.id: f for f in facts}

    for conflict in conflicts:
        group = conflict.group_key
        for fid in conflict.fact_ids:
            fact = by_id.get(fid)
            if fact is not None:
                fact.is_conflicting = True
                fact.conflict_group_id = group
            index.setdefault(fid, []).append(conflict)
    return index
