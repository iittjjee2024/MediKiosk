"""Tests for the deterministic engines.

The determinism test is the one that protects the central architectural claim:
if question selection or red-flag firing ever varies for identical input, the
instrument is not reproducible and therefore not clinically validatable.
"""
from __future__ import annotations

import random
from datetime import date

import pytest

from app.engines import confidence, conflict, question_engine, redflag_engine
from app.engines import summary_builder as sb
from app.engines import timeline as tl
from app.engines.rules import (AnswerRow, FactRow, RuleError, SessionState,
                               evaluate)
from app.models import ClinicalFact
from app.seed import (PRAKRITI_WEIGHTS, allopathic_questionnaire,
                      ayush_questionnaire, red_flag_rules)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def state(**answers) -> SessionState:
    """Build session state from field_code=value kwargs (dots as __)."""
    rows = []
    for key, val in answers.items():
        field = key.replace("__", ".")
        if isinstance(val, (list, tuple)):
            rows.append(AnswerRow(field_code=field,
                                  selected_options=tuple(val)))
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            rows.append(AnswerRow(field_code=field,
                                  value_normalized=str(val),
                                  numeric_value=float(val)))
        else:
            rows.append(AnswerRow(field_code=field, value_normalized=val))
    return SessionState.build([], rows)


def fact(fid, category, *, concept=None, field_code=None, value=None,
         source="answer", eff=None, precision="day", label=None,
         status="unreviewed", verification="auto") -> ClinicalFact:
    return ClinicalFact(
        id=fid, tenant_id="t", session_id="s", patient_id="p",
        category=category, clinical_concept=concept, field_code=field_code,
        value_normalized=value, label=label, confidence=0.95,
        source_type=source, effective_date=eff, date_precision=precision,
        physician_status=status, verification_status=verification)


# ---------------------------------------------------------------------------
# rule evaluator grammar
# ---------------------------------------------------------------------------

class TestRuleEvaluator:
    def test_empty_condition_is_vacuously_true(self):
        assert evaluate(None, SessionState()).satisfied
        assert evaluate({}, SessionState()).satisfied

    def test_slot_eq_in_gte_lte(self):
        s = state(hpi__severity=8, chief_complaint__primary="chest_pain")
        assert evaluate({"slot": {"field": "chief_complaint.primary",
                                  "eq": "chest_pain"}}, s).satisfied
        assert evaluate({"slot": {"field": "hpi.severity", "gte": 8}},
                        s).satisfied
        assert not evaluate({"slot": {"field": "hpi.severity", "gte": 9}},
                            s).satisfied
        assert evaluate({"slot": {"field": "hpi.severity", "lte": 8}},
                        s).satisfied

    def test_slot_in_matches_multi_select(self):
        s = state(hpi__associated=["nausea", "dyspnoea"])
        assert evaluate({"slot": {"field": "hpi.associated",
                                  "in": ["dyspnoea"]}}, s).satisfied
        assert not evaluate({"slot": {"field": "hpi.associated",
                                      "in": ["palpitations"]}}, s).satisfied

    def test_exists_distinguishes_skipped_from_answered(self):
        answered = SessionState.build([], [AnswerRow("f", value_normalized="x")])
        skipped = SessionState.build(
            [], [AnswerRow("f", skipped_reason="dependency_unmet")])
        cond = {"slot": {"field": "f", "exists": True}}
        assert evaluate(cond, answered).satisfied
        assert not evaluate(cond, skipped).satisfied

    def test_all_any_not_composition(self):
        s = state(a="1", b="2")
        assert evaluate({"all": [{"answered": "a"}, {"answered": "b"}]},
                        s).satisfied
        assert evaluate({"any": [{"answered": "zz"}, {"answered": "a"}]},
                        s).satisfied
        assert evaluate({"not": {"answered": "zz"}}, s).satisfied
        assert not evaluate({"all": [{"answered": "a"}, {"answered": "zz"}]},
                            s).satisfied

    def test_fired_leaves_report_references(self):
        s = state(chief_complaint__primary="chest_pain")
        res = evaluate({"slot": {"field": "chief_complaint.primary",
                                 "eq": "chest_pain"}}, s)
        assert res.refs == ("answer:chief_complaint.primary",)

    def test_fact_matching_by_category_and_concept(self):
        s = SessionState.build(
            [FactRow(id="f1", category="medication", concept="metformin")], [])
        assert evaluate({"fact": {"category": "medication"}}, s).refs == ("f1",)
        assert evaluate({"fact": {"concept": "metformin"}}, s).satisfied
        assert not evaluate({"fact": {"concept": "aspirin"}}, s).satisfied

    @pytest.mark.parametrize("bad", [
        {"unknown_op": 1},
        {"all": {"not": "a list"}},
        {"slot": {"field": "f"}},          # no comparison operator
        {"slot": {"no_field": True}},
        {"all": [], "any": []},            # two operators in one object
        "not an object",
    ])
    def test_malformed_conditions_raise(self, bad):
        with pytest.raises(RuleError):
            evaluate(bad, SessionState())


