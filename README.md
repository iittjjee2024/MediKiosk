# MediKiosk

Pre-consultation clinical history capture for Indian public hospital OPDs.

A patient records a structured medical history by voice or touch, in their own
language, during the hours they already spend waiting. The physician receives a
cited, provenance-linked draft summary before the patient enters the room.

## The architectural claim

The defining decision is a hard **determinism boundary**. AI is confined to
perception. Every clinical decision surface is a deterministic, versioned rule
engine.

| AI does this (perception)        | Determinism does this (clinical decisions) |
| -------------------------------- | ------------------------------------------ |
| Language identification          | Which question is asked next               |
| Speech recognition               | Whether a required field applies           |
| Clinical NLU / slot extraction   | Whether a red flag fires                   |
| OCR and layout understanding     | Whether two facts conflict                 |
| Medical entity extraction        | Whether a fact is admitted at all          |
| Evidence-constrained draft prose | Chronological ordering, completeness score  |

Three properties follow, and none are available from a general-purpose
conversational agent:

1. **Reproducibility** — identical facts always yield the identical next
   question, because those paths contain no sampling.
2. **Auditability** — questionnaires and red-flag rules are versioned rows with
   effective dates, so any historical session can be explained by the exact
   protocol that governed it.
3. **Traceability** — every fact carries source, extraction method, model
   version, confidence and timestamp; every summary sentence carries citations,
   and an uncited sentence is mechanically removed before publication.

Nothing enters the clinical record without physician approval.

---

## Starting the application

Two services: a FastAPI backend (port 8000) and a Next.js frontend (port 3000).
The frontend proxies every `/api/*` call to the backend, so once both are up
you only ever open **<http://localhost:3000>**.

Prerequisites: **Python 3.12+** and **Node 20+** (the build was verified on
Node 22). No database server is needed for the demo — the backend uses SQLite
and creates the file on first run.

### Option A — Docker (whole stack, one command)

```bash
cd medikiosk
docker compose up --build
```

Wait for the `api` service to report healthy, then open:

- App:      <http://localhost:3000>
- API docs: <http://localhost:8000/docs>

To start with the demo already populated (four sample patients, alerts,
analytics), set the seed flag before bringing the stack up:

```bash
# Linux / macOS
MEDIKIOSK_SEED_DEMO=1 docker compose up --build
```
```powershell
# Windows PowerShell
$env:MEDIKIOSK_SEED_DEMO="1"; docker compose up --build
```

### Option B — run locally (recommended for the demo)

Use two terminals. Start the backend first.

**Terminal 1 — backend** (from `medikiosk/backend`):

