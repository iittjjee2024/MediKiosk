"""Deterministic Question Engine.

Selects the next clinical field to ask. Contains no sampling, no model call
and no randomness of any kind: given the same SessionState and the same
questionnaire version, `next_question` returns the identical field every time.

Completeness is the share of *applicable* required fields that are answered.
Fields skipped because a dependency was unmet are excluded from the
denominator, so a patient with a simple presentation is not penalised for
having fewer applicable fields than a complex one. The score is explainable on
demand -- `completeness` can name every unanswered and every skipped field,
because "85% complete" is meaningless to a clinician who cannot see what the
missing 15% is.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import SECTION_ORDER, Question, Questionnaire
from .rules import RuleError, SessionState, evaluate


@dataclass(frozen=True)
class QuestionView:
    """A question rendered for the client.

    `prompt_key` is an i18n key, never literal text: the same protocol serves
    every language, and translation is a client concern.
    """
    field_code: str
    section: str
    prompt_key: str
    answer_type: str
    options: tuple[str, ...]
    clinical_concept: str | None
    is_required: bool
    display_order: int

    @classmethod
    def of(cls, q: Question) -> "QuestionView":
        return cls(
            field_code=q.field_code,
            section=q.section,
            prompt_key=q.prompt_key,
            answer_type=q.answer_type,
            options=tuple(q.options or ()),
            clinical_concept=q.clinical_concept,
            is_required=_required(q),
            display_order=int(q.display_order or 0),
        )


@dataclass(frozen=True)
class SkippedField:
    field_code: str
    section: str
    reason: str


@dataclass(frozen=True)
class Completeness:
    score: float
    applicable_required: int
    answered_required: int
    unanswered: tuple[str, ...]
    skipped: tuple[SkippedField, ...]

    def explain(self) -> str:
        if not self.unanswered:
            return (f"{self.score:.0f}% complete - all "
                    f"{self.applicable_required} applicable required fields "
                    f"answered.")
        return (f"{self.score:.0f}% complete - "
                f"{self.answered_required}/{self.applicable_required} "
                f"applicable required fields answered. Outstanding: "
                f"{', '.join(self.unanswered)}.")


def _section_rank(section: str) -> int:
    try:
        return SECTION_ORDER.index(section)
    except ValueError:
        return len(SECTION_ORDER)


def _required(question: Question) -> bool:
    """Required unless explicitly marked optional.

    SQLAlchemy column defaults only apply at INSERT, so an unattached Question
    carries None here. Treating None as required matches the column default and
    keeps in-memory behaviour identical to DB-loaded behaviour -- a question
    must never be silently dropped from the protocol because of how it was
    constructed.
    """
    return question.is_required is not False


def _ordered(questions: list[Question]) -> list[Question]:
    """Total order: section, then display_order, then field_code.

    field_code is the final tiebreaker so the order is total even if an author
    gives two questions the same display_order. Without it the engine would be
    at the mercy of database row order, and determinism would be accidental
    rather than guaranteed.
    """
    return sorted(questions,
                  key=lambda q: (_section_rank(q.section),
                                 int(q.display_order or 0),
                                 q.field_code))


def is_applicable(question: Question, state: SessionState) -> tuple[bool, str | None]:
    """Whether a question applies given current state.

    A malformed dependency rule makes the field applicable (we ask the patient)
    rather than silently dropping it. Losing a clinical question to an
    authoring typo is worse than asking one extra question.
    """
    try:
        if evaluate(question.dependency_rule, state).satisfied:
            return True, None
        return False, "dependency_unmet"
    except RuleError:
        return True, None


def next_question(questionnaire: Questionnaire,
                  state: SessionState) -> QuestionView | None:
    """First applicable, required, unanswered field in protocol order.

    Returns None when the questionnaire is complete.
    """
    for q in _ordered(list(questionnaire.questions)):
        if not _required(q):
            continue
        ans = state.answers.get(q.field_code)
        if ans is not None and (ans.is_answered or ans.skipped_reason):
            continue
        applicable, _ = is_applicable(q, state)
        if applicable:
            return QuestionView.of(q)
    return None


def remaining_questions(questionnaire: Questionnaire,
                        state: SessionState) -> tuple[QuestionView, ...]:
    """Every still-askable field, in the exact order it will be asked."""
    out: list[QuestionView] = []
    for q in _ordered(list(questionnaire.questions)):
        if not _required(q):
            continue
        ans = state.answers.get(q.field_code)
        if ans is not None and (ans.is_answered or ans.skipped_reason):
            continue
        if is_applicable(q, state)[0]:
            out.append(QuestionView.of(q))
    return tuple(out)


def completeness(questionnaire: Questionnaire,
                 state: SessionState) -> Completeness:
    applicable = 0
    answered = 0
    unanswered: list[str] = []
    skipped: list[SkippedField] = []

    for q in _ordered(list(questionnaire.questions)):
        ans = state.answers.get(q.field_code)

        ok, reason = is_applicable(q, state)
        if not ok:
            skipped.append(SkippedField(q.field_code, q.section,
                                        reason or "dependency_unmet"))
            continue

        if ans is not None and ans.skipped_reason:

            skipped.append(SkippedField(q.field_code, q.section,
                                        ans.skipped_reason))
            continue

        if not _required(q):
            continue

        applicable += 1
        if ans is not None and ans.is_answered:
            answered += 1
        else:
            unanswered.append(q.field_code)

    score = 100.0 if applicable == 0 else round(answered / applicable * 100, 2)
    return Completeness(score=score,
                        applicable_required=applicable,
                        answered_required=answered,
                        unanswered=tuple(unanswered),
                        skipped=tuple(skipped))


def section_progress(questionnaire: Questionnaire,
                     state: SessionState) -> dict[str, dict[str, int]]:
    """Per-section answered/applicable counts, for the client progress bar."""
    out: dict[str, dict[str, int]] = {}
    for q in _ordered(list(questionnaire.questions)):
        if not _required(q):
            continue
        if not is_applicable(q, state)[0]:
            continue
        bucket = out.setdefault(q.section, {"answered": 0, "applicable": 0})
        ans = state.answers.get(q.field_code)
        if ans is not None and ans.skipped_reason:
            continue
        bucket["applicable"] += 1
        if ans is not None and ans.is_answered:
            bucket["answered"] += 1
    return out