# ---------------------------------------------------------------------------
# question engine
# ---------------------------------------------------------------------------

class TestQuestionEngine:
    def test_first_question_is_chief_complaint(self):
        q = question_engine.next_question(allopathic_questionnaire(),
                                          SessionState())
        assert q is not None
        assert q.field_code == "chief_complaint.primary"

    def test_determinism_over_shuffled_answer_order(self):
        """Identical facts must yield the identical next question.

        The answers are shuffled and the questionnaire's question list is
        shuffled too, because determinism must come from the engine's total
        ordering, not from incidental database row order.
        """
        answers = {
            "chief_complaint__primary": "chest_pain",
            "hpi__onset": "today",
            "hpi__severity": 7,
            "socrates__character": "squeezing",
        }
        expected = None
        rng = random.Random(20260828)
        for _ in range(25):
            items = list(answers.items())
            rng.shuffle(items)
            proto = allopathic_questionnaire()
            rng.shuffle(proto.questions)
            got = question_engine.next_question(proto, state(**dict(items)))
            assert got is not None
            if expected is None:
                expected = got.field_code
            assert got.field_code == expected

    def test_dependency_gates_pain_only_fields(self):
        """A fever complaint must never be asked the SOCRATES pain fields."""
        proto = allopathic_questionnaire()
        remaining = question_engine.remaining_questions(
            proto, state(chief_complaint__primary="fever"))
        codes = {q.field_code for q in remaining}
        assert "socrates.character" not in codes
        assert "socrates.radiation" not in codes
        assert "fever.pattern" in codes

    def test_dependency_gates_fever_field_for_pain(self):
        proto = allopathic_questionnaire()
        remaining = question_engine.remaining_questions(
            proto, state(chief_complaint__primary="chest_pain"))
        codes = {q.field_code for q in remaining}
        assert "fever.pattern" not in codes
        assert "socrates.character" in codes

    def test_radiation_detail_skipped_when_radiation_is_nowhere(self):
        proto = allopathic_questionnaire()
        s = state(chief_complaint__primary="chest_pain",
                  socrates__radiation="nowhere")
        codes = {q.field_code
                 for q in question_engine.remaining_questions(proto, s)}
        assert "socrates.radiation_detail" not in codes

        s2 = state(chief_complaint__primary="chest_pain",
                   socrates__radiation="left_arm")
        codes2 = {q.field_code
                  for q in question_engine.remaining_questions(proto, s2)}
        assert "socrates.radiation_detail" in codes2

    def test_completeness_excludes_inapplicable_fields(self):
        """A simple presentation is not penalised for having fewer fields."""
        proto = allopathic_questionnaire()
        pain = question_engine.completeness(
            proto, state(chief_complaint__primary="chest_pain"))
        fever = question_engine.completeness(
            proto, state(chief_complaint__primary="fever"))
        assert fever.applicable_required < pain.applicable_required
        skipped = {s.field_code for s in fever.skipped}
        assert "socrates.character" in skipped

    def test_completeness_reaches_100_and_engine_terminates(self):
        proto = allopathic_questionnaire()
        answers: dict[str, object] = {}
        for _ in range(200):
            s = state(**answers)
            nxt = question_engine.next_question(proto, s)
            if nxt is None:
                break
            key = nxt.field_code.replace(".", "__")
            answers[key] = (list(nxt.options[:1]) if nxt.answer_type == "multi"
                            else (nxt.options[0] if nxt.options else "yes"))
        else:
            pytest.fail("question engine did not terminate")

        final = question_engine.completeness(proto, state(**answers))
        assert final.score == 100.0
        assert final.unanswered == ()

    def test_completeness_is_explainable(self):
        proto = allopathic_questionnaire()
        c = question_engine.completeness(
            proto, state(chief_complaint__primary="chest_pain"))
        text = c.explain()
        assert "Outstanding" in text
        assert c.unanswered

    def test_malformed_dependency_keeps_field_askable(self):
        """An authoring typo must not silently drop a clinical question."""
        proto = allopathic_questionnaire()
        proto.questions[3].dependency_rule = {"slot": {"oops": True}}
        ok, _ = question_engine.is_applicable(proto.questions[3],
                                             SessionState())
        assert ok is True

    def test_ayush_protocol_covers_dashavidha_and_shares_fact_model(self):
        proto = ayush_questionnaire()
        codes = {q.field_code for q in proto.questions}
        for expected in ("prakriti.body_frame", "agni.digestion",
                         "koshtha.bowel", "ahara_shakti.quantity",
                         "vyayama_shakti.tolerance"):
            assert expected in codes
        # same fact model as allopathic -- one instrument, two systems
        assert "drug_allergy.current_medication" in codes

    def test_every_prakriti_option_has_declared_weights(self):
        """Scoring must be transparent: no option may be silently unweighted."""
        proto = ayush_questionnaire()
        for q in proto.questions:
            if q.field_code.startswith("prakriti."):
                for opt in q.options or []:
                    assert opt in PRAKRITI_WEIGHTS, f"{opt} has no weights"


