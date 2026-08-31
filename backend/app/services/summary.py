"""Summary generation, physician review and FHIR export.

Approval is a single atomic transaction: it creates the final record, freezes
fact statuses, writes the review action and inserts the outbox rows together.
A partial approval must be impossible, because a record that exists without its
export queued -- or an export queued for a record that does not exist -- is a
correctness defect in a medical system, not a retry-able inconvenience.

Export goes through a transactional outbox, so the physician's approval commits
regardless of whether ABDM or the hospital HIS is reachable. No external system
sits on the critical path of patient care.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..engines import conflict as cf
from ..engines import summary_builder as sb
from ..engines import timeline as tl
from ..models import (ClinicalFact, Document, Encounter, FinalClinicalRecord,
                      IntakeSession, IntegrationEvent, Patient,
                      PhysicianReview, Summary, SummaryCitation,
                      SummarySentence, TimelineEvent)
from . import audit
from .intake import load_facts


class ReviewStateError(RuntimeError):
    """Illegal transition in the physician review state machine."""

def generate(db: Session, session: IntakeSession, *,
             actor_id: str | None = None) -> Summary:
    """Build (or rebuild) the draft summary for a session."""
    facts = load_facts(db, session.id)

    conflicts = cf.detect(facts)
    cf.apply(facts, conflicts)

    dated, undated = tl.build(facts)
    _persist_timeline(db, session, dated + undated)

    pending = db.scalar(select(Document).where(
        Document.session_id == session.id,
        Document.processing_status.in_(("queued", "ocr_running")))
        .with_only_columns(Document.id).limit(1))

    pending_count = len(list(db.scalars(select(Document.id).where(
        Document.session_id == session.id,
        Document.processing_status.in_(("queued", "ocr_running"))))))

    sentences = sb.build_template(
        facts, timeline=dated, conflicts=conflicts,
        pending_documents=pending_count,
        interaction_check_performed=False)

    report = sb.validate_grounding(sentences, facts)

    last = db.scalar(select(Summary).where(Summary.session_id == session.id)
                     .order_by(Summary.version.desc()).limit(1))
    version = (last.version + 1) if last else 1

    summary = Summary(
        tenant_id=session.tenant_id, session_id=session.id, version=version,
        status="draft", generation_mode="template_only",
        grounding_pass_rate=report.pass_rate,
        body_structured=sb.to_structured(report.kept),
        pending_documents=pending_count,
        interaction_check_performed=False,
    )
    db.add(summary)
    db.flush()

    for order, sentence in enumerate(report.kept):
        row = SummarySentence(
            tenant_id=session.tenant_id, summary_id=summary.id,
            section=sentence.section, sentence_order=order,
            sentence_text=sentence.text, grounded=True)
        db.add(row)
        db.flush()
        for fid in sentence.fact_ids:
            db.add(SummaryCitation(tenant_id=session.tenant_id,
                                   summary_sentence_id=row.id,
                                   clinical_fact_id=fid))
    db.flush()

    audit.record(db, tenant_id=session.tenant_id, actor_type="system",
                 actor_id=actor_id, action="summary_generated",
                 entity_type="summary", entity_id=summary.id,
                 detail={"version": version,
                         "grounding_pass_rate": report.pass_rate,
                         "sentences_kept": len(report.kept),
                         "sentences_dropped": len(report.dropped),
                         "conflicts": len(conflicts),
                         "pending_documents": pending_count})
    return summary


def _persist_timeline(db: Session, session: IntakeSession,
                      entries: list[tl.TimelineEntry]) -> None:
    db.query(TimelineEvent).filter(
        TimelineEvent.session_id == session.id).delete()
    encounter = db.get(Encounter, session.encounter_id)
    for e in entries:
        db.add(TimelineEvent(
            tenant_id=session.tenant_id, patient_id=encounter.patient_id,
            session_id=session.id, clinical_fact_id=e.fact_id,
            event_date=e.event_date, date_precision=e.date_precision,
            event_category=e.category, display_label=e.label))
    db.flush()


def latest(db: Session, session_id: str) -> Summary | None:
    return db.scalar(select(Summary).where(Summary.session_id == session_id)
                     .order_by(Summary.version.desc()).limit(1))


def citations(db: Session, summary: Summary) -> dict:
    """sentence -> facts -> provenance, for the physician's source viewer."""
    out: list[dict] = []
    for sentence in summary.sentences:
        cited = []
        for c in sentence.citations:
            fact = db.get(ClinicalFact, c.clinical_fact_id)
            if fact is None:
                continue
            prov = fact.provenance
            cited.append({
                "fact_id": fact.id,
                "category": fact.category,
                "label": fact.label,
                "value": fact.value_normalized,
                "confidence": fact.confidence,
                "verification_status": fact.verification_status,
                "physician_status": fact.physician_status,
                "is_conflicting": fact.is_conflicting,
                "provenance": None if prov is None else {
                    "source_type": prov.source_type,
                    "document_id": prov.source_document_id,
                    "page_number": prov.source_page_number,
                    "region_bbox": prov.source_region_bbox,
                    "extraction_method": prov.extraction_method,
                    "model_name": prov.model_name,
                    "model_version": prov.model_version,
                    "confidence": prov.confidence,
                    "captured_at": prov.captured_at,
                    "device_id": prov.device_id,
                },
            })
        out.append({"sentence_id": sentence.id, "section": sentence.section,
                    "order": sentence.sentence_order,
                    "text": sentence.sentence_text, "citations": cited})
    return {"summary_id": summary.id, "version": summary.version,
            "status": summary.status,
            "grounding_pass_rate": summary.grounding_pass_rate,
            "sentences": out}

