"""End-to-end API tests.

These exercise the full journey the demo shows: identity, consent, adaptive
interview, red-flag escalation, summary with citations, physician review and
approval with FHIR export.
"""
from __future__ import annotations

from conftest import (answer, auth, key, run_interview, staff_login,
                      start_session)

def test_health_and_readiness(client):
    assert client.get("/api/v1/health/live").json()["status"] == "ok"
    assert client.get("/api/v1/health/ready").json()["database"] == "ok"

class TestConsent:
    def test_interview_blocked_without_a_session_token(self, client):
        r = client.post("/api/v1/identity/resolve",
                        headers={"Idempotency-Key": key()},
                        json={"display_name": "No Consent"})
        token = r.json()["token"]
        r = client.get("/api/v1/interview/next-question", headers=auth(token))
        assert r.status_code == 409

    def test_declining_consent_creates_no_session_and_no_penalty(self, client):
        r = client.post("/api/v1/identity/resolve",
                        headers={"Idempotency-Key": key()},
                        json={"display_name": "Declines"})
        token = r.json()["token"]
        r = client.post("/api/v1/consent",
                        headers={**auth(token), "Idempotency-Key": key()},
                        json={"scope_interview": False})
        assert r.status_code == 200
        body = r.json()
        assert body["granted"] is False
        assert body["session_id"] is None
        assert "no penalty" in body["message"]

    def test_scopes_are_independent(self, client):
        r = client.post("/api/v1/identity/resolve",
                        headers={"Idempotency-Key": key()},
                        json={"display_name": "Partial"})
        token = r.json()["token"]
        r = client.post("/api/v1/consent",
                        headers={**auth(token), "Idempotency-Key": key()},
                        json={"scope_interview": True,
                              "scope_documents": False,
                              "scope_abdm_share": False})
        scopes = r.json()["scopes"]
        assert scopes["interview"] is True
        assert scopes["documents"] is False
        assert scopes["abdm_share"] is False

class TestInterview:
    def test_first_question_is_chief_complaint(self, client):
        token = start_session(client)
        q = client.get("/api/v1/interview/next-question",
                       headers=auth(token)).json()
        assert q["field_code"] == "chief_complaint.primary"
        assert q["prompt_key"].startswith("q.")
        assert "chest_pain" in q["options"]

    def test_touch_answer_is_admitted_with_full_confidence(self, client):
        token = start_session(client)
        out = answer(client, token, "chief_complaint.primary",
                     value="chest_pain")
        assert out["fact_admitted"] is True
        assert out["confidence_band"] == "high"
        assert out["admitted_fact_ids"]
        assert out["next_question"]["field_code"] != "chief_complaint.primary"

    def test_low_confidence_voice_is_withheld_and_queued(self, client):
        """Guessing is worse than declaring uncertainty in a clinical record."""
        token = start_session(client)
        out = answer(client, token, "chief_complaint.primary",
                     value="chest_pain", mode="voice", asr=0.31, nlu=0.28)
        assert out["fact_admitted"] is False
        assert out["confidence_band"] == "low"
        assert out["confirm_back_required"] is True
        assert out["verification_item_ids"]
        assert out["admitted_fact_ids"] == []

    def test_medium_confidence_admitted_but_marked(self, client):
        token = start_session(client)
        out = answer(client, token, "chief_complaint.primary",
                     value="fever", mode="voice", asr=0.70, nlu=0.72)
        assert out["fact_admitted"] is True
        assert out["confidence_band"] == "medium"
        assert out["confirm_back_required"] is True

    def test_dependency_gating_never_asks_pain_fields_for_fever(self, client):
        token = start_session(client)
        asked = run_interview(
            client, token,
            answers={"chief_complaint.primary": "fever"})
        assert "socrates.character" not in asked
        assert "socrates.radiation" not in asked
        assert "fever.pattern" in asked

    def test_dependency_gating_never_asks_fever_field_for_chest_pain(self, client):
        token = start_session(client)
        asked = run_interview(
            client, token,
            answers={"chief_complaint.primary": "chest_pain"})
        assert "fever.pattern" not in asked
        assert "socrates.character" in asked

    def test_completeness_reaches_100_and_is_explainable(self, client):
        token = start_session(client)
        run_interview(client, token,
                      answers={"chief_complaint.primary": "cough"})
        state = client.get("/api/v1/interview/state",
                           headers=auth(token)).json()
        assert state["completeness"]["score"] == 100.0
        assert state["completeness"]["unanswered"] == []
        assert "complete" in state["completeness"]["explanation"]

    def test_reanswering_supersedes_and_does_not_duplicate_facts(self, client):
        """Last-write-wins per field, with the change visible in audit."""
        token = start_session(client)
        answer(client, token, "chief_complaint.primary", value="chest_pain")
        again = answer(client, token, "chief_complaint.primary", value="fever")
        assert again["superseded_previous"] is True

        state = client.get("/api/v1/interview/state",
                           headers=auth(token)).json()
        assert state["answered"] == 1
        assert state["facts"] == 1

    def test_unknown_field_is_rejected(self, client):
        token = start_session(client)
        r = client.post("/api/v1/interview/answers",
                        headers={**auth(token), "Idempotency-Key": key()},
                        json={"field_code": "not.a.real.field",
                              "value": "x"})
        assert r.status_code == 422

    def test_idempotent_replay_returns_original_response(self, client):
        token = start_session(client)
        k = key()
        body = {"field_code": "chief_complaint.primary", "value": "chest_pain"}
        first = client.post("/api/v1/interview/answers",
                            headers={**auth(token), "Idempotency-Key": k},
                            json=body).json()
        second = client.post("/api/v1/interview/answers",
                             headers={**auth(token), "Idempotency-Key": k},
                             json=body).json()
        assert first["admitted_fact_ids"] == second["admitted_fact_ids"]

        state = client.get("/api/v1/interview/state",
                           headers=auth(token)).json()
        assert state["facts"] == 1

    def test_missing_idempotency_key_is_refused(self, client):
        token = start_session(client)
        r = client.post("/api/v1/interview/answers", headers=auth(token),
                        json={"field_code": "chief_complaint.primary",
                              "value": "fever"})
        assert r.status_code == 400

    def test_extra_fields_are_rejected_at_the_boundary(self, client):
        token = start_session(client)
        r = client.post("/api/v1/interview/answers",
                        headers={**auth(token), "Idempotency-Key": key()},
                        json={"field_code": "chief_complaint.primary",
                              "value": "fever", "injected": "payload"})
        assert r.status_code == 422