# ---------------------------------------------------------------------------
# red-flag engine
# ---------------------------------------------------------------------------

class TestRedFlagEngine:
    def test_acs_fires_on_chest_pain_plus_dyspnoea(self):
        s = state(chief_complaint__primary="chest_pain",
                  hpi__associated=["dyspnoea"])
        firing = redflag_engine.fired(
            redflag_engine.evaluate_all(red_flag_rules(), s))
        assert [e.rule_code for e in firing] == ["CARDIAC_ACS_SUSPICION"]
        assert firing[0].severity == "critical"

    def test_acs_does_not_fire_on_chest_pain_alone(self):
        s = state(chief_complaint__primary="chest_pain",
                  hpi__associated=["none"])
        assert redflag_engine.fired(
            redflag_engine.evaluate_all(red_flag_rules(), s)) == []

    def test_acs_fires_on_radiation_to_jaw(self):
        s = state(chief_complaint__primary="chest_pain",
                  socrates__radiation="jaw")
        assert any(e.rule_code == "CARDIAC_ACS_SUSPICION"
                   for e in redflag_engine.fired(
                       redflag_engine.evaluate_all(red_flag_rules(), s)))

    def test_negative_evaluations_are_returned_for_logging(self):
        """Sensitivity analysis is impossible without negative logging."""
        s = state(chief_complaint__primary="cough")
        evals = redflag_engine.evaluate_all(red_flag_rules(), s)
        assert len(evals) == len(red_flag_rules())
        assert all(e.fired is False for e in evals)

    def test_evaluation_error_fails_safe_by_raising_an_alert(self):
        """A rule-engine bug must never suppress a potential emergency."""
        rules = red_flag_rules()
        rules[0].condition = {"slot": {"broken": True}}
        ev = redflag_engine.evaluate_rule(rules[0], SessionState())
        assert ev.fired is True
        assert ev.errored is True
        assert ev.message_key == "alert.rule_evaluation_error"

    def test_fired_alerts_are_ordered_most_severe_first(self):
        s = state(chief_complaint__primary="chest_pain",
                  hpi__associated=["dyspnoea"],
                  ros__weight_loss="yes", ros__appetite="reduced")
        firing = redflag_engine.fired(
            redflag_engine.evaluate_all(red_flag_rules(), s))
        assert [e.severity for e in firing] == ["critical", "moderate"]

    def test_stroke_rule_requires_all_three_conditions(self):
        rules = red_flag_rules()
        partial = state(chief_complaint__primary="headache",
                        hpi__onset="today", hpi__severity=5)
        full = state(chief_complaint__primary="headache",
                     hpi__onset="today", hpi__severity=9)
        assert not any(e.rule_code == "STROKE_SUSPICION"
                       for e in redflag_engine.fired(
                           redflag_engine.evaluate_all(rules, partial)))
        assert any(e.rule_code == "STROKE_SUSPICION"
                   for e in redflag_engine.fired(
                       redflag_engine.evaluate_all(rules, full)))

    def test_rule_versions_are_stamped_on_every_evaluation(self):
        evals = redflag_engine.evaluate_all(red_flag_rules(), SessionState())
        assert all(e.rule_version >= 1 for e in evals)

    def test_effective_dating_excludes_retired_rules(self):
        rules = red_flag_rules()
        rules[0].effective_to = date(2025, 6, 30)
        active = {r.rule_code
                  for r in redflag_engine.active_rules(rules,
                                                       date(2026, 8, 28))}
        assert "CARDIAC_ACS_SUSPICION" not in active

    def test_determinism_across_repeated_evaluation(self):
        s = state(chief_complaint__primary="chest_pain",
                  hpi__associated=["dyspnoea", "nausea"],
                  socrates__radiation="left_arm")
        rng = random.Random(7)
        baseline = None
        for _ in range(20):
            rules = red_flag_rules()
            rng.shuffle(rules)
            got = tuple(e.rule_code
                        for e in redflag_engine.fired(
                            redflag_engine.evaluate_all(rules, s)))
            baseline = baseline or got
            assert got == baseline