_OPENABLE = {"draft", "under_review", "edited", "clarification_requested"}


def open_for_review(db: Session, summary: Summary, physician_id: str) -> Summary:
    if summary.status in {"approved", "exported"}:
        raise ReviewStateError(f"summary already {summary.status}")
    summary.status = "under_review"
    _review(db, summary, physician_id, "opened")
    return summary


def edit_fact(db: Session, summary: Summary, physician_id: str, *,
              fact_id: str, new_value: str) -> ClinicalFact:
    fact = _fact_in_summary(db, summary, fact_id)
    previous = fact.value_normalized
    fact.value_normalized = new_value
    fact.physician_status = "edited"
    summary.status = "edited"
    _review(db, summary, physician_id, "edited_fact", target_fact_id=fact_id,
            previous_value=previous, new_value=new_value)
    return fact


def reject_fact(db: Session, summary: Summary, physician_id: str, *,
                fact_id: str, note: str | None = None) -> ClinicalFact:
    """Exclude one fact. Distinct from rejecting the whole draft.

    "This extracted value is wrong" and "this entire intake is unusable" are
    very different clinical judgements, so they are different actions with
    different audit entries.
    """
    fact = _fact_in_summary(db, summary, fact_id)
    fact.physician_status = "rejected"
    summary.status = "under_review"
    _review(db, summary, physician_id, "rejected_fact",
            target_fact_id=fact_id, previous_value=fact.value_normalized,
            note=note)
    return fact


def request_clarification(db: Session, summary: Summary, physician_id: str, *,
                          note: str) -> Summary:
    summary.status = "clarification_requested"
    _review(db, summary, physician_id, "requested_clarification", note=note)
    return summary


def reject(db: Session, summary: Summary, physician_id: str, *,
           note: str | None = None) -> Summary:
    summary.status = "rejected"
    _review(db, summary, physician_id, "rejected", note=note)
    return summary


