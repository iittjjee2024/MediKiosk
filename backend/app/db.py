"""Database session and declarative base.

Column types are deliberately portable (String UUIDs, JSON rather than JSONB)
so the deterministic engines can be unit-tested against in-memory SQLite with
no Docker dependency. Production runs on PostgreSQL, where JSON maps to JSONB
via the dialect and the same indexes apply.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, mapped_column, sessionmaker

from .config import settings


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_aware(value: datetime | None) -> datetime | None:
    """Normalise a stored datetime to timezone-aware UTC.

    SQLite has no native timezone type, so values round-trip as naive. Any
    comparison against `utcnow()` would raise. Normalising on read keeps SLA
    arithmetic correct on both SQLite and PostgreSQL.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


def pk():
    return mapped_column(String(36), primary_key=True, default=new_uuid)


def fk(target: str, *, nullable: bool = False, index: bool = True):
    from sqlalchemy import ForeignKey
    return mapped_column(String(36), ForeignKey(target), nullable=nullable,
                         index=index)


def created():
    return mapped_column(DateTime(timezone=True), default=utcnow,
                         nullable=False)


_engine = None
_SessionLocal = None


def get_engine(url: str | None = None):
    global _engine, _SessionLocal
    if _engine is None:
        target = url or settings.database_url
        # default=str so date/datetime inside JSON columns (FHIR bundles,
        # audit detail) serialise instead of raising at INSERT time
        kwargs = {
            "pool_pre_ping": True,
            "future": True,
            "json_serializer": lambda obj: json.dumps(
                obj, default=str, separators=(",", ":")),
        }
        if target.startswith("sqlite"):
            from sqlalchemy.pool import StaticPool
            kwargs.update(connect_args={"check_same_thread": False},
                          poolclass=StaticPool)
        _engine = create_engine(target, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False,
                                     expire_on_commit=False, future=True)
    return _engine


def get_sessionmaker():
    get_engine()
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    factory = get_sessionmaker()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
