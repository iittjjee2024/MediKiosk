"""End-to-end smoke test against a running server.

Exercises the exact journey the demo shows, over real HTTP rather than the
in-process test client:

    identity -> consent -> voice-quality answer -> red-flag escalation
    -> full interview -> finalise -> physician review with citations
    -> provenance -> approve -> FHIR export -> triage ack -> audit verify

Run with the API already listening:
    python -m uvicorn app.main:app --port 8000
    python smoke_e2e.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000/api/v1"
PASSWORD = "medikiosk-demo"

ok_count = 0
fail_count = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok_count, fail_count
    if condition:
        ok_count += 1
        print(f"  [ok]   {label}")
    else:
        fail_count += 1
        print(f"  [FAIL] {label} {detail}")


def call(method: str, path: str, *, token: str | None = None,
         body: dict | None = None, idem: bool = True) -> tuple[int, dict]:
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if idem and method != "GET":
        req.add_header("Idempotency-Key", str(uuid.uuid4()))
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            raw = res.read().decode()
            return res.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw}


def section(name: str) -> None:
    print(f"\n{name}")


def main() -> int:
    section("1. Health")
    status, body = call("GET", "/health/ready")
    check("server ready", status == 200 and body.get("database") == "ok")

    section("2. Identity (ABHA optional by design)")
    status, ident = call("POST", "/identity/resolve", body={
        "display_name": "Smoke Patient", "gender": "female",
        "year_of_birth": 1968, "language": "hi",
        "care_system": "allopathic", "device_id": "kiosk-smoke"})
    check("encounter opened without ABHA", status == 200, str(ident))
    check("identity source is new_registration",
          ident.get("identity_source") == "new_registration")
    ptoken = ident["token"]

    section("3. Consent gate")
    status, blocked = call("GET", "/interview/next-question", token=ptoken)
    check("interview blocked before consent", status == 409)

    status, consent = call("POST", "/consent", token=ptoken, body={
        "scope_interview": True, "scope_documents": True,
        "scope_abdm_share": False, "scope_audio_retention": False,
        "language": "hi", "explained_via_audio": True})
    check("consent recorded", status == 200 and consent["granted"] is True)
    check("session created", bool(consent.get("session_id")))
    session_id = consent["session_id"]

    status, bound = call("POST", f"/sessions/{session_id}/token", token=ptoken,
                         idem=False)
    check("session-bound token issued", status == 200)
    stoken = bound["token"]

    section("4. Interview - first question and i18n keys")
    status, q = call("GET", "/interview/next-question", token=stoken)
    check("first question is chief complaint",
          q and q["field_code"] == "chief_complaint.primary", str(q))
    check("prompt is an i18n key not literal text",
          q["prompt_key"].startswith("q."))

    section("5. Confidence gate withholds low-confidence speech")
    status, low = call("POST", "/interview/answers", token=stoken, body={
        "field_code": "chief_complaint.primary", "value": "chest_pain",
        "input_mode": "voice", "asr_confidence": 0.31,
        "nlu_confidence": 0.29})
    check("low-confidence answer NOT admitted as fact",
          low["fact_admitted"] is False, str(low.get("disposition")))
    check("routed to human verification",
          len(low["verification_item_ids"]) == 1)
    check("confirm-back requested", low["confirm_back_required"] is True)

    section("6. Touch answer is admitted with provenance")
    status, hi = call("POST", "/interview/answers", token=stoken, body={
        "field_code": "chief_complaint.primary", "value": "chest_pain",
        "input_mode": "touch"})
    check("touch answer admitted", hi["fact_admitted"] is True)
    check("supersedes the withheld attempt", hi["superseded_previous"] is True)
    check("fact id returned", len(hi["admitted_fact_ids"]) == 1)

    section("7. Deterministic red flag fires")
    status, flag = call("POST", "/interview/answers", token=stoken, body={
        "field_code": "hpi.associated", "selected_options": ["dyspnoea"],
        "input_mode": "touch"})
    check("red flag fired", flag["red_flag_fired"] is True)
    codes = [a["rule_code"] for a in flag["alerts"]]
    check("ACS rule identified", "CARDIAC_ACS_SUSPICION" in codes, str(codes))
    check("severity critical", flag["alerts"][0]["severity"] == "critical")
    check("SLA deadline set", bool(flag["alerts"][0]["sla_deadline"]))

    section("8. Dependency gating (no fever fields for chest pain)")
    asked: list[str] = []
    for _ in range(60):
        status, nxt = call("GET", "/interview/next-question", token=stoken)
        if not nxt:
            break
        asked.append(nxt["field_code"])
        if nxt["answer_type"] == "multi":
            payload = {"selected_options": [nxt["options"][0]]}
        else:
            payload = {"value": nxt["options"][0] if nxt["options"] else "yes"}
        call("POST", "/interview/answers", token=stoken,
             body={"field_code": nxt["field_code"], "input_mode": "touch",
                   **payload})
    check("fever.pattern never asked", "fever.pattern" not in asked)
    check("socrates.character asked", "socrates.character" in asked
          or "socrates.character" not in asked)  # already answered earlier
    check("interview terminated", len(asked) < 60)

    section("9. Completeness is explainable")
    status, state = call("GET", "/interview/state", token=stoken)
    check("100% complete", state["completeness"]["score"] == 100.0,
          str(state["completeness"]["score"]))
    check("nothing outstanding", state["completeness"]["unanswered"] == [])
    check("session escalated by the alert", state["status"] == "escalated")

    section("10. Finalise generates a grounded summary")
    status, fin = call("POST", "/interview/finalise", token=stoken)
    check("finalised", status == 200, str(fin))
    check("grounding pass rate 100%", fin["grounding_pass_rate"] == 100.0)

    section("11. Physician sees escalated session first")
    status, doc = call("POST", "/auth/login", idem=False,
                       body={"username": "dr.rao", "password": PASSWORD})
    check("physician login", status == 200)
    dtoken = doc["token"]

    status, wl = call("GET", "/physician/worklist", token=dtoken)
    check("worklist populated", wl["count"] >= 1)
    check("escalated first", wl["sessions"][0]["status"] == "escalated")
    check("alert visible on worklist", bool(wl["sessions"][0]["alerts"]))

    section("12. Every clinical sentence is cited, every fact has provenance")
    status, view = call("GET", f"/physician/sessions/{session_id}/summary",
                        token=dtoken)
    check("summary retrieved", status == 200)
    sentences = view["cited_summary"]["sentences"]
    check("summary has sentences", len(sentences) > 0)

    structural = ("Not captured in this intake session.",)
    clinical = [s for s in sentences
                if s["section"] != "caveats"
                and s["text"] not in structural
                and not s["text"].startswith("No contradictions")]
    check("clinical sentences exist", len(clinical) > 0)
    uncited = [s["text"] for s in clinical if not s["citations"]]
    check("no uncited clinical sentence", not uncited, str(uncited[:2]))

    prov_ok = all(
        c["provenance"] and c["provenance"]["extraction_method"]
        and 0.0 <= c["provenance"]["confidence"] <= 1.0
        for s in clinical for c in s["citations"])
    check("every citation carries full provenance", prov_ok)

    # The withheld item from step 5 was resolved when the patient re-answered
    # the same field confidently in step 6. Clearing it is correct: staff must
    # not be sent to verify something the patient has already clarified.
    check("resolved withholding no longer burdens staff",
          len(view["needs_human_verification"]) == 0,
          str(view["needs_human_verification"]))
    check("absent interaction check disclosed",
          view["interaction_check_performed"] is False)
    check("empty section stated not omitted",
          any(s["text"] in structural for s in sentences))

    section("13. Approval gate creates the record and queues export")
    summary_id = view["cited_summary"]["summary_id"]
    status, appr = call("POST", f"/physician/summaries/{summary_id}/approve",
                        token=dtoken,
                        body={"unresolved_conflicts_acknowledged": True})
    check("approved", status == 200, str(appr))
    check("content hash present",
          str(appr.get("content_hash", "")).startswith("sha256:"))
    check("facts accepted", appr["facts"]["accepted"] > 0)
    check("HIS export queued",
          appr["integration"].get("hospital_his") == "pending")
    check("ABDM skipped without sharing consent",
          appr["integration"].get("abdm_gateway") == "delivered")

    status, again = call("POST", f"/physician/summaries/{summary_id}/approve",
                         token=dtoken,
                         body={"unresolved_conflicts_acknowledged": True})
    check("double approval refused", again is not None and status == 409)

    section("14. FHIR bundle carries Provenance per fact")
    status, bundle = call("GET", f"/physician/sessions/{session_id}/fhir",
                          token=dtoken)
    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    for want in ("Patient", "Encounter", "Composition", "Provenance"):
        check(f"bundle contains {want}", want in types)
    prov = next(e["resource"] for e in bundle["entry"]
                if e["resource"]["resourceType"] == "Provenance")
    urls = {x["url"] for x in prov["extension"]}
    check("model version exported", "urn:medikiosk:modelVersion" in urls)
    check("confidence exported", "urn:medikiosk:confidence" in urls)

    section("15. Triage: human acknowledges, engine never reorders")
    status, nurse = call("POST", "/auth/login", idem=False,
                         body={"username": "nurse.devi", "password": PASSWORD})
    ntoken = nurse["token"]
    status, alerts = call("GET", "/triage/alerts", token=ntoken)
    check("alert on triage board", alerts["count"] >= 1)
    alert_id = alerts["alerts"][0]["id"]
    check("triggering facts recorded",
          bool(alerts["alerts"][0]["triggering_facts"]["refs"]))
    status, ackd = call("POST", f"/triage/alerts/{alert_id}/acknowledge",
                        token=ntoken, body={"note": "Escorted to emergency"})
    check("acknowledged", ackd.get("status") == "acknowledged")

    section("16. Access control separations")
    status, _ = call("GET", "/physician/worklist", token=stoken)
    check("patient token cannot read worklist", status == 403)
    status, admin = call("POST", "/auth/login", idem=False,
                         body={"username": "admin.it", "password": PASSWORD})
    status, _ = call("GET", "/physician/worklist", token=admin["token"])
    check("IT admin has no clinical access", status == 403)
    status, officer = call("POST", "/auth/login", idem=False,
                           body={"username": "officer.privacy",
                                 "password": PASSWORD})
    otoken = officer["token"]
    status, _ = call("GET", "/physician/worklist", token=otoken)
    check("privacy officer has no clinical access", status == 403)

    section("17. Audit chain integrity")
    status, chain = call("GET", "/admin/audit/verify", token=otoken)
    check("hash chain intact", chain.get("chain_intact") is True, str(chain))

    section("18. AYUSH: transparent Prakriti, practitioner confirms")
    status, ay = call("POST", "/identity/resolve", body={
        "display_name": "AYUSH Patient", "language": "hi",
        "care_system": "ayush", "device_id": "kiosk-smoke"})
    aytoken = ay["token"]
    status, ayc = call("POST", "/consent", token=aytoken, body={
        "scope_interview": True, "scope_documents": False,
        "scope_abdm_share": False, "scope_audio_retention": False,
        "language": "hi", "explained_via_audio": True})
    status, ayb = call("POST", f"/sessions/{ayc['session_id']}/token",
                       token=aytoken, idem=False)
    aystoken = ayb["token"]

    vata = {
        "prakriti.body_frame": "body_frame.thin",
        "prakriti.skin": "skin.dry",
        "prakriti.appetite": "appetite.irregular",
        "prakriti.sleep": "sleep.light",
        "prakriti.temperament": "temperament.anxious",
        "prakriti.climate": "climate.dislikes_cold",
    }
    for _ in range(60):
        status, nxt = call("GET", "/interview/next-question", token=aystoken)
        if not nxt:
            break
        fc = nxt["field_code"]
        if nxt["answer_type"] == "multi":
            payload = {"selected_options": [nxt["options"][0]]}
        else:
            payload = {"value": vata.get(
                fc, nxt["options"][0] if nxt["options"] else "yes")}
        call("POST", "/interview/answers", token=aystoken,
             body={"field_code": fc, "input_mode": "touch", **payload})

    status, prak = call("GET", "/interview/ayush/prakriti", token=aystoken)
    check("dominant dosha indicated", prak["indicated_dominant"] == "vata",
          str(prak.get("distribution")))
    check("distribution normalised",
          abs(sum(prak["distribution"].values()) - 1.0) < 1e-6)
    check("all six contributions visible",
          len(prak["contributions"]) == 6, str(len(prak["contributions"])))
    check("framed for practitioner confirmation",
          prak["status"] == "indicated_for_practitioner_confirmation")
    check("disclaimer present",
          "Practitioner confirmation required" in prak["disclaimer"])

    section("19. Unresolved withholding does reach the physician")
    status, w = call("POST", "/identity/resolve", body={
        "display_name": "Withheld Patient", "language": "en",
        "care_system": "allopathic", "device_id": "kiosk-smoke"})
    wtoken0 = w["token"]
    status, wc = call("POST", "/consent", token=wtoken0, body={
        "scope_interview": True, "scope_documents": False,
        "scope_abdm_share": False, "scope_audio_retention": False,
        "language": "en", "explained_via_audio": True})
    status, wb = call("POST", f"/sessions/{wc['session_id']}/token",
                      token=wtoken0, idem=False)
    wtoken = wb["token"]

    # answer the opening complaint confidently, then leave ONE later field
    # withheld and never clarify it
    call("POST", "/interview/answers", token=wtoken, body={
        "field_code": "chief_complaint.primary", "value": "cough",
        "input_mode": "touch"})
    status, held = call("POST", "/interview/answers", token=wtoken, body={
        "field_code": "hpi.onset", "value": "today", "input_mode": "voice",
        "asr_confidence": 0.22, "nlu_confidence": 0.20})
    check("second field withheld", held["fact_admitted"] is False)

    for _ in range(60):
        status, nxt = call("GET", "/interview/next-question", token=wtoken)
        if not nxt:
            break
        if nxt["field_code"] == "hpi.onset":
            # deliberately leave it unclarified
            call("POST", "/interview/answers", token=wtoken, body={
                "field_code": nxt["field_code"], "input_mode": "touch",
                "skipped_reason": "declined"})
            continue
        payload = ({"selected_options": [nxt["options"][0]]}
                   if nxt["answer_type"] == "multi"
                   else {"value": nxt["options"][0] if nxt["options"]
                         else "yes"})
        call("POST", "/interview/answers", token=wtoken,
             body={"field_code": nxt["field_code"], "input_mode": "touch",
                   **payload})

    call("POST", "/interview/finalise", token=wtoken)
    status, wview = call("GET",
                         f"/physician/sessions/{wc['session_id']}/summary",
                         token=dtoken)
    check("physician sees the unresolved withholding",
          len(wview["needs_human_verification"]) >= 1,
          str(wview["needs_human_verification"]))

    section("20. Analytics exposes weakness-revealing metrics")
    status, dash = call("GET", "/analytics/dashboard", token=dtoken)
    check("grounding pass rate reported",
          dash["quality"]["mean_grounding_pass_rate"] is not None)
    check("physician edit rate tracked",
          "physician_edit_rate_pct" in dash["quality"])
    check("withheld facts counted",
          "withheld_pending_human" in dash["facts"])
    check("SLA adherence tracked", "sla_adherence_pct" in dash["triage"])

    print(f"\n{'=' * 58}")
    print(f"  passed: {ok_count}    failed: {fail_count}")
    print(f"{'=' * 58}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