def approve(db: Session, summary: Summary, physician_id: str, *,
            attestation: str,
            unresolved_conflicts_acknowledged: bool = False
            ) -> FinalClinicalRecord:
    """Atomic: final record + frozen facts + review action + outbox rows."""
    if summary.status in {"approved", "exported"}:
        raise ReviewStateError("summary already approved")
    if summary.status == "rejected":
        raise ReviewStateError("cannot approve a rejected summary")

    session = db.get(IntakeSession, summary.session_id)
    facts = load_facts(db, session.id)

    unresolved = [f.id for f in facts
                  if f.is_conflicting and f.physician_status == "unreviewed"]
    if unresolved and not unresolved_conflicts_acknowledged:
        raise ReviewStateError(
            f"{len(unresolved)} flagged conflict(s) require acknowledgement")

    for fact in facts:
        if fact.physician_status == "unreviewed":
            fact.physician_status = "accepted"

    bundle = build_fhir_bundle(db, session, summary)
    payload = json.dumps(bundle, sort_keys=True, separators=(",", ":"),
                         default=str)
    content_hash = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

    record = FinalClinicalRecord(
        tenant_id=session.tenant_id, session_id=session.id,
        summary_id=summary.id, approved_by=physician_id,
        attestation=attestation, content_hash=content_hash,
        fhir_bundle=bundle)
    db.add(record)

    summary.status = "approved"
    session.status = "completed"
    session.completed_at = session.completed_at or utcnow()

    _review(db, summary, physician_id, "approved", note=attestation)
    _enqueue_exports(db, session, summary, bundle)

    audit.record(db, tenant_id=session.tenant_id, actor_type="physician",
                 actor_id=physician_id, action="summary_approved",
                 entity_type="final_clinical_record", entity_id=record.id,
                 detail={"summary_version": summary.version,
                         "content_hash": content_hash,
                         "facts_total": len(facts),
                         "facts_rejected": sum(
                             1 for f in facts
                             if f.physician_status == "rejected"),
                         "facts_edited": sum(
                             1 for f in facts
                             if f.physician_status == "edited"),
                         "unresolved_conflicts_acknowledged":
                             unresolved_conflicts_acknowledged})
    db.flush()
    return record


def _fact_in_summary(db: Session, summary: Summary,
                     fact_id: str) -> ClinicalFact:
    fact = db.get(ClinicalFact, fact_id)
    if fact is None or fact.session_id != summary.session_id:
        raise LookupError("fact does not belong to this summary's session")
    return fact


def _review(db: Session, summary: Summary, physician_id: str, action: str,
            **kwargs) -> None:
    db.add(PhysicianReview(
        tenant_id=summary.tenant_id, summary_id=summary.id,
        session_id=summary.session_id, physician_id=physician_id,
        action=action, **kwargs))
    db.flush()

