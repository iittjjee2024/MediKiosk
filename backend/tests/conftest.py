from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.db as dbmod


@pytest.fixture()
def client(monkeypatch):
    """Fresh in-memory database per test, wired through the real app."""
    monkeypatch.setattr(dbmod, "_engine", None, raising=False)
    monkeypatch.setattr(dbmod, "_SessionLocal", None, raising=False)
    dbmod.get_engine("sqlite+pysqlite:///:memory:")

    from app.main import bootstrap, create_app
    bootstrap()
    application = create_app()
    with TestClient(application) as c:
        yield c


@pytest.fixture()
def db():
    factory = dbmod.get_sessionmaker()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def key() -> str:
    """A fresh idempotency key."""
    return str(uuid.uuid4())


@pytest.fixture()
def idem():
    return key


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def staff_login(client, username: str = "dr.rao") -> str:
    r = client.post("/api/v1/auth/login",
                    json={"username": username, "password": "medikiosk-demo"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def start_session(client, *, care_system: str = "allopathic",
                  abdm_share: bool = False) -> str:
    """Identity -> consent -> session-bound patient token."""
    r = client.post("/api/v1/identity/resolve",
                    headers={"Idempotency-Key": key()},
                    json={"display_name": "Test Patient", "gender": "female",
                          "year_of_birth": 1970, "care_system": care_system,
                          "language": "hi", "device_id": "kiosk-test-1"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    r = client.post("/api/v1/consent", headers={**auth(token),
                                                "Idempotency-Key": key()},
                    json={"scope_interview": True, "scope_documents": True,
                          "scope_abdm_share": abdm_share,
                          "explained_via_audio": True, "language": "hi"})
    assert r.status_code == 200, r.text
    session_id = r.json()["session_id"]
    assert session_id

    r = client.post(f"/api/v1/sessions/{session_id}/token",
                    headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()["token"]


def answer(client, token: str, field_code: str, *, value=None,
           options=None, mode="touch", asr=None, nlu=None, skip=None):
    body: dict = {"field_code": field_code, "input_mode": mode}
    if value is not None:
        body["value"] = value
    if options is not None:
        body["selected_options"] = options
    if asr is not None:
        body["asr_confidence"] = asr
    if nlu is not None:
        body["nlu_confidence"] = nlu
    if skip is not None:
        body["skipped_reason"] = skip
    r = client.post("/api/v1/interview/answers",
                    headers={**auth(token), "Idempotency-Key": key()},
                    json=body)
    assert r.status_code == 200, r.text
    return r.json()


def run_interview(client, token: str, *, answers: dict | None = None,
                  limit: int = 100) -> list[str]:
    """Walk the engine to completion, honouring supplied answers."""
    answers = answers or {}
    asked: list[str] = []
    for _ in range(limit):
        r = client.get("/api/v1/interview/next-question", headers=auth(token))
        assert r.status_code == 200, r.text
        q = r.json()
        if not q:
            break
        asked.append(q["field_code"])
        chosen = answers.get(q["field_code"])
        if q["answer_type"] == "multi":
            options = chosen if isinstance(chosen, list) else [q["options"][0]]
            answer(client, token, q["field_code"], options=options)
        else:
            value = chosen if isinstance(chosen, str) else (
                q["options"][0] if q["options"] else "yes")
            answer(client, token, q["field_code"], value=value)
    return asked
