"""Populate a demo tenant with realistic, fully-consistent sessions.

Rather than inserting rows directly, this drives the real services -- identity,
consent, the gated answer path, document ingestion, red-flag evaluation,
finalisation and summary generation. The result is data that is internally
consistent by construction: every fact has provenance, every summary sentence
has a citation, every red flag has a rule evaluation behind it. Nothing here
takes a shortcut the live API would not take.

Idempotent: keyed off a marker patient, so calling it repeatedly does nothing.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import (CARE_ALLOPATHIC, CARE_AYUSH, Consent, Encounter,
                     IntakeSession, Patient, Questionnaire, Tenant)
from .seed import ALLOPATHIC_CODE, AYUSH_CODE
from .services import documents, intake
from .services import summary as summary_service

MARKER = "DEMO-SEEDED"


def _questionnaire(db: Session, code: str) -> Questionnaire:
    return db.scalar(select(Questionnaire).where(
        Questionnaire.code == code, Questionnaire.is_active.is_(True))
        .order_by(Questionnaire.version.desc()).limit(1))


def _new_session(db: Session, tenant: Tenant, *, name: str, gender: str,
                 yob: int, care_system: str, department: str,
                 abha: str | None = None) -> IntakeSession:
    patient = Patient(
        tenant_id=tenant.id, abha_id=abha,
        hospital_local_id=f"DEMO-{name.replace(' ', '')[:10]}-{yob}",
        display_name=name, gender=gender, year_of_birth=yob,
        preferred_language="hi")
    db.add(patient)
    db.flush()

    encounter = Encounter(tenant_id=tenant.id, patient_id=patient.id,
                          department=department, care_system=care_system,
                          status="open", token_number=None)
    db.add(encounter)
    db.flush()

    db.add(Consent(
        tenant_id=tenant.id, encounter_id=encounter.id,
        scope_interview=True, scope_documents=True,
        scope_abdm_share=(abha is not None), scope_audio_retention=False,
        language="hi", explained_via_audio=True, granted_at=utcnow()))
    db.flush()

    code = AYUSH_CODE if care_system == CARE_AYUSH else ALLOPATHIC_CODE
    proto = _questionnaire(db, code)
    session = IntakeSession(
        tenant_id=tenant.id, encounter_id=encounter.id,
        questionnaire_id=proto.id, questionnaire_version=proto.version,
        language="hi", channel="kiosk", device_id="demo-kiosk",
        status="in_progress")
    db.add(session)
    db.flush()
    return session


def _answer(db: Session, session: IntakeSession, field: str, *,
            value=None, options=None, mode="touch", asr=None, nlu=None):
    intake.submit_answer(db, session, field_code=field, value=value,
                         selected_options=options, input_mode=mode,
                         asr_confidence=asr, nlu_confidence=nlu,
                         actor_id="demo")


def _walk_remaining(db: Session, session: IntakeSession,
                    preset: dict[str, object]) -> None:
    """Answer whatever the engine asks next until the interview completes."""
    proto = intake.get_questionnaire(db, session)
    from .engines import question_engine as qe
    for _ in range(80):
        state = intake.build_state(db, session.id)
        nxt = qe.next_question(proto, state)
        if nxt is None:
            break
        chosen = preset.get(nxt.field_code)
        if nxt.answer_type == "multi":
            opts = chosen if isinstance(chosen, list) else [
                (nxt.options[0] if nxt.options else "none")]
            _answer(db, session, nxt.field_code, options=opts)
        else:
            val = chosen if isinstance(chosen, str) else (
                nxt.options[0] if nxt.options else "yes")
            _answer(db, session, nxt.field_code, value=val)


def _finish(db: Session, session: IntakeSession) -> None:
    intake.finalise(db, session, actor_id="demo")
    summary_service.generate(db, session, actor_id="demo")


def seed_demo(db: Session) -> dict:
    """Create the demo sessions if not already present."""
    tenant = db.scalar(select(Tenant).where(Tenant.is_active.is_(True))
                       .order_by(Tenant.created_at).limit(1))
    if tenant is None:
        return {"seeded": False, "reason": "no tenant"}

    already = db.scalar(select(Patient).where(
        Patient.tenant_id == tenant.id,
        Patient.hospital_local_id.like("DEMO-%")).limit(1))
    if already is not None:
        return {"seeded": False, "reason": "already present"}

    created: list[str] = []

    s1 = _new_session(db, tenant, name="Kamla Devi", gender="female",
                      yob=1962, care_system=CARE_ALLOPATHIC,
                      department="general_opd", abha="91-1111-2222-3333")
    _answer(db, s1, "chief_complaint.primary", value="chest_pain",
            mode="voice", asr=0.93, nlu=0.95)
    _answer(db, s1, "hpi.associated", options=["dyspnoea", "diaphoresis"])
    _answer(db, s1, "hpi.onset", value="today", mode="voice", asr=0.9, nlu=0.9)
    _answer(db, s1, "hpi.severity", value="8")
    _answer(db, s1, "socrates.character", value="squeezing")
    _answer(db, s1, "socrates.radiation", value="left_arm")
    _answer(db, s1, "drug_allergy.current_medication", value="no")
    documents.ingest_document(db, s1, document_kind="lab_report",
                              actor_id="demo")
    documents.ingest_document(db, s1, document_kind="prescription",
                              actor_id="demo")
    _walk_remaining(db, s1, {"socrates.radiation_detail": "on_exertion",
                             "hpi.timing": "on_exertion",
                             "hpi.exacerbating": "walking",
                             "past_medical.diagnosed_conditions": ["diabetes",
                                                                   "hypertension"],
                             "ros.weight_loss": "no", "ros.appetite": "reduced",
                             "family.history": ["cardiac_disease"],
                             "personal.tobacco": "former",
                             "personal.alcohol": "never"})
    _finish(db, s1)
    created.append("Kamla Devi (chest pain + red flag + conflict)")


    s2 = _new_session(db, tenant, name="Ramesh Kumar", gender="male",
                      yob=1988, care_system=CARE_ALLOPATHIC,
                      department="general_opd")
    _answer(db, s2, "chief_complaint.primary", value="fever")

    _answer(db, s2, "hpi.onset", value="yesterday", mode="voice",
            asr=0.24, nlu=0.22)
    _walk_remaining(db, s2, {"fever.pattern": "with_chills",
                             "hpi.associated": ["nausea"],
                             "hpi.severity": "5",
                             "past_medical.diagnosed_conditions": ["none"],
                             "drug_allergy.current_medication": "no",
                             "drug_allergy.known_allergy": "no",
                             "ros.weight_loss": "no",
                             "personal.tobacco": "never",
                             "personal.alcohol": "never",
                             "family.history": ["none"]})
    _finish(db, s2)
    created.append("Ramesh Kumar (fever + withheld low-confidence answer)")


    s3 = _new_session(db, tenant, name="Lakshmi Iyer", gender="female",
                      yob=1975, care_system=CARE_AYUSH,
                      department="ayush_opd")
    _answer(db, s3, "chief_complaint.primary", value="joint_pain"
            if _has_option(db, s3, "chief_complaint.primary", "joint_pain")
            else "digestive_complaint")
    _walk_remaining(db, s3, {
        "prakriti.body_frame": "body_frame.thin",
        "prakriti.skin": "skin.dry",
        "prakriti.appetite": "appetite.irregular",
        "prakriti.sleep": "sleep.light",
        "prakriti.temperament": "temperament.anxious",
        "prakriti.climate": "climate.dislikes_cold",
        "agni.digestion": "variable", "koshtha.bowel": "krura_hard",
        "ahara.diet_type": "irregular_timing", "vihara.activity": "moderate",
        "ahara_shakti.quantity": "small", "vyayama_shakti.tolerance": "low",
        "past_medical.diagnosed_conditions": ["none"],
        "drug_allergy.current_medication": "no"})
    _finish(db, s3)
    created.append("Lakshmi Iyer (AYUSH, Vata-dominant Prakriti)")


    s4 = _new_session(db, tenant, name="Suresh Patil", gender="male",
                      yob=1995, care_system=CARE_ALLOPATHIC,
                      department="general_opd", abha="91-4444-5555-6666")
    _answer(db, s4, "chief_complaint.primary", value="cough")
    _walk_remaining(db, s4, {"hpi.onset": "this_week", "hpi.severity": "3",
                             "hpi.associated": ["none"],
                             "hpi.timing": "intermittent",
                             "hpi.exacerbating": "nothing",
                             "past_medical.diagnosed_conditions": ["none"],
                             "drug_allergy.current_medication": "no",
                             "drug_allergy.known_allergy": "no",
                             "ros.weight_loss": "no", "ros.appetite": "normal",
                             "personal.tobacco": "never",
                             "personal.alcohol": "occasional",
                             "family.history": ["none"]})
    _finish(db, s4)
    created.append("Suresh Patil (clean cough baseline)")

    db.flush()
    return {"seeded": True, "sessions": created}


def _has_option(db: Session, session: IntakeSession, field: str,
                option: str) -> bool:
    proto = intake.get_questionnaire(db, session)
    for q in proto.questions:
        if q.field_code == field:
            return option in (q.options or [])
    return False
