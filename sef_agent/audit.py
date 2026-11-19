"""Append-only, hash-chained audit log."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, utcnow

GENESIS = "0" * 64


def _digest(prev_hash: str, ts: datetime, actor: str, event: str, entity: str, entity_id: str, data: dict) -> str:
    payload = json.dumps(
        {"prev": prev_hash, "ts": ts.isoformat(), "actor": actor, "event": event,
         "entity": entity, "entity_id": entity_id, "data": data},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def log(session: Session, actor: str, event: str, entity: str, entity_id, data: dict | None = None) -> AuditEvent:
    last = session.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)).scalar_one_or_none()
    prev = last.hash if last else GENESIS
    ts = utcnow().replace(microsecond=0)
    data = json.loads(json.dumps(data or {}, default=str))
    ev = AuditEvent(ts=ts, actor=actor, event=event, entity=entity, entity_id=str(entity_id), data=data,
                    prev_hash=prev, hash=_digest(prev, ts, actor, event, entity, str(entity_id), data))
    session.add(ev)
    session.flush()
    return ev


def verify_chain(session: Session) -> tuple[bool, int | None]:
    """Return (ok, first_bad_id)."""
    prev = GENESIS
    for ev in session.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars():
        ts = ev.ts.replace(tzinfo=None) if ev.ts.tzinfo is None else ev.ts
        expected = _digest(prev, _as_utc(ts), ev.actor, ev.event, ev.entity, ev.entity_id, ev.data)
        if ev.prev_hash != prev or ev.hash != expected:
            return False, ev.id
        prev = ev.hash
    return True, None


def _as_utc(ts: datetime) -> datetime:
    from datetime import timezone

    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
