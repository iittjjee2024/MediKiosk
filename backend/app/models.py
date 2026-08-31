"""MediKiosk data model.

Mirrors section 13 of the solution document. Three modelling decisions carry
the architecture and are worth stating here:

1. `Answer` and `ExtractedEntity` are *observations of input*. `ClinicalFact`
   is an *admitted, validated, normalised statement*. Keeping them separate
   makes the confidence-gated admission step explicit rather than implied.

2. `Provenance` is a table, not a column set. It is queried, exported as FHIR
   Provenance, and rendered in the physician's source viewer. Modelling it as a
   first-class row is what prevents partial provenance.

3. `SummarySentence` + `SummaryCitation` exist so that "every sentence is
   cited" is a database-enforced invariant rather than a slogan. A sentence
   with no citation row is never published.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey,
                        Integer, String, Text, UniqueConstraint, Index)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, created, fk, pk, utcnow

# ---------------------------------------------------------------------------
# Vocabularies (kept as plain string constants: they are protocol data, and
# using DB enums would make protocol evolution a migration event)
# ---------------------------------------------------------------------------

CARE_ALLOPATHIC = "allopathic"
CARE_AYUSH = "ayush"

SECTION_ORDER = [
    "chief_complaint",
    "hpi",
    "past_medical",
    "drug_allergy",
    "family",
    "personal",
    "review_of_systems",
    "ayush_dashavidha",
]

SECTION_LABELS = {
    "chief_complaint": "Chief Complaint",
    "hpi": "History of Present Illness",
    "past_medical": "Past Medical & Surgical History",
    "drug_allergy": "Drug & Allergy History",
    "family": "Family History",
    "personal": "Personal History",
    "review_of_systems": "Review of Systems",
    "ayush_dashavidha": "Dashavidha Pariksha",
}

# fact admission verdicts
ADMIT_ACCEPTED = "accepted"
ADMIT_UNCONFIRMED = "accepted_unconfirmed"
ADMIT_WITHHELD = "withheld_low_confidence"
ADMIT_UNREADABLE = "withheld_unreadable"

SKIP_DEPENDENCY = "dependency_unmet"
SKIP_NOT_APPLICABLE = "not_applicable"
SKIP_DECLINED = "declined"


# ---------------------------------------------------------------------------
# Tenancy and users
# ---------------------------------------------------------------------------

class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    facility_type: Mapped[str] = mapped_column(String(30), default="district")
    hospital_local_code: Mapped[str] = mapped_column(String(50), unique=True)
    abdm_facility_id: Mapped[str | None] = mapped_column(String(60))
    state: Mapped[str | None] = mapped_column(String(60))
    district: Mapped[str | None] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("tenant_id", "username"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # physician | ayush_practitioner | nurse | triage_staff
    # | clinical_admin | it_admin | privacy_officer
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    department: Mapped[str | None] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()


# ---------------------------------------------------------------------------
# Patient, encounter, consent, session
# ---------------------------------------------------------------------------

class Patient(Base):
    __tablename__ = "patient"
    __table_args__ = (UniqueConstraint("tenant_id", "hospital_local_id"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    abha_id: Mapped[str | None] = mapped_column(String(20), index=True)
    hospital_local_id: Mapped[str] = mapped_column(String(60), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(10))
    year_of_birth: Mapped[int | None] = mapped_column(Integer)
    preferred_language: Mapped[str] = mapped_column(String(8), default="hi")
    created_at: Mapped[datetime] = created()


class Encounter(Base):
    __tablename__ = "encounter"

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    patient_id: Mapped[str] = fk("patient.id")
    department: Mapped[str] = mapped_column(String(60), default="general_opd")
    token_number: Mapped[str | None] = mapped_column(String(30))
    care_system: Mapped[str] = mapped_column(String(20),
                                             default=CARE_ALLOPATHIC)
    status: Mapped[str] = mapped_column(String(20), default="open")
    arrived_at: Mapped[datetime] = created()
    consulted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))


class Consent(Base):
    """Granular, revocable, audio-explained. Four independent scopes.

    Refusal carries no penalty: the patient proceeds to a normal consultation
    and the refusal itself is recorded.
    """
    __tablename__ = "consent"

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    encounter_id: Mapped[str] = fk("encounter.id")
    scope_interview: Mapped[bool] = mapped_column(Boolean, default=False)
    scope_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    scope_abdm_share: Mapped[bool] = mapped_column(Boolean, default=False)
    scope_audio_retention: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str] = mapped_column(String(8), default="hi")
    explained_via_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version: Mapped[str] = mapped_column(String(20), default="1.0")
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    device_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = created()

    @property
    def is_active(self) -> bool:
        return self.granted_at is not None and self.revoked_at is None


class IntakeSession(Base):
    __tablename__ = "intake_session"
    __table_args__ = (UniqueConstraint("tenant_id", "encounter_id"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    encounter_id: Mapped[str] = fk("encounter.id")
    questionnaire_id: Mapped[str] = fk("questionnaire.id")
    questionnaire_version: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="hi")
    # kiosk | patient_phone | staff_assisted
    channel: Mapped[str] = mapped_column(String(20), default="kiosk")
    device_id: Mapped[str | None] = mapped_column(String(80))
    # in_progress | ready_for_physician | escalated | abandoned | completed
    status: Mapped[str] = mapped_column(String(30), default="in_progress")
    completeness_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_offline: Mapped[bool] = mapped_column(Boolean, default=False)
    sync_version: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = created()
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Versioned clinical protocol -- questionnaires and rules are DATA.
# A clinical committee changes protocol without a code deployment, and any
# historical session can be explained by the exact version that governed it.
# ---------------------------------------------------------------------------

class Questionnaire(Base):
    __tablename__ = "questionnaire"
    __table_args__ = (UniqueConstraint("code", "version"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    care_system: Mapped[str] = mapped_column(String(20),
                                             default=CARE_ALLOPATHIC)
    department: Mapped[str | None] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer, default=1)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()

    questions: Mapped[list["Question"]] = relationship(
        back_populates="questionnaire", cascade="all, delete-orphan",
        order_by="Question.display_order")


class Question(Base):
    __tablename__ = "question"
    __table_args__ = (UniqueConstraint("questionnaire_id", "field_code"),)

    id: Mapped[str] = pk()
    questionnaire_id: Mapped[str] = fk("questionnaire.id")
    section: Mapped[str] = mapped_column(String(40), nullable=False)
    field_code: Mapped[str] = mapped_column(String(80), nullable=False)
    clinical_concept: Mapped[str | None] = mapped_column(String(80))
    prompt_key: Mapped[str] = mapped_column(String(120), nullable=False)
    # single | multi | scale | duration | numeric | free_text
    answer_type: Mapped[str] = mapped_column(String(20), default="single")
    options: Mapped[list | None] = mapped_column(JSON)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    # deterministic applicability condition, same grammar as red-flag rules
    dependency_rule: Mapped[dict | None] = mapped_column(JSON)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    fact_category: Mapped[str | None] = mapped_column(String(40))

    questionnaire: Mapped[Questionnaire] = relationship(
        back_populates="questions")


class RedFlagRule(Base):
    __tablename__ = "red_flag_rule"
    __table_args__ = (UniqueConstraint("rule_code", "version"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rule_code: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    severity: Mapped[str] = mapped_column(String(15), default="high")
    condition: Mapped[dict] = mapped_column(JSON, nullable=False)
    alert_message_key: Mapped[str] = mapped_column(String(120))
    sla_seconds: Mapped[int] = mapped_column(Integer, default=300)
    escalation_tier: Mapped[int] = mapped_column(Integer, default=1)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------------------
# Capture -- raw observations of input
# ---------------------------------------------------------------------------

class Answer(Base):
    __tablename__ = "answer"
    __table_args__ = (UniqueConstraint("tenant_id", "session_id", "field_code"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    session_id: Mapped[str] = fk("intake_session.id")
    question_id: Mapped[str | None] = fk("question.id", nullable=True)
    field_code: Mapped[str] = mapped_column(String(80), nullable=False)
    input_mode: Mapped[str] = mapped_column(String(10), default="touch")
    raw_transcript: Mapped[str | None] = mapped_column(Text)
    value_raw: Mapped[str | None] = mapped_column(Text)
    value_normalized: Mapped[str | None] = mapped_column(Text)
    selected_options: Mapped[list | None] = mapped_column(JSON)
    language_detected: Mapped[str | None] = mapped_column(String(8))
    asr_confidence: Mapped[float | None] = mapped_column(Float)
    nlu_confidence: Mapped[float | None] = mapped_column(Float)
    confirmed_by_patient: Mapped[bool] = mapped_column(Boolean, default=False)
    audio_object_ref: Mapped[str | None] = mapped_column(String(255))
    skipped_reason: Mapped[str | None] = mapped_column(String(30))
    sequence_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created()

    @property
    def effective_confidence(self) -> float:
        """Touch input is certain by construction: the patient chose it."""
        if self.input_mode == "touch":
            return 1.0
        vals = [v for v in (self.asr_confidence, self.nlu_confidence)
                if v is not None]
        return min(vals) if vals else 0.0


class Document(Base):
    __tablename__ = "document"

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    session_id: Mapped[str] = fk("intake_session.id")
    # prescription | lab_report | discharge_summary | imaging | unknown
    document_type: Mapped[str] = mapped_column(String(30), default="unknown")
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    document_date: Mapped[date | None] = mapped_column(Date)
    # pending | passed | rejected  (quality check runs client-side at capture)
    quality_status: Mapped[str] = mapped_column(String(15), default="pending")
    quality_reason: Mapped[str | None] = mapped_column(String(30))
    # queued | ocr_running | extracted | needs_verification | failed
    processing_status: Mapped[str] = mapped_column(String(25), default="queued")
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    uploaded_at: Mapped[datetime] = created()
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))


class DocumentPage(Base):
    __tablename__ = "document_page"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    document_id: Mapped[str] = fk("document.id")
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    image_object_ref: Mapped[str] = mapped_column(String(255))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    layout_json: Mapped[dict | None] = mapped_column(JSON)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    ocr_model_version: Mapped[str | None] = mapped_column(String(60))


class ExtractedEntity(Base):
    __tablename__ = "extracted_entity"

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    document_page_id: Mapped[str] = fk("document_page.id")
    # diagnosis | medication | lab_value | procedure | document_date
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    text_raw: Mapped[str] = mapped_column(Text, nullable=False)
    value_normalized: Mapped[str | None] = mapped_column(String(200))
    unit_normalized: Mapped[str | None] = mapped_column(String(40))
    reference_range: Mapped[str | None] = mapped_column(String(80))
    is_abnormal: Mapped[bool | None] = mapped_column(Boolean)
    region_bbox: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(30),
                                                   default="ner_model")
    model_version: Mapped[str | None] = mapped_column(String(60))
    # auto | pending_human | human_verified | rejected
    verification_status: Mapped[str] = mapped_column(String(20), default="auto")
    entity_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = created()


# ---------------------------------------------------------------------------
# Clinical fact store -- the record of truth
# ---------------------------------------------------------------------------

class ClinicalFact(Base):
    __tablename__ = "clinical_fact"
    __table_args__ = (
        Index("ix_fact_session_cat", "session_id", "category"),
        Index("ix_fact_conflict", "conflict_group_id"),
    )

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    session_id: Mapped[str] = fk("intake_session.id")
    patient_id: Mapped[str] = fk("patient.id")
    # symptom | diagnosis | medication | allergy | lab_value | procedure
    # | family_history | personal_history | ayush_parameter
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    field_code: Mapped[str | None] = mapped_column(String(80), index=True)
    clinical_concept: Mapped[str | None] = mapped_column(String(80),
                                                          index=True)
    coding_system: Mapped[str | None] = mapped_column(String(30))
    coding_code: Mapped[str | None] = mapped_column(String(40))
    label: Mapped[str | None] = mapped_column(String(200))
    value_raw: Mapped[str | None] = mapped_column(Text)
    value_normalized: Mapped[str | None] = mapped_column(String(200))
    unit: Mapped[str | None] = mapped_column(String(40))
    effective_date: Mapped[date | None] = mapped_column(Date)
    date_precision: Mapped[str] = mapped_column(String(10), default="day")
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_type: Mapped[str] = mapped_column(String(15), nullable=False)
    source_answer_id: Mapped[str | None] = fk("answer.id", nullable=True)
    source_entity_id: Mapped[str | None] = fk("extracted_entity.id",
                                              nullable=True)
    is_conflicting: Mapped[bool] = mapped_column(Boolean, default=False)
    conflict_group_id: Mapped[str | None] = mapped_column(String(36))
    # auto | unconfirmed | human_verified
    verification_status: Mapped[str] = mapped_column(String(20), default="auto")
    # unreviewed | accepted | edited | rejected
    physician_status: Mapped[str] = mapped_column(String(15),
                                                  default="unreviewed")
    created_at: Mapped[datetime] = created()

    provenance: Mapped["Provenance"] = relationship(
        back_populates="fact", uselist=False, cascade="all, delete-orphan")


class Provenance(Base):
    """One row per fact. Without this the summary is indefensible."""
    __tablename__ = "provenance"
    __table_args__ = (UniqueConstraint("clinical_fact_id"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    clinical_fact_id: Mapped[str] = fk("clinical_fact.id")
    source_type: Mapped[str] = mapped_column(String(15), nullable=False)
    source_document_id: Mapped[str | None] = mapped_column(String(36))
    source_page_number: Mapped[int | None] = mapped_column(Integer)
    source_region_bbox: Mapped[dict | None] = mapped_column(JSON)
    extraction_method: Mapped[str] = mapped_column(String(30), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(60))
    model_version: Mapped[str | None] = mapped_column(String(60))
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=utcnow)
    device_id: Mapped[str | None] = mapped_column(String(80))

    fact: Mapped[ClinicalFact] = relationship(back_populates="provenance")


class VerificationItem(Base):
    """Where low-confidence perception goes instead of into the record."""
    __tablename__ = "verification_item"

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    session_id: Mapped[str] = fk("intake_session.id")
    origin: Mapped[str] = mapped_column(String(15), nullable=False)
    source_answer_id: Mapped[str | None] = mapped_column(String(36))
    source_entity_id: Mapped[str | None] = mapped_column(String(36))
    field_code: Mapped[str | None] = mapped_column(String(80))
    candidate_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    # pending | resolved | discarded
    status: Mapped[str] = mapped_column(String(15), default="pending")
    resolved_value: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = created()


class TimelineEvent(Base):
    __tablename__ = "timeline_event"

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    patient_id: Mapped[str] = fk("patient.id")
    session_id: Mapped[str] = fk("intake_session.id")
    clinical_fact_id: Mapped[str] = fk("clinical_fact.id")
    event_date: Mapped[date | None] = mapped_column(Date)
    # day | month | year | inferred | undated
    date_precision: Mapped[str] = mapped_column(String(10), default="day")
    event_category: Mapped[str] = mapped_column(String(40))
    display_label: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = created()


class RedFlag(Base):
    __tablename__ = "red_flag"

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    session_id: Mapped[str] = fk("intake_session.id")
    rule_id: Mapped[str | None] = mapped_column(String(36))
    rule_code: Mapped[str] = mapped_column(String(60), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, default=1)
    severity: Mapped[str] = mapped_column(String(15), default="high")
    triggering_facts: Mapped[dict] = mapped_column(JSON)
    message_key: Mapped[str | None] = mapped_column(String(120))
    # created | staff_notified | auto_escalated | acknowledged | resolved
    status: Mapped[str] = mapped_column(String(20), default="created")
    sla_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(36))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class RuleEvaluation(Base):
    """Negative evaluations are logged too.

    Without recording non-firing evaluations it is impossible to ask, for any
    past session, whether a rule *should* have fired. That question is the
    basis of retrospective sensitivity analysis.
    """
    __tablename__ = "rule_evaluation"

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    session_id: Mapped[str] = fk("intake_session.id")
    rule_code: Mapped[str] = mapped_column(String(60), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, default=1)
    fired: Mapped[bool] = mapped_column(Boolean, default=False)
    errored: Mapped[bool] = mapped_column(Boolean, default=False)
    fact_set_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created()


# ---------------------------------------------------------------------------
# Summary, citations, physician review
# ---------------------------------------------------------------------------

class Summary(Base):
    __tablename__ = "summary"
    __table_args__ = (UniqueConstraint("session_id", "version"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    session_id: Mapped[str] = fk("intake_session.id")
    version: Mapped[int] = mapped_column(Integer, default=1)
    # draft | under_review | edited | clarification_requested
    # | approved | rejected | exported
    status: Mapped[str] = mapped_column(String(30), default="draft")
    # template_only | llm_constrained
    generation_mode: Mapped[str] = mapped_column(String(20),
                                                  default="template_only")
    llm_model_version: Mapped[str | None] = mapped_column(String(60))
    grounding_pass_rate: Mapped[float] = mapped_column(Float, default=100.0)
    body_structured: Mapped[dict] = mapped_column(JSON)
    pending_documents: Mapped[int] = mapped_column(Integer, default=0)
    interaction_check_performed: Mapped[bool] = mapped_column(Boolean,
                                                              default=False)
    generated_at: Mapped[datetime] = created()

    sentences: Mapped[list["SummarySentence"]] = relationship(
        back_populates="summary", cascade="all, delete-orphan",
        order_by="SummarySentence.sentence_order")


class SummarySentence(Base):
    __tablename__ = "summary_sentence"

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    summary_id: Mapped[str] = fk("summary.id")
    section: Mapped[str] = mapped_column(String(40), nullable=False)
    sentence_order: Mapped[int] = mapped_column(Integer, default=0)
    sentence_text: Mapped[str] = mapped_column(Text, nullable=False)
    grounded: Mapped[bool] = mapped_column(Boolean, default=True)

    summary: Mapped[Summary] = relationship(back_populates="sentences")
    citations: Mapped[list["SummaryCitation"]] = relationship(
        back_populates="sentence", cascade="all, delete-orphan")


class SummaryCitation(Base):
    __tablename__ = "summary_citation"
    __table_args__ = (UniqueConstraint("summary_sentence_id",
                                        "clinical_fact_id"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    summary_sentence_id: Mapped[str] = fk("summary_sentence.id")
    clinical_fact_id: Mapped[str] = fk("clinical_fact.id")

    sentence: Mapped[SummarySentence] = relationship(
        back_populates="citations")


class PhysicianReview(Base):
    __tablename__ = "physician_review"

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    summary_id: Mapped[str] = fk("summary.id")
    session_id: Mapped[str] = fk("intake_session.id")
    physician_id: Mapped[str] = fk("app_user.id")
    # opened | edited_fact | rejected_fact | requested_clarification
    # | approved | rejected
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    target_fact_id: Mapped[str | None] = mapped_column(String(36))
    previous_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    acted_at: Mapped[datetime] = created()


class FinalClinicalRecord(Base):
    __tablename__ = "final_clinical_record"
    __table_args__ = (UniqueConstraint("session_id"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    session_id: Mapped[str] = fk("intake_session.id")
    summary_id: Mapped[str] = fk("summary.id")
    approved_by: Mapped[str] = fk("app_user.id")
    approved_at: Mapped[datetime] = created()
    attestation: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    fhir_bundle: Mapped[dict | None] = mapped_column(JSON)


# ---------------------------------------------------------------------------
# Audit, integration outbox, offline sync
# ---------------------------------------------------------------------------

class AuditEvent(Base):
    """Append-only with hash chaining.

    Hash chaining gives tamper evidence without the operational cost of a
    distributed ledger -- the honest alternative to a blockchain claim.
    """
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                     autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    occurred_at: Mapped[datetime] = created()
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[dict | None] = mapped_column(JSON)
    device_id: Mapped[str | None] = mapped_column(String(80))
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class IntegrationEvent(Base):
    """Transactional outbox: export can never block the clinical transaction."""
    __tablename__ = "integration_event"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    session_id: Mapped[str | None] = mapped_column(String(36))
    target_system: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    # pending | in_flight | delivered | failed | dead_lettered
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class SyncOperation(Base):
    __tablename__ = "sync_operation"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = fk("tenant.id")
    session_id: Mapped[str | None] = mapped_column(String(36))
    device_id: Mapped[str] = mapped_column(String(80), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    result: Mapped[dict | None] = mapped_column(JSON)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()
