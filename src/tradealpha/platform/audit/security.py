"""
Security hardening for live trading (trading-engine Phase 4, L7).

Audit log hash chain
    Every ``audit_log`` row is sealed with ``chain_sha = sha256(previous chain_sha + canonical row)`` (genesis = 64
    zeros). Sealing is done by ONE sealer under a process lock, in id order, after the rows committed (a writer never
    computes its own link, so concurrent writers cannot fork the chain). ``verify_audit`` walks the chain: an edited
    or deleted sealed row breaks it; unsealed rows may only exist at the tail (written since the last seal).
    ``anchor_audit`` publishes the chain head as a TradeEvent ``audit.anchor`` - the research node / any consumer keeps
    a copy outside this database, so even a rewrite of the whole table is detectable.

Secret rotation with a grace window
    ``rotate_secret(name, value, grace_h)`` for the HMAC secrets shared with other systems (``ideas_intake_secret``:
    submissions from the idea engine; ``trade_events_webhook_secret``: the relay / trader -> research ingest). The old
    value stays valid for ``grace_h`` hours: intake accepts either signature, the relay sends both
    (``X-Historify-Signature`` new, ``X-Historify-Signature-Prev`` old) so the receiver can switch without a gap.
    API tokens (``AuthService.rotate_token``) and the order-route token (``SignalService.rotate_token``) rotate the
    same way.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from tradealpha.platform.db.models import AuditLog

GENESIS = "0" * 64
ROTATABLE = ("ideas_intake_secret", "trade_events_webhook_secret")
ROTATION_KEY = "secret_rotation"                     # {name: grace-until ISO (UTC)}
ANCHOR_KEY = "audit_anchor"                          # {id, chain_sha, at} of the last published anchor
_seal_lock = threading.Lock()


def _canon(a: AuditLog) -> bytes:
    return json.dumps({"id": a.id, "at": a.at.isoformat() if a.at else None, "actor": a.actor, "action": a.action,
                       "object": a.object, "detail": a.detail_json}, sort_keys=True, separators=(",", ":")).encode()


def link(prev: str, a: AuditLog) -> str:
    return hashlib.sha256(prev.encode() + _canon(a)).hexdigest()


def seal_audit(sv) -> int:
    """Seal every unsealed audit row (id order). Returns the number sealed."""
    with _seal_lock, sv.meta.session() as s:
        last = s.scalar(select(AuditLog).where(AuditLog.chain_sha.is_not(None)).order_by(AuditLog.id.desc()).limit(1))
        prev = last.chain_sha if last is not None else GENESIS
        lo = last.id if last is not None else 0
        n = 0
        for a in s.scalars(select(AuditLog).where(AuditLog.id > lo, AuditLog.chain_sha.is_(None))
                           .order_by(AuditLog.id)):
            a.chain_sha = link(prev, a)
            prev = a.chain_sha
            n += 1
        s.commit()
    return n


def verify_audit(sv) -> dict:
    """Walk the chain from the genesis. -> {ok, checked, unsealed, head, broken_at?}"""
    with sv.meta.session() as s:
        prev, n, unsealed, head = GENESIS, 0, 0, None
        for a in s.scalars(select(AuditLog).order_by(AuditLog.id)):
            if a.chain_sha is None:
                unsealed += 1
                continue
            if unsealed:                                  # a sealed row after an unsealed one: the chain was cut
                return {"ok": False, "checked": n, "unsealed": unsealed, "broken_at": a.id,
                        "reason": "a sealed row follows unsealed rows"}
            if link(prev, a) != a.chain_sha:
                return {"ok": False, "checked": n, "unsealed": unsealed, "broken_at": a.id,
                        "reason": "the row or its predecessor was changed / removed"}
            prev, head = a.chain_sha, a.id
            n += 1
    anchor = sv.settings.get(ANCHOR_KEY, None) or {}
    if anchor.get("id") and head is not None and int(anchor["id"]) <= head:
        with sv.meta.session() as s:
            row = s.get(AuditLog, int(anchor["id"]))
        if row is None or row.chain_sha != anchor.get("chain_sha"):
            return {"ok": False, "checked": n, "unsealed": unsealed, "broken_at": int(anchor["id"]),
                    "reason": "the published anchor no longer matches the chain (rewritten history)"}
    elif anchor.get("id") and (head or 0) < int(anchor["id"]):
        return {"ok": False, "checked": n, "unsealed": unsealed, "broken_at": head,
                "reason": "rows up to the published anchor are missing (truncated)"}
    return {"ok": True, "checked": n, "unsealed": unsealed, "head": head}


def anchor_audit(sv) -> dict | None:
    """Seal, then publish the chain head as a TradeEvent (only when it moved)."""
    seal_audit(sv)
    with sv.meta.session() as s:
        a = s.scalar(select(AuditLog).where(AuditLog.chain_sha.is_not(None)).order_by(AuditLog.id.desc()).limit(1))
        if a is None:
            return None
        cur = sv.settings.get(ANCHOR_KEY, None) or {}
        if cur.get("id") == a.id:
            return cur
        out = {"id": a.id, "chain_sha": a.chain_sha, "at": datetime.now(UTC).isoformat()}
        from tradealpha.platform.events import outbox as tev
        tev.emit(s, "audit.anchor", ref=a.id, payload={"audit_id": a.id, "chain_sha": a.chain_sha})
        s.commit()
    sv.settings.set(ANCHOR_KEY, out, "json")
    return out


# ------------------------------------------------------------------------------------------------ secret rotation
def _now(sv) -> datetime:
    return sv.clock.now().astimezone(UTC)


def rotate_secret(sv, name: str, value: str, grace_h: float = 24.0, actor: str = "user") -> dict:
    if name not in ROTATABLE:
        raise ValueError(f"unknown secret {name!r} (rotatable: {list(ROTATABLE)})")
    value = str(value or "")
    if len(value) < 16:
        raise ValueError("the new secret must be at least 16 characters")
    old = sv.settings.get(name, None)
    if old == value:
        raise ValueError("the new secret equals the current one")
    grace_h = max(0.0, min(float(grace_h), 24 * 14))
    until = (_now(sv) + timedelta(hours=grace_h)).isoformat()
    rot = dict(sv.settings.get(ROTATION_KEY, None) or {})
    if old and grace_h > 0:
        sv.settings.set(f"{name}_prev", old)
        rot[name] = until
    else:
        sv.settings.set(f"{name}_prev", None)
        rot.pop(name, None)
    sv.settings.set(ROTATION_KEY, rot, "json")
    sv.settings.set(name, value)
    sv.signals.audit("secret_rotated", name, {"grace_h": grace_h, "had_previous": bool(old)}, actor)
    return {"name": name, "rotated": True, "previous_valid_until": rot.get(name)}


def previous_secret(sv, name: str) -> str | None:
    """The previous value of a rotated secret while its grace window lasts (else None)."""
    until = (sv.settings.get(ROTATION_KEY, None) or {}).get(name)
    if not until:
        return None
    try:
        if _now(sv) > datetime.fromisoformat(until):
            return None
    except ValueError:
        return None
    return sv.settings.get(f"{name}_prev", None)


def candidates(sv, name: str) -> list[str]:
    out = [sv.settings.get(name, None), previous_secret(sv, name)]
    return [x for x in out if x]


def rotation_status(sv) -> dict:
    rot = sv.settings.get(ROTATION_KEY, None) or {}
    return {n: {"configured": bool(sv.settings.get(n, None)),
                "previous_valid_until": rot.get(n) if previous_secret(sv, n) else None} for n in ROTATABLE}
