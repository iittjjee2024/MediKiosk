"""Deterministic condition evaluator.

One grammar serves two purposes: question dependency rules ("is this field
applicable?") and red-flag rule conditions ("should an alert fire?"). Using a
single evaluator means both are testable the same way and a clinician reads
one syntax, not two.

Grammar
-------
    {"all":  [<cond>, ...]}                 every child must hold
    {"any":  [<cond>, ...]}                 at least one child must hold
    {"not":  <cond>}                        negation
    {"fact": {"category": str?,             a matching admitted fact exists
              "concept":  str?,
              "field":    str?,
              "value":    str|[str]?}}
    {"slot": {"field": str,                 an answer on a field
              "eq":  str        |
              "in":  [str, ...] |
              "gte": number     |
              "lte": number     |
              "exists": bool}}
    {"answered": str}                       field has a non-skipped answer

Every leaf that holds contributes a reference (fact id or "answer:<field>") so
a fired red flag can record exactly what satisfied it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable, Sequence


class RuleError(ValueError):
    """Malformed condition. Callers decide the fail posture.

    The red-flag engine treats this as fail-safe (raise an alert for a human)
    rather than fail-silent, because a rule-engine bug must never have the
    effect of suppressing a potential emergency.
    """


@dataclass(frozen=True)
class FactRow:
    """Read-model of one admitted clinical fact."""
    id: str
    category: str
    concept: str | None = None
    field_code: str | None = None
    value_normalized: str | None = None
    numeric_value: float | None = None

    def matches(self, spec: dict) -> bool:
        if (c := spec.get("category")) is not None and self.category != c:
            return False
        if (c := spec.get("concept")) is not None and self.concept != c:
            return False
        if (f := spec.get("field")) is not None and self.field_code != f:
            return False
        if (v := spec.get("value")) is not None:
            allowed = v if isinstance(v, (list, tuple, set)) else [v]
            if self.value_normalized not in allowed:
                return False
        return True


@dataclass(frozen=True)
class AnswerRow:
    """Read-model of one captured answer."""
    field_code: str
    value_normalized: str | None = None
    selected_options: tuple[str, ...] = ()
    numeric_value: float | None = None
    skipped_reason: str | None = None

    @property
    def is_answered(self) -> bool:
        if self.skipped_reason:
            return False
        return bool(self.value_normalized) or bool(self.selected_options) \
            or self.numeric_value is not None

    @property
    def values(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.value_normalized:
            out.append(self.value_normalized)
        out.extend(self.selected_options)
        return tuple(out)


@dataclass
class SessionState:
    """Everything the deterministic engines are allowed to see.

    Deliberately narrow: no raw transcripts, no raw OCR text, no model
    internals. The engines reason over admitted facts and captured answers
    only, which is what keeps them explainable.
    """
    facts: tuple[FactRow, ...] = ()
    answers: dict[str, AnswerRow] = dc_field(default_factory=dict)

    @classmethod
    def build(cls, facts: Iterable[FactRow],
              answers: Iterable[AnswerRow]) -> "SessionState":
        return cls(facts=tuple(facts),
                   answers={a.field_code: a for a in answers})

    def fingerprint(self) -> str:
        """Stable hash of the state, for determinism tests and audit."""
        payload = {
            "facts": sorted(
                [f.category, f.concept or "", f.field_code or "",
                 f.value_normalized or ""] for f in self.facts),
            "answers": sorted(
                [k, v.value_normalized or "", *sorted(v.selected_options),
                 v.skipped_reason or ""]
                for k, v in self.answers.items()),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class MatchResult:
    satisfied: bool
    refs: tuple[str, ...] = ()

    def __bool__(self) -> bool:      # pragma: no cover - convenience
        return self.satisfied


_LEAF_KEYS = {"fact", "slot", "answered"}
_NODE_KEYS = {"all", "any", "not"} | _LEAF_KEYS


def _as_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _eval_fact(spec: Any, state: SessionState) -> MatchResult:
    if not isinstance(spec, dict):
        raise RuleError(f"'fact' expects an object, got {type(spec).__name__}")
    hits = tuple(f.id for f in state.facts if f.matches(spec))
    return MatchResult(bool(hits), hits)


def _eval_slot(spec: Any, state: SessionState) -> MatchResult:
    if not isinstance(spec, dict) or "field" not in spec:
        raise RuleError("'slot' requires a 'field'")
    fieldc = spec["field"]
    ans = state.answers.get(fieldc)
    ref = (f"answer:{fieldc}",)

    # Validate the operator BEFORE any early return. Otherwise a malformed rule
    # silently evaluates to False whenever the field happens to be unanswered,
    # and an authoring error would surface only for some patients.
    operators = {"exists", "eq", "in", "gte", "lte"} & set(spec)
    if not operators:
        raise RuleError(f"'slot' has no comparison operator: {sorted(spec)}")
    if len(operators) > 1:
        raise RuleError(f"'slot' has conflicting operators: {sorted(operators)}")

    if "exists" in spec:
        want = bool(spec["exists"])
        got = ans is not None and ans.is_answered
        return MatchResult(got == want, ref if got else ())

    if ans is None or not ans.is_answered:
        return MatchResult(False)

    if "eq" in spec:
        return MatchResult(spec["eq"] in ans.values,
                           ref if spec["eq"] in ans.values else ())
    if "in" in spec:
        allowed = spec["in"]
        if not isinstance(allowed, (list, tuple, set)):
            raise RuleError("'slot.in' expects a list")
        hit = any(v in allowed for v in ans.values)
        return MatchResult(hit, ref if hit else ())
    for op, cmp in (("gte", lambda a, b: a >= b), ("lte", lambda a, b: a <= b)):
        if op in spec:
            bound = _as_number(spec[op])
            actual = ans.numeric_value
            if actual is None:
                actual = next((n for n in (_as_number(v) for v in ans.values)
                               if n is not None), None)
            if bound is None or actual is None:
                return MatchResult(False)
            hit = cmp(actual, bound)
            return MatchResult(hit, ref if hit else ())

    raise RuleError(f"'slot' has no comparison operator: {sorted(spec)}")  # pragma: no cover


def _eval_answered(spec: Any, state: SessionState) -> MatchResult:
    if not isinstance(spec, str):
        raise RuleError("'answered' expects a field code string")
    ans = state.answers.get(spec)
    hit = ans is not None and ans.is_answered
    return MatchResult(hit, (f"answer:{spec}",) if hit else ())


def evaluate(condition: Any, state: SessionState) -> MatchResult:
    """Evaluate a condition tree against session state.

    An empty or absent condition is vacuously true: a question with no
    dependency rule is always applicable.
    """
    if condition is None or condition == {}:
        return MatchResult(True)
    if not isinstance(condition, dict):
        raise RuleError(f"condition must be an object, got "
                        f"{type(condition).__name__}")

    unknown = set(condition) - _NODE_KEYS
    if unknown:
        raise RuleError(f"unknown condition keys: {sorted(unknown)}")
    if len(condition) != 1:
        raise RuleError("condition object must hold exactly one operator, "
                        f"got {sorted(condition)}")

    (op, spec), = condition.items()

    if op == "all":
        if not isinstance(spec, Sequence) or isinstance(spec, (str, bytes)):
            raise RuleError("'all' expects a list")
        refs: list[str] = []
        for child in spec:
            res = evaluate(child, state)
            if not res.satisfied:
                return MatchResult(False)
            refs.extend(res.refs)
        return MatchResult(True, tuple(dict.fromkeys(refs)))

    if op == "any":
        if not isinstance(spec, Sequence) or isinstance(spec, (str, bytes)):
            raise RuleError("'any' expects a list")
        refs = []
        satisfied = False
        for child in spec:
            res = evaluate(child, state)
            if res.satisfied:
                satisfied = True
                refs.extend(res.refs)
        return MatchResult(satisfied, tuple(dict.fromkeys(refs)))

    if op == "not":
        return MatchResult(not evaluate(spec, state).satisfied)

    if op == "fact":
        return _eval_fact(spec, state)
    if op == "slot":
        return _eval_slot(spec, state)
    return _eval_answered(spec, state)


def validate_condition(condition: Any) -> None:
    """Raise RuleError if a condition is malformed.

    Called when protocol is published so a bad rule is rejected at authoring
    time rather than discovered during a patient's interview.
    """
    evaluate(condition, SessionState())