# ---------------------------------------------------------------------------
# confidence gate
# ---------------------------------------------------------------------------

class TestConfidenceGate:
    def test_high_confidence_is_admitted(self):
        v = confidence.classify(0.95)
        assert v.admit and v.band == "high"
        assert not v.confirm_back_required

    def test_medium_confidence_admitted_but_marked_and_confirmed(self):
        v = confidence.classify(0.72)
        assert v.admit
        assert v.verification_status == "unconfirmed"
        assert v.confirm_back_required

    def test_low_confidence_is_never_admitted(self):
        """Guessing is worse than declaring uncertainty in a clinical record."""
        v = confidence.classify(0.30)
        assert not v.admit
        assert v.needs_human
        assert v.disposition == "withheld_low_confidence"

    def test_documents_are_held_to_a_higher_bar_than_speech(self):
        speech = confidence.classify(0.87, confidence.SOURCE_ANSWER)
        document = confidence.classify(0.87, confidence.SOURCE_DOCUMENT)
        assert speech.band == "high"
        assert document.band == "medium"

    def test_very_low_document_confidence_is_declared_unreadable(self):
        v = confidence.classify(0.20, confidence.SOURCE_DOCUMENT)
        assert v.band == "unreadable"
        assert not v.admit
        assert v.reason == "region_unreadable"

    def test_gate_is_monotonic(self):
        order = {"unreadable": 0, "low": 1, "medium": 2, "high": 3}
        prev = -1
        for c in [0.0, 0.2, 0.4, 0.5, 0.65, 0.8, 0.86, 0.9, 1.0]:
            rank = order[confidence.classify(
                c, confidence.SOURCE_DOCUMENT).band]
            assert rank >= prev
            prev = rank


