"""Request/response contracts.

Strict validation at the boundary is the cheapest defence against malformed
clinical data entering the fact store.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------- identity ------

class IdentifyIn(Strict):
    abha_id: str | None = Field(default=None, max_length=20)
    hospital_local_id: str | None = Field(default=None, max_length=60)
    display_name: str | None = Field(default=None, max_length=200)
    gender: str | None = Field(default=None, pattern="^(male|female|other|unknown)$")
    year_of_birth: int | None = Field(default=None, ge=1900, le=2026)
    department: str = Field(default="general_opd", max_length=60)
    care_system: str = Field(default="allopathic",
                             pattern="^(allopathic|ayush)$")
    language: str = Field(default="hi", max_length=8)
    channel: str = Field(default="kiosk",
                         pattern="^(kiosk|patient_phone|staff_assisted)$")
    device_id: str | None = Field(default=None, max_length=80)


class IdentifyOut(Strict):
    token: str
    patient_id: str
    encounter_id: str
    identity_source: str
    consent_required: bool = True


class ConsentIn(Strict):
    scope_interview: bool = False
    scope_documents: bool = False
    scope_abdm_share: bool = False
    scope_audio_retention: bool = False
    explained_via_audio: bool = True
    language: str = Field(default="hi", max_length=8)


class ConsentOut(Strict):
    consent_id: str
    granted: bool
    scopes: dict
    session_id: str | None = None
    message: str


# ------------------------------------------------------------- interview -----

class QuestionOut(Strict):
    field_code: str
    section: str
    prompt_key: str
    answer_type: str
    options: list[str]
    clinical_concept: str | None = None


class AnswerIn(Strict):
    field_code: str = Field(max_length=80)
    value: str | None = Field(default=None, max_length=500)
    selected_options: list[str] | None = None
    input_mode: str = Field(default="touch", pattern="^(touch|voice)$")
    raw_transcript: str | None = Field(default=None, max_length=2000)
    asr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    nlu_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    skipped_reason: str | None = Field(
        default=None, pattern="^(not_applicable|declined)$")


class AlertOut(Strict):
    id: str
    rule_code: str
    rule_version: int
    severity: str
    message_key: str | None
    status: str
    sla_deadline: str | None


# ------------------------------------------------------------- perception ----

class VoiceIn(Strict):
    field_code: str = Field(max_length=80)
    transcript: str = Field(max_length=2000)
    asr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    language: str | None = Field(default=None, max_length=8)
    commit: bool = True


class DocumentIn(Strict):
    # Name a built-in fixture, OR supply text + entities (real OCR output).
    document_kind: str | None = Field(
        default=None, pattern="^(lab_report|prescription|discharge_summary)$")
    ocr_text: str | None = Field(default=None, max_length=8000)
    document_type: str = Field(default="unknown", max_length=30)
    ocr_confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class CompletenessOut(Strict):
    score: float
    applicable_required: int
    answered_required: int
    unanswered: list[str]
    skipped: list[dict]
    explanation: str


class AnswerOut(Strict):
    field_code: str
    confidence_band: str
    fact_admitted: bool
    disposition: str
    confirm_back_required: bool
    admitted_fact_ids: list[str]
    verification_item_ids: list[str]
    superseded_previous: bool
    red_flag_fired: bool
    alerts: list[AlertOut]
    completeness: CompletenessOut
    next_question: QuestionOut | None


class FinaliseOut(Strict):
    session_id: str
    status: str
    completeness: CompletenessOut
    summary_id: str
    grounding_pass_rate: float


# ------------------------------------------------------------- physician -----

class LoginIn(Strict):
    username: str = Field(max_length=80)
    password: str = Field(max_length=200)


class LoginOut(Strict):
    token: str
    user_id: str
    full_name: str
    role: str
    department: str | None


class EditFactIn(Strict):
    fact_id: str
    new_value: str = Field(max_length=500)


class RejectFactIn(Strict):
    fact_id: str
    note: str | None = Field(default=None, max_length=1000)


class ClarifyIn(Strict):
    note: str = Field(max_length=1000)


class ApproveIn(Strict):
    attestation: str = Field(
        default=("I have reviewed this history and confirm it reflects the "
                 "patient encounter."),
        max_length=1000)
    unresolved_conflicts_acknowledged: bool = False


class ApproveOut(Strict):
    final_clinical_record_id: str
    summary_version: int
    content_hash: str
    facts: dict
    integration: dict


class AcknowledgeIn(Strict):
    note: str | None = Field(default=None, max_length=1000)
