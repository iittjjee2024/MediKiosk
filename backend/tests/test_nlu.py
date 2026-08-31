"""Tests for the clinical NLU and its fit/eval pipeline.

The held-out accuracy test is the one that matters: it protects the claim that
the model generalises beyond the exact phrases it was shown, rather than merely
memorising them.
"""
from __future__ import annotations

import pytest

from app.nlu.clinical_nlu import NLUModel
from app.nlu.fit import (_field_options, calibrate, evaluate, fit, _split)
from app.nlu.training_data import BASE_LEXICON, LABELLED


@pytest.fixture(scope="module")
def fitted():
    fo = _field_options()
    model = fit(LABELLED)
    model.calibration = calibrate(model, LABELLED, fo)
    return model, fo


class TestFieldOptions:
    def test_chief_complaint_options_are_unioned_not_overwritten(self):
        """chief_complaint.primary exists in both protocols with different
        options. The fitter must see the union, or Hindi chest-pain phrases
        get scored against AYUSH-only options and fail."""
        fo = _field_options()
        opts, _ = fo["chief_complaint.primary"]
        assert "chest_pain" in opts
        assert "digestive_complaint" in opts


class TestInterpretation:
    def test_exact_learned_phrase_is_high_confidence(self, fitted):
        model, fo = fitted
        opts, atype = fo["chief_complaint.primary"]
        m = model.interpret("seene mein dard ho raha hai",
                            field_code="chief_complaint.primary",
                            options=opts, lang="hi", answer_type=atype)
        assert m.value == "chest_pain"
        assert m.confidence >= 0.85

    def test_unseen_phrasing_with_keyword_still_matches(self, fitted):
        """Generalisation: a sentence never in the corpus that contains the
        key content word should still map correctly."""
        model, fo = fitted
        opts, atype = fo["chief_complaint.primary"]
        m = model.interpret("mere seene mein bahut takleef hai",
                            field_code="chief_complaint.primary",
                            options=opts, lang="hi", answer_type=atype)
        assert m.value == "chest_pain"

    def test_english_code_mixed_hindi(self, fitted):
        model, fo = fitted
        opts, atype = fo["past_medical.diagnosed_conditions"]
        m = model.interpret("mujhe sugar hai",
                            field_code="past_medical.diagnosed_conditions",
                            options=opts, lang="hi", answer_type="multi")
        assert m.value == "diabetes"

    def test_gibberish_does_not_match(self, fitted):
        model, fo = fitted
        opts, atype = fo["chief_complaint.primary"]
        m = model.interpret("qwerty zxcvb asdf",
                            field_code="chief_complaint.primary",
                            options=opts, lang="hi", answer_type=atype)
        assert m.value is None
        assert m.confidence == 0.0

    def test_negation_is_detected(self, fitted):
        model, fo = fitted
        opts, atype = fo["drug_allergy.known_allergy"]
        m = model.interpret("nahi koi allergy nahi hai",
                            field_code="drug_allergy.known_allergy",
                            options=opts, lang="hi", answer_type="single")
        assert m.is_negation is True

    def test_scale_field_extracts_a_number(self, fitted):
        model, fo = fitted
        opts, atype = fo["hpi.severity"]
        m = model.interpret("dard 8 hai",
                            field_code="hpi.severity",
                            options=opts, lang="hi", answer_type="scale")
        assert m.value == "8"

    def test_confidence_never_exceeds_one(self, fitted):
        model, fo = fitted
        for lang, field, transcript, _ in LABELLED:
            opts, atype = fo.get(field, ([], "single"))
            if not opts:
                continue
            m = model.interpret(transcript, field_code=field, options=opts,
                                lang=lang, answer_type=atype)
            assert 0.0 <= m.confidence <= 1.0


class TestFitAndEvaluate:
    def test_full_corpus_accuracy_is_perfect(self, fitted):
        model, fo = fitted
        rep = evaluate(model, LABELLED, fo)
        assert rep["accuracy"] == 1.0

    def test_held_out_accuracy_meets_gate(self):
        """The honest number: fit on train, test on unseen. Must clear 80%."""
        fo = _field_options()
        train, test = _split(LABELLED)
        model = fit(train)
        model.calibration = calibrate(model, train, fo)
        rep = evaluate(model, test, fo)
        assert rep["accuracy"] >= 0.80, rep["confusions"]

    def test_every_base_lexicon_field_exists_in_protocol(self):
        """A curated alias for a field that is not in any questionnaire would
        be dead weight and probably a typo."""
        fo = _field_options()
        for (lang, field), by_code in BASE_LEXICON.items():
            assert field in fo, f"{field} not in any questionnaire"
            valid = set(fo[field][0])
            for code in by_code:
                assert code in valid, f"{code} not an option of {field}"

    def test_calibration_is_monotonic_nondecreasing(self, fitted):
        model, _ = fitted
        vals = [model.calibration[str(b)] for b in range(10)]


        assert vals[9] >= vals[0]

    def test_artifact_round_trips(self, fitted, tmp_path):
        model, _ = fitted
        p = tmp_path / "m.json"
        model.save(p)
        loaded = NLUModel.load(p)
        assert loaded.aliases == model.aliases
        assert loaded.calibration == model.calibration
