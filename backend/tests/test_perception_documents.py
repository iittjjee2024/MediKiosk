"""Tests for the voice-perception and document endpoints.

These verify the two new perception surfaces route through the SAME confidence
gate and provenance model as every other input -- a weak interpretation is
withheld, a strong one is admitted with provenance, and a document conflict is
surfaced to the physician.
"""
from __future__ import annotations

from conftest import auth, key, run_interview, staff_login, start_session


def voice(client, token, field_code, transcript, *, asr=None):
    body = {"field_code": field_code, "transcript": transcript}
    if asr is not None:
        body["asr_confidence"] = asr
    r = client.post("/api/v1/interview/voice",
                    headers={**auth(token), "Idempotency-Key": key()},
                    json=body)
    assert r.status_code == 200, r.text
    return r.json()


def upload(client, token, kind):
    r = client.post("/api/v1/interview/documents",
                    headers={**auth(token), "Idempotency-Key": key()},
                    json={"document_kind": kind})
    return r


class TestVoicePerception:
    def test_clear_utterance_is_interpreted_and_committed(self, client):
        token = start_session(client)
        out = voice(client, token, "chief_complaint.primary",
                    "seene mein dard ho raha hai", asr=0.95)
        assert out["committed"] is True
        assert out["interpretation"]["interpreted_value"] == "chest_pain"
        assert out["answer"]["fact_admitted"] is True

    def test_generalises_to_an_unseen_phrasing(self, client):
        token = start_session(client)
        out = voice(client, token, "chief_complaint.primary",
                    "mere seene mein takleef ho rahi hai", asr=0.9)
        assert out["interpretation"]["interpreted_value"] == "chest_pain"

    def test_unmatched_transcript_commits_nothing(self, client):
        """We never fabricate an option to force a submission."""
        token = start_session(client)
        out = voice(client, token, "chief_complaint.primary",
                    "poora din theek tha kuch khaas nahi", asr=0.9)
        assert out["committed"] is False
        assert out["confirm_back_required"] is True
        assert out["answer"] is None

    def test_low_asr_confidence_is_withheld_by_the_gate(self, client):
        """A correct interpretation of a badly-heard phrase is still bounded by
        the ASR confidence, so it should be withheld, not admitted."""
        token = start_session(client)
        out = voice(client, token, "chief_complaint.primary",
                    "seene mein dard", asr=0.20)
        assert out["committed"] is True
        assert out["answer"]["fact_admitted"] is False
        assert out["answer"]["confidence_band"] == "low"
        assert out["answer"]["verification_item_ids"]

    def test_voice_can_fire_a_red_flag(self, client):
        token = start_session(client)
        voice(client, token, "chief_complaint.primary",
              "seene mein dard ho raha hai", asr=0.95)
        out = voice(client, token, "hpi.associated",
                    "saans phool rahi hai", asr=0.92)
        assert out["committed"] is True
        assert out["answer"]["red_flag_fired"] is True

    def test_unknown_field_is_rejected(self, client):
        token = start_session(client)
        r = client.post("/api/v1/interview/voice",
                        headers={**auth(token), "Idempotency-Key": key()},
                        json={"field_code": "not.real", "transcript": "x"})
        assert r.status_code == 422

    def test_voice_is_idempotent(self, client):
        token = start_session(client)
        k = key()
        body = {"field_code": "chief_complaint.primary",
                "transcript": "mujhe bukhar hai", "asr_confidence": 0.9}
        first = client.post("/api/v1/interview/voice",
                            headers={**auth(token), "Idempotency-Key": k},
                            json=body).json()
        second = client.post("/api/v1/interview/voice",
                             headers={**auth(token), "Idempotency-Key": k},
                             json=body).json()
        assert first == second
        state = client.get("/api/v1/interview/state",
                           headers=auth(token)).json()
        assert state["facts"] == 1


class TestDocumentIngestion:
    def test_lab_report_admits_abnormal_facts_with_provenance(self, client):
        token = start_session(client)
        r = upload(client, token, "lab_report")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["document_type"] == "lab_report"
        assert len(out["admitted_fact_ids"]) >= 1

    def test_handwritten_prescription_marks_unreadable_region(self, client):
        """The illegible line must be flagged unreadable, never guessed."""
        token = start_session(client)
        out = upload(client, token, "prescription").json()
        assert len(out["unreadable_region_ids"]) >= 1

    def test_document_requires_consent_scope(self, client):

        r = client.post("/api/v1/identity/resolve",
                        headers={"Idempotency-Key": key()},
                        json={"display_name": "No Doc Consent",
                              "language": "hi"})
        ptoken = r.json()["token"]
        r = client.post("/api/v1/consent",
                        headers={**auth(ptoken), "Idempotency-Key": key()},
                        json={"scope_interview": True,
                              "scope_documents": False})
        sid = r.json()["session_id"]
        stoken = client.post(f"/api/v1/sessions/{sid}/token",
                             headers=auth(ptoken)).json()["token"]
        r = client.post("/api/v1/interview/documents",
                        headers={**auth(stoken), "Idempotency-Key": key()},
                        json={"document_kind": "lab_report"})
        assert r.status_code == 403

    def test_document_conflict_surfaces_to_physician(self, client):
        """Patient denies medication; prescription shows drugs. Both kept,
        contradiction flagged for the physician."""
        token = start_session(client)
        run_interview(client, token, answers={
            "chief_complaint.primary": "chest_pain",
            "drug_allergy.current_medication": "no"})
        upload(client, token, "prescription")
        client.post("/api/v1/interview/finalise",
                    headers={**auth(token), "Idempotency-Key": key()})

        doc = staff_login(client)
        wl = client.get("/api/v1/physician/worklist", headers=auth(doc)).json()
        sid = wl["sessions"][0]["session_id"]
        view = client.get(f"/api/v1/physician/sessions/{sid}/summary",
                          headers=auth(doc)).json()
        assert len(view["conflicts"]) >= 1
