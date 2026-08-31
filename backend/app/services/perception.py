"""Voice perception: raw transcript -> interpreted answer candidate.

Sits between ASR and the intake service. Given the transcript for the current
field, it uses the fitted clinical NLU to pick the best option code and a
calibrated confidence, then hands that to the ordinary answer-submission path
so the SAME confidence gate applies -- a weak interpretation is withheld and
sent to a human, never guessed.

Crucially, this component does not decide anything clinical. It only proposes a
(value, confidence). Whether that value is admitted is decided downstream by
the deterministic confidence gate, exactly as for any other perception source.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..nlu import get_model
from ..models import IntakeSession, Question, Questionnaire
from . import intake


class FieldNotAskable(ValueError):
    """The field is not part of the session's questionnaire."""


def _question(proto: Questionnaire, field_code: str) -> Question:
    for q in proto.questions:
        if q.field_code == field_code:
            return q
    raise FieldNotAskable(field_code)


def interpret_transcript(db: Session, session: IntakeSession, *,
                         field_code: str, transcript: str,
                         asr_confidence: float | None = None,
                         language: str | None = None) -> dict:
    """Interpret a transcript without committing anything. For preview/UX."""
    proto = intake.get_questionnaire(db, session)
    question = _question(proto, field_code)
    model = get_model()

    match = model.interpret(
        transcript, field_code=field_code,
        options=list(question.options or []),
        lang=language or session.language,
        answer_type=question.answer_type)

    # combined confidence: the ASR's own confidence bounds the NLU's, because a
    # perfect interpretation of a badly-heard phrase is still only as good as
    # the hearing. If ASR gives no score, the NLU confidence stands alone.
    nlu_conf = match.confidence
    combined = min(nlu_conf, asr_confidence) if asr_confidence is not None \
        else nlu_conf

    return {
        "field_code": field_code,
        "answer_type": question.answer_type,
        "interpreted_value": match.value,
        "matched_alias": match.matched_alias,
        "is_negation": match.is_negation,
        "nlu_confidence": round(nlu_conf, 3),
        "asr_confidence": asr_confidence,
        "combined_confidence": round(combined, 3),
        "model_version": match.as_dict()["model_version"],
        "no_match": match.value is None,
    }


def submit_transcript(db: Session, session: IntakeSession, *,
                      field_code: str, transcript: str,
                      asr_confidence: float | None = None,
                      language: str | None = None,
                      actor_id: str | None = None
                      ) -> tuple[dict, intake.SubmissionResult | None]:
    """Interpret, then submit through the normal gated answer path.

    If the NLU cannot map the transcript to any option, nothing is submitted --
    the caller should prompt the patient to confirm or tap instead. We never
    fabricate an option to force a submission through.
    """
    interp = interpret_transcript(
        db, session, field_code=field_code, transcript=transcript,
        asr_confidence=asr_confidence, language=language)

    if interp["no_match"] or interp["interpreted_value"] is None:
        return interp, None

    question = _question(intake.get_questionnaire(db, session), field_code)
    value = interp["interpreted_value"]

    kwargs = dict(
        field_code=field_code, input_mode="voice", raw_transcript=transcript,
        asr_confidence=asr_confidence, nlu_confidence=interp["nlu_confidence"],
        actor_id=actor_id)
    if question.answer_type == "multi":
        result = intake.submit_answer(db, session, selected_options=[value],
                                      **kwargs)
    else:
        result = intake.submit_answer(db, session, value=value, **kwargs)

    return interp, result