class TestRedFlags:
    def test_acs_alert_fires_and_escalates_the_session(self, client):
        token = start_session(client)
        answer(client, token, "chief_complaint.primary", value="chest_pain")
        out = answer(client, token, "hpi.associated", options=["dyspnoea"])

        assert out["red_flag_fired"] is True
        codes = [a["rule_code"] for a in out["alerts"]]
        assert "CARDIAC_ACS_SUSPICION" in codes
        assert out["alerts"][0]["severity"] == "critical"
        assert out["alerts"][0]["sla_deadline"]

        state = client.get("/api/v1/interview/state",
                           headers=auth(token)).json()
        assert state["status"] == "escalated"

    def test_no_alert_for_chest_pain_alone(self, client):
        token = start_session(client)
        answer(client, token, "chief_complaint.primary", value="chest_pain")
        out = answer(client, token, "hpi.associated", options=["none"])
        assert out["red_flag_fired"] is False

    def test_alert_appears_on_triage_console_with_sla(self, client):
        token = start_session(client)
        answer(client, token, "chief_complaint.primary", value="chest_pain")
        answer(client, token, "hpi.associated", options=["diaphoresis"])

        nurse = staff_login(client, "nurse.devi")
        board = client.get("/api/v1/triage/alerts", headers=auth(nurse)).json()
        assert board["count"] >= 1
        alert = board["alerts"][0]
        assert alert["severity"] == "critical"
        assert alert["status"] == "created"
        assert alert["triggering_facts"]["refs"]

    def test_nurse_acknowledges_alert(self, client):
        token = start_session(client)
        answer(client, token, "chief_complaint.primary", value="chest_pain")
        answer(client, token, "hpi.associated", options=["dyspnoea"])

        nurse = staff_login(client, "nurse.devi")
        alert_id = client.get("/api/v1/triage/alerts",
                              headers=auth(nurse)).json()["alerts"][0]["id"]
        r = client.post(f"/api/v1/triage/alerts/{alert_id}/acknowledge",
                        headers={**auth(nurse), "Idempotency-Key": key()},
                        json={"note": "Escorted to emergency; ECG ordered."})
        assert r.status_code == 200
        assert r.json()["status"] == "acknowledged"

    def test_alert_is_not_duplicated_on_further_answers(self, client):
        token = start_session(client)
        answer(client, token, "chief_complaint.primary", value="chest_pain")
        answer(client, token, "hpi.associated", options=["dyspnoea"])
        answer(client, token, "hpi.onset", value="today")

        nurse = staff_login(client, "nurse.devi")
        codes = [a["rule_code"] for a in client.get(
            "/api/v1/triage/alerts", headers=auth(nurse)).json()["alerts"]]
        assert codes.count("CARDIAC_ACS_SUSPICION") == 1