# ---------------------------------------------------------------------------
# conflict detection
# ---------------------------------------------------------------------------

class TestConflictDetection:
    def test_denial_versus_document_evidence(self):
        facts = [
            fact("a1", "medication", field_code="drug_allergy.current_medication",
                 value="no", source="answer"),
            fact("d1", "medication", concept="metformin", value="500mg",
                 source="document", label="Metformin"),
        ]
        found = conflict.detect(facts)
        assert len(found) == 1
        assert found[0].kind == conflict.CONFLICT_NEGATION
        assert set(found[0].fact_ids) == {"a1", "d1"}

    def test_no_conflict_when_patient_confirms_medication(self):
        facts = [
            fact("a1", "medication", field_code="drug_allergy.current_medication",
                 value="yes", source="answer"),
            fact("d1", "medication", concept="metformin", value="500mg",
                 source="document"),
        ]
        assert conflict.detect(facts) == []

    def test_conflicts_are_never_auto_resolved_only_marked(self):
        facts = [
            fact("a1", "medication", field_code="drug_allergy.current_medication",
                 value="no", source="answer"),
            fact("d1", "medication", concept="metformin", value="500mg",
                 source="document"),
        ]
        found = conflict.detect(facts)
        conflict.apply(facts, found)
        # both survive, both flagged, sharing one group
        assert all(f.is_conflicting for f in facts)
        assert facts[0].conflict_group_id == facts[1].conflict_group_id
        assert len(facts) == 2

    def test_duplicate_medication_divergence(self):
        facts = [
            fact("d1", "medication", concept="amlodipine", value="5mg",
                 source="document"),
            fact("d2", "medication", concept="amlodipine", value="10mg",
                 source="document"),
        ]
        kinds = {c.kind for c in conflict.detect(facts)}
        assert conflict.CONFLICT_DUPLICATE in kinds

    def test_detection_order_is_stable(self):
        facts = [
            fact("a1", "medication", field_code="drug_allergy.current_medication",
                 value="no", source="answer"),
            fact("d1", "medication", concept="metformin", value="500mg",
                 source="document"),
            fact("d2", "medication", concept="metformin", value="850mg",
                 source="document"),
        ]
        first = [c.group_key for c in conflict.detect(facts)]
        for _ in range(5):
            assert [c.group_key for c in conflict.detect(facts)] == first


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------

class TestTimeline:
    def test_dated_events_are_newest_first(self):
        facts = [
            fact("f1", "lab_value", concept="hba1c", value="9.2",
                 eff=date(2025, 1, 10), source="document"),
            fact("f2", "lab_value", concept="hba1c", value="7.1",
                 eff=date(2026, 3, 2), source="document"),
        ]
        dated, undated = tl.build(facts)
        assert [e.fact_id for e in dated] == ["f2", "f1"]
        assert undated == []

    def test_undated_facts_are_grouped_not_given_a_fabricated_date(self):
        facts = [fact("f1", "diagnosis", concept="asthma", source="document")]
        dated, undated = tl.build(facts)
        assert dated == []
        assert len(undated) == 1
        assert undated[0].is_undated
        assert tl.describe(undated[0]) == "date not established"

    def test_precision_is_never_overstated(self):
        f = fact("f1", "diagnosis", concept="tb", eff=date(2024, 5, 1),
                 precision="month", source="document")
        entry = tl.build([f])[0][0]
        assert "month only" in tl.describe(entry)

    def test_ordering_is_total_and_reproducible(self):
        facts = [
            fact(f"f{i}", "lab_value", concept="x", value=str(i),
                 eff=date(2026, 1, 1), source="document")
            for i in range(6)
        ]
        first = [e.fact_id for e in tl.build(facts)[0]]
        for _ in range(5):
            random.shuffle(facts)
            assert [e.fact_id for e in tl.build(facts)[0]] == first


