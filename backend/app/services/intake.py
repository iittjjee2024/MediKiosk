"""Intake orchestration: answers in, validated facts and alerts out.

This is the only place where an AI candidate can become part of the clinical
record, and it is deliberately narrow. Every submission passes the confidence
gate; anything below the admission threshold is preserved verbatim and routed
to a human instead of being guessed at.

Re-answering is expected -- a patient correcting an earlier statement is the
common case. The policy is last-write-wins per field, with the superseded value
retained in the audit trail. The correction wins, but the change stays visible.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..engines import confidence as conf
from ..engines import question_engine as qe
from ..engines import redflag_engine as rfe
from ..engines.rules import AnswerRow, FactRow, SessionState
from ..models import (Answer, ClinicalFact, Consent, Encounter, IntakeSession,
                      Provenance, Question, Questionnaire, RedFlag,
                      RedFlagRule, RuleEvaluation, VerificationItem)
from . import audit

NEGATIVE_TOKENS = {"no", "none", "nil", "never"}


class ConsentRequired(PermissionError):
    """Raised when clinical capture is attempted without an active consent."""


class UnknownField(KeyError):
    pass


@dataclass
class SubmissionResult:
    field_code: str
    verdict: conf.Verdict
    admitted_fact_ids: list[str] = dc_field(default_factory=list)
    verification_item_ids: list[str] = dc_field(default_factory=list)
    fired_alerts: list[RedFlag] = dc_field(default_factory=list)
    next_question: qe.QuestionView | None = None
    completeness: qe.Completeness | None = None
    superseded: bool = False

def _numeric(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def load_answers(db: Session, session_id: str) -> list[Answer]:
    return list(db.scalars(
        select(Answer).where(Answer.session_id == session_id)
        .order_by(Answer.sequence_order)))


def load_facts(db: Session, session_id: str) -> list[ClinicalFact]:
    return list(db.scalars(
        select(ClinicalFact).where(ClinicalFact.session_id == session_id)
        .order_by(ClinicalFact.created_at, ClinicalFact.id)))


def build_state(db: Session, session_id: str) -> SessionState:
    """Project DB rows into the narrow read-model the engines may see."""
    answers = [
        AnswerRow(
            field_code=a.field_code,
            value_normalized=a.value_normalized,
            selected_options=tuple(a.selected_options or ()),
            numeric_value=_numeric(a.value_normalized),
            skipped_reason=a.skipped_reason,
        )
        for a in load_answers(db, session_id)
    ]
    facts = [
        FactRow(id=f.id, category=f.category, concept=f.clinical_concept,
                field_code=f.field_code,
                value_normalized=f.value_normalized,
                numeric_value=_numeric(f.value_normalized))
        for f in load_facts(db, session_id)
    ]
    return SessionState.build(facts, answers)


def get_questionnaire(db: Session, session: IntakeSession) -> Questionnaire:
    proto = db.get(Questionnaire, session.questionnaire_id)
    if proto is None:
        raise LookupError("questionnaire missing for session")
    return proto


def _question(proto: Questionnaire, field_code: str) -> Question:
    for q in proto.questions:
        if q.field_code == field_code:
            return q
    raise UnknownField(field_code)


def _active_consent(db: Session, session: IntakeSession) -> Consent:
    consent = db.scalar(
        select(Consent).where(Consent.encounter_id == session.encounter_id)
        .order_by(Consent.created_at.desc()).limit(1))
    if consent is None or not consent.is_active:
        raise ConsentRequired("no active consent for this encounter")
    return consent

def _supersede(db: Session, session: IntakeSession, field_code: str) -> dict | None:
    """Remove facts derived from a previous answer to the same field.

    The prior value is returned so the caller can put it in the audit trail:
    the record must show that an earlier statement existed and was replaced.
    """
    prior = list(db.scalars(select(ClinicalFact).where(
        ClinicalFact.session_id == session.id,
        ClinicalFact.field_code == field_code)))
    if not prior:
        return None
    snapshot = {"values": [f.value_normalized for f in prior],
                "fact_ids": [f.id for f in prior]}
    for fact in prior:
        if fact.provenance is not None:
            db.delete(fact.provenance)
        db.delete(fact)
    db.flush()
    return snapshot


def _values_for(question: Question, answer: Answer) -> list[str]:
    if question.answer_type == "multi":
        return list(answer.selected_options or [])
    return [answer.value_normalized] if answer.value_normalized else []


def _admit_facts(db: Session, session: IntakeSession, patient_id: str,
                 question: Question, answer: Answer,
                 verdict: conf.Verdict) -> list[ClinicalFact]:
    """One fact per selected value, each with its own provenance row."""
    category = question.fact_category or "symptom"
    created: list[ClinicalFact] = []

    for value in _values_for(question, answer):
        concept = question.clinical_concept
        if concept is None and value not in NEGATIVE_TOKENS:

            concept = value if question.answer_type in {"single", "multi"} else None

        fact = ClinicalFact(
            tenant_id=session.tenant_id, session_id=session.id,
            patient_id=patient_id, category=category,
            field_code=question.field_code, clinical_concept=concept,
            label=question.field_code.split(".")[-1].replace("_", " ").title(),
            value_raw=answer.value_raw, value_normalized=value,
            confidence=answer.effective_confidence, source_type="answer",
            source_answer_id=answer.id,
            verification_status=verdict.verification_status,
            effective_date=None, date_precision="day",
        )
        db.add(fact)
        db.flush()

        db.add(Provenance(
            tenant_id=session.tenant_id, clinical_fact_id=fact.id,
            source_type="answer", extraction_method=(
                "touch_selection" if answer.input_mode == "touch"
                else "asr_nlu"),
            model_name=None if answer.input_mode == "touch" else "indic_asr",
            model_version=None if answer.input_mode == "touch" else "seed-1",
            confidence=answer.effective_confidence,
            captured_at=utcnow(), device_id=session.device_id,
        ))
        created.append(fact)

    db.flush()
    return created


def _queue_for_human(db: Session, session: IntakeSession, answer: Answer,
                     verdict: conf.Verdict) -> VerificationItem:
    item = VerificationItem(
        tenant_id=session.tenant_id, session_id=session.id, origin="answer",
        source_answer_id=answer.id, field_code=answer.field_code,
        candidate_text=answer.raw_transcript or answer.value_raw,
        confidence=answer.effective_confidence,
        reason=verdict.reason or "low_confidence", status="pending",
    )
    db.add(item)
    db.flush()
    return item

def evaluate_red_flags(db: Session, session: IntakeSession,
                       state: SessionState) -> list[RedFlag]:
    """Evaluate every active rule and persist all outcomes.

    Non-firing evaluations are persisted too. Without them it is impossible to
    ask, for any past session, whether a rule should have fired -- and that is
    the entire basis of retrospective sensitivity analysis.
    """
    rules = list(db.scalars(select(RedFlagRule).where(
        RedFlagRule.tenant_id.is_(None)
        | (RedFlagRule.tenant_id == session.tenant_id))))

    fingerprint = state.fingerprint()
    already = {
        r.rule_code for r in db.scalars(select(RedFlag).where(
            RedFlag.session_id == session.id))
    }
    created: list[RedFlag] = []

    for ev in rfe.evaluate_all(rules, state):
        db.add(RuleEvaluation(
            tenant_id=session.tenant_id, session_id=session.id,
            rule_code=ev.rule_code, rule_version=ev.rule_version,
            fired=ev.fired, errored=ev.errored, fact_set_hash=fingerprint))

        if not ev.fired or ev.rule_code in already:
            continue

        alert = RedFlag(
            tenant_id=session.tenant_id, session_id=session.id,
            rule_id=ev.rule_id, rule_code=ev.rule_code,
            rule_version=ev.rule_version, severity=ev.severity,
            triggering_facts={"refs": list(ev.refs),
                              "errored": ev.errored,
                              "error_detail": ev.error_detail},
            message_key=ev.message_key, status="created",
            sla_deadline=ev.sla_deadline)
        db.add(alert)
        created.append(alert)
        already.add(ev.rule_code)

    db.flush()
    if created:


        session.status = "escalated"
    return created

def submit_answer(db: Session, session: IntakeSession, *, field_code: str,
                  value: str | None = None,
                  selected_options: list[str] | None = None,
                  input_mode: str = "touch",
                  raw_transcript: str | None = None,
                  asr_confidence: float | None = None,
                  nlu_confidence: float | None = None,
                  skipped_reason: str | None = None,
                  actor_id: str | None = None) -> SubmissionResult:
    """Record one answer, admit or withhold facts, evaluate rules, advance."""
    _active_consent(db, session)

    proto = get_questionnaire(db, session)
    question = _question(proto, field_code)
    encounter = db.get(Encounter, session.encounter_id)

    existing = db.scalar(select(Answer).where(
        Answer.session_id == session.id, Answer.field_code == field_code))

    superseded_facts = _supersede(db, session, field_code) if existing else None
    superseded = existing is not None

    if existing is None:
        max_order = db.scalar(select(Answer.sequence_order).where(
            Answer.session_id == session.id)
            .order_by(Answer.sequence_order.desc()).limit(1)) or 0
        answer = Answer(tenant_id=session.tenant_id, session_id=session.id,
                        question_id=question.id, field_code=field_code,
                        sequence_order=max_order + 1)
        db.add(answer)
    else:
        answer = existing
        db.query(VerificationItem).filter(
            VerificationItem.source_answer_id == answer.id,
            VerificationItem.status == "pending").delete()

    answer.input_mode = input_mode
    answer.raw_transcript = raw_transcript
    answer.value_raw = value
    answer.value_normalized = value
    answer.selected_options = selected_options
    answer.asr_confidence = asr_confidence
    answer.nlu_confidence = nlu_confidence
    answer.skipped_reason = skipped_reason
    db.flush()

    result = SubmissionResult(field_code=field_code,
                              verdict=conf.classify(1.0),
                              superseded=superseded)

    if skipped_reason:


        result.verdict = conf.classify(1.0)
    else:
        verdict = conf.classify(answer.effective_confidence,
                                conf.SOURCE_ANSWER)
        result.verdict = verdict
        if verdict.admit:
            facts = _admit_facts(db, session, encounter.patient_id, question,
                                 answer, verdict)
            result.admitted_fact_ids = [f.id for f in facts]
        else:
            item = _queue_for_human(db, session, answer, verdict)
            result.verification_item_ids = [item.id]

    state = build_state(db, session.id)
    result.fired_alerts = evaluate_red_flags(db, session, state)

    completeness = qe.completeness(proto, state)
    session.completeness_score = completeness.score
    result.completeness = completeness
    result.next_question = qe.next_question(proto, state)

    audit.record(
        db, tenant_id=session.tenant_id, actor_type="patient",
        actor_id=actor_id, action="answer_submitted",
        entity_type="intake_session", entity_id=session.id,
        device_id=session.device_id,
        detail={
            "field_code": field_code,
            "input_mode": input_mode,
            "confidence_band": result.verdict.band,
            "admitted": result.verdict.admit,
            "fact_ids": result.admitted_fact_ids,
            "superseded_previous": superseded_facts,
            "alerts_fired": [a.rule_code for a in result.fired_alerts],
            "completeness": completeness.score,
        })
    db.flush()
    return result


def finalise(db: Session, session: IntakeSession,
             actor_id: str | None = None) -> qe.Completeness:
    """Completeness check, then hand the session to the physician."""
    proto = get_questionnaire(db, session)
    state = build_state(db, session.id)
    completeness = qe.completeness(proto, state)

    session.completeness_score = completeness.score
    session.completed_at = utcnow()


    if session.status != "escalated":
        session.status = "ready_for_physician"

    audit.record(db, tenant_id=session.tenant_id, actor_type="patient",
                 actor_id=actor_id, action="session_finalised",
                 entity_type="intake_session", entity_id=session.id,
                 detail={"completeness": completeness.score,
                         "unanswered": list(completeness.unanswered),
                         "explanation": completeness.explain()})
    db.flush()
    return completeness


def prakriti_scores(db: Session, session_id: str) -> dict:
    """Transparent dosha tally with per-item contributions.

    Presented as an *indicated distribution for practitioner confirmation*,
    never a determination: Prakriti assessment involves pulse and examination
    findings that a questionnaire cannot capture, and standardised instruments
    vary. The practitioner sees every contribution and can reject any of them.
    """
    from ..seed import PRAKRITI_WEIGHTS

    totals = {"vata": 0.0, "pitta": 0.0, "kapha": 0.0}
    contributions: list[dict] = []

    for answer in load_answers(db, session_id):
        if not answer.field_code.startswith("prakriti."):
            continue
        for value in ([answer.value_normalized] if answer.value_normalized
                      else list(answer.selected_options or [])):
            weights = PRAKRITI_WEIGHTS.get(value)
            if not weights:
                continue
            for dosha, w in weights.items():
                totals[dosha] += w
            contributions.append({"field_code": answer.field_code,
                                  "value": value, "weights": weights})

    total = sum(totals.values())
    distribution = ({k: round(v / total, 3) for k, v in totals.items()}
                    if total else totals)
    dominant = (max(distribution, key=distribution.get) if total else None)
    return {
        "distribution": distribution,
        "raw_totals": totals,
        "indicated_dominant": dominant,
        "contributions": contributions,
        "status": "indicated_for_practitioner_confirmation",
        "disclaimer": ("Questionnaire-elicitable portion only. Prakriti "
                       "assessment requires pulse and examination findings. "
                       "Practitioner confirmation required."),
    }