class TestSummaryAndReview:
    def _completed(self, client, **kw):
        token = start_session(client, **kw)
        run_interview(client, token,
                      answers={"chief_complaint.primary": "chest_pain"})
        r = client.post("/api/v1/interview/finalise",
                        headers={**auth(token), "Idempotency-Key": key()})
        assert r.status_code == 200, r.text
        return token, r.json()

    def test_finalise_generates_a_fully_grounded_summary(self, client):
        _, out = self._completed(client)
        assert out["status"] in ("ready_for_physician", "escalated")
        assert out["grounding_pass_rate"] == 100.0
        assert out["completeness"]["score"] == 100.0

    def test_every_clinical_sentence_has_a_resolving_citation(self, client):
        _, out = self._completed(client)
        doc = staff_login(client)
        session_id = out["session_id"]
        body = client.get(
            f"/api/v1/physician/sessions/{session_id}/summary",
            headers=auth(doc)).json()

        sentences = body["cited_summary"]["sentences"]
        assert sentences
        clinical = [s for s in sentences
                    if s["section"] not in ("caveats",)
                    and s["text"] != "Not captured in this intake session."
                    and not s["text"].startswith("No contradictions")]
        assert clinical
        for s in clinical:
            assert s["citations"], f"uncited sentence: {s['text']}"
            for c in s["citations"]:
                assert c["provenance"] is not None
                assert c["provenance"]["extraction_method"]
                assert 0.0 <= c["provenance"]["confidence"] <= 1.0

    def test_empty_section_is_stated_not_omitted(self, client):
        _, out = self._completed(client)
        doc = staff_login(client)
        body = client.get(
            f"/api/v1/physician/sessions/{out['session_id']}/summary",
            headers=auth(doc)).json()
        text = " ".join(s["text"]
                        for s in body["cited_summary"]["sentences"])
        assert "Not captured in this intake session." in text

    def test_absent_interaction_check_is_disclosed(self, client):
        _, out = self._completed(client)
        doc = staff_login(client)
        body = client.get(
            f"/api/v1/physician/sessions/{out['session_id']}/summary",
            headers=auth(doc)).json()
        assert body["interaction_check_performed"] is False
        text = " ".join(s["text"]
                        for s in body["cited_summary"]["sentences"])
        assert "NOT performed" in text

    def test_physician_can_edit_and_reject_individual_facts(self, client):
        _, out = self._completed(client)
        doc = staff_login(client)
        session_id = out["session_id"]
        body = client.get(f"/api/v1/physician/sessions/{session_id}/summary",
                          headers=auth(doc)).json()
        summary_id = body["cited_summary"]["summary_id"]
        fact_id = next(c["fact_id"]
                       for s in body["cited_summary"]["sentences"]
                       if s["citations"] for c in s["citations"])

        r = client.patch(f"/api/v1/physician/summaries/{summary_id}/facts",
                         headers={**auth(doc), "Idempotency-Key": key()},
                         json={"fact_id": fact_id, "new_value": "corrected"})
        assert r.status_code == 200
        assert r.json()["physician_status"] == "edited"

        r = client.post(
            f"/api/v1/physician/summaries/{summary_id}/facts/reject",
            headers={**auth(doc), "Idempotency-Key": key()},
            json={"fact_id": fact_id, "note": "misheard"})
        assert r.json()["physician_status"] == "rejected"

    def test_approval_creates_record_and_queues_export(self, client):
        _, out = self._completed(client)
        doc = staff_login(client)
        session_id = out["session_id"]
        body = client.get(f"/api/v1/physician/sessions/{session_id}/summary",
                          headers=auth(doc)).json()
        summary_id = body["cited_summary"]["summary_id"]

        r = client.post(f"/api/v1/physician/summaries/{summary_id}/approve",
                        headers={**auth(doc), "Idempotency-Key": key()},
                        json={"unresolved_conflicts_acknowledged": True})
        assert r.status_code == 200, r.text
        approved = r.json()
        assert approved["content_hash"].startswith("sha256:")
        assert approved["facts"]["accepted"] > 0
        assert approved["integration"]["hospital_his"] == "pending"

    def test_abdm_export_is_skipped_without_sharing_consent(self, client):
        _, out = self._completed(client)
        doc = staff_login(client)
        body = client.get(
            f"/api/v1/physician/sessions/{out['session_id']}/summary",
            headers=auth(doc)).json()
        summary_id = body["cited_summary"]["summary_id"]
        r = client.post(f"/api/v1/physician/summaries/{summary_id}/approve",
                        headers={**auth(doc), "Idempotency-Key": key()},
                        json={"unresolved_conflicts_acknowledged": True})
        assert r.json()["integration"]["abdm_gateway"] == "delivered"

    def test_abdm_export_is_queued_when_consent_granted(self, client):
        token = start_session(client, abdm_share=True)
        run_interview(client, token,
                      answers={"chief_complaint.primary": "cough"})
        fin = client.post("/api/v1/interview/finalise",
                          headers={**auth(token),
                                   "Idempotency-Key": key()}).json()
        doc = staff_login(client)
        body = client.get(
            f"/api/v1/physician/sessions/{fin['session_id']}/summary",
            headers=auth(doc)).json()
        r = client.post(
            f"/api/v1/physician/summaries/"
            f"{body['cited_summary']['summary_id']}/approve",
            headers={**auth(doc), "Idempotency-Key": key()},
            json={"unresolved_conflicts_acknowledged": True})
        assert r.json()["integration"]["abdm_gateway"] == "pending"

    def test_double_approval_is_refused(self, client):
        _, out = self._completed(client)
        doc = staff_login(client)
        body = client.get(
            f"/api/v1/physician/sessions/{out['session_id']}/summary",
            headers=auth(doc)).json()
        sid = body["cited_summary"]["summary_id"]
        payload = {"unresolved_conflicts_acknowledged": True}
        client.post(f"/api/v1/physician/summaries/{sid}/approve",
                    headers={**auth(doc), "Idempotency-Key": key()},
                    json=payload)
        r = client.post(f"/api/v1/physician/summaries/{sid}/approve",
                        headers={**auth(doc), "Idempotency-Key": key()},
                        json=payload)
        assert r.status_code == 409

    def test_fhir_bundle_carries_provenance_per_fact(self, client):
        _, out = self._completed(client)
        doc = staff_login(client)
        bundle = client.get(
            f"/api/v1/physician/sessions/{out['session_id']}/fhir",
            headers=auth(doc)).json()
        assert bundle["resourceType"] == "Bundle"
        types = [e["resource"]["resourceType"] for e in bundle["entry"]]
        for expected in ("Patient", "Encounter", "Composition", "Provenance"):
            assert expected in types
        prov = next(e["resource"] for e in bundle["entry"]
                    if e["resource"]["resourceType"] == "Provenance")
        urls = {x["url"] for x in prov["extension"]}
        assert "urn:medikiosk:modelVersion" in urls
        assert "urn:medikiosk:confidence" in urls

    def test_escalated_session_surfaces_first_on_worklist(self, client):
        calm = start_session(client)
        run_interview(client, calm,
                      answers={"chief_complaint.primary": "cough"})
        client.post("/api/v1/interview/finalise",
                    headers={**auth(calm), "Idempotency-Key": key()})

        urgent = start_session(client)
        answer(client, urgent, "chief_complaint.primary", value="chest_pain")
        answer(client, urgent, "hpi.associated", options=["dyspnoea"])
        run_interview(client, urgent)
        client.post("/api/v1/interview/finalise",
                    headers={**auth(urgent), "Idempotency-Key": key()})

        doc = staff_login(client)
        wl = client.get("/api/v1/physician/worklist",
                        headers=auth(doc)).json()
        assert wl["count"] == 2
        assert wl["sessions"][0]["status"] == "escalated"
        assert wl["sessions"][0]["alerts"]