def build_fhir_bundle(db: Session, session: IntakeSession,
                      summary: Summary) -> dict:
    """Map the internal canonical model to FHIR once.

    Mapping happens in exactly one place so the HIS adapter and the ABDM
    adapter consume identical output. Divergence between destinations becomes
    impossible by construction rather than by discipline.
    """
    encounter = db.get(Encounter, session.encounter_id)
    patient = db.get(Patient, encounter.patient_id)
    facts = [f for f in load_facts(db, session.id)
             if f.physician_status != "rejected"]

    entries: list[dict] = [
        {"resource": {
            "resourceType": "Patient",
            "id": patient.id,
            "identifier": ([{"system": "https://healthid.abdm.gov.in",
                             "value": patient.abha_id}]
                           if patient.abha_id else [])
                          + [{"system": "urn:hospital:local",
                              "value": patient.hospital_local_id}],
            "gender": patient.gender or "unknown",
            "birthDate": (str(patient.year_of_birth)
                          if patient.year_of_birth else None),
        }},
        {"resource": {
            "resourceType": "Encounter",
            "id": encounter.id,
            "status": "finished",
            "class": {"code": "AMB", "display": "ambulatory"},
            "subject": {"reference": f"Patient/{patient.id}"},
            "serviceType": {"text": encounter.department},
        }},
    ]

    category_to_resource = {
        "diagnosis": "Condition",
        "symptom": "Condition",
        "family_history": "Condition",
        "personal_history": "Observation",
        "lab_value": "Observation",
        "ayush_parameter": "Observation",
        "medication": "MedicationStatement",
        "allergy": "AllergyIntolerance",
        "procedure": "Procedure",
    }

    for fact in facts:
        rtype = category_to_resource.get(fact.category, "Observation")
        resource = {
            "resourceType": rtype,
            "id": fact.id,
            "subject": {"reference": f"Patient/{patient.id}"},
            "encounter": {"reference": f"Encounter/{encounter.id}"},
            "code": {"text": fact.label or fact.clinical_concept
                     or fact.category},
        }
        if rtype == "Observation":
            resource["valueString"] = fact.value_normalized
            resource["status"] = "preliminary"
        elif rtype == "Condition":
            resource["clinicalStatus"] = {"coding": [{"code": "active"}]}
            resource["verificationStatus"] = {
                "coding": [{"code": "provisional"}]}
        elif rtype == "MedicationStatement":
            resource["status"] = "recorded"
            resource["medicationCodeableConcept"] = {
                "text": fact.clinical_concept or fact.value_normalized}
        elif rtype == "AllergyIntolerance":
            resource["code"] = {"text": fact.value_normalized}
        if fact.effective_date:
            resource["effectiveDateTime"] = fact.effective_date.isoformat()
        entries.append({"resource": resource})

        prov = fact.provenance
        if prov is not None:
            entries.append({"resource": {
                "resourceType": "Provenance",
                "id": prov.id,
                "target": [{"reference": f"{rtype}/{fact.id}"}],
                "recorded": (prov.captured_at.isoformat()
                             if prov.captured_at else None),
                "agent": [{
                    "type": {"text": "device" if prov.model_name
                             else "patient-entered"},
                    "who": {"display": prov.model_name or "patient"},
                }],
                "entity": ([{"role": "source",
                             "what": {"reference":
                                      f"DocumentReference/"
                                      f"{prov.source_document_id}"}}]
                           if prov.source_document_id else []),
                "extension": [
                    {"url": "urn:medikiosk:extractionMethod",
                     "valueString": prov.extraction_method},
                    {"url": "urn:medikiosk:modelVersion",
                     "valueString": prov.model_version or "n/a"},
                    {"url": "urn:medikiosk:confidence",
                     "valueDecimal": prov.confidence},
                ],
            }})

    sections = []
    for sentence in summary.sentences:
        sections.append({"title": sentence.section,
                         "text": {"status": "generated",
                                  "div": sentence.sentence_text}})
    entries.append({"resource": {
        "resourceType": "Composition",
        "id": summary.id,
        "status": "preliminary" if summary.status != "approved" else "final",
        "type": {"text": "Clinical history intake summary"},
        "subject": {"reference": f"Patient/{patient.id}"},
        "encounter": {"reference": f"Encounter/{encounter.id}"},
        "title": "MediKiosk pre-consultation clinical history",
        "section": sections,
    }})

    return {"resourceType": "Bundle", "type": "document", "entry": entries}


def _enqueue_exports(db: Session, session: IntakeSession, summary: Summary,
                     bundle: dict) -> None:
    """Outbox rows. ABDM is skipped without explicit sharing consent."""
    from ..models import Consent

    base = f"{session.id}:{summary.version}"
    db.add(IntegrationEvent(
        tenant_id=session.tenant_id, session_id=session.id,
        target_system="hospital_his", event_type="HISPushRequested",
        payload=bundle, idempotency_key=f"his:{base}"))

    consent = db.scalar(select(Consent).where(
        Consent.encounter_id == session.encounter_id)
        .order_by(Consent.created_at.desc()).limit(1))

    if consent is not None and consent.scope_abdm_share and consent.is_active:
        db.add(IntegrationEvent(
            tenant_id=session.tenant_id, session_id=session.id,
            target_system="abdm_gateway", event_type="ABDMSyncRequested",
            payload=bundle, idempotency_key=f"abdm:{base}"))
    else:
        db.add(IntegrationEvent(
            tenant_id=session.tenant_id, session_id=session.id,
            target_system="abdm_gateway", event_type="ABDMSyncSkipped",
            payload={"reason": "sharing_consent_not_granted"},
            status="delivered", idempotency_key=f"abdm-skip:{base}"))
    db.flush()
