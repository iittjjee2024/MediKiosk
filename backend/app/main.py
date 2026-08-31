"""Application entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .api import router
from .config import settings
from .db import Base, get_engine, get_sessionmaker
from .models import AppUser
from .seed import seed
from .services.security import hash_password

log = logging.getLogger("medikiosk")

DEMO_USERS = [
    ("dr.rao", "physician", "Dr. A. Rao", "general_opd"),
    ("dr.iyer", "ayush_practitioner", "Dr. S. Iyer", "ayush_opd"),
    ("nurse.devi", "nurse", "Nurse K. Devi", "triage"),
    ("admin.it", "it_admin", "IT Administrator", None),
    ("officer.privacy", "privacy_officer", "Privacy Officer", None),
]
DEMO_PASSWORD = "medikiosk-demo"


def _ensure_nlu_artifact() -> None:
    """Fit the clinical NLU on first run if the artifact is missing.

    Deploying without the fitted lexicon still works -- option codes are always
    candidates -- but fitting gives the natural-language aliases and the
    calibrated confidence, so we do it once automatically.
    """
    from .nlu import get_model
    from .nlu.clinical_nlu import default_model_path
    if default_model_path().exists():
        return
    try:
        from .nlu import fit as fit_module
        from .nlu.fit import _field_options, calibrate, fit
        from .nlu.training_data import LABELLED
        model = fit(LABELLED)
        model.calibration = calibrate(model, LABELLED, _field_options())
        model.save(default_model_path())
        get_model()
        log.info("clinical NLU artifact fitted on first run")
    except Exception as exc:                    # pragma: no cover - startup
        log.warning("NLU fit skipped: %s", exc)


def bootstrap(*, with_demo_data: bool | None = None) -> None:
    """Create schema, seed protocol, create demo staff. Idempotent.

    If ``with_demo_data`` (or MEDIKIOSK_SEED_DEMO=1) is set, also populate a
    batch of realistic completed sessions so the physician, triage and
    analytics screens are alive on first load.
    """
    import os

    _ensure_nlu_artifact()

    engine = get_engine()
    Base.metadata.create_all(engine)

    factory = get_sessionmaker()
    with factory() as db:
        tenant = seed(db)
        for username, role, name, dept in DEMO_USERS:
            if db.scalar(select(AppUser).where(AppUser.username == username)):
                continue
            db.add(AppUser(tenant_id=tenant.id, username=username, role=role,
                           full_name=name, department=dept,
                           password_hash=hash_password(DEMO_PASSWORD)))
        db.commit()

        seed_demo = (with_demo_data if with_demo_data is not None
                     else os.environ.get("MEDIKIOSK_SEED_DEMO") == "1")
        if seed_demo:
            from .demo_seed import seed_demo as run_demo
            result = run_demo(db)
            db.commit()
            if result.get("seeded"):
                log.info("demo data seeded: %s sessions",
                         len(result.get("sessions", [])))
    log.info("bootstrap complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="MediKiosk API",
        version="0.1.0",
        description=(
            "AI-powered clinical history intake. AI is confined to perception; "
            "every clinical decision runs in deterministic versioned rule "
            "engines, every fact carries provenance, and nothing enters the "
            "clinical record without physician approval."),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/api/v1/health/live")
    def live():
        return {"status": "ok", "service": settings.app_name}

    @app.get("/api/v1/health/ready")
    def ready():
        from sqlalchemy import text
        try:
            factory = get_sessionmaker()
            with factory() as db:
                db.execute(text("SELECT 1"))
            return {"status": "ready", "database": "ok"}
        except Exception as exc:            # pragma: no cover - operational
            return {"status": "degraded", "database": repr(exc)}

    @app.on_event("startup")
    def _startup():
        bootstrap()

    return app


app = create_app()