class TestAyush:
    def test_prakriti_is_transparent_and_requires_confirmation(self, client):
        token = start_session(client, care_system="ayush")
        run_interview(client, token, answers={
            "prakriti.body_frame": "body_frame.thin",
            "prakriti.skin": "skin.dry",
            "prakriti.appetite": "appetite.irregular",
            "prakriti.sleep": "sleep.light",
            "prakriti.temperament": "temperament.anxious",
            "prakriti.climate": "climate.dislikes_cold",
        })
        r = client.get("/api/v1/interview/ayush/prakriti", headers=auth(token))
        assert r.status_code == 200
        out = r.json()

        assert out["indicated_dominant"] == "vata"
        assert out["status"] == "indicated_for_practitioner_confirmation"
        assert "Practitioner confirmation required" in out["disclaimer"]

        assert len(out["contributions"]) == 6
        assert abs(sum(out["distribution"].values()) - 1.0) < 1e-6

    def test_ayush_uses_the_same_fact_model_and_approval_gate(self, client):
        token = start_session(client, care_system="ayush")
        run_interview(client, token)
        fin = client.post("/api/v1/interview/finalise",
                          headers={**auth(token),
                                   "Idempotency-Key": key()}).json()
        practitioner = staff_login(client, "dr.iyer")
        body = client.get(
            f"/api/v1/physician/sessions/{fin['session_id']}/summary",
            headers=auth(practitioner)).json()
        assert body["cited_summary"]["sentences"]
        r = client.post(
            f"/api/v1/physician/summaries/"
            f"{body['cited_summary']['summary_id']}/approve",
            headers={**auth(practitioner), "Idempotency-Key": key()},
            json={"unresolved_conflicts_acknowledged": True})
        assert r.status_code == 200

