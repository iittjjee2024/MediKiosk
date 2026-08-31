"""Labelled utterances for fitting and evaluating the clinical NLU.

Each row is (language, field_code, spoken_transcript, expected_option_code).
These are the natural-language ways a patient actually voices each answer,
across Hindi (incl. code-mixed English medical terms), English and Tamil.

This is the corpus the fit step learns aliases from and the eval step scores
against. It is small and hand-authored on purpose: the vocabulary is closed,
and in production this set would be extended from real (consented,
de-identified) waiting-hall recordings at the pilot site. Keeping it explicit
and readable is the point -- anyone can see and correct what the model learned.
"""
from __future__ import annotations

# (lang, field_code, transcript, expected_code)
LABELLED: list[tuple[str, str, str, str]] = [
    # ---- chief complaint : Hindi (code-mixed) --------------------------
    ("hi", "chief_complaint.primary", "seene mein dard ho raha hai", "chest_pain"),
    ("hi", "chief_complaint.primary", "chaati mein dard hai", "chest_pain"),
    ("hi", "chief_complaint.primary", "mujhe bukhar hai", "fever"),
    ("hi", "chief_complaint.primary", "tez bukhar aa raha hai", "fever"),
    ("hi", "chief_complaint.primary", "pet mein dard hai", "abdominal_pain"),
    ("hi", "chief_complaint.primary", "pet dard ho raha hai", "abdominal_pain"),
    ("hi", "chief_complaint.primary", "saans phool rahi hai", "breathlessness"),
    ("hi", "chief_complaint.primary", "saans lene mein takleef", "breathlessness"),
    ("hi", "chief_complaint.primary", "sar dard hai", "headache"),
    ("hi", "chief_complaint.primary", "sir mein dard", "headache"),
    ("hi", "chief_complaint.primary", "khaansi aa rahi hai", "cough"),
    ("hi", "chief_complaint.primary", "bahut khaansi hai", "cough"),
    # ---- chief complaint : English -------------------------------------
    ("en", "chief_complaint.primary", "i have chest pain", "chest_pain"),
    ("en", "chief_complaint.primary", "pain in my chest", "chest_pain"),
    ("en", "chief_complaint.primary", "i am having fever", "fever"),
    ("en", "chief_complaint.primary", "running a temperature", "fever"),
    ("en", "chief_complaint.primary", "stomach pain", "abdominal_pain"),
    ("en", "chief_complaint.primary", "pain in the abdomen", "abdominal_pain"),
    ("en", "chief_complaint.primary", "shortness of breath", "breathlessness"),
    ("en", "chief_complaint.primary", "i cannot breathe properly", "breathlessness"),
    ("en", "chief_complaint.primary", "i have a headache", "headache"),
    ("en", "chief_complaint.primary", "my head hurts", "headache"),
    ("en", "chief_complaint.primary", "i have a cough", "cough"),
    ("en", "chief_complaint.primary", "bad cough", "cough"),
    # ---- chief complaint : Tamil ---------------------------------------
    ("ta", "chief_complaint.primary", "nenju vali", "chest_pain"),
    ("ta", "chief_complaint.primary", "kaaichal iruku", "fever"),
    ("ta", "chief_complaint.primary", "vayiru vali", "abdominal_pain"),
    ("ta", "chief_complaint.primary", "moochu vaanga kashtama iruku", "breathlessness"),
    ("ta", "chief_complaint.primary", "thalai vali", "headache"),
    ("ta", "chief_complaint.primary", "irumal iruku", "cough"),

    # ---- onset ---------------------------------------------------------
    ("hi", "hpi.onset", "aaj se", "today"),
    ("hi", "hpi.onset", "aaj subah se", "today"),
    ("hi", "hpi.onset", "kal se", "yesterday"),
    ("hi", "hpi.onset", "is hafte se", "this_week"),
    ("hi", "hpi.onset", "is mahine se", "this_month"),
    ("hi", "hpi.onset", "ek mahine se zyada", "over_a_month"),
    ("en", "hpi.onset", "since today", "today"),
    ("en", "hpi.onset", "started yesterday", "yesterday"),
    ("en", "hpi.onset", "for the past week", "this_week"),
    ("en", "hpi.onset", "about a month", "this_month"),
    ("en", "hpi.onset", "more than a month", "over_a_month"),
    ("ta", "hpi.onset", "innaikku", "today"),
    ("ta", "hpi.onset", "nettru", "yesterday"),

    # ---- character -----------------------------------------------------
    ("hi", "socrates.character", "jalan jaisa dard", "burning"),
    ("hi", "socrates.character", "dabaav jaisa lagta hai", "squeezing"),
    ("hi", "socrates.character", "chubhan jaisa", "stabbing"),
    ("hi", "socrates.character", "halka dard", "dull"),
    ("hi", "socrates.character", "marod jaisa", "cramping"),
    ("en", "socrates.character", "burning pain", "burning"),
    ("en", "socrates.character", "squeezing feeling", "squeezing"),
    ("en", "socrates.character", "stabbing", "stabbing"),
    ("en", "socrates.character", "dull ache", "dull"),
    ("en", "socrates.character", "cramping pain", "cramping"),

    # ---- radiation -----------------------------------------------------
    ("hi", "socrates.radiation", "baaye haath mein jaata hai", "left_arm"),
    ("hi", "socrates.radiation", "jabde tak", "jaw"),
    ("hi", "socrates.radiation", "peeth tak", "back"),
    ("hi", "socrates.radiation", "kahin nahi", "nowhere"),
    ("en", "socrates.radiation", "goes to my left arm", "left_arm"),
    ("en", "socrates.radiation", "up to the jaw", "jaw"),
    ("en", "socrates.radiation", "into my back", "back"),
    ("en", "socrates.radiation", "nowhere", "nowhere"),

    # ---- associated (multi) --------------------------------------------
    ("hi", "hpi.associated", "saans phool rahi hai", "dyspnoea"),
    ("hi", "hpi.associated", "thanda paseena aa raha hai", "diaphoresis"),
    ("hi", "hpi.associated", "ji michla raha hai", "nausea"),
    ("hi", "hpi.associated", "ulti ho rahi hai", "vomiting"),
    ("hi", "hpi.associated", "dil ki dhadkan tez", "palpitations"),
    ("en", "hpi.associated", "shortness of breath", "dyspnoea"),
    ("en", "hpi.associated", "cold sweat", "diaphoresis"),
    ("en", "hpi.associated", "feeling nauseous", "nausea"),
    ("en", "hpi.associated", "vomiting", "vomiting"),
    ("en", "hpi.associated", "heart racing", "palpitations"),

    # ---- timing / exacerbating ----------------------------------------
    ("hi", "hpi.timing", "lagataar rehta hai", "constant"),
    ("hi", "hpi.timing", "aata jaata rehta hai", "intermittent"),
    ("hi", "hpi.timing", "chalne par badhta hai", "on_exertion"),
    ("en", "hpi.timing", "it is constant", "constant"),
    ("en", "hpi.timing", "comes and goes", "intermittent"),
    ("en", "hpi.timing", "when i walk", "on_exertion"),
    ("hi", "hpi.exacerbating", "chalne par", "walking"),
    ("hi", "hpi.exacerbating", "saans lene par", "breathing"),
    ("hi", "hpi.exacerbating", "khaane ke baad", "eating"),
    ("hi", "hpi.exacerbating", "letne par", "lying_down"),
    ("en", "hpi.exacerbating", "on walking", "walking"),
    ("en", "hpi.exacerbating", "when breathing", "breathing"),
    ("en", "hpi.exacerbating", "after eating", "eating"),

    # ---- fever pattern -------------------------------------------------
    ("hi", "fever.pattern", "lagataar bukhar", "continuous"),
    ("hi", "fever.pattern", "kabhi kabhi aata hai", "intermittent"),
    ("hi", "fever.pattern", "kaampte hue bukhar", "with_chills"),
    ("hi", "fever.pattern", "sirf raat ko", "night_only"),
    ("en", "fever.pattern", "continuous fever", "continuous"),
    ("en", "fever.pattern", "with chills and shivering", "with_chills"),

    # ---- yes / no ------------------------------------------------------
    ("hi", "past_medical.surgery", "haan hua tha", "yes"),
    ("hi", "past_medical.surgery", "nahi", "no"),
    ("hi", "drug_allergy.current_medication", "haan leta hoon", "yes"),
    ("hi", "drug_allergy.current_medication", "nahi leta", "no"),
    ("hi", "drug_allergy.known_allergy", "haan hai", "yes"),
    ("hi", "drug_allergy.known_allergy", "nahi hai", "no"),
    ("en", "drug_allergy.current_medication", "yes i take medicines", "yes"),
    ("en", "drug_allergy.current_medication", "no medication", "no"),
    ("en", "drug_allergy.known_allergy", "no allergies", "no"),

    # ---- past medical (multi) ------------------------------------------
    ("hi", "past_medical.diagnosed_conditions", "shugar hai", "diabetes"),
    ("hi", "past_medical.diagnosed_conditions", "bp ki bimari", "hypertension"),
    ("hi", "past_medical.diagnosed_conditions", "dama hai", "asthma"),
    ("en", "past_medical.diagnosed_conditions", "i am diabetic", "diabetes"),
    ("en", "past_medical.diagnosed_conditions", "high blood pressure", "hypertension"),
    ("en", "past_medical.diagnosed_conditions", "no such condition", "none"),

    # ---- personal ------------------------------------------------------
    ("hi", "personal.tobacco", "kabhi nahi", "never"),
    ("hi", "personal.tobacco", "abhi leta hoon", "current"),
    ("hi", "personal.alcohol", "kabhi kabhi", "occasional"),
    ("en", "personal.tobacco", "never used tobacco", "never"),
    ("en", "personal.alcohol", "i drink regularly", "regular"),

    # ---- review of systems --------------------------------------------
    ("hi", "ros.weight_loss", "haan wazan kam hua", "yes"),
    ("hi", "ros.weight_loss", "nahi", "no"),
    ("hi", "ros.appetite", "bhookh kam ho gayi", "reduced"),
    ("hi", "ros.appetite", "theek hai", "normal"),
    ("en", "ros.weight_loss", "yes i lost weight", "yes"),
    ("en", "ros.appetite", "reduced appetite", "reduced"),

    # ---- AYUSH prakriti ------------------------------------------------
    ("hi", "prakriti.body_frame", "patla sharir", "body_frame.thin"),
    ("hi", "prakriti.body_frame", "madhyam", "body_frame.medium"),
    ("hi", "prakriti.body_frame", "bhaari sharir", "body_frame.heavy"),
    ("hi", "prakriti.skin", "rookhi twacha", "skin.dry"),
    ("hi", "prakriti.appetite", "aniyamit bhookh", "appetite.irregular"),
    ("hi", "prakriti.sleep", "kachi neend", "sleep.light"),
    ("hi", "prakriti.temperament", "chintit rehta hoon", "temperament.anxious"),
    ("en", "prakriti.body_frame", "thin build", "body_frame.thin"),
    ("en", "prakriti.skin", "dry skin", "skin.dry"),
    ("en", "prakriti.temperament", "i am calm", "temperament.calm"),
]


