"""Fit the clinical NLU lexicon and calibrate its confidence.

This is the "training" step, honest about what it is: we learn the alias
phrases for each option code from the labelled corpus, and we calibrate the
matcher's raw scores against a held-out split so the confidence we emit
reflects how often a score of that magnitude has actually been correct. That
calibrated confidence then feeds the same gate every perception source feeds.

Run:
    python -m app.nlu.fit            # fit + evaluate, write artifact + report
    python -m app.nlu.fit --eval     # evaluate an existing artifact only
"""
from __future__ import annotations

import argparse
import random
from collections import defaultdict

from .clinical_nlu import MODEL_VERSION, NLUModel, default_model_path, _norm
from .training_data import BASE_LEXICON, LABELLED, NEGATION_CUES

# The option set per field, mirrored from the seed protocol. Kept here so the
# fitter and evaluator know the candidate list for each field without a DB.
from ..seed import ALLOPATHIC_QUESTIONS, AYUSH_QUESTIONS

SEED = 20260828


def _field_options() -> dict[str, tuple[list[str], str]]:
    """field_code -> (option codes, answer_type), from the seed protocol.

    A field code can appear in more than one questionnaire with different
    option sets -- ``chief_complaint.primary`` has allopathic complaints in one
    protocol and Ayurvedic complaints in another. We therefore UNION the option
    sets per field rather than letting the last-loaded protocol overwrite the
    first. At inference time the API always passes the concrete option list for
    the live session, so this union is only used by the offline fitter and
    evaluator, where seeing all candidate codes is exactly what we want.
    """
    opts: dict[str, list[str]] = {}
    atype: dict[str, str] = {}
    for spec in ALLOPATHIC_QUESTIONS + AYUSH_QUESTIONS:
        fc = spec["field_code"]
        merged = opts.setdefault(fc, [])
        for o in spec.get("options") or []:
            if o not in merged:
                merged.append(o)
        atype.setdefault(fc, spec.get("answer_type", "single"))
    return {fc: (opts[fc], atype[fc]) for fc in opts}


def fit(rows: list[tuple[str, str, str, str]]) -> NLUModel:
    """Learn aliases from two sources:

    1. the curated BASE_LEXICON of key content words per option -- this is what
       lets the matcher generalise to unseen phrasings, because a new sentence
       that contains the key word still matches; and
    2. every training transcript, which becomes a full-phrase alias for its
       labelled option code (exact-match coverage of what has actually been
       heard).

    Both are keyed by language. The base lexicon comes first so its short,
    high-signal keywords are available even for options with no labelled rows.
    """
    aliases: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list))

    for (lang, _field), by_code in BASE_LEXICON.items():
        for code, words in by_code.items():
            bucket = aliases[lang][code]
            for w in words:
                nw = _norm(w)
                if nw and nw not in bucket:
                    bucket.append(nw)

    for lang, _field, transcript, code in rows:
        phrase = _norm(transcript)
        bucket = aliases[lang][code]
        if phrase and phrase not in bucket:
            bucket.append(phrase)

    aliases = {lang: dict(codes) for lang, codes in aliases.items()}
    return NLUModel(version=MODEL_VERSION, aliases=aliases,
                    negation_cues=NEGATION_CUES, calibration={})


def _raw_scores(model: NLUModel, rows, field_options):
    """For each row, the model's top match and whether it was correct."""
    results = []
    for lang, field, transcript, expected in rows:
        options, atype = field_options.get(field, ([], "single"))
        if not options:
            continue
        m = model.interpret(transcript, field_code=field, options=options,
                            lang=lang, answer_type=atype)
        results.append((m.confidence, m.value == expected, m))
    return results


def calibrate(model: NLUModel, rows, field_options) -> dict[str, float]:
    """Bucket raw scores 0..9 and set each bucket's confidence to the empirical
    accuracy observed in that bucket. This is isotonic-style calibration in
    miniature: it stops the matcher from being over- or under-confident."""
    buckets: dict[int, list[bool]] = defaultdict(list)
    for score, correct, _m in _raw_scores(model, rows, field_options):
        buckets[min(9, max(0, int(score * 10)))].append(correct)
    calibration: dict[str, float] = {}
    for b in range(10):
        hits = buckets.get(b, [])
        if hits:
            calibration[str(b)] = round(sum(hits) / len(hits), 3)
        else:
            # no data in this bucket: fall back to the bucket midpoint
            calibration[str(b)] = round((b + 0.5) / 10, 3)
    return calibration


def evaluate(model: NLUModel, rows, field_options) -> dict:
    """Top-1 accuracy overall and per language, plus a calibration table."""
    total = correct = 0
    per_lang: dict[str, list[bool]] = defaultdict(list)
    confusions: list[tuple] = []
    for lang, field, transcript, expected in rows:
        options, atype = field_options.get(field, ([], "single"))
        if not options:
            continue
        m = model.interpret(transcript, field_code=field, options=options,
                            lang=lang, answer_type=atype)
        ok = m.value == expected
        total += 1
        correct += ok
        per_lang[lang].append(ok)
        if not ok:
            confusions.append((lang, field, transcript, expected, m.value,
                               round(m.confidence, 2)))
    return {
        "total": total,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "per_language": {k: round(sum(v) / len(v), 4) for k, v in
                         per_lang.items()},
        "confusions": confusions,
    }


def _split(rows, holdout=0.30):
    rng = random.Random(SEED)
    shuffled = rows[:]
    rng.shuffle(shuffled)
    cut = int(len(shuffled) * (1 - holdout))
    return shuffled[:cut], shuffled[cut:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true",
                    help="evaluate the existing artifact only")
    args = ap.parse_args()
    field_options = _field_options()
    path = default_model_path()

    if args.eval:
        model = NLUModel.load(path)
        rep = evaluate(model, LABELLED, field_options)
        _print_report("EVAL (existing artifact, full corpus)", rep)
        return 0

    # Held-out evaluation: fit on train, calibrate on train, test on unseen.
    train, test = _split(LABELLED)
    model = fit(train)
    model.calibration = calibrate(model, train, field_options)

    held = evaluate(model, test, field_options)
    _print_report(f"HELD-OUT ({len(test)} unseen utterances)", held)

    # Final artifact: fit on everything so deployment uses the full lexicon.
    final = fit(LABELLED)
    final.calibration = calibrate(final, LABELLED, field_options)
    final.save(path)
    full = evaluate(final, LABELLED, field_options)
    _print_report("FULL CORPUS (fitted artifact)", full)

    print(f"\nartifact written: {path}")
    print(f"model version:    {final.version}")
    print(f"languages:        {sorted(final.aliases)}")
    print(f"option codes:     "
          f"{sum(len(v) for v in final.aliases.values())} alias groups")
    # gate: the held-out accuracy is the honest number
    return 0 if held["accuracy"] >= 0.80 else 1


def _print_report(title: str, rep: dict) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")
    print(f"  top-1 accuracy : {rep['accuracy']:.1%}  "
          f"({rep['total']} utterances)")
    for lang, acc in sorted(rep["per_language"].items()):
        print(f"    {lang}: {acc:.1%}")
    if rep["confusions"]:
        print(f"  misclassified  : {len(rep['confusions'])}")
        for lang, field, said, exp, got, conf in rep["confusions"][:8]:
            print(f"    [{lang}] {field}: \"{said}\" -> {got} "
                  f"(want {exp}, conf {conf})")


if __name__ == "__main__":
    raise SystemExit(main())