# ---------------------------------------------------------------------------
# summary grounding
# ---------------------------------------------------------------------------

class TestSummaryGrounding:
    def _facts(self):
        return [
            fact("f1", "symptom", field_code="chief_complaint.primary",
                 value="chest_pain", label="Chest pain"),
            fact("f2", "symptom", field_code="hpi.onset", value="today",
                 label="Onset"),
            fact("f3", "medication", concept="metformin", value="500mg",
                 source="document", label="Metformin",
                 eff=date(2026, 2, 1)),
        ]

    def test_every_clinical_sentence_carries_a_citation(self):
        facts = self._facts()
        sentences = sb.build_template(facts)
        for s in sentences:
            if s.text == sb.NOT_CAPTURED or s.section == "caveats":
                continue
            if s.text.startswith("No contradictions"):
                continue
            assert s.fact_ids, f"uncited clinical sentence: {s.text}"

    def test_citations_resolve_to_real_facts(self):
        facts = self._facts()
        valid = {f.id for f in facts}
        for s in sb.build_template(facts):
            for fid in s.fact_ids:
                assert fid in valid

    def test_ungrounded_sentence_is_dropped(self):
        """An LLM claim that cites nothing real must not survive publication."""
        facts = self._facts()
        proposed = sb.build_template(facts)
        proposed.append(sb.DraftSentence(
            "hpi", "Patient likely has coronary artery disease.", ()))
        proposed.append(sb.DraftSentence(
            "hpi", "Patient reports palpitations.", ("does-not-exist",)))

        report = sb.validate_grounding(proposed, facts)
        dropped = {s.text for s in report.dropped}
        assert "Patient likely has coronary artery disease." in dropped
        assert "Patient reports palpitations." in dropped
        assert report.pass_rate < 100.0

    def test_clean_template_grounds_fully(self):
        facts = self._facts()
        report = sb.validate_grounding(sb.build_template(facts), facts)
        assert report.dropped == []
        assert report.pass_rate == 100.0

    def test_rejected_fact_invalidates_its_citation(self):
        facts = self._facts()
        sentences = sb.build_template(facts)
        facts[2].physician_status = "rejected"
        report = sb.validate_grounding(sentences, facts)
        assert any("Metformin" in s.text for s in report.dropped)

    def test_empty_section_says_so_explicitly(self):
        """Absence of data must be distinguishable from absence of a finding."""
        sentences = sb.build_template([fact("f1", "symptom",
                                            field_code="chief_complaint.primary",
                                            value="fever")])
        family = [s for s in sentences if s.section == "family"]
        assert family and family[0].text == sb.NOT_CAPTURED

    def test_absent_interaction_check_is_stated_not_implied(self):
        """A missing warning must never read as a clean check."""
        sentences = sb.build_template([], interaction_check_performed=False)
        assert any("NOT performed" in s.text for s in sentences)

        done = sb.build_template([], interaction_check_performed=True)
        assert not any("NOT performed" in s.text for s in done)

    def test_summary_never_asserts_a_diagnosis(self):
        sentences = sb.build_template(self._facts())
        assert any("not a diagnosis" in s.text.lower() for s in sentences)

    def test_unconfirmed_facts_stay_visibly_uncertain(self):
        f = fact("f1", "symptom", field_code="chief_complaint.primary",
                 value="chest_pain", label="Chest pain",
                 verification="unconfirmed")
        text = sb.render_text(sb.build_template([f]))
        assert "unconfirmed" in text

    def test_pending_documents_are_disclosed(self):
        sentences = sb.build_template([], pending_documents=2)
        assert any("still being" in s.text for s in sentences)

    def test_render_is_deterministic(self):
        facts = self._facts()
        first = sb.render_text(sb.build_template(facts))
        for _ in range(5):
            random.shuffle(facts)
            assert sb.render_text(sb.build_template(facts)) == first
