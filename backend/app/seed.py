"""Clinical protocol seed data.

Questionnaires and red-flag rules are DATA, not code. A hospital's clinical
committee owns them through the admin console; publishing a new version never
mutates history, and every past session records the exact version that
governed it.

Prompt values are i18n keys, never literal text, so one protocol serves every
language. Option values are canonical codes -- the engines reason over codes,
and translation is purely a client concern.

AYUSH note: the Dashavidha Pariksha items below carry declared dosha weights
and the engine shows per-item contributions, so a practitioner can see *why* a
dominance was indicated. The output is an indicated distribution for
practitioner confirmation, never a determination -- Prakriti assessment
involves pulse and examination findings a questionnaire cannot capture. This
content requires sign-off by a qualified Ayurvedic practitioner before any
deployment.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .engines.rules import validate_condition
from .models import (CARE_ALLOPATHIC, CARE_AYUSH, Questionnaire, Question,
                     RedFlagRule, Tenant)

EFFECTIVE_FROM = date(2025, 1, 1)

# ---------------------------------------------------------------------------
# Allopathic OPD questionnaire
# ---------------------------------------------------------------------------

ALLOPATHIC_CODE = "opd_general_allopathic"

ALLOPATHIC_QUESTIONS: list[dict] = [
    # ---- chief complaint -------------------------------------------------
    dict(section="chief_complaint", field_code="chief_complaint.primary",
         prompt_key="q.cc.primary", answer_type="single",
         clinical_concept=None, fact_category="symptom", display_order=10,
         options=["chest_pain", "fever", "abdominal_pain", "breathlessness",
                  "headache", "cough", "other"]),

    # ---- HPI: SOCRATES, gated on a pain-type complaint -------------------
    dict(section="hpi", field_code="hpi.onset", prompt_key="q.hpi.onset",
         answer_type="single", fact_category="symptom", display_order=20,
         options=["today", "yesterday", "this_week", "this_month",
                  "over_a_month"]),
    dict(section="hpi", field_code="hpi.severity", prompt_key="q.hpi.severity",
         answer_type="scale", fact_category="symptom", display_order=30,
         options=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]),
    dict(section="hpi", field_code="socrates.character",
         prompt_key="q.socrates.character", answer_type="single",
         fact_category="symptom", display_order=40,
         options=["burning", "squeezing", "stabbing", "dull", "cramping"],
         dependency_rule={"slot": {"field": "chief_complaint.primary",
                                    "in": ["chest_pain", "abdominal_pain",
                                           "headache"]}}),
    dict(section="hpi", field_code="socrates.radiation",
         prompt_key="q.socrates.radiation", answer_type="single",
         fact_category="symptom", display_order=50,
         options=["left_arm", "jaw", "back", "shoulder", "nowhere"],
         dependency_rule={"slot": {"field": "chief_complaint.primary",
                                    "in": ["chest_pain", "abdominal_pain"]}}),
    # only asked when radiation actually exists -- the skip keeps completeness
    # honest instead of penalising a patient with a simple presentation
    dict(section="hpi", field_code="socrates.radiation_detail",
         prompt_key="q.socrates.radiation_detail", answer_type="single",
         fact_category="symptom", display_order=60,
         options=["constant", "intermittent", "on_exertion"],
         dependency_rule={"all": [
             {"slot": {"field": "socrates.radiation", "exists": True}},
             {"not": {"slot": {"field": "socrates.radiation",
                                "eq": "nowhere"}}}]}),
    dict(section="hpi", field_code="hpi.associated",
         prompt_key="q.hpi.associated", answer_type="multi",
         fact_category="symptom", display_order=70,
         options=["dyspnoea", "diaphoresis", "nausea", "vomiting",
                  "palpitations", "none"]),
    dict(section="hpi", field_code="hpi.timing", prompt_key="q.hpi.timing",
         answer_type="single", fact_category="symptom", display_order=80,
         options=["constant", "intermittent", "on_exertion"]),
    dict(section="hpi", field_code="hpi.exacerbating",
         prompt_key="q.hpi.exacerbating", answer_type="single",
         fact_category="symptom", display_order=90,
         options=["walking", "breathing", "eating", "lying_down", "nothing"]),

    # ---- fever-specific branch ------------------------------------------
    dict(section="hpi", field_code="fever.pattern",
         prompt_key="q.fever.pattern", answer_type="single",
         fact_category="symptom", display_order=100,
         options=["continuous", "intermittent", "with_chills", "night_only"],
         dependency_rule={"slot": {"field": "chief_complaint.primary",
                                    "eq": "fever"}}),

    # ---- past medical ----------------------------------------------------
    dict(section="past_medical", field_code="past_medical.diagnosed_conditions",
         prompt_key="q.pmh.conditions", answer_type="multi",
         fact_category="diagnosis", display_order=110,
         options=["diabetes", "hypertension", "asthma", "tuberculosis",
                  "cardiac_disease", "thyroid_disorder", "none"]),
    dict(section="past_medical", field_code="past_medical.surgery",
         prompt_key="q.pmh.surgery", answer_type="single",
         fact_category="procedure", display_order=120,
         options=["yes", "no"]),

    # ---- drug and allergy ------------------------------------------------
    dict(section="drug_allergy", field_code="drug_allergy.current_medication",
         prompt_key="q.drug.current", answer_type="single",
         fact_category="medication", display_order=130,
         options=["yes", "no"]),
    dict(section="drug_allergy", field_code="drug_allergy.known_allergy",
         prompt_key="q.drug.allergy", answer_type="single",
         fact_category="allergy", display_order=140,
         options=["yes", "no"]),

    # ---- family ----------------------------------------------------------
    dict(section="family", field_code="family.history",
         prompt_key="q.family.history", answer_type="multi",
         fact_category="family_history", display_order=150,
         options=["diabetes", "hypertension", "cardiac_disease", "stroke",
                  "cancer", "none"]),

    # ---- personal --------------------------------------------------------
    dict(section="personal", field_code="personal.tobacco",
         prompt_key="q.personal.tobacco", answer_type="single",
         fact_category="personal_history", display_order=160,
         options=["never", "current", "former"]),
    dict(section="personal", field_code="personal.alcohol",
         prompt_key="q.personal.alcohol", answer_type="single",
         fact_category="personal_history", display_order=170,
         options=["never", "occasional", "regular"]),

    # ---- review of systems -----------------------------------------------
    dict(section="review_of_systems", field_code="ros.weight_loss",
         prompt_key="q.ros.weight_loss", answer_type="single",
         fact_category="symptom", display_order=180,
         options=["yes", "no"]),
    dict(section="review_of_systems", field_code="ros.appetite",
         prompt_key="q.ros.appetite", answer_type="single",
         fact_category="symptom", display_order=190,
         options=["normal", "reduced", "increased"]),
]

# ---------------------------------------------------------------------------
# AYUSH questionnaire -- Dashavidha Pariksha (Prakriti subset)
# ---------------------------------------------------------------------------

AYUSH_CODE = "opd_ayush_dashavidha"

# option -> dosha weights. Transparent by design: the engine reports per-item
# contributions so the practitioner sees the derivation, not just a label.
PRAKRITI_WEIGHTS: dict[str, dict[str, float]] = {
    "body_frame.thin": {"vata": 1.0},
    "body_frame.medium": {"pitta": 1.0},
    "body_frame.heavy": {"kapha": 1.0},
    "skin.dry": {"vata": 1.0},
    "skin.warm_oily": {"pitta": 1.0},
    "skin.cool_smooth": {"kapha": 1.0},
    "appetite.irregular": {"vata": 1.0},
    "appetite.strong": {"pitta": 1.0},
    "appetite.steady_low": {"kapha": 1.0},
    "sleep.light": {"vata": 1.0},
    "sleep.moderate": {"pitta": 1.0},
    "sleep.deep": {"kapha": 1.0},
    "temperament.anxious": {"vata": 1.0},
    "temperament.irritable": {"pitta": 1.0},
    "temperament.calm": {"kapha": 1.0},
    "climate.dislikes_cold": {"vata": 1.0},
    "climate.dislikes_heat": {"pitta": 1.0},
    "climate.dislikes_damp": {"kapha": 1.0},
}

AYUSH_QUESTIONS: list[dict] = [
    dict(section="chief_complaint", field_code="chief_complaint.primary",
         prompt_key="q.cc.primary", answer_type="single",
         fact_category="symptom", display_order=10,
         options=["digestive_complaint", "joint_pain", "skin_disorder",
                  "sleep_disturbance", "respiratory_complaint", "other"]),

    # ---- Prakriti (constitution) -----------------------------------------
    dict(section="ayush_dashavidha", field_code="prakriti.body_frame",
         prompt_key="q.prakriti.body_frame", answer_type="single",
         fact_category="ayush_parameter", display_order=20,
         options=["body_frame.thin", "body_frame.medium", "body_frame.heavy"]),
    dict(section="ayush_dashavidha", field_code="prakriti.skin",
         prompt_key="q.prakriti.skin", answer_type="single",
         fact_category="ayush_parameter", display_order=30,
         options=["skin.dry", "skin.warm_oily", "skin.cool_smooth"]),
    dict(section="ayush_dashavidha", field_code="prakriti.appetite",
         prompt_key="q.prakriti.appetite", answer_type="single",
         fact_category="ayush_parameter", display_order=40,
         options=["appetite.irregular", "appetite.strong",
                  "appetite.steady_low"]),
    dict(section="ayush_dashavidha", field_code="prakriti.sleep",
         prompt_key="q.prakriti.sleep", answer_type="single",
         fact_category="ayush_parameter", display_order=50,
         options=["sleep.light", "sleep.moderate", "sleep.deep"]),
    dict(section="ayush_dashavidha", field_code="prakriti.temperament",
         prompt_key="q.prakriti.temperament", answer_type="single",
         fact_category="ayush_parameter", display_order=60,
         options=["temperament.anxious", "temperament.irritable",
                  "temperament.calm"]),
    dict(section="ayush_dashavidha", field_code="prakriti.climate",
         prompt_key="q.prakriti.climate", answer_type="single",
         fact_category="ayush_parameter", display_order=70,
         options=["climate.dislikes_cold", "climate.dislikes_heat",
                  "climate.dislikes_damp"]),

    # ---- Agni / Koshtha / Ahara-Vihara ----------------------------------
    dict(section="ayush_dashavidha", field_code="agni.digestion",
         prompt_key="q.agni.digestion", answer_type="single",
         fact_category="ayush_parameter", display_order=80,
         options=["strong", "variable", "weak", "very_weak"]),
    dict(section="ayush_dashavidha", field_code="koshtha.bowel",
         prompt_key="q.koshtha.bowel", answer_type="single",
         fact_category="ayush_parameter", display_order=90,
         options=["mridu_soft", "madhyama_moderate", "krura_hard"]),
    dict(section="ayush_dashavidha", field_code="ahara.diet_type",
         prompt_key="q.ahara.diet_type", answer_type="single",
         fact_category="ayush_parameter", display_order=100,
         options=["vegetarian", "mixed", "predominantly_fried",
                  "irregular_timing"]),
    dict(section="ayush_dashavidha", field_code="vihara.activity",
         prompt_key="q.vihara.activity", answer_type="single",
         fact_category="ayush_parameter", display_order=110,
         options=["sedentary", "moderate", "heavy_exertion"]),
    dict(section="ayush_dashavidha", field_code="ahara_shakti.quantity",
         prompt_key="q.ahara_shakti.quantity", answer_type="single",
         fact_category="ayush_parameter", display_order=120,
         options=["small", "moderate", "large"]),
    dict(section="ayush_dashavidha", field_code="vyayama_shakti.tolerance",
         prompt_key="q.vyayama_shakti.tolerance", answer_type="single",
         fact_category="ayush_parameter", display_order=130,
         options=["low", "moderate", "high"]),

    # ---- shared allopathic-equivalent history --------------------------
    dict(section="past_medical", field_code="past_medical.diagnosed_conditions",
         prompt_key="q.pmh.conditions", answer_type="multi",
         fact_category="diagnosis", display_order=140,
         options=["diabetes", "hypertension", "asthma", "thyroid_disorder",
                  "none"]),
    dict(section="drug_allergy", field_code="drug_allergy.current_medication",
         prompt_key="q.drug.current", answer_type="single",
         fact_category="medication", display_order=150,
         options=["yes", "no"]),
]

# ---------------------------------------------------------------------------
# Red-flag rules
# ---------------------------------------------------------------------------

RED_FLAG_RULES: list[dict] = [
    dict(rule_code="CARDIAC_ACS_SUSPICION", version=3, severity="critical",
         sla_seconds=120, escalation_tier=1,
         alert_message_key="alert.cardiac.acs_suspicion",
         condition={"all": [
             {"slot": {"field": "chief_complaint.primary", "eq": "chest_pain"}},
             {"any": [
                 {"slot": {"field": "hpi.associated", "in": ["dyspnoea",
                                                             "diaphoresis"]}},
                 {"slot": {"field": "socrates.radiation",
                            "in": ["left_arm", "jaw"]}},
                 {"slot": {"field": "hpi.timing", "eq": "on_exertion"}},
             ]},
         ]}),

    dict(rule_code="STROKE_SUSPICION", version=1, severity="critical",
         sla_seconds=120, escalation_tier=1,
         alert_message_key="alert.neuro.stroke_suspicion",
         condition={"all": [
             {"slot": {"field": "chief_complaint.primary", "eq": "headache"}},
             {"slot": {"field": "hpi.onset", "eq": "today"}},
             {"slot": {"field": "hpi.severity", "gte": 8}},
         ]}),

    dict(rule_code="RESPIRATORY_DISTRESS", version=1, severity="critical",
         sla_seconds=180, escalation_tier=1,
         alert_message_key="alert.resp.distress",
         condition={"all": [
             {"slot": {"field": "chief_complaint.primary",
                        "eq": "breathlessness"}},
             {"any": [
                 {"slot": {"field": "hpi.onset", "in": ["today",
                                                        "yesterday"]}},
                 {"slot": {"field": "hpi.severity", "gte": 7}},
             ]},
         ]}),

    dict(rule_code="FEBRILE_RED_FLAG", version=2, severity="high",
         sla_seconds=600, escalation_tier=1,
         alert_message_key="alert.fever.systemic",
         condition={"all": [
             {"slot": {"field": "chief_complaint.primary", "eq": "fever"}},
             {"any": [
                 {"slot": {"field": "fever.pattern", "eq": "with_chills"}},
                 {"slot": {"field": "hpi.associated", "in": ["vomiting"]}},
             ]},
         ]}),

    dict(rule_code="CONSTITUTIONAL_WARNING", version=1, severity="moderate",
         sla_seconds=1800, escalation_tier=2,
         alert_message_key="alert.constitutional.weight_loss",
         condition={"all": [
             {"slot": {"field": "ros.weight_loss", "eq": "yes"}},
             {"slot": {"field": "ros.appetite", "eq": "reduced"}},
         ]}),
]


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------

def build_questionnaire(code: str, care_system: str,
                        specs: list[dict], version: int = 1) -> Questionnaire:
    """Build an unattached Questionnaire graph (also used directly by tests)."""
    q = Questionnaire(code=code, care_system=care_system, version=version,
                      effective_from=EFFECTIVE_FROM, is_active=True)
    for spec in specs:
        validate_condition(spec.get("dependency_rule"))
        # set explicitly: column defaults only apply at INSERT, and these
        # objects are also used unattached by the engines and tests
        q.questions.append(Question(**{"is_required": True,
                                       "display_order": 0, **spec}))
    return q


def allopathic_questionnaire(version: int = 1) -> Questionnaire:
    return build_questionnaire(ALLOPATHIC_CODE, CARE_ALLOPATHIC,
                               ALLOPATHIC_QUESTIONS, version)


def ayush_questionnaire(version: int = 1) -> Questionnaire:
    return build_questionnaire(AYUSH_CODE, CARE_AYUSH, AYUSH_QUESTIONS,
                               version)


def red_flag_rules() -> list[RedFlagRule]:
    out = []
    for spec in RED_FLAG_RULES:
        validate_condition(spec["condition"])
        out.append(RedFlagRule(effective_from=EFFECTIVE_FROM, is_active=True,
                               **spec))
    return out


def seed(db: Session, *, tenant_name: str = "District Hospital (Demo)",
         tenant_code: str = "DH-DEMO-01") -> Tenant:
    """Idempotent seed. Safe to call on every startup."""
    tenant = db.scalar(select(Tenant).where(
        Tenant.hospital_local_code == tenant_code))
    if tenant is None:
        tenant = Tenant(name=tenant_name, hospital_local_code=tenant_code,
                        facility_type="district", state="Demo State",
                        district="Demo District")
        db.add(tenant)
        db.flush()

    for builder in (allopathic_questionnaire, ayush_questionnaire):
        proto = builder()
        exists = db.scalar(select(Questionnaire).where(
            Questionnaire.code == proto.code,
            Questionnaire.version == proto.version))
        if exists is None:
            db.add(proto)

    for rule in red_flag_rules():
        exists = db.scalar(select(RedFlagRule).where(
            RedFlagRule.rule_code == rule.rule_code,
            RedFlagRule.version == rule.version))
        if exists is None:
            db.add(rule)

    db.flush()
    return tenant