# Negation cue words per language. These are the words whose presence means
# the patient is DENYING something -- used to distinguish "no chest pain" from
# "chest pain", a distinction that changes clinical meaning entirely.
NEGATION_CUES: dict[str, list[str]] = {
    "en": ["no", "not", "none", "never", "without", "nil", "denies"],
    "hi": ["nahi", "nahin", "koi", "bina"],
    "ta": ["illai", "illa", "kidaiyathu"],
}


# Curated base lexicon: the KEY words per option, per language. Unlike the
# labelled utterances (which are whole phrases), these are the content tokens a
# matcher should key on, so an unseen phrasing that contains the key word still
# matches. This is what lets the model generalise beyond the exact sentences it
# was shown, and it is fully auditable -- anyone can read and correct it.
#
# (lang, field_code) -> {option_code: [keyword, ...]}
BASE_LEXICON: dict[tuple[str, str], dict[str, list[str]]] = {
    ("hi", "chief_complaint.primary"): {
        "chest_pain": ["seene", "chaati", "seena", "seene mein dard"],
        "fever": ["bukhar", "taap"],
        "abdominal_pain": ["pet", "pet dard"],
        "breathlessness": ["saans", "saans phool", "dam"],
        "headache": ["sar", "sir", "sardard", "sar dard"],
        "cough": ["khaansi", "khansi"],
    },
    ("en", "chief_complaint.primary"): {
        "chest_pain": ["chest", "chest pain"],
        "fever": ["fever", "temperature"],
        "abdominal_pain": ["stomach", "abdomen", "belly"],
        "breathlessness": ["breath", "breathing", "breathless"],
        "headache": ["headache", "head"],
        "cough": ["cough"],
    },
    ("ta", "chief_complaint.primary"): {
        "chest_pain": ["nenju", "nenju vali"],
        "fever": ["kaaichal", "juram"],
        "abdominal_pain": ["vayiru", "vayiru vali"],
        "breathlessness": ["moochu"],
        "headache": ["thalai", "thalai vali"],
        "cough": ["irumal"],
    },
    ("hi", "hpi.onset"): {
        "today": ["aaj"], "yesterday": ["kal"], "this_week": ["hafte", "hafta"],
        "this_month": ["mahine", "mahina"], "over_a_month": ["zyada", "purana"],
    },
    ("en", "hpi.onset"): {
        "today": ["today"], "yesterday": ["yesterday"],
        "this_week": ["week"], "this_month": ["month"],
        "over_a_month": ["more than a month", "longer"],
    },
    ("ta", "hpi.onset"): {"today": ["innaikku"], "yesterday": ["nettru"]},
    ("hi", "socrates.character"): {
        "burning": ["jalan"], "squeezing": ["dabaav", "dabav"],
        "stabbing": ["chubhan", "chubh"], "dull": ["halka"],
        "cramping": ["marod", "maror"],
    },
    ("hi", "socrates.radiation"): {
        "left_arm": ["baaye", "baayaan", "haath"], "jaw": ["jabde", "jabda"],
        "back": ["peeth", "peet", "kamar"], "shoulder": ["kandha"],
        "nowhere": ["kahin nahi", "kahin"],
    },
    ("en", "socrates.radiation"): {
        "left_arm": ["arm", "left arm"], "jaw": ["jaw"], "back": ["back"],
        "shoulder": ["shoulder"], "nowhere": ["nowhere"],
    },
    ("hi", "hpi.associated"): {
        "dyspnoea": ["saans", "saans phool"], "diaphoresis": ["paseena", "pasina"],
        "nausea": ["michla", "michli", "ji michla"], "vomiting": ["ulti", "ulTi"],
        "palpitations": ["dhadkan"],
    },
    ("en", "hpi.associated"): {
        "dyspnoea": ["breath", "breathless"], "diaphoresis": ["sweat", "sweating"],
        "nausea": ["nausea", "nauseous"], "vomiting": ["vomit", "vomiting"],
        "palpitations": ["palpitation", "racing", "heart racing"],
    },
    ("hi", "hpi.timing"): {
        "constant": ["lagataar", "lagatar"], "intermittent": ["aata jaata", "kabhi"],
        "on_exertion": ["chalne", "chalne par"],
    },
    ("hi", "hpi.exacerbating"): {
        "walking": ["chalne", "chal"], "breathing": ["saans"],
        "eating": ["khaane", "khaana", "khana"], "lying_down": ["letne", "let"],
        "nothing": ["kuch nahi"],
    },
    ("hi", "fever.pattern"): {
        "continuous": ["lagataar", "lagatar"], "intermittent": ["kabhi kabhi"],
        "with_chills": ["kaamp", "kaampte", "thand"], "night_only": ["raat"],
    },
    ("en", "fever.pattern"): {
        "continuous": ["continuous"], "with_chills": ["chills", "shivering"],
        "intermittent": ["comes and goes"], "night_only": ["night"],
    },
    ("hi", "personal.tobacco"): {
        "never": ["kabhi nahi", "nahi"], "current": ["abhi", "leta", "leti"],
        "former": ["pehle", "chhod"],
    },
    ("hi", "personal.alcohol"): {
        "never": ["nahi", "kabhi nahi"], "occasional": ["kabhi kabhi"],
        "regular": ["roz", "niyamit"],
    },
    ("hi", "ros.appetite"): {
        "normal": ["theek", "sahi"], "reduced": ["kam"], "increased": ["zyada", "badh"],
    },
    ("hi", "past_medical.diagnosed_conditions"): {
        "diabetes": ["shugar", "sugar", "madhumeh"], "hypertension": ["bp", "blood pressure"],
        "asthma": ["dama", "dama"], "tuberculosis": ["tb", "ksay"],
        "cardiac_disease": ["dil"], "thyroid_disorder": ["thyroid"], "none": ["koi nahi"],
    },
    ("hi", "prakriti.body_frame"): {
        "body_frame.thin": ["patla", "patli", "dubla"],
        "body_frame.medium": ["madhyam", "theek"],
        "body_frame.heavy": ["bhaari", "mota", "moti"],
    },
    ("hi", "prakriti.skin"): {
        "skin.dry": ["rookhi", "rukhi", "sookhi"],
        "skin.warm_oily": ["garam", "tel"], "skin.cool_smooth": ["thandi", "chikni"],
    },
    ("hi", "prakriti.appetite"): {
        "appetite.irregular": ["aniyamit", "badalti"],
        "appetite.strong": ["tez", "achhi"], "appetite.steady_low": ["kam"],
    },
    ("hi", "prakriti.sleep"): {
        "sleep.light": ["kachi", "halki"], "sleep.moderate": ["theek"],
        "sleep.deep": ["gehri", "gahri"],
    },
    ("hi", "prakriti.temperament"): {
        "temperament.anxious": ["chintit", "pareshan"],
        "temperament.irritable": ["chidchida", "gussa"],
        "temperament.calm": ["shaant", "shant"],
    },
    ("hi", "prakriti.climate"): {
        "climate.dislikes_cold": ["thand"], "climate.dislikes_heat": ["garmi"],
        "climate.dislikes_damp": ["nami", "seelan"],
    },
    ("en", "prakriti.body_frame"): {
        "body_frame.thin": ["thin", "slim"], "body_frame.medium": ["medium"],
        "body_frame.heavy": ["heavy", "large"],
    },
    ("en", "prakriti.skin"): {
        "skin.dry": ["dry"], "skin.warm_oily": ["oily", "warm"],
        "skin.cool_smooth": ["cool", "smooth"],
    },
    ("en", "prakriti.temperament"): {
        "temperament.anxious": ["anxious", "worried"],
        "temperament.irritable": ["irritable"], "temperament.calm": ["calm"],
    },
    # yes/no across the boolean fields
    ("hi", "past_medical.surgery"): {"yes": ["haan", "hua"], "no": ["nahi"]},
    ("hi", "drug_allergy.current_medication"): {"yes": ["haan", "leta", "leti"], "no": ["nahi"]},
    ("hi", "drug_allergy.known_allergy"): {"yes": ["haan"], "no": ["nahi"]},
    ("hi", "ros.weight_loss"): {"yes": ["haan", "kam hua"], "no": ["nahi"]},
    ("en", "drug_allergy.current_medication"): {"yes": ["yes", "take"], "no": ["no"]},
    ("en", "drug_allergy.known_allergy"): {"yes": ["yes"], "no": ["no"]},
    ("en", "ros.weight_loss"): {"yes": ["yes", "lost"], "no": ["no"]},
    ("en", "ros.appetite"): {"normal": ["normal"], "reduced": ["reduced", "less"],
                             "increased": ["increased", "more"]},
    ("en", "past_medical.diagnosed_conditions"): {
        "diabetes": ["diabetic", "diabetes"], "hypertension": ["blood pressure", "hypertension"],
        "asthma": ["asthma"], "none": ["none", "no such"],
    },
    ("en", "personal.tobacco"): {"never": ["never"], "current": ["currently", "smoke"],
                                 "former": ["used to", "quit"]},
    ("en", "personal.alcohol"): {"never": ["never"], "occasional": ["occasional", "sometimes"],
                                 "regular": ["regular", "daily"]},
}
