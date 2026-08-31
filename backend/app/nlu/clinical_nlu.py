"""Clinical NLU: turn a spoken transcript into a (field, value, confidence).

Why this shape rather than a neural model
-----------------------------------------
This is the perception edge of the platform, and it is deliberately a *fitted
lexicon matcher*, not an end-to-end network. Three reasons:

1. The target vocabulary is closed. Every answerable value is an option code in
   a versioned questionnaire. The task is "map free speech onto one of a known
   set of codes for the current field", which is entity linking against a
   fixed lexicon -- not open-ended generation.

2. It must be inspectable. A clinician or reviewer can read exactly why "seene
   mein dard" mapped to `chest_pain`, and the alias list can be audited and
   corrected. A black-box classifier cannot offer that.

3. It runs on a commodity tablet with no GPU and works offline once the model
   artifact is cached.

The confidence it returns is real and calibrated (see fit.py): it feeds the
same confidence gate every other perception source feeds, so a weak match is
withheld and sent to a human rather than guessed. The heavy lifting -- speech
to text -- is done upstream by an ASR model (Bhashini / IndicConformer); this
component only interprets the resulting text against the current field.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field as dc_field
from pathlib import Path

MODEL_VERSION = "clinical-nlu-lexicon-1"

# A tiny stopword set per language, enough to stop function words from
# diluting token-overlap scores. Not a linguistic resource, just noise removal.
_STOP = {
    "en": {"the", "a", "an", "is", "am", "are", "i", "me", "my", "have", "has",
           "having", "feel", "feeling", "since", "from", "for", "of", "to",
           "and", "in", "on", "it", "that", "this", "some", "bit", "little",
           "very", "really", "doctor", "sir"},
    "hi": {"hai", "ho", "raha", "rahi", "raha", "se", "mein", "me", "mera",
           "meri", "mujhe", "ko", "ka", "ki", "aur", "bahut", "thoda", "hota",
           "hoti", "kuch", "par"},
    "ta": {"iruku", "irukku", "enakku", "en", "naan", "romba", "konjam"},
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower().strip()
    text = re.sub(r"[^\w\u0900-\u097F\u0B80-\u0BFF\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str, lang: str) -> list[str]:
    stop = _STOP.get(lang, set()) | _STOP["en"]
    return [t for t in _norm(text).split() if t and t not in stop]


@dataclass
class Match:
    field_code: str
    value: str | None
    confidence: float
    method: str
    matched_alias: str | None = None
    is_negation: bool = False
    raw: str = ""

    def as_dict(self) -> dict:
        return {
            "field_code": self.field_code,
            "value": self.value,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "matched_alias": self.matched_alias,
            "is_negation": self.is_negation,
            "model_version": MODEL_VERSION,
        }


@dataclass
class NLUModel:
    """Fitted artifact. ``aliases`` maps option code -> list of alias phrases,
    per language. ``calibration`` maps a raw score bucket to an empirical
    accuracy, learned from the labelled set so the confidence we emit reflects
    how often that score has actually been correct."""

    version: str = MODEL_VERSION
    # lang -> option_code -> [alias, ...]
    aliases: dict[str, dict[str, list[str]]] = dc_field(default_factory=dict)
    # negation cue words per language
    negation_cues: dict[str, list[str]] = dc_field(default_factory=dict)
    # score bucket (0..9) -> calibrated confidence
    calibration: dict[str, float] = dc_field(default_factory=dict)

    # ---- persistence ----
    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({
            "version": self.version,
            "aliases": self.aliases,
            "negation_cues": self.negation_cues,
            "calibration": self.calibration,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "NLUModel":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(version=data["version"], aliases=data["aliases"],
                   negation_cues=data["negation_cues"],
                   calibration=data["calibration"])

    # ---- inference ----
    def _calibrate(self, raw_score: float) -> float:
        bucket = str(min(9, max(0, int(raw_score * 10))))
        return self.calibration.get(bucket, raw_score)

    def _alias_score(self, said_tokens: list[str], said_norm: str,
                     alias: str, lang: str) -> tuple[float, str]:
        alias_norm = _norm(alias)
        if not alias_norm:
            return 0.0, "none"
        # exact phrase
        if said_norm == alias_norm:
            return 1.0, "exact"
        # phrase containment (either direction) is a strong signal
        if alias_norm in said_norm or said_norm in alias_norm:
            longer = max(len(alias_norm), len(said_norm))
            shorter = min(len(alias_norm), len(said_norm))
            return 0.80 + 0.15 * (shorter / longer), "phrase"
        # token overlap (Jaccard-ish, biased toward covering the alias)
        alias_tokens = set(_tokens(alias, lang))
        if not alias_tokens:
            return 0.0, "none"
        said_set = set(said_tokens)
        overlap = alias_tokens & said_set
        if not overlap:
            return 0.0, "none"
        coverage = len(overlap) / len(alias_tokens)
        precision = len(overlap) / max(1, len(said_set))
        return 0.55 * coverage + 0.25 * precision, "token"

    def interpret(self, transcript: str, *, field_code: str,
                  options: list[str], lang: str = "hi",
                  answer_type: str = "single") -> Match:
        """Map a transcript to one of ``options`` for the current field."""
        said_norm = _norm(transcript)
        said_tokens = _tokens(transcript, lang)

        negated = self._detect_negation(said_tokens, lang)

        # numeric fields: pull the first number in range
        if answer_type == "scale" or answer_type == "numeric":
            num = self._first_number(said_norm)
            if num is not None and str(num) in options:
                return Match(field_code, str(num), 0.95, "numeric",
                             raw=transcript)

        lang_aliases = self.aliases.get(lang, {})
        best_code, best_score, best_alias, best_method = None, 0.0, None, "none"

        for code in options:
            candidates = lang_aliases.get(code, [])
            # the code itself and its de-slugged form are always candidates
            candidates = candidates + [code, code.replace("_", " "),
                                       code.split(".")[-1].replace("_", " ")]
            for alias in candidates:
                score, method = self._alias_score(said_tokens, said_norm,
                                                  alias, lang)
                if score > best_score:
                    best_code, best_score = code, score
                    best_alias, best_method = alias, method

        if best_code is None or best_score <= 0.0:
            return Match(field_code, None, 0.0, "no_match", raw=transcript,
                         is_negation=negated)

        confidence = self._calibrate(best_score)
        return Match(field_code, best_code, confidence, best_method,
                     matched_alias=best_alias, is_negation=negated,
                     raw=transcript)

    def _detect_negation(self, tokens: list[str], lang: str) -> bool:
        cues = set(self.negation_cues.get(lang, []))
        return bool(cues & set(tokens))

    @staticmethod
    def _first_number(text: str) -> int | None:
        m = re.search(r"\b(\d{1,2})\b", text)
        if m:
            return int(m.group(1))
        words = {"ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5,
                 "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        for w, n in words.items():
            if re.search(rf"\b{w}\b", text):
                return n
        return None


_ACTIVE: NLUModel | None = None


def default_model_path() -> Path:
    return Path(__file__).with_name("nlu_model.json")


def get_model() -> NLUModel:
    """Load the fitted artifact once, or fall back to an empty lexicon.

    The empty fallback still works: option codes and their de-slugged forms are
    always candidates, so touch-equivalent phrases match even before fitting.
    Fitting adds the natural-language aliases and the confidence calibration.
    """
    global _ACTIVE
    if _ACTIVE is None:
        path = default_model_path()
        if path.exists():
            _ACTIVE = NLUModel.load(path)
        else:
            _ACTIVE = NLUModel(negation_cues={
                "en": ["no", "not", "none", "never", "without"],
                "hi": ["nahi", "nahin", "koi", "bina"],
                "ta": ["illai", "illa"]})
    return _ACTIVE


def reset_model() -> None:
    global _ACTIVE
    _ACTIVE = None