```powershell
# Windows PowerShell
cd medikiosk\backend
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# demo-ready: SQLite + pre-seeded sample sessions
$env:MEDIKIOSK_DATABASE_URL = "sqlite+pysqlite:///./medikiosk_demo.db"
$env:MEDIKIOSK_SEED_DEMO    = "1"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
# Linux / macOS
cd medikiosk/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export MEDIKIOSK_DATABASE_URL="sqlite+pysqlite:///./medikiosk_demo.db"
export MEDIKIOSK_SEED_DEMO=1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On first start the backend automatically, and idempotently:
creates the schema · seeds the clinical protocol · creates the demo staff
accounts · **fits the clinical NLU model** if the artifact is missing · and,
when `MEDIKIOSK_SEED_DEMO=1`, populates four realistic completed sessions.

Confirm it is up: <http://127.0.0.1:8000/api/v1/health/ready> should return
`{"status":"ready","database":"ok"}`.

Drop `MEDIKIOSK_SEED_DEMO` to start with an empty worklist (then create
patients live at `/kiosk`). Delete `medikiosk_demo.db` to reset all data.

**Terminal 2 — frontend** (from `medikiosk/frontend`):

```powershell
# Windows PowerShell
cd medikiosk\frontend
npm install
$env:MEDIKIOSK_API = "http://127.0.0.1:8000"   # backend URL for the proxy
npm run dev
```

```bash
# Linux / macOS
cd medikiosk/frontend
npm install
MEDIKIOSK_API=http://127.0.0.1:8000 npm run dev
```

Then open **<http://localhost:3000>**.

> **Cloud-synced folders (Google Drive / OneDrive / Dropbox).** `npm install`
> fails on these volumes with `tar` write errors — the virtual filesystem
> cannot handle the volume of small file writes, and a junction to local disk
> is not possible because the drive is not NTFS. Copy `frontend/` to a local
> path (for example `C:\medikiosk-frontend`), run `npm install` and
> `npm run dev` there, and point it at the backend with the same
> `MEDIKIOSK_API` variable. Re-copy the `src/` and `public/` folders whenever
> you change frontend source. The backend is unaffected and runs fine from the
> synced folder.

### Fit or re-fit the clinical NLU (optional)

The NLU model artifact (`app/nlu/nlu_model.json`) is fitted automatically on
first backend start. To fit and evaluate it explicitly — for example after
editing the labelled utterances or the keyword lexicon:

```bash
cd medikiosk/backend
python -m app.nlu.fit          # fit, evaluate on a held-out split, write artifact
python -m app.nlu.fit --eval   # evaluate the existing artifact only
```

The fit prints held-out top-1 accuracy per language and exits non-zero if it
falls below 80%.

### Demo accounts

Password for all: `medikiosk-demo`

| Username           | Role                 | Can see                                |
| ------------------ | -------------------- | -------------------------------------- |
| `dr.rao`           | `physician`          | Worklist, cited summaries, approval    |
| `dr.iyer`          | `ayush_practitioner` | Same, for AYUSH sessions               |
| `nurse.devi`       | `nurse`              | Triage alerts only                     |
| `admin.it`         | `it_admin`           | Operations. **No clinical data**       |
| `officer.privacy`  | `privacy_officer`    | Audit chain. **No clinical content**   |

The last two separations are deliberate: administrative capability and clinical
data access are distinct privileges.

---

## Demo path

With `MEDIKIOSK_SEED_DEMO=1` the physician, triage and analytics screens are
already populated, so you can start anywhere. The four seeded patients are:

| Patient | What it demonstrates |
| ------- | -------------------- |
| **Kamla Devi** | Chest pain + a fired cardiac red flag + a document conflict (she denies medication, her scanned prescription shows drugs) |
| **Ramesh Kumar** | Fever + a low-confidence voice answer that was **withheld** and sent for human verification |
| **Lakshmi Iyer** | AYUSH session with a transparent Vata-dominant Prakriti assessment |
| **Suresh Patil** | A clean baseline case for contrast |

A good live run:

1. **`/physician`** (`dr.rao`) — the two escalated sessions surface first. Open
   *Kamla Devi* and **click any sentence**: the facts behind it appear with full
   provenance — source document, page, extraction method, model version,
   confidence, timestamp. Note the flagged medication conflict. Edit or reject a
   fact, then approve; the FHIR bundle is built and the export queued.
2. **`/triage`** (`nurse.devi`) — the critical `CARDIAC_ACS_SUSPICION` alert is
   there with its rule version, triggering facts and SLA deadline. Acknowledge
   it.
3. **`/analytics`** — grounding pass rate, physician edit rate, withheld-fact
   count, conflict count, SLA adherence. Deliberately surfaces the metrics that
   can embarrass the platform.
4. **`/kiosk`** — run a fresh patient. Pick a language, grant audio-explained
   consent, and answer by **voice or touch**. Voice transcripts are interpreted
   by the fitted clinical NLU; choose *chest pain* then *breathlessness* and a
   critical red flag fires live. Scan documents from the built-in fixtures.

The voice and document flows are real endpoints, not mocks. Interpret a Hindi
transcript directly:

```bash
curl -X POST http://localhost:8000/api/v1/interview/voice \
  -H "Authorization: Bearer <session-token>" \
  -H "Idempotency-Key: <uuid>" \
  -H "Content-Type: application/json" \
  -d '{"field_code":"chief_complaint.primary",
       "transcript":"seene mein dard ho raha hai","asr_confidence":0.95}'
```

To see the confidence gate withhold an answer, send a low-confidence one:

```bash
curl -X POST http://localhost:8000/api/v1/interview/answers \
  -H "Authorization: Bearer <session-token>" \
  -H "Idempotency-Key: <uuid>" \
  -H "Content-Type: application/json" \
  -d '{"field_code":"hpi.onset","value":"today","input_mode":"voice",
       "asr_confidence":0.22,"nlu_confidence":0.20}'
```

It returns `fact_admitted: false` and creates a verification item. Guessing is
worse than declaring uncertainty in a clinical record.

---

## Layout

```
medikiosk/
├── backend/
│   ├── app/
│   │   ├── engines/            # deterministic. no DB, no network, no sampling
│   │   │   ├── rules.py            one condition grammar for dependencies
│   │   │   │                       AND red-flag conditions
│   │   │   ├── question_engine.py  next field, applicability, completeness
│   │   │   ├── redflag_engine.py   versioned rules, fail-safe on error
│   │   │   ├── confidence.py       the admission gate
│   │   │   ├── conflict.py         answer vs document contradictions
│   │   │   ├── timeline.py         ordering with honest date precision
│   │   │   └── summary_builder.py  template + citations + grounding
│   │   ├── nlu/                # clinical NLU (perception, fitted lexicon)
│   │   │   ├── clinical_nlu.py     transcript -> option code + confidence
│   │   │   ├── training_data.py    labelled utterances + keyword lexicon
│   │   │   ├── fit.py              fit + calibrate + held-out evaluate
│   │   │   └── nlu_model.json      the fitted artifact
│   │   ├── services/           # transactions, consent, provenance, audit
│   │   │   ├── intake.py           gated answer path -> facts + red flags
│   │   │   ├── perception.py       voice transcript -> gated answer
│   │   │   ├── documents.py        OCR pipeline (fixtures) -> facts
│   │   │   └── summary.py          generation, review, FHIR export
│   │   ├── models.py           # provenance and citation are first-class
│   │   ├── seed.py             # clinical protocol AS DATA
│   │   ├── demo_seed.py        # four realistic sessions, via the real services
│   │   └── api.py
│   ├── tests/                  # 127 tests (engines, API, NLU, perception)
│   └── smoke_e2e.py            # 69 checks against a running server
└── frontend/
    ├── src/app/kiosk/          # patient interview, voice + touch
    ├── src/app/physician/      # cited summary + provenance viewer
    ├── src/app/triage/         # red-flag console with SLA
    ├── src/lib/offline.ts      # IndexedDB queue, idempotent replay
    └── public/sw.js            # app-shell cache (never caches clinical API)
