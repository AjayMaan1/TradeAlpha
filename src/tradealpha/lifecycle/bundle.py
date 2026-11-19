"""
Deployment bundles: research node -> trader node (trading-engine Phase 4, L3).

A research node (``HISTORIFY_ROLE=research``) evaluates ideas and runs the signals / shadow stages; the order stages
run on a trader node (``HISTORIFY_ROLE=trader``) that holds the broker credentials and nothing else. What crosses is a
signed, self-contained bundle of ONE hosted (IR) deployment:

    format        "historify.deployment-bundle/1"
    origin        the exporting node id; exported_at
    deployment    id (kept: trade events of both nodes refer to the same deployment), name, strategy ref, code_sha,
                  params, universe (resolved to an explicit symbol list - the trader needs no reference data), book,
                  execution, limits, delivery, gate policy, stage, evidence (+ the idea id)
    ir            the strategy's canonical IR text and its sha256 (= the deployment's code_sha; re-checked on import)
    history       the deployment's promotion events (the gate reports that approved it)
    gate          with ``promote_to`` = "paper": the research node's passing shadow -> paper gate report (the
                  research node never places orders itself; the trader node starts the deployment in paper)
    sha256        of the canonical bundle without ``sha256`` / ``signature``
    signature     HMAC-SHA256 of that hash with the shared ``trade_events_webhook_secret`` (the node-link secret; the
                  same secret signs the trader's event relay back to the research node)

Import (trader): verifies hash, signature and the IR hash, registers the IR version (same content = same version),
freezes it, creates the deployment with the same id in the exported stage (never above ``eligible_live``: the live
promotion always happens on the trader node, through its own live gate), and starts its hosted session. Importing the
same bundle twice is a no-op; a newer bundle of the same deployment (a later stage) moves the stage forward.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

from sqlalchemy import select

from tradealpha.lifecycle.signals.common import SignalError
from tradealpha.platform.db.models import Deployment, DeploymentEvent, Instrument

FORMAT = "historify.deployment-bundle/1"
STAGE_ORDER = ("draft", "signals", "shadow", "paper", "eligible_live", "live")
MAX_IMPORT_STAGE = "eligible_live"


def _canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str).encode()


def _digest(b: dict) -> str:
    return hashlib.sha256(_canon({k: v for k, v in b.items() if k not in ("sha256", "signature")})).hexdigest()


def _secret(sv) -> str:
    sec = sv.settings.get("trade_events_webhook_secret", None)
    if not sec:
        raise SignalError("set the node-link secret (trade_events_webhook_secret) on both nodes first: bundles are "
                          "only exchanged signed", 409)
    return sec


def export_bundle(sv, dep_id: str, promote_to: str | None = None) -> dict:
    """``promote_to`` ("paper"): the research node runs the gate of that order stage (it cannot promote there itself,
    HISTORIFY_ROLE=research) and the bundle carries the deployment in that stage with the passing gate report - the
    trader node starts it there. Without it the bundle carries the current stage."""
    from tradealpha.lifecycle.signals.host import _host_version_of, is_hosted
    from tradealpha.platform.events import outbox as tev
    from tradealpha.platform.time.timeutil import IST
    with sv.meta.session() as s:
        r = s.get(Deployment, dep_id)
        if r is None:
            raise KeyError(dep_id)
        s.expunge(r)
        history = [{"at": e.at.isoformat() + "Z", "kind": e.kind, "actor": e.actor,
                    "detail": json.loads(e.detail_json or "{}")}
                   for e in s.scalars(select(DeploymentEvent).where(DeploymentEvent.deployment_id == dep_id,
                                                                    DeploymentEvent.kind.in_(("promoted", "created")))
                                      .order_by(DeploymentEvent.id))]
    if not is_hosted(r):
        raise SignalError("only hosted (IR) deployments can be exported to a trader node", 409)
    if r.status == "retired":
        raise SignalError("retired deployments cannot be exported", 409)
    name, ir_text, sha = _host_version_of(sv, r)
    stage, gate = r.stage, None
    if promote_to is not None:
        from tradealpha.lifecycle.gates import promotion as pm
        if promote_to != pm.NEXT.get(r.stage) or promote_to not in ("paper",):
            raise SignalError(f"a bundle can promote {r.stage} -> {pm.NEXT.get(r.stage)} only when that is paper "
                              "(eligible_live and live are promoted on the trader node, on its own evidence)", 409)
        la = sv.backtests.verify_lookahead(r.strategy, r.code_sha)
        if la.get("verified") is not True:
            raise SignalError(f"{r.strategy} is not verified free of look-ahead - it cannot reach an order stage", 409)
        gate = sv.signals.gate_report(r, promote_to, {})
        failed = [c for c in gate["criteria"] if not c["pass"]]
        if failed:
            raise SignalError("the promotion gate did not pass: " + "; ".join(
                f"{c['criterion']} = {c['value']} (needs {c['threshold']})" for c in failed), 409)
        stage = promote_to
    today = sv.clock.now().astimezone(IST).date()
    insts = sv.backtests.snapshots.resolve_universe(json.loads(r.universe_json or "{}"), today, today)["instruments"]
    with sv.meta.session() as s:
        idea = tev._idea_of(s, dep_id)
    ev = json.loads(r.evidence_json or "{}")
    if idea:
        ev["idea_id"] = idea
    b = {"format": FORMAT, "origin": tev.node_id(sv), "exported_at": datetime.now(UTC).isoformat(),
         "deployment": {"id": r.id, "name": r.name, "version": r.version, "strategy": r.strategy,
                        "code_sha": r.code_sha, "params": json.loads(r.params_json or "{}"),
                        "universe": {"kind": "symbols", "symbols": [f"{i.exchange}:{i.symbol}" for i in insts]},
                        "book": json.loads(r.book_json or "{}"), "execution": json.loads(r.execution_json or "{}"),
                        "limits": json.loads(r.limits_json or "{}"), "triggers": json.loads(r.triggers_json or "{}"),
                        "delivery": json.loads(r.delivery_json or "{}"), "gate_policy": r.gate_policy,
                        "stage": stage, "evidence": ev},
         "ir": {"name": name, "ir_json": ir_text, "sha256": sha}, "history": history,
         "gate": json.loads(json.dumps(gate, default=str)) if gate else None, "exported_from_stage": r.stage}
    b["sha256"] = _digest(b)
    b["signature"] = hmac.new(_secret(sv).encode(), b["sha256"].encode(), hashlib.sha256).hexdigest()
    with sv.meta.session() as s:
        s.add(DeploymentEvent(at=datetime.now(UTC).replace(tzinfo=None), deployment_id=dep_id, kind="exported",
                              actor="system", detail_json=json.dumps({"sha256": b["sha256"], "stage": stage,
                                                                      "gate": gate}, default=str)))
        sv.signals.audit("bundle_exported", dep_id, {"sha256": b["sha256"], "stage": stage}, "system", s)
        s.commit()
    return b


def verify_bundle(sv, b: dict) -> None:
    if not isinstance(b, dict) or b.get("format") != FORMAT:
        raise SignalError(f"not a {FORMAT} bundle")
    if _digest(b) != b.get("sha256"):
        raise SignalError("bundle hash mismatch: the bundle was changed after export", 400)
    from tradealpha.platform.audit import security
    secrets_ = security.candidates(sv, "trade_events_webhook_secret") or [_secret(sv)]
    if not any(hmac.compare_digest(hmac.new(sec.encode(), b["sha256"].encode(), hashlib.sha256).hexdigest(),
                                   str(b.get("signature") or "")) for sec in secrets_):
        raise SignalError("bundle signature invalid (a different node-link secret?)", 401)
    ir = b.get("ir") or {}
    if not ir.get("ir_json") or ir.get("sha256") != (b.get("deployment") or {}).get("code_sha"):
        raise SignalError("the bundle's IR is not the deployment's code version", 400)   # re-compiled on import


def import_bundle(sv, b: dict, actor: str = "user", start: bool = True) -> dict:
    from tradealpha.lifecycle.signals.host import STAGE_MODE
    from tradealpha.platform.events import outbox as tev
    verify_bundle(sv, b)
    d, ir = b["deployment"], b["ir"]
    if b.get("origin") == tev.node_id(sv):
        raise SignalError("this bundle was exported by this node", 409)
    # 1. the IR version (identical content = the same version) - its sha must be the deployment's code_sha
    lab = sv.lab
    from tradealpha.platform.db.lab_models import LabStrategy, LabStrategyVersion
    with sv.meta.session() as s:
        st_ = s.scalar(select(LabStrategy).where(LabStrategy.name == ir["name"]))
        sid = st_.id if st_ is not None else None
    if sid is None:
        lab.create_strategy(ir["name"], ir["ir_json"], description=f"imported from {b.get('origin')}")
    else:
        lab.add_version(sid, ir["ir_json"], note=f"imported from {b.get('origin')}")
    with sv.meta.session() as s:
        v = s.scalar(select(LabStrategyVersion).join(LabStrategy, LabStrategy.id == LabStrategyVersion.strategy_id)
                     .where(LabStrategy.name == ir["name"], LabStrategyVersion.ir_sha256 == d["code_sha"]))
        if v is None:
            raise SignalError("the imported IR does not hash to the deployment's code_sha (a different compiler / "
                              "ops version on this node?)", 409)
        ref = f"ir:{ir['name']}@{v.version}"
    sv.backtests.freeze(ref, note=f"bundle {b['sha256'][:12]} from {b.get('origin')}")
    # 2. the instruments (an explicit symbol list - created when unknown here; the bar feed downloads their data)
    created = []
    with sv.meta.session() as s:
        for key in d["universe"]["symbols"]:
            ex, sym = key.split(":", 1)
            if s.scalar(select(Instrument.id).where(Instrument.symbol == sym, Instrument.exchange == ex)) is None:
                s.add(Instrument(symbol=sym, exchange=ex, name=sym, instrument_type="EQ", segment="EQ"))
                created.append(key)
        s.commit()
    # 3. the deployment (same id); stage capped - live is promoted here, through this node's live gate
    stage = d["stage"] if STAGE_ORDER.index(d["stage"]) <= STAGE_ORDER.index(MAX_IMPORT_STAGE) else MAX_IMPORT_STAGE
    now = sv.clock.now().astimezone(UTC).replace(tzinfo=None)
    with sv.meta.session() as s:
        row = s.get(Deployment, d["id"])
        if row is not None:
            if STAGE_ORDER.index(stage) <= STAGE_ORDER.index(row.stage):
                return {"deployment_id": row.id, "imported": False, "stage": row.stage,
                        "note": "already imported (same or later stage)"}
            row.stage, row.stage_since = stage, now
            kind = "stage_imported"
        else:
            s.add(Deployment(id=d["id"], name=d["name"][:100], version=int(d.get("version") or 1), strategy=ref,
                             code_sha=d["code_sha"], params_json=json.dumps(d["params"]),
                             universe_json=json.dumps(d["universe"]), triggers_json=json.dumps(d.get("triggers") or {}),
                             book_json=json.dumps(d["book"]), execution_json=json.dumps(d["execution"]),
                             limits_json=json.dumps(d["limits"]), gate_policy=d.get("gate_policy") or "block",
                             delivery_json=json.dumps(d.get("delivery") or {}),
                             evidence_json=json.dumps(d.get("evidence") or {}), stage=stage, stage_since=now,
                             status="active", runtime="bar_host", created_at=now))
            kind = "imported"
        s.add(DeploymentEvent(at=now, deployment_id=d["id"], kind=kind, actor=actor,
                              detail_json=json.dumps({"origin": b.get("origin"), "sha256": b["sha256"],
                                                      "stage": stage, "exported_stage": d["stage"],
                                                      "history": b.get("history", [])[-5:]}, default=str)))
        tev.try_emit(s, "lifecycle.stage", deployment_id=d["id"], ref=stage,
                     payload={"from": None if kind == "imported" else "imported", "to": stage, "imported": True,
                              "origin": b.get("origin"), "bundle": b["sha256"]})
        sv.signals.audit("bundle_imported", d["id"], {"origin": b.get("origin"), "sha256": b["sha256"],
                                                      "stage": stage}, actor, s)
        s.commit()
    session = None
    if start and stage in STAGE_MODE:
        session = sv.signals.host_sync(d["id"])
    return {"deployment_id": d["id"], "imported": True, "stage": stage, "strategy": ref,
            "created_instruments": created, "session": session}