class TestAccessControl:
    def test_patient_token_cannot_reach_physician_routes(self, client):
        token = start_session(client)
        r = client.get("/api/v1/physician/worklist", headers=auth(token))
        assert r.status_code == 403

    def test_nurse_cannot_approve_a_summary(self, client):
        nurse = staff_login(client, "nurse.devi")
        r = client.post("/api/v1/physician/summaries/any/approve",
                        headers={**auth(nurse), "Idempotency-Key": key()},
                        json={})
        assert r.status_code == 403

    def test_it_admin_has_no_clinical_data_access(self, client):
        """Administrative capability and clinical access are separate rights."""
        admin = staff_login(client, "admin.it")
        r = client.get("/api/v1/physician/worklist", headers=auth(admin))
        assert r.status_code == 403

    def test_privacy_officer_can_verify_audit_but_not_read_records(self, client):
        officer = staff_login(client, "officer.privacy")
        assert client.get("/api/v1/admin/audit/verify",
                          headers=auth(officer)).status_code == 200
        assert client.get("/api/v1/physician/worklist",
                          headers=auth(officer)).status_code == 403

    def test_unauthenticated_requests_are_refused(self, client):
        assert client.get("/api/v1/interview/next-question").status_code == 401
        assert client.get("/api/v1/physician/worklist").status_code == 401

    def test_tampered_token_is_rejected(self, client):
        token = start_session(client)
        r = client.get("/api/v1/interview/next-question",
                       headers=auth(token[:-4] + "AAAA"))
        assert r.status_code == 401

class TestAudit:
    def test_chain_is_intact_after_a_full_journey(self, client):
        token = start_session(client)
        run_interview(client, token,
                      answers={"chief_complaint.primary": "chest_pain"})
        client.post("/api/v1/interview/finalise",
                    headers={**auth(token), "Idempotency-Key": key()})
        officer = staff_login(client, "officer.privacy")
        out = client.get("/api/v1/admin/audit/verify",
                          headers=auth(officer)).json()
        assert out["chain_intact"] is True
        assert out["first_broken_row_id"] is None

    def test_tampering_with_audit_is_detected(self, client):
        from sqlalchemy import select

        import app.db as dbmod
        from app.models import AuditEvent

        token = start_session(client)
        answer(client, token, "chief_complaint.primary", value="fever")

        factory = dbmod.get_sessionmaker()
        with factory() as db:
            row = db.scalars(select(AuditEvent)
                             .order_by(AuditEvent.id)).all()[1]
            row.detail = {"field_code": "tampered"}
            db.commit()

        officer = staff_login(client, "officer.privacy")
        out = client.get("/api/v1/admin/audit/verify",
                          headers=auth(officer)).json()
        assert out["chain_intact"] is False
        assert out["first_broken_row_id"] is not None

def test_dashboard_reports_metrics_that_can_embarrass_the_platform(client):
    token = start_session(client)
    run_interview(client, token,
                  answers={"chief_complaint.primary": "chest_pain"})
    client.post("/api/v1/interview/finalise",
                headers={**auth(token), "Idempotency-Key": key()})

    doc = staff_login(client)
    data = client.get("/api/v1/analytics/dashboard",
                      headers=auth(doc)).json()

    assert data["sessions"]["total"] >= 1
    assert data["quality"]["mean_grounding_pass_rate"] is not None

    assert "physician_edit_rate_pct" in data["quality"]
    assert "withheld_pending_human" in data["facts"]
    assert "sla_adherence_pct" in data["triage"]