```

---

## Verification

```bash
cd backend  && python -m pytest          # 127 passed
cd backend  && python -m app.nlu.fit     # NLU held-out ~97%, artifact written
cd frontend && npm run typecheck         # 0 errors
cd frontend && npm run build             # 6 routes compiled
```

End-to-end against a live server:

```bash
python -m uvicorn app.main:app --port 8000    # terminal 1
python smoke_e2e.py                           # terminal 2 -> 69 passed
```

The most important test is `test_determinism_over_shuffled_answer_order`. It
shuffles both the answers and the questionnaire's question list, then asserts
the engine still selects the same next field every time. If that ever fails,
the central claim of this architecture is void.

The NLU has its own gate: `test_held_out_accuracy_meets_gate` fits on a
training split and requires ≥80% top-1 accuracy on unseen utterances, so the
lexicon has to generalise rather than merely memorise.

Seven real defects were caught by these tests during development, including
three that would have shipped silently: SQLAlchemy column defaults not applying
to unattached objects (every question read as not-required); a second-
resolution patient identifier that would collide constantly at 4,000+
registrations per day; and a field-option map that let the AYUSH protocol
overwrite the allopathic options for a shared field code, which the NLU
evaluation exposed.

---

## What this deliberately does not do

| Excluded                                     | Why                                                                 |
| -------------------------------------------- | ------------------------------------------------------------------- |
| Diagnose                                     | A physician act. No differential, no probability, no suggestion.    |
| Prescribe or suggest treatment               | Out of scope entirely.                                              |
| Write to the record autonomously             | Physician approval is mandatory and atomic.                         |
| Triage autonomously                          | The engine alerts. It never reorders the queue.                      |
| Require ABHA                                 | Improves continuity, but is not a precondition for value.            |
| Send health data to third-party LLM endpoints | Inference is self-hostable; enforced by egress allow-list.          |
| Use blockchain                                | No decentralised trust problem. Hash-chained audit gives tamper evidence at a fraction of the cost. |

---

## MVP vs production

Production-grade in this build: the determinism boundary, fact-level
provenance, citation-indexed summaries with grounding validation, the physician
approval state machine, conflict detection, timeline construction, the audit
hash chain, and the transactional integration outbox.

Not yet built, and honestly scoped:

| Area                | Status                                                            |
| ------------------- | ----------------------------------------------------------------- |
| Clinical NLU        | Fitted lexicon matcher, calibrated confidence, ~97% held-out on the labelled set. Interprets voice transcripts into the gated answer path. Trained on a hand-authored corpus, not real waiting-hall speech. |
| ASR / TTS           | Browser Web Speech API in the kiosk. Bhashini / IndicConformer integration is designed but not wired. |
| OCR pipeline        | Full pipeline runs — quality gate, entity extraction, normalisation, provenance, confidence gating, unreadable-region handling, conflict detection. OCR itself is fixture-driven; Surya inference not wired. |
| ABDM                | FHIR bundle is built and queued. No live HIP onboarding.           |
| Hospital HIS        | Outbox and adapter seam exist. No live integration.                |
| Drug interactions   | Absence is explicitly stated in the summary rather than implied.   |
| Kafka / Redis       | Modelled as the outbox and sync tables. Single-process for now.    |
| Languages           | Hindi, English, Tamil UI and NLU. Protocol supports any number.    |
| Accuracy figures    | The ~97% NLU figure is on a hand-authored corpus, not clinical field data. No clinical accuracy claimed — that requires a pilot dataset. |

Also required before any deployment: clinical committee sign-off on the
questionnaires and red-flag rules, and qualified Ayurvedic practitioner sign-off
on the Dashavidha Pariksha content and dosha weightings.

## Privacy posture

Designed to support DPDP 2023-aligned privacy and data-governance requirements
and the ABDM consent framework. **No compliance claim is made** — that requires
review by qualified counsel.

Implemented: consent before any clinical question with four independent
revocable scopes and audio explanation; no Aadhaar stored; audio treated as
transient; tenant isolation; RBAC where IT and privacy roles hold no clinical
data access; immutable hash-chained audit of consent, access, approval and
export; kiosk session teardown between patients.

## Licence note

`Surya` (planned OCR) ships Apache-2.0 code with model weights under a modified
AI-Pubs Open-RAIL-M licence carrying commercial thresholds. Confirm the licence
position for your deployment scale, or select an alternative, before commercial
use.
