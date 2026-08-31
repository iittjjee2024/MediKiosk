"""Deterministic Red-Flag Engine.

Design commitments, each of which is a deliberate trade rather than an
oversight:

* Rules, not a model. A missed emergency is the worst outcome in the system.
  A rule set is testable, reviewable by a clinician in a meeting, and
  explainable for one specific patient at one specific moment. A probabilistic
  classifier is none of those things at the point of that patient's alert.

* Sensitivity over specificity. A false alert costs a nurse a brief
  assessment; a missed alert can cost a life. The trade is stated here rather
  than hidden inside a threshold.

* Fail-safe, not fail-silent. A malformed rule or an evaluator bug raises an
  alert for human assessment. A rule-engine exception must never have the
  effect of suppressing a potential emergency.

* Every evaluation is recorded, including non-firing ones. Without negative
  logging it is impossible to ask, for any past session, whether a rule should
  have fired -- and that question is the whole basis of retrospective
  sensitivity analysis.

* The engine alerts; it never reorders the queue. Triage is a clinical act
  performed by a human. This code surfaces information and tracks whether it
  was acted upon.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..config import settings
from ..db import utcnow
from ..models import RedFlagRule
from .rules import RuleError, SessionState, evaluate

SEVERITY_RANK = {"critical": 0, "high": 1, "moderate": 2, "low": 3}


@dataclass(frozen=True)
class Evaluation:
    rule_code: str
    rule_version: int
    severity: str
    fired: bool
    errored: bool
    refs: tuple[str, ...]
    message_key: str | None
    sla_seconds: int
    escalation_tier: int
    rule_id: str | None = None
    error_detail: str | None = None

    @property
    def sla_deadline(self):
        return utcnow() + timedelta(seconds=self.sla_seconds)


def active_rules(rules: list[RedFlagRule],
                 on: date | None = None) -> list[RedFlagRule]:
    """Rules in force on a given date, in deterministic order.

    Ordering by (severity, rule_code, version) makes the returned list stable
    so alert creation order never depends on database row order.
    """
    when = on or date.today()
    live = [
        r for r in rules
        if r.is_active
        and r.effective_from <= when
        and (r.effective_to is None or r.effective_to >= when)
    ]
    return sorted(live, key=lambda r: (SEVERITY_RANK.get(r.severity, 9),
                                       r.rule_code, r.version))


def evaluate_rule(rule: RedFlagRule, state: SessionState) -> Evaluation:
    """Evaluate one rule. Never raises."""
    try:
        result = evaluate(rule.condition, state)
        return Evaluation(
            rule_code=rule.rule_code,
            rule_version=rule.version,
            severity=rule.severity,
            fired=result.satisfied,
            errored=False,
            refs=result.refs,
            message_key=rule.alert_message_key,
            sla_seconds=rule.sla_seconds or settings.default_red_flag_sla_seconds,
            escalation_tier=rule.escalation_tier,
            rule_id=rule.id,
        )
    except RuleError as exc:

        return Evaluation(
            rule_code=rule.rule_code,
            rule_version=rule.version,
            severity="high",
            fired=True,
            errored=True,
            refs=(),
            message_key="alert.rule_evaluation_error",
            sla_seconds=settings.default_red_flag_sla_seconds,
            escalation_tier=rule.escalation_tier,
            rule_id=rule.id,
            error_detail=str(exc),
        )
    except Exception as exc:
        return Evaluation(
            rule_code=rule.rule_code,
            rule_version=rule.version,
            severity="high",
            fired=True,
            errored=True,
            refs=(),
            message_key="alert.rule_evaluation_error",
            sla_seconds=settings.default_red_flag_sla_seconds,
            escalation_tier=rule.escalation_tier,
            rule_id=rule.id,
            error_detail=repr(exc),
        )


def evaluate_all(rules: list[RedFlagRule], state: SessionState,
                 on: date | None = None) -> list[Evaluation]:
    """Evaluate every active rule. Returns one Evaluation per rule.

    Non-firing evaluations are included by design -- the caller persists all of
    them so sensitivity can be analysed retrospectively.
    """
    return [evaluate_rule(r, state) for r in active_rules(rules, on)]


def fired(evaluations: list[Evaluation]) -> list[Evaluation]:
    """Fired alerts, most severe first."""
    return sorted((e for e in evaluations if e.fired),
                  key=lambda e: (SEVERITY_RANK.get(e.severity, 9), e.rule_code))
