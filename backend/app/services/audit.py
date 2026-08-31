"""Hash-chained audit trail.

Each row's hash covers the previous row's hash plus its own canonical payload,
so any silent modification or deletion breaks the chain and becomes detectable
on verification. This delivers tamper evidence without the operational cost,
latency and governance burden of a distributed ledger -- it is the honest
alternative to claiming a blockchain solves a problem that has a single
custodian and no multi-party consensus requirement.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..models import AuditEvent

GENESIS = "0" * 64


def _canonical(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"),
                      default=str)


def _row_hash(prev_hash: str, payload: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(payload)).encode()).hexdigest()


def record(db: Session, *, tenant_id: str, actor_type: str, action: str,
           entity_type: str, entity_id: str | None = None,
           actor_id: str | None = None, detail: dict | None = None,
           device_id: str | None = None) -> AuditEvent:
    """Append one immutable audit row. Never updates or deletes."""
    prev = db.scalar(
        select(AuditEvent.row_hash)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(desc(AuditEvent.id))
        .limit(1)
    ) or GENESIS

    payload = {
        "tenant_id": tenant_id, "actor_type": actor_type, "actor_id": actor_id,
        "action": action, "entity_type": entity_type, "entity_id": entity_id,
        "detail": detail, "device_id": device_id,
    }
    event = AuditEvent(
        tenant_id=tenant_id, actor_type=actor_type, actor_id=actor_id,
        action=action, entity_type=entity_type, entity_id=entity_id,
        detail=detail, device_id=device_id,
        prev_hash=prev, row_hash=_row_hash(prev, payload),
    )
    db.add(event)
    db.flush()
    return event


def verify_chain(db: Session, tenant_id: str) -> tuple[bool, int | None]:
    """Recompute the chain. Returns (intact, first_broken_row_id)."""
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.id)
    ).all()

    expected_prev = GENESIS
    for row in rows:
        payload = {
            "tenant_id": row.tenant_id, "actor_type": row.actor_type,
            "actor_id": row.actor_id, "action": row.action,
            "entity_type": row.entity_type, "entity_id": row.entity_id,
            "detail": row.detail, "device_id": row.device_id,
        }
        if row.prev_hash != expected_prev:
            return False, row.id
        if row.row_hash != _row_hash(expected_prev, payload):
            return False, row.id
        expected_prev = row.row_hash
    return True, None
