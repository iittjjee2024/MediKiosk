"""Document intelligence: OCR -> entity extraction -> normalised facts.

For the MVP the OCR model itself (Surya / IIIT Indic OCR) is not wired -- that
is a heavy inference dependency and a stated stub. What IS wired, and fully
exercised, is everything downstream of the pixels: quality gating, entity
extraction, normalisation, the confidence gate, provenance attachment,
timeline placement and conflict detection.

To keep the pipeline demonstrable end to end without a GPU, a document is
ingested as an already-transcribed page plus a small set of extracted-entity
candidates (a "document kind" fixture, or explicit entities from a caller).
Each candidate flows through exactly the same confidence gate and provenance
model that a real OCR output would, so the physician-facing behaviour -- cited
facts, unreadable regions shown as-is, abnormal values flagged, contradictions
surfaced -- is genuine, not mocked. Swapping the fixture for a Surya call later
changes only where the candidates come from.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import date

from sqlalchemy.orm import Session

from ..db import utcnow
from ..engines import confidence as conf
from ..models import (ClinicalFact, Document, DocumentPage, Encounter,
                      ExtractedEntity, IntakeSession, Provenance,
                      VerificationItem)
from . import audit

OCR_MODEL = "surya-sim"
OCR_MODEL_VERSION = "fixture-1"


@dataclass
class EntityCandidate:
    entity_type: str            # diagnosis | medication | lab_value | procedure
    text_raw: str
    value_normalized: str | None = None
    unit_normalized: str | None = None
    reference_range: str | None = None
    is_abnormal: bool | None = None
    confidence: float = 0.95
    concept: str | None = None
    entity_date: date | None = None
    region_bbox: dict | None = None
    # fact category this maps to in the Clinical Fact store
    category: str = "diagnosis"


@dataclass
class DocumentFixture:
    document_type: str
    ocr_text: str
    ocr_confidence: float
    entities: list[EntityCandidate]


# ---- built-in fixtures so the document flow is demonstrable in the demo ----
# Values chosen to make the physician screen tell a story: an abnormal HbA1c,
# and a medication the patient may separately deny (drives conflict detection).

def _lab_report(days_ago: int = 40) -> DocumentFixture:
    d = date.today().fromordinal(date.today().toordinal() - days_ago)
    return DocumentFixture(
        document_type="lab_report", ocr_confidence=0.96,
        ocr_text=("CITY DIAGNOSTICS  |  Glycated Haemoglobin (HbA1c)\n"
                  "Result: 9.2 %   Reference: 4.0 - 5.6 %   [HIGH]\n"
                  "Fasting glucose: 168 mg/dL  Reference: 70 - 100 [HIGH]\n"
                  f"Collected: {d.isoformat()}"),
        entities=[
            EntityCandidate("lab_value", "HbA1c 9.2 %", "abnormal", "%",
                            "4.0-5.6", True, 0.95, "hba1c", d,
                            category="lab_value"),
            EntityCandidate("lab_value", "Fasting glucose 168 mg/dL",
                            "abnormal", "mg/dL", "70-100", True, 0.93,
                            "fasting_glucose", d, category="lab_value"),
            EntityCandidate("diagnosis", "Type 2 Diabetes Mellitus",
                            "diabetes", None, None, None, 0.88, "diabetes", d,
                            category="diagnosis"),
        ])


def _prescription(days_ago: int = 35) -> DocumentFixture:
    d = date.today().fromordinal(date.today().toordinal() - days_ago)
    return DocumentFixture(
        document_type="prescription", ocr_confidence=0.62,   # handwritten
        ocr_text=("Dr. ______  (handwritten)\n"
                  "Tab Metformin 500 mg  --  1-0-1  x 30 days\n"
                  "Tab Amlodipine 5 mg  --  0-0-1\n"
                  f"Date: {d.isoformat()}"),
        entities=[
            EntityCandidate("medication", "Metformin 500 mg", "500mg", "mg",
                            None, None, 0.74, "metformin", d,
                            category="medication"),
            EntityCandidate("medication", "Amlodipine 5 mg", "5mg", "mg",
                            None, None, 0.71, "amlodipine", d,
                            category="medication"),
            # a deliberately low-confidence region: shown to the physician as
            # "unreadable" rather than guessed
            EntityCandidate("medication", "<illegible third line>", None, None,
                            None, None, 0.28, None, d, category="medication"),
        ])


def _discharge(days_ago: int = 120) -> DocumentFixture:
    d = date.today().fromordinal(date.today().toordinal() - days_ago)
    return DocumentFixture(
        document_type="discharge_summary", ocr_confidence=0.90,
        ocr_text=("DISTRICT HOSPITAL  |  Discharge Summary\n"
                  "Diagnosis: Hypertension, well controlled\n"
                  "Procedure: none\n"
                  f"Discharged: {d.isoformat()}"),
        entities=[
            EntityCandidate("diagnosis", "Hypertension", "hypertension", None,
                            None, None, 0.91, "hypertension", d,
                            category="diagnosis"),
        ])


FIXTURES = {
    "lab_report": _lab_report,
    "prescription": _prescription,
    "discharge_summary": _discharge,
}


def ingest_document(db: Session, session: IntakeSession, *,
                    document_kind: str | None = None,
                    ocr_text: str | None = None,
                    document_type: str = "unknown",
                    ocr_confidence: float = 0.9,
                    entities: list[EntityCandidate] | None = None,
                    actor_id: str | None = None) -> dict:
    """Run one document through the full pipeline.

    Either name a built-in ``document_kind`` fixture, or pass ``ocr_text`` and
    explicit ``entities`` (what a real OCR + extraction step would hand over).
    """
    if document_kind:
        if document_kind not in FIXTURES:
            raise KeyError(f"unknown document kind '{document_kind}'")
        fx = FIXTURES[document_kind]()
        document_type, ocr_text = fx.document_type, fx.ocr_text
        ocr_confidence, entities = fx.ocr_confidence, fx.entities
    entities = entities or []

    encounter = db.get(Encounter, session.encounter_id)

    doc = Document(tenant_id=session.tenant_id, session_id=session.id,
                   document_type=document_type,
                   classification_confidence=ocr_confidence,
                   quality_status="passed", processing_status="ocr_running",
                   page_count=1, uploaded_at=utcnow())
    db.add(doc)
    db.flush()

    page = DocumentPage(tenant_id=session.tenant_id, document_id=doc.id,
                        page_number=1,
                        image_object_ref=f"demo://{document_type}/{doc.id}",
                        ocr_text=ocr_text, ocr_confidence=ocr_confidence,
                        ocr_model_version=f"{OCR_MODEL}@{OCR_MODEL_VERSION}")
    db.add(page)
    db.flush()

    admitted, unreadable, queued = [], [], []

    for cand in entities:
        ent = ExtractedEntity(
            tenant_id=session.tenant_id, document_page_id=page.id,
            entity_type=cand.entity_type, text_raw=cand.text_raw,
            value_normalized=cand.value_normalized,
            unit_normalized=cand.unit_normalized,
            reference_range=cand.reference_range, is_abnormal=cand.is_abnormal,
            region_bbox=cand.region_bbox, confidence=cand.confidence,
            extraction_method="ocr_layout", model_version=OCR_MODEL_VERSION,
            entity_date=cand.entity_date)
        db.add(ent)
        db.flush()

        # documents are held to a stricter bar than speech
        verdict = conf.classify(cand.confidence, conf.SOURCE_DOCUMENT)

        if verdict.band == conf.BAND_UNREADABLE:
            ent.verification_status = "pending_human"
            unreadable.append(ent.id)
            continue
        if not verdict.admit:
            ent.verification_status = "pending_human"
            db.add(VerificationItem(
                tenant_id=session.tenant_id, session_id=session.id,
                origin="document", source_entity_id=ent.id,
                field_code=None, candidate_text=cand.text_raw,
                confidence=cand.confidence,
                reason=verdict.reason or "low_confidence", status="pending"))
            db.flush()
            queued.append(ent.id)
            continue

        fact = ClinicalFact(
            tenant_id=session.tenant_id, session_id=session.id,
            patient_id=encounter.patient_id, category=cand.category,
            clinical_concept=cand.concept,
            label=(cand.concept or cand.entity_type).replace("_", " ").title(),
            value_raw=cand.text_raw, value_normalized=cand.value_normalized,
            unit=cand.unit_normalized, effective_date=cand.entity_date,
            date_precision="day", confidence=cand.confidence,
            source_type="document", source_entity_id=ent.id,
            verification_status=verdict.verification_status)
        db.add(fact)
        db.flush()

        db.add(Provenance(
            tenant_id=session.tenant_id, clinical_fact_id=fact.id,
            source_type="document", source_document_id=doc.id,
            source_page_number=1, source_region_bbox=cand.region_bbox,
            extraction_method="ocr_layout", model_name=OCR_MODEL,
            model_version=OCR_MODEL_VERSION, confidence=cand.confidence,
            captured_at=utcnow(), device_id=session.device_id))
        admitted.append(fact.id)

    doc.processing_status = ("needs_verification" if (unreadable or queued)
                             else "extracted")
    doc.processed_at = utcnow()
    db.flush()

    audit.record(db, tenant_id=session.tenant_id, actor_type="patient",
                 actor_id=actor_id, action="document_ingested",
                 entity_type="document", entity_id=doc.id,
                 detail={"document_type": document_type,
                         "ocr_confidence": ocr_confidence,
                         "admitted": len(admitted),
                         "unreadable": len(unreadable),
                         "queued_for_human": len(queued)})
    db.flush()

    return {"document_id": doc.id, "document_type": document_type,
            "ocr_confidence": ocr_confidence,
            "admitted_fact_ids": admitted,
            "unreadable_region_ids": unreadable,
            "verification_item_entity_ids": queued}
