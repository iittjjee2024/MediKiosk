"""REST surface.

Cross-cutting rules enforced here rather than per-handler:

* Tenant is always derived from the token, never accepted from the client.
* Mutating endpoints require an Idempotency-Key. Offline clients retry, and
  without idempotency a retried submission duplicates clinical facts -- a
  correctness bug in a medical record, not a cosmetic one.
* No patient value is ever written to a log line; identifiers only.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import schemas as S
from .db import as_aware, get_db, new_uuid, utcnow
from .engines import question_engine as qe
from .models import (CARE_AYUSH, AppUser, ClinicalFact, Consent, Encounter,
                     IntakeSession, IntegrationEvent, Patient, Questionnaire,
                     RedFlag, Summary, SyncOperation, VerificationItem)
from .seed import ALLOPATHIC_CODE, AYUSH_CODE
from .services import audit, documents, intake, perception
from .services import summary as summary_service
from .services.security import TokenClaims, TokenError, decode_token
from .services.security import issue_token, verify_password

router = APIRouter(prefix="/api/v1")


# ------------------------------------------------------------ dependencies ---

def _claims(authorization: str | None) -> TokenClaims:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "missing bearer token")
    try:
        return decode_token(authorization.split(" ", 1)[1].strip())
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))


def patient_claims(authorization: str | None = Header(default=None)
                   ) -> TokenClaims:
    c = _claims(authorization)
    if c.kind != "patient":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "patient session token required")
    return c


def staff_claims(authorization: str | None = Header(default=None)
                 ) -> TokenClaims:
    c = _claims(authorization)
    if c.kind != "staff":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "staff token required")
    return c


def require_role(*roles: str):
    def guard(c: TokenClaims = Depends(staff_claims)) -> TokenClaims:
        if c.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"role '{c.role}' not permitted")
        return c
    return guard


def idempotency_key(idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key")) -> str:
    if not idempotency_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Idempotency-Key header required")
    return idempotency_key


def _session_for(db: Session, c: TokenClaims) -> IntakeSession:
    if not c.session_id:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "no intake session on this token; grant consent "
                            "first")
    session = db.get(IntakeSession, c.session_id)
    if session is None or session.tenant_id != c.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return session


def _tenant_session(db: Session, session_id: str,
                    c: TokenClaims) -> IntakeSession:
    session = db.get(IntakeSession, session_id)
    if session is None or session.tenant_id != c.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return session


# ------------------------------------------------------------- serialisers ---

def _question_out(q: qe.QuestionView | None) -> S.QuestionOut | None:
    if q is None:
        return None
    return S.QuestionOut(field_code=q.field_code, section=q.section,
                         prompt_key=q.prompt_key, answer_type=q.answer_type,
                         options=list(q.options),
                         clinical_concept=q.clinical_concept)


def _completeness_out(c: qe.Completeness) -> S.CompletenessOut:
    return S.CompletenessOut(
        score=c.score, applicable_required=c.applicable_required,
        answered_required=c.answered_required,
        unanswered=list(c.unanswered),
        skipped=[{"field_code": s.field_code, "section": s.section,
                  "reason": s.reason} for s in c.skipped],
        explanation=c.explain())


def _alert_out(a: RedFlag) -> S.AlertOut:
    return S.AlertOut(
        id=a.id, rule_code=a.rule_code, rule_version=a.rule_version,
        severity=a.severity, message_key=a.message_key, status=a.status,
        sla_deadline=a.sla_deadline.isoformat() if a.sla_deadline else None)


# =========================================================== identity ========

@router.post("/identity/resolve", response_model=S.IdentifyOut)
def resolve_identity(body: S.IdentifyIn, db: Session = Depends(get_db),
                     _k: str = Depends(idempotency_key)):
    """Identity ladder: ABHA, then hospital local ID, then new registration.

    ABHA is optional by design. A large share of arriving patients will not
    have a usable linkage, and the platform must still deliver full value --
    requiring it would exclude exactly the population that needs this most.
    """
    from .models import Tenant
    tenant = db.scalar(select(Tenant).where(Tenant.is_active.is_(True))
                       .order_by(Tenant.created_at).limit(1))
    if tenant is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "no active facility configured")

    patient = None
    source = "new_registration"
    if body.abha_id:
        patient = db.scalar(select(Patient).where(
            Patient.tenant_id == tenant.id, Patient.abha_id == body.abha_id))
        if patient:
            source = "abha"
    if patient is None and body.hospital_local_id:
        patient = db.scalar(select(Patient).where(
            Patient.tenant_id == tenant.id,
            Patient.hospital_local_id == body.hospital_local_id))
        if patient:
            source = "hospital_local_id"

    if patient is None:
        # A timestamp is not unique enough: at 4,000-10,000 OPD registrations
        # a day, same-second arrivals are routine, so the local id carries a
        # random suffix.
        local_id = (body.hospital_local_id
                    or f"LOC-{utcnow():%Y%m%d}-{new_uuid()[:8].upper()}")
        patient = Patient(
            tenant_id=tenant.id, abha_id=body.abha_id,
            hospital_local_id=local_id,
            display_name=body.display_name or "Unnamed Patient",
            gender=body.gender, year_of_birth=body.year_of_birth,
            preferred_language=body.language)
        db.add(patient)
        db.flush()

    encounter = Encounter(tenant_id=tenant.id, patient_id=patient.id,
                          department=body.department,
                          care_system=body.care_system, status="open")
    db.add(encounter)
    db.flush()

    audit.record(db, tenant_id=tenant.id, actor_type="patient",
                 action="encounter_opened", entity_type="encounter",
                 entity_id=encounter.id, device_id=body.device_id,
                 detail={"identity_source": source,
                         "care_system": body.care_system,
                         "channel": body.channel})

    token = issue_token(TokenClaims(
        sub=patient.id, tenant_id=tenant.id, kind="patient",
        session_id=None, device_id=body.device_id,
        department=body.department, role="patient"))
    return S.IdentifyOut(token=token, patient_id=patient.id,
                         encounter_id=encounter.id, identity_source=source)


@router.post("/consent", response_model=S.ConsentOut)
def grant_consent(body: S.ConsentIn, db: Session = Depends(get_db),
                  c: TokenClaims = Depends(patient_claims),
                  _k: str = Depends(idempotency_key)):
    """Consent before any clinical question. Refusal carries no penalty."""
    encounter = db.scalar(select(Encounter).where(
        Encounter.patient_id == c.sub, Encounter.tenant_id == c.tenant_id,
        Encounter.status == "open").order_by(Encounter.arrived_at.desc())
        .limit(1))
    if encounter is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no open encounter")

    consent = Consent(
        tenant_id=c.tenant_id, encounter_id=encounter.id,
        scope_interview=body.scope_interview,
        scope_documents=body.scope_documents,
        scope_abdm_share=body.scope_abdm_share,
        scope_audio_retention=body.scope_audio_retention,
        language=body.language, explained_via_audio=body.explained_via_audio,
        device_id=c.device_id,
        granted_at=utcnow() if body.scope_interview else None)
    db.add(consent)
    db.flush()

    scopes = {"interview": body.scope_interview,
              "documents": body.scope_documents,
              "abdm_share": body.scope_abdm_share,
              "audio_retention": body.scope_audio_retention}
    audit.record(db, tenant_id=c.tenant_id, actor_type="patient",
                 actor_id=c.sub,
                 action="consent_granted" if body.scope_interview
                 else "consent_declined",
                 entity_type="consent", entity_id=consent.id,
                 device_id=c.device_id,
                 detail={"scopes": scopes, "language": body.language,
                         "explained_via_audio": body.explained_via_audio,
                         "policy_version": consent.policy_version})

    if not body.scope_interview:
        return S.ConsentOut(
            consent_id=consent.id, granted=False, scopes=scopes,
            message=("Consent declined and recorded. The patient proceeds to a "
                     "normal consultation with no penalty."))

    code = AYUSH_CODE if encounter.care_system == CARE_AYUSH else ALLOPATHIC_CODE
    proto = db.scalar(select(Questionnaire).where(
        Questionnaire.code == code, Questionnaire.is_active.is_(True),
        Questionnaire.effective_from <= date.today())
        .order_by(Questionnaire.version.desc()).limit(1))
    if proto is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"no active questionnaire '{code}'")

    session = IntakeSession(
        tenant_id=c.tenant_id, encounter_id=encounter.id,
        questionnaire_id=proto.id, questionnaire_version=proto.version,
        language=body.language, channel="kiosk", device_id=c.device_id,
        status="in_progress")
    db.add(session)
    db.flush()

    return S.ConsentOut(
        consent_id=consent.id, granted=True, scopes=scopes,
        session_id=session.id,
        message=("Consent recorded. Re-authenticate with the returned session "
                 "token to begin the interview."))


@router.post("/consent/{consent_id}/revoke")
def revoke_consent(consent_id: str, db: Session = Depends(get_db),
                   c: TokenClaims = Depends(patient_claims),
                   _k: str = Depends(idempotency_key)):
    consent = db.get(Consent, consent_id)
    if consent is None or consent.tenant_id != c.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "consent not found")
    consent.revoked_at = utcnow()
    consent.revocation_reason = "patient_request"
    audit.record(db, tenant_id=c.tenant_id, actor_type="patient",
                 actor_id=c.sub, action="consent_revoked",
                 entity_type="consent", entity_id=consent.id)
    return {"consent_id": consent.id, "revoked": True,
            "message": "Capture stopped immediately."}


@router.post("/sessions/{session_id}/token")
def session_token(session_id: str, db: Session = Depends(get_db),
                  c: TokenClaims = Depends(patient_claims)):
    """Bind the patient token to a session once consent has created one."""
    session = _tenant_session(db, session_id, c)
    encounter = db.get(Encounter, session.encounter_id)
    if encounter.patient_id != c.sub:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your session")
    return {"token": issue_token(TokenClaims(
        sub=c.sub, tenant_id=c.tenant_id, kind="patient", role="patient",
        session_id=session.id, device_id=c.device_id,
        department=c.department))}


# =========================================================== interview =======

@router.get("/interview/next-question", response_model=S.QuestionOut | None)
def next_question(db: Session = Depends(get_db),
                  c: TokenClaims = Depends(patient_claims)):
    session = _session_for(db, c)
    proto = intake.get_questionnaire(db, session)
    state = intake.build_state(db, session.id)
    return _question_out(qe.next_question(proto, state))


@router.post("/interview/answers", response_model=S.AnswerOut)
def submit_answer(body: S.AnswerIn, db: Session = Depends(get_db),
                  c: TokenClaims = Depends(patient_claims),
                  key: str = Depends(idempotency_key)):
    session = _session_for(db, c)

    existing = db.scalar(select(SyncOperation).where(
        SyncOperation.idempotency_key == key))
    if existing is not None and existing.status == "success":
        return S.AnswerOut(**existing.result)

    try:
        result = intake.submit_answer(
            db, session, field_code=body.field_code, value=body.value,
            selected_options=body.selected_options,
            input_mode=body.input_mode, raw_transcript=body.raw_transcript,
            asr_confidence=body.asr_confidence,
            nlu_confidence=body.nlu_confidence,
            skipped_reason=body.skipped_reason, actor_id=c.sub)
    except intake.ConsentRequired as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    except intake.UnknownField:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"field '{body.field_code}' is not part of this "
                            f"session's questionnaire version")

    out = S.AnswerOut(
        field_code=result.field_code,
        confidence_band=result.verdict.band,
        fact_admitted=result.verdict.admit,
        disposition=result.verdict.disposition,
        confirm_back_required=result.verdict.confirm_back_required,
        admitted_fact_ids=result.admitted_fact_ids,
        verification_item_ids=result.verification_item_ids,
        superseded_previous=result.superseded,
        red_flag_fired=bool(result.fired_alerts),
        alerts=[_alert_out(a) for a in result.fired_alerts],
        completeness=_completeness_out(result.completeness),
        next_question=_question_out(result.next_question))

    db.add(SyncOperation(
        tenant_id=c.tenant_id, session_id=session.id,
        device_id=c.device_id or "unknown", operation_type="submit_answer",
        idempotency_key=key, status="success", result=out.model_dump()))
    db.flush()
    return out


@router.post("/interview/voice")
def submit_voice(body: S.VoiceIn, db: Session = Depends(get_db),
                 c: TokenClaims = Depends(patient_claims),
                 key: str = Depends(idempotency_key)):
    """Interpret an ASR transcript via the fitted clinical NLU, then submit it
    through the same gated answer path a touch answer takes.

    If the NLU cannot map the transcript to any offered option, nothing is
    committed: the response says so and the client prompts the patient to
    confirm or tap. We never fabricate an option to force a submission."""
    session = _session_for(db, c)

    cached = db.scalar(select(SyncOperation).where(
        SyncOperation.idempotency_key == key))
    if cached is not None and cached.status == "success":
        return cached.result

    try:
        interp, result = perception.submit_transcript(
            db, session, field_code=body.field_code,
            transcript=body.transcript, asr_confidence=body.asr_confidence,
            language=body.language, actor_id=c.sub)
    except perception.FieldNotAskable:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"field '{body.field_code}' is not part of this "
                            f"session's questionnaire version")
    except intake.ConsentRequired as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))

    payload: dict = {"interpretation": interp}
    if result is None:
        # unmatched: preserved verbatim, patient asked to confirm or tap
        payload["committed"] = False
        payload["confirm_back_required"] = True
        payload["answer"] = None
    else:
        payload["committed"] = True
        payload["confirm_back_required"] = result.verdict.confirm_back_required
        payload["answer"] = S.AnswerOut(
            field_code=result.field_code,
            confidence_band=result.verdict.band,
            fact_admitted=result.verdict.admit,
            disposition=result.verdict.disposition,
            confirm_back_required=result.verdict.confirm_back_required,
            admitted_fact_ids=result.admitted_fact_ids,
            verification_item_ids=result.verification_item_ids,
            superseded_previous=result.superseded,
            red_flag_fired=bool(result.fired_alerts),
            alerts=[_alert_out(a) for a in result.fired_alerts],
            completeness=_completeness_out(result.completeness),
            next_question=_question_out(result.next_question)).model_dump()

    db.add(SyncOperation(
        tenant_id=c.tenant_id, session_id=session.id,
        device_id=c.device_id or "unknown", operation_type="submit_voice",
        idempotency_key=key, status="success", result=payload))
    db.flush()
    return payload


@router.post("/interview/documents")
def upload_document(body: S.DocumentIn, db: Session = Depends(get_db),
                    c: TokenClaims = Depends(patient_claims),
                    _k: str = Depends(idempotency_key)):
    """Ingest a scanned document through the full extraction pipeline.

    Requires the documents consent scope. For the MVP the OCR model is stubbed
    behind named fixtures, but everything downstream -- confidence gating,
    provenance, unreadable-region handling, conflict detection -- is real."""
    session = _session_for(db, c)

    consent = db.scalar(select(Consent).where(
        Consent.encounter_id == session.encounter_id)
        .order_by(Consent.created_at.desc()).limit(1))
    if consent is None or not consent.is_active or not consent.scope_documents:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "document scanning consent not granted")

    try:
        return documents.ingest_document(
            db, session, document_kind=body.document_kind,
            ocr_text=body.ocr_text, document_type=body.document_type,
            ocr_confidence=body.ocr_confidence, actor_id=c.sub)
    except KeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


@router.get("/interview/state")
def interview_state(db: Session = Depends(get_db),
                    c: TokenClaims = Depends(patient_claims)):
    session = _session_for(db, c)
    proto = intake.get_questionnaire(db, session)
    state = intake.build_state(db, session.id)
    completeness = qe.completeness(proto, state)
    return {
        "session_id": session.id, "status": session.status,
        "language": session.language,
        "completeness": _completeness_out(completeness).model_dump(),
        "progress": qe.section_progress(proto, state),
        "next_question": (_question_out(qe.next_question(proto, state)) or None),
        "answered": len(intake.load_answers(db, session.id)),
        "facts": len(intake.load_facts(db, session.id)),
    }


@router.post("/interview/finalise", response_model=S.FinaliseOut)
def finalise(db: Session = Depends(get_db),
             c: TokenClaims = Depends(patient_claims),
             _k: str = Depends(idempotency_key)):
    session = _session_for(db, c)
    completeness = intake.finalise(db, session, actor_id=c.sub)
    generated = summary_service.generate(db, session, actor_id=c.sub)
    return S.FinaliseOut(
        session_id=session.id, status=session.status,
        completeness=_completeness_out(completeness),
        summary_id=generated.id,
        grounding_pass_rate=generated.grounding_pass_rate)


@router.get("/interview/ayush/prakriti")
def ayush_prakriti(db: Session = Depends(get_db),
                   c: TokenClaims = Depends(patient_claims)):
    session = _session_for(db, c)
    return intake.prakriti_scores(db, session.id)


# =========================================================== staff auth ======

@router.post("/auth/login", response_model=S.LoginOut)
def login(body: S.LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(AppUser).where(AppUser.username == body.username,
                                          AppUser.is_active.is_(True)))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "invalid credentials")
    token = issue_token(TokenClaims(
        sub=user.id, tenant_id=user.tenant_id, kind="staff", role=user.role,
        department=user.department))
    return S.LoginOut(token=token, user_id=user.id,
                      full_name=user.full_name, role=user.role,
                      department=user.department)


# =========================================================== physician =======

PHYSICIAN_ROLES = ("physician", "ayush_practitioner")


@router.get("/physician/worklist")
def worklist(db: Session = Depends(get_db),
             c: TokenClaims = Depends(require_role(*PHYSICIAN_ROLES))):
    rows = db.scalars(select(IntakeSession).where(
        IntakeSession.tenant_id == c.tenant_id,
        IntakeSession.status.in_(("ready_for_physician", "escalated")))
        .order_by(IntakeSession.completed_at)).all()

    out = []
    for s in rows:
        encounter = db.get(Encounter, s.encounter_id)
        patient = db.get(Patient, encounter.patient_id)
        alerts = db.scalars(select(RedFlag).where(
            RedFlag.session_id == s.id,
            RedFlag.status != "resolved")).all()
        summary = summary_service.latest(db, s.id)
        out.append({
            "session_id": s.id, "status": s.status,
            "completeness": s.completeness_score,
            "care_system": encounter.care_system,
            "department": encounter.department,
            "patient": {"id": patient.id, "name": patient.display_name,
                        "gender": patient.gender,
                        "year_of_birth": patient.year_of_birth,
                        "abha_linked": bool(patient.abha_id)},
            "alerts": [_alert_out(a).model_dump() for a in alerts],
            "summary_id": summary.id if summary else None,
            "grounding_pass_rate": summary.grounding_pass_rate if summary
            else None,
        })
    # escalated sessions surface first: a triage alert outranks queue order
    out.sort(key=lambda r: (0 if r["status"] == "escalated" else 1))
    return {"count": len(out), "sessions": out}


@router.get("/physician/sessions/{session_id}/summary")
def get_summary(session_id: str, db: Session = Depends(get_db),
                c: TokenClaims = Depends(require_role(*PHYSICIAN_ROLES))):
    session = _tenant_session(db, session_id, c)
    summary = summary_service.latest(db, session.id)
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "no summary generated for this session")
    summary_service.open_for_review(db, summary, c.sub)

    facts = intake.load_facts(db, session.id)
    verification = db.scalars(select(VerificationItem).where(
        VerificationItem.session_id == session.id,
        VerificationItem.status == "pending")).all()

    return {
        "cited_summary": summary_service.citations(db, summary),
        "body": summary.body_structured,
        "pending_documents": summary.pending_documents,
        "interaction_check_performed": summary.interaction_check_performed,
        "conflicts": [
            {"fact_id": f.id, "group": f.conflict_group_id,
             "label": f.label, "value": f.value_normalized,
             "source_type": f.source_type}
            for f in facts if f.is_conflicting],
        "needs_human_verification": [
            {"id": v.id, "field_code": v.field_code,
             "candidate_text": v.candidate_text, "confidence": v.confidence,
             "reason": v.reason} for v in verification],
    }


@router.patch("/physician/summaries/{summary_id}/facts")
def edit_fact(summary_id: str, body: S.EditFactIn,
              db: Session = Depends(get_db),
              c: TokenClaims = Depends(require_role(*PHYSICIAN_ROLES)),
              _k: str = Depends(idempotency_key)):
    summary = db.get(Summary, summary_id)
    if summary is None or summary.tenant_id != c.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "summary not found")
    try:
        fact = summary_service.edit_fact(db, summary, c.sub,
                                        fact_id=body.fact_id,
                                        new_value=body.new_value)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return {"fact_id": fact.id, "value": fact.value_normalized,
            "physician_status": fact.physician_status,
            "summary_status": summary.status}


@router.post("/physician/summaries/{summary_id}/facts/reject")
def reject_fact(summary_id: str, body: S.RejectFactIn,
                db: Session = Depends(get_db),
                c: TokenClaims = Depends(require_role(*PHYSICIAN_ROLES)),
                _k: str = Depends(idempotency_key)):
    summary = db.get(Summary, summary_id)
    if summary is None or summary.tenant_id != c.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "summary not found")
    try:
        fact = summary_service.reject_fact(db, summary, c.sub,
                                           fact_id=body.fact_id,
                                           note=body.note)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return {"fact_id": fact.id, "physician_status": fact.physician_status}


@router.post("/physician/summaries/{summary_id}/clarify")
def clarify(summary_id: str, body: S.ClarifyIn, db: Session = Depends(get_db),
            c: TokenClaims = Depends(require_role(*PHYSICIAN_ROLES)),
            _k: str = Depends(idempotency_key)):
    summary = db.get(Summary, summary_id)
    if summary is None or summary.tenant_id != c.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "summary not found")
    summary_service.request_clarification(db, summary, c.sub, note=body.note)
    return {"summary_id": summary.id, "status": summary.status}


@router.post("/physician/summaries/{summary_id}/approve",
             response_model=S.ApproveOut)
def approve(summary_id: str, body: S.ApproveIn, db: Session = Depends(get_db),
            c: TokenClaims = Depends(require_role(*PHYSICIAN_ROLES)),
            _k: str = Depends(idempotency_key)):
    """Atomic approval. Nothing reaches the record without this call."""
    summary = db.get(Summary, summary_id)
    if summary is None or summary.tenant_id != c.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "summary not found")
    try:
        record = summary_service.approve(
            db, summary, c.sub, attestation=body.attestation,
            unresolved_conflicts_acknowledged=
            body.unresolved_conflicts_acknowledged)
    except summary_service.ReviewStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))

    facts = intake.load_facts(db, summary.session_id)
    events = db.scalars(select(IntegrationEvent).where(
        IntegrationEvent.session_id == summary.session_id)).all()
    return S.ApproveOut(
        final_clinical_record_id=record.id,
        summary_version=summary.version,
        content_hash=record.content_hash,
        facts={"accepted": sum(1 for f in facts
                               if f.physician_status == "accepted"),
               "edited": sum(1 for f in facts
                             if f.physician_status == "edited"),
               "rejected": sum(1 for f in facts
                               if f.physician_status == "rejected")},
        integration={e.target_system: e.status for e in events})


@router.get("/physician/sessions/{session_id}/fhir")
def fhir_bundle(session_id: str, db: Session = Depends(get_db),
                c: TokenClaims = Depends(require_role(*PHYSICIAN_ROLES))):
    session = _tenant_session(db, session_id, c)
    summary = summary_service.latest(db, session.id)
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no summary")
    return summary_service.build_fhir_bundle(db, session, summary)


# =========================================================== triage =========

TRIAGE_ROLES = ("nurse", "triage_staff", "physician", "ayush_practitioner")


@router.get("/triage/alerts")
def alerts(db: Session = Depends(get_db),
           c: TokenClaims = Depends(require_role(*TRIAGE_ROLES))):
    rows = db.scalars(select(RedFlag).where(
        RedFlag.tenant_id == c.tenant_id,
        RedFlag.status.notin_(("resolved",)))).all()

    order = {"critical": 0, "high": 1, "moderate": 2, "low": 3}
    now = utcnow()
    out = []
    for a in rows:
        session = db.get(IntakeSession, a.session_id)
        encounter = db.get(Encounter, session.encounter_id)
        patient = db.get(Patient, encounter.patient_id)
        deadline = as_aware(a.sla_deadline)
        overdue = bool(deadline and a.acknowledged_at is None
                       and deadline < now)
        out.append({
            "id": a.id, "rule_code": a.rule_code,
            "rule_version": a.rule_version, "severity": a.severity,
            "status": a.status, "message_key": a.message_key,
            "triggering_facts": a.triggering_facts,
            "sla_deadline": deadline.isoformat() if deadline else None,
            "sla_breached": overdue,
            "session_id": session.id,
            "patient": {"id": patient.id, "name": patient.display_name},
        })
    out.sort(key=lambda r: (not r["sla_breached"],
                            order.get(r["severity"], 9)))
    return {"count": len(out), "alerts": out}


@router.post("/triage/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: str, body: S.AcknowledgeIn,
                db: Session = Depends(get_db),
                c: TokenClaims = Depends(require_role(*TRIAGE_ROLES)),
                _k: str = Depends(idempotency_key)):
    """A human assesses. The engine never reordered the queue itself."""
    alert = db.get(RedFlag, alert_id)
    if alert is None or alert.tenant_id != c.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    alert.status = "acknowledged"
    alert.acknowledged_at = utcnow()
    alert.acknowledged_by = c.sub
    alert.resolution_note = body.note
    audit.record(db, tenant_id=c.tenant_id, actor_type=c.role or "staff",
                 actor_id=c.sub, action="alert_acknowledged",
                 entity_type="red_flag", entity_id=alert.id,
                 detail={"rule_code": alert.rule_code, "note": body.note})
    return {"alert_id": alert.id, "status": alert.status,
            "acknowledged_at": alert.acknowledged_at.isoformat()}


# =========================================================== analytics ======

@router.get("/analytics/dashboard")
def dashboard(db: Session = Depends(get_db),
              c: TokenClaims = Depends(staff_claims)):
    """Includes the metrics that can embarrass the platform, by design.

    Physician edit rate, grounding pass rate and withheld-fact counts are all
    surfaced because a dashboard that can only show success is not a
    measurement system.
    """
    tid = c.tenant_id
    sessions = db.scalars(select(IntakeSession).where(
        IntakeSession.tenant_id == tid)).all()
    completed = [s for s in sessions if s.status in ("completed",
                                                     "ready_for_physician",
                                                     "escalated")]
    summaries = db.scalars(select(Summary).where(
        Summary.tenant_id == tid)).all()
    reviews = db.scalars(select(func.count()).select_from(
        select(ClinicalFact).where(ClinicalFact.tenant_id == tid).subquery()))

    facts = db.scalars(select(ClinicalFact).where(
        ClinicalFact.tenant_id == tid)).all()
    withheld = db.scalars(select(VerificationItem).where(
        VerificationItem.tenant_id == tid)).all()
    alerts = db.scalars(select(RedFlag).where(RedFlag.tenant_id == tid)).all()

    edited = sum(1 for f in facts if f.physician_status == "edited")
    rejected = sum(1 for f in facts if f.physician_status == "rejected")
    reviewed = sum(1 for f in facts if f.physician_status != "unreviewed")

    ack = [a for a in alerts if a.acknowledged_at]
    within = sum(1 for a in ack
                 if a.sla_deadline
                 and as_aware(a.acknowledged_at) <= as_aware(a.sla_deadline))

    return {
        "sessions": {
            "total": len(sessions),
            "completed": len(completed),
            "in_progress": sum(1 for s in sessions
                               if s.status == "in_progress"),
            "escalated": sum(1 for s in sessions if s.status == "escalated"),
            "mean_completeness": round(
                sum(s.completeness_score for s in completed) / len(completed), 2)
            if completed else 0.0,
        },
        "facts": {
            "total": len(facts),
            "unconfirmed": sum(1 for f in facts
                               if f.verification_status == "unconfirmed"),
            "conflicting": sum(1 for f in facts if f.is_conflicting),
            "withheld_pending_human": len(
                [v for v in withheld if v.status == "pending"]),
        },
        "quality": {
            "mean_grounding_pass_rate": round(
                sum(s.grounding_pass_rate for s in summaries)
                / len(summaries), 2) if summaries else None,
            "physician_edit_rate_pct": round(edited / reviewed * 100, 2)
            if reviewed else None,
            "physician_reject_rate_pct": round(rejected / reviewed * 100, 2)
            if reviewed else None,
        },
        "triage": {
            "alerts_total": len(alerts),
            "acknowledged": len(ack),
            "sla_adherence_pct": round(within / len(ack) * 100, 2)
            if ack else None,
            "open": sum(1 for a in alerts if a.status != "resolved"),
        },
    }


@router.get("/admin/audit/verify")
def verify_audit(db: Session = Depends(get_db),
                 c: TokenClaims = Depends(
                     require_role("privacy_officer", "it_admin"))):
    intact, broken = audit.verify_chain(db, c.tenant_id)
    return {"chain_intact": intact, "first_broken_row_id": broken}
