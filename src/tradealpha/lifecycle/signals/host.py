"""
ONE paper lifecycle (unification U4) - a mixin of SignalService.

Before: an idea had two unrelated ways to reach paper trading. A Python strategy (flagfix2) became a *deployment*
(draft -> signals -> shadow -> paper -> eligible_live -> live, gate reports, audit, delivery) run by the P4 replay
runtime. An IR strategy (flagfix1) became a *lab paper session* (per-bar host, as-seen journal, OMS, guardrails,
nightly parity, drills) with no lifecycle, no promotion gate and no delivery.

Now the deployment IS the lifecycle for both, and the runtime is chosen by the strategy kind:

    deployment.runtime = "replay"     Python strategies: P4 replay runtime (unchanged)
    deployment.runtime = "bar_host"   IR strategies (``ir:<name>``): the hbt per-bar host. Every stage that runs has
                                      its own lab paper session (``lab_paper_sessions.deployment_id / _stage``):

        stage            hosted session mode          orders
        draft            -                            -
        signals          paper (simulated broker)     none at any broker; signals delivered (webhook / Telegram / UI)
        shadow           paper (simulated broker)     none at any broker; the simulated book is the shadow book
        paper            analyzer (G2, OpenAlgo)      OpenAlgo Analyzer orders, protective stops rest at the broker
        eligible_live    analyzer                     unchanged until the live promotion
        live             dry_run, then live           dry_run: the live route's decisions + OMS, never sent (first
                                                      ``dry_run_sessions`` sessions); live: OpenAlgo with Analyzer
                                                      OFF, stepped caps, margin check, auto demotion (Phase 4)

    A promotion creates the next stage's session (validated by the session guardrails first: a refusal fails the
    promotion) and stops the previous one; pause / retire stop it, resume restarts it. A stage therefore always
    starts flat at the new route - simulated positions are never "moved" to a broker.

Every decision the host journals (as-seen bars hash, intents, fills) becomes a persisted Signal of the deployment
(fingerprint = the as-seen bars hash) and goes through the one delivery pipeline. The promotion gate reads its
evidence from the host's journal:

    sessions          distinct trading days with journaled decisions in the stage
    logic             nightly parity divergences (the as-seen replay differs from the journal, or a final-data
                      difference no revised bar explains) - the flagfix1 as-seen hash parity
    data_revision     parity "revision" days (explained by a revised / missing bar) - reported, never blocking
    gate pass rate    decisions journaled without a host error and without a stale-feed block
    shadow returns    realised P&L net of fees per day of the stage's simulated book
    slippage ratio    adverse broker fill vs the quote mid (Analyzer) / the engine's slippage model (ADR-001)
    loss stops        G-IN-2 daily-loss events in the stage

paper -> eligible_live additionally needs the operational drills (kill-9, kill switch, feed stop, auth expiry) passed
in the last 30 days and a readiness sign-off (flagfix1 T0 checklist) on record - for every runtime.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from tradealpha.lifecycle.signals.common import SignalError
from tradealpha.platform.db.lab_models import (
    LabDecision,
    LabDrill,
    LabFill,
    LabGuardrailEvent,
    LabOrder,
    LabPaperMember,
    LabPaperSession,
    LabParityReport,
    LabSignoff,
    LabStrategy,
    LabStrategyVersion,
)
from tradealpha.platform.db.models import Deployment, DeploymentEvent, Instrument, Signal
from tradealpha.platform.time.timeutil import IST

log = logging.getLogger(__name__)

BAR_HOST = "bar_host"
STAGE_MODE = {"signals": "paper", "shadow": "paper", "paper": "analyzer", "eligible_live": "analyzer",
              "live": "dry_run"}                     # live: dry_run for the first sessions, then the live route
HOST_MAX_MEMBERS = 50
DRILLS = ("kill9", "kill_switch", "feed_stop", "auth_expiry")
ACTIONS = {"enter_long": "ENTER_LONG", "enter_short": "ENTER_SHORT", "exit_long": "EXIT", "exit_short": "EXIT",
           "exit_all": "EXIT"}


def is_hosted(r) -> bool:
    return (getattr(r, "runtime", None) or "replay") == BAR_HOST


def base_name(strategy: str | None) -> str:
    """``ir:name@3`` and ``ir:name`` are the same strategy (the version is pinned by the code hash)."""
    s = str(strategy or "")
    return s.split("@", 1)[0] if s.startswith("ir:") else s


class HostMixin:
    # ------------------------------------------------------------------ sessions of a deployment
    def host_sessions(self, dep_id: str, stage: str | None = None) -> list[LabPaperSession]:
        with self.sv.meta.session() as s:
            q = select(LabPaperSession).where(LabPaperSession.deployment_id == dep_id)
            if stage:
                q = q.where(LabPaperSession.deployment_stage == stage)
            rows = list(s.scalars(q.order_by(LabPaperSession.created_at)))
            for x in rows:
                s.expunge(x)
        return rows

    def host_current(self, dep_id: str) -> dict | None:
        rows = [x for x in self.host_sessions(dep_id) if x.status in ("created", "running", "stopped", "killed")]
        if not rows:
            return None
        x = rows[-1]
        return {"id": x.id, "name": x.name, "mode": x.mode, "status": x.status, "stage": x.deployment_stage,
                "kill_reason": x.kill_reason}

    def _host_version_id(self, r) -> int:
        name = base_name(r.strategy)[3:]
        with self.sv.meta.session() as s:
            vid = s.scalar(select(LabStrategyVersion.id).join(LabStrategy, LabStrategy.id == LabStrategyVersion.strategy_id)
                           .where(LabStrategy.name == name, LabStrategyVersion.ir_sha256 == r.code_sha)
                           .order_by(LabStrategyVersion.version.desc()).limit(1))
        if vid is None:
            raise SignalError(f"the IR version {r.code_sha[:12]} of {r.strategy} no longer exists", 409)
        return int(vid)

    def _host_slippage(self, inst_ids: list[int]) -> float:
        """flagfix2 slippage model (ADR-001), the same as an IR backtest: the liquidity bucket of the median daily
        turnover of the last 60 sessions; the session uses the most conservative bucket of its members."""
        from tradealpha.platform.time.intervals import INTERVALS
        from tradealpha.research.kernel import rules
        ex = rules.ExecConfig()
        worst = 0.0
        for iid in inst_ids:
            bars = self.sv.store.read_range(iid, INTERVALS["D"].id, limit=60, tail=True)
            turnover = float((bars["close"] * bars["volume"]).median()) if len(bars) else float("nan")
            frac = float(ex.slippage_bps[rules.liquidity_bucket(turnover)]) / 1e4 * float(ex.slippage_mult)
            worst = max(worst, frac)
        return round(min(worst, 0.05), 6)

    def _host_spec(self, r, inst_ids: list[int]) -> dict:
        """The ExecSpec of a hosted session = the unified backtest's (ADR-001: square-off, cut-off, participation and
        its remainder rule, MIS leverage, the flagfix2 slippage model) - so paper trades what the backtest modelled."""
        from tradealpha.research.kernel import ir_engine
        from tradealpha.research.kernel.rules import ExecConfig
        cls = self.sv.backtests.load_strategy(r.strategy, r.code_sha)
        over = {k: v for k, v in json.loads(r.execution_json or "{}").items() if k in ExecConfig.__dataclass_fields__}
        ex = ExecConfig(**{**over, "product": over.get("product") or cls.product})
        spec = {k: v for k, v in ir_engine.unified_spec(ex).items() if k != "product"}
        return {**spec, **ir_engine.slippage_spec(ex, self._host_slippage(inst_ids))}

    # ------------------------------------------------------------------ live (Phase 4 L1)
    def _live_info(self, r) -> dict:
        return json.loads(r.live_json or "{}")

    def live_mode_for(self, r) -> str:
        """dry_run until ``dry_run_sessions`` completed sessions (days with journaled decisions, today excluded) of the
        live stage, then live."""
        info = self._live_info(r)
        n = int(info.get("dry_run_sessions", self.live_config()["dry_run_sessions"]) or 0)
        if n <= 0:
            return "live"
        return "live" if self._stage_days(r.id, "live", "dry_run") >= n else "dry_run"

    def _stage_days(self, dep_id: str, stage: str, mode: str | None = None, since=None) -> int:
        today = self.now().astimezone(IST).date()
        with self.sv.meta.session() as s:
            q = select(LabPaperSession.id).where(LabPaperSession.deployment_id == dep_id,
                                                 LabPaperSession.deployment_stage == stage)
            if mode:
                q = q.where(LabPaperSession.mode == mode)
            sids = list(s.scalars(q))
            if not sids:
                return 0
            mids = list(s.scalars(select(LabPaperMember.id).where(LabPaperMember.session_id.in_(sids))))
            rows = list(s.execute(select(LabDecision.member_id, LabDecision.bar_ts).where(
                LabDecision.member_id.in_(mids), LabDecision.bars_sha256 != "-"))) if mids else []
        bar_s = self._member_bar_seconds(mids)
        lo = int(since.timestamp()) if since is not None else None
        days = {datetime.fromtimestamp(t, IST).date() for m, t in rows if lo is None or t + bar_s.get(m, 0) >= lo}
        return len({d for d in days if d < today})

    def _live_guard(self, r, mode: str) -> dict:
        """Stepped caps of the live stage (signals.live.steps) as session guardrails; margin checked on live."""
        caps = self.step_caps(int(self._live_info(r).get("step") or 1))
        return {"max_order_notional": float(caps["max_order_notional"]),
                "max_gross_exposure": float(caps["max_gross"]),
                "max_orders_per_day": int(caps["max_orders_per_day"]), "require_margin": mode == "live"}

    def hosted_live_extras(self, r) -> list[dict]:
        """Extra live-gate criteria for hosted deployments: the hbt Lab's drills + readiness sign-off, verified
        tariffs, and a clean broker reconciliation record of the paper stages."""
        from tradealpha.execution.risk import guardrails as G
        from tradealpha.lifecycle.gates import promotion as pm
        from tradealpha.lifecycle.gates.drills import LIVE_DRILLS
        from tradealpha.platform.db.lab_models import LabBrokerProfile, LabCostSchedule
        from tradealpha.platform.db.models import BrokerReconciliation
        crits = self.ops_readiness()
        since = (self.now() - timedelta(days=30)).astimezone(UTC).replace(tzinfo=None)
        with self.sv.meta.session() as s:
            latest = {}
            for d in s.scalars(select(LabDrill).where(LabDrill.created_at >= since, LabDrill.name.in_(LIVE_DRILLS))
                               .order_by(LabDrill.id)):
                latest[d.name] = bool(d.passed)
        missing = [n for n in LIVE_DRILLS if latest.get(n) is not True]
        crits.append(pm.crit("live drills passed (30 days)", sorted(k for k, v in latest.items() if v),
                             list(LIVE_DRILLS), not missing,
                             f"missing / failed: {missing} (historify lab drills run --live)" if missing else ""))
        cc = getattr(self.sv, "clock_check", None)
        off = None
        if cc is not None:
            off = cc.last_offset()
            if off is None and cc.server() is not None:
                off = cc.refresh().get("offset_s")
        lim = float(G.DEFAULTS["max_clock_offset_s"])
        crits.append(pm.crit("clock offset measured (NTP, G-SYS-3)", None if off is None else round(off, 4),
                             f"|offset| <= {lim} s", off is not None and abs(off) <= lim,
                             "" if off is not None else "set ntp_server (Settings) - the live route needs a "
                                                        "measured clock"))
        key = self.sv.settings.get("lab_broker", None)
        with self.sv.meta.session() as s:
            b = s.scalar(select(LabBrokerProfile).where(LabBrokerProfile.key == key)) if key else None
            n = s.scalar(select(func.count()).select_from(LabCostSchedule)) or 0
            v = s.scalar(select(func.count()).select_from(LabCostSchedule).where(
                LabCostSchedule.verified.is_(True))) or 0
            recs = list(s.scalars(select(BrokerReconciliation).where(BrokerReconciliation.deployment_id == r.id)))
        crits.append(pm.crit("broker tariff + statutory charges verified (D-13)",
                             {"broker": bool(b and b.verified), "statutory": f"{v}/{n}"}, "verified",
                             bool(b and b.verified) and n > 0 and v == n,
                             "" if b else "set lab_broker and verify it on a real contract note"))
        ok = sum(1 for x in recs if x.status == "ok")
        bad = sum(1 for x in recs if x.status == "diverged")
        crits.append(pm.crit("broker reconciliation record (paper)", {"ok": ok, "diverged": bad},
                             ">= 1 ok, 0 diverged", ok >= 1 and bad == 0))
        return crits

    def live_step(self, dep_id: str, step: int, confirm: str | None, actor: str = "user") -> dict:
        """Move a live hosted deployment's caps to ``step`` (down: any time; up: one step, after
        ``step_min_sessions`` clean live sessions at the current step). Typed confirmation: the deployment name."""
        from tradealpha.platform.db.models import BrokerReconciliation
        cfg = self.live_config()
        with self.sv.meta.session() as s:
            r = s.get(Deployment, dep_id)
            if r is None:
                raise KeyError(dep_id)
            s.expunge(r)
        if not is_hosted(r) or r.stage != "live":
            raise SignalError("stepped caps apply to a hosted deployment in the live stage", 409)
        if confirm != r.name:
            raise SignalError(f"type the deployment name ({r.name!r}) to change its live caps", 409)
        info = self._live_info(r)
        cur = int(info.get("step") or 1)
        step = int(step)
        if not 1 <= step <= len(cfg["steps"]):
            raise SignalError(f"step must be 1..{len(cfg['steps'])}")
        if step > cur:
            if step != cur + 1:
                raise SignalError("caps go up one step at a time", 409)
            since = datetime.fromisoformat(info["steps"][-1]["at"]).replace(tzinfo=UTC) if info.get("steps") else None
            days = self._stage_days(dep_id, "live", "live", since)
            with self.sv.meta.session() as s:
                bad = s.scalar(select(func.count()).select_from(BrokerReconciliation).where(
                    BrokerReconciliation.deployment_id == dep_id, BrokerReconciliation.status == "diverged")) or 0
            if days < int(cfg["step_min_sessions"]) or bad:
                raise SignalError(f"step {cur} -> {step} needs {cfg['step_min_sessions']} clean live sessions at step "
                                  f"{cur} (have {days}, reconciliation differences {bad})", 409)
        info["step"] = step
        info.setdefault("steps", []).append({"step": step, "at": self._utc().isoformat(), "by": actor})
        caps = self.step_caps(step)
        with self.sv.meta.session() as s:
            row = s.get(Deployment, dep_id)
            row.live_json = json.dumps(info)
            for x in s.scalars(select(LabPaperSession).where(LabPaperSession.deployment_id == dep_id,
                                                             LabPaperSession.deployment_stage == "live")):
                g = json.loads(x.guardrails_json or "{}")
                g.update({k: v for k, v in self._live_guard(row, x.mode).items() if k != "require_margin"})
                x.guardrails_json = json.dumps(g)
            from tradealpha.platform.events import outbox as tev
            tev.try_emit(s, "lifecycle.stage", deployment_id=dep_id, ref=f"live-step-{step}",
                         payload={"from": "live", "to": "live", "step": step, "caps": caps, "actor": actor})
            self.audit("live_step", dep_id, {"from": cur, "to": step, "caps": caps}, actor, s)
            s.commit()
        return self.get(dep_id)

    def demote(self, dep_id: str, reason: str, actor: str = "system") -> dict | None:
        """Automatic demotion of a live deployment (Phase 4.5): live -> eligible_live, paused, its live session killed
        (positions flattened unless ``signals.live.demote_flatten`` is off), notification + events + audit."""
        with self.sv.meta.session() as s:
            row = s.get(Deployment, dep_id)
            if row is None or row.stage != "live":
                return None
            row.stage, row.stage_since = "eligible_live", self._utc()
            row.status, row.paused_reason = "paused", f"demoted from live: {reason}"[:500]
            s.add(DeploymentEvent(at=self._utc(), deployment_id=dep_id, kind="demoted", actor=actor,
                                  detail_json=json.dumps({"reason": reason})))
            from tradealpha.platform.events import outbox as tev
            tev.try_emit(s, "lifecycle.stage", deployment_id=dep_id, ref="eligible_live",
                         payload={"from": "live", "to": "eligible_live", "demoted": True, "reason": reason,
                                  "actor": actor})
            self.audit("demoted", dep_id, {"reason": reason}, actor, s)
            name = row.name
            s.commit()
        flatten = bool(self.live_config().get("demote_flatten", True))
        for x in self.host_sessions(dep_id, "live"):
            if x.status in ("running", "stopped"):
                try:
                    self.sv.lab_paper.kill(session_id=x.id, flatten=flatten, reason=f"demotion: {reason}",
                                           source="demotion")
                except Exception:
                    log.exception("demotion: killing %s failed", x.id)
        notify = getattr(self.sv, "notify", None)
        if notify is not None:
            notify.notify("error", "live_demoted", f"Live deployment demoted: {name}", reason, ref=dep_id,
                          fingerprint=f"live_demoted:{dep_id}", throttle_s=0)
        self._host_follow(dep_id)
        return {"deployment_id": dep_id, "reason": reason, "flattened": flatten}

    def _live_demotion_reason(self, r, cur: dict) -> str | None:
        from tradealpha.platform.db.models import BrokerReconciliation
        sids = [x.id for x in self.host_sessions(r.id, "live")]
        if not sids:
            return None
        cfg = self.live_config()
        today0 = datetime.combine(self.now().astimezone(IST).date(), datetime.min.time(), IST) \
            .astimezone(UTC).replace(tzinfo=None)
        with self.sv.meta.session() as s:
            if s.scalar(select(func.count()).select_from(LabParityReport).where(
                    LabParityReport.session_id.in_(sids), LabParityReport.status == "divergence")):
                return "nightly parity diverged (a logic divergence on the live route)"
            if s.scalar(select(func.count()).select_from(BrokerReconciliation).where(
                    BrokerReconciliation.session_id.in_(sids), BrokerReconciliation.status == "diverged")):
                return "broker reconciliation found an unexplained difference"
            rejects = s.scalar(select(func.count()).select_from(LabOrder).where(
                LabOrder.session_id.in_(sids), LabOrder.status == "rejected", LabOrder.created_at >= today0)) or 0
            if rejects >= int(cfg["max_rejects_per_day"]):
                return f"{rejects} broker rejects today (limit {cfg['max_rejects_per_day']})"
            ev_ = s.scalar(select(LabGuardrailEvent).where(
                LabGuardrailEvent.session_id.in_(sids), LabGuardrailEvent.guardrail.in_(("G-IN-2", "G-IN-3")),
                LabGuardrailEvent.created_at >= today0).limit(1))
            if ev_ is not None:
                return f"{ev_.guardrail}: {ev_.message}"
        if cur.get("status") == "killed":
            return f"the live session was killed: {cur.get('kill_reason') or 'kill switch'}"
        return None

    def _host_prepare(self, r, stage: str, mode: str | None = None) -> str:
        """Create (not start) the session the deployment runs in ``stage``. Raises SignalError when the session's
        guardrails refuse it (e.g. an entry without a protective stop)."""
        from tradealpha.research.runs.lab import LabError
        mode = mode or (self.live_mode_for(r) if stage == "live" else STAGE_MODE[stage])
        vid = self._host_version_id(r)
        uni = json.loads(r.universe_json or "{}")
        today = self.now().astimezone(IST).date()
        try:
            insts = self.sv.backtests.snapshots.resolve_universe(uni, today, today)["instruments"]
        except Exception as e:
            raise SignalError(f"universe: {e}") from e
        if len(insts) > HOST_MAX_MEMBERS:
            raise SignalError(f"hosted (IR) deployments are limited to {HOST_MAX_MEMBERS} instruments for now", 409)
        book = json.loads(r.book_json or "{}")
        params = json.loads(r.params_json or "{}")
        members = [{"strategy_version_id": vid, "instrument_id": i.id, "params": params} for i in insts]
        spec = self._host_spec(r, [i.id for i in insts])
        guard = dict(self.config().get("guardrails") or {})
        from tradealpha.platform.db.models import IdeaVersion
        with self.sv.meta.session() as s:
            is_idea = s.scalar(select(IdeaVersion.id).where(IdeaVersion.deployment_id == r.id).limit(1)) is not None
        if is_idea:                                   # Phase 3.3: the cross-idea caps ride on the session guardrails
            from tradealpha.ideas import risk as pf
            guard.update(pf.guardrail_params(self.sv.ideas.config().get("risk") or {}))
        plan = None
        if len(insts) > 1:
            # the same account rule as the backtest (hbt portfolio mode): equal-weight slots, at most max_positions
            # open - enforced at the OMS by G-PRE-5 (the engine of each member may still hold a blocked entry)
            ex = json.loads(r.execution_json or "{}")
            max_pos = max(1, min(int(ex.get("max_positions") or 10), len(insts)))
            plan = {"sizing": "pct_equity", "sizing_value": round(1.0 / max_pos, 10)}
            guard["max_open_positions"] = min(int(guard.get("max_open_positions") or max_pos), max_pos)
        if stage == "live":
            guard.update(self._live_guard(r, mode))
        n = len(self.host_sessions(r.id)) + 1
        name = f"{r.name[:50]} · {stage}{'/' + mode if stage == 'live' else ''} · {r.id[:8]} #{n}"
        try:
            sess = self.sv.lab_paper.create_session(
                name, members, cash=float(book.get("capital") or 1_000_000.0), mode=mode, spec=spec, plan=plan,
                guardrails=guard or None, warmup_days=int(book.get("warmup_days") or 20),
                allow_live=stage == "live")
        except LabError as e:
            raise SignalError(f"the {stage} session was refused: {e}", 409) from e
        with self.sv.meta.session() as s:
            x = s.get(LabPaperSession, sess["id"])
            x.deployment_id, x.deployment_stage = r.id, stage
            s.commit()
        return sess["id"]

    def _host_feed(self, dep_id: str, sid: str | None) -> dict | None:
        """The ONE market-data follow service (unification U5): a hosted deployment owns a persistent live-bar feed
        (FeedService) of its session's instruments and interval, so its bars are downloaded as they close whether or
        not a browser is open; ``sid`` None deactivates it (paused / retired / stage without a session)."""
        feeds = getattr(self.sv, "bar_feeds", None)
        if feeds is None:
            return None
        name = f"deployment {dep_id[:8]}"
        cur = next((f for f in feeds.list() if f["name"] == name), None)
        if sid is None:
            return feeds.set_active(cur["id"], False) if cur and cur["active"] else cur
        with self.sv.meta.session() as s:
            x = s.get(LabPaperSession, sid)
            ids = sorted(set(s.scalars(select(LabPaperMember.instrument_id).where(LabPaperMember.session_id == sid))))
            interval = x.interval
        if cur is not None:
            if cur["active"] and cur["intervals"] == [interval] and cur["instruments"] == len(ids):
                return cur
            feeds.delete(cur["id"])
        return feeds.create(name, instrument_ids=ids, intervals=(interval,))

    def host_sync(self, dep_id: str) -> dict | None:
        """Bring the hosted session in line with the deployment's stage and status (pause / resume / retire) and, in
        the live stage, with the live mode (dry_run for the first sessions, then live)."""
        with self.sv.meta.session() as s:
            r = s.get(Deployment, dep_id)
            if r is None or not is_hosted(r):
                return None
            s.expunge(r)
        want_running = r.status == "active" and r.stage in STAGE_MODE
        mode = (self.live_mode_for(r) if r.stage == "live" else STAGE_MODE.get(r.stage)) if want_running else None
        cur = [x for x in self.host_sessions(dep_id) if x.deployment_stage == r.stage
               and (r.stage != "live" or x.mode == mode)]
        for x in self.host_sessions(dep_id):
            if x.status == "running" and (not want_running or x.deployment_stage != r.stage
                                          or (r.stage == "live" and x.mode != mode)):
                self.sv.lab_paper.stop_session(x.id)
        if want_running and self.config()["kill_switch"]:
            running = [x for x in cur if x.status == "running"]
            # ADR-006: a running session keeps its feed under the latch (it must manage exits, stops and the
            # square-off); a route is never STARTED while the kill switch is on
            self._host_feed(dep_id, running[-1].id if running else None)
            return self.host_current(dep_id)
        if want_running:
            live = [x for x in cur if x.status != "killed"]
            if not live:                                   # killed (kill switch / G-IN-3): a deliberate new session
                sid = self._host_prepare(r, r.stage, mode)
            else:
                sid = live[-1].id
            self.sv.lab_paper.start_session(sid)
            self._host_feed(dep_id, sid)
        else:
            self._host_feed(dep_id, None)
        return self.host_current(dep_id)

    def _host_watch(self, rows) -> None:
        """Hosted deployments follow their session: a killed session or a parity divergence pauses the deployment
        (the P4 rule: a logic divergence is a bug - paused until investigated). In the live stage the same events -
        plus a broker reconciliation difference, too many rejects or a loss stop - DEMOTE it (Phase 4.5), and the
        dry_run -> live switch happens here once the dry-run sessions are over."""
        for r in rows:
            if not is_hosted(r) or r.status != "active" or r.stage not in STAGE_MODE:
                continue
            cur = self.host_current(r.id)
            if cur is None or cur["stage"] != r.stage:
                continue
            if r.stage == "live":
                why = self._live_demotion_reason(r, cur)
                if why:
                    self.demote(r.id, why)
                    continue
                if cur["mode"] != self.live_mode_for(r) and not self.config()["kill_switch"]:
                    try:
                        self.host_sync(r.id)
                    except SignalError as e:
                        self.demote(r.id, f"the live session was refused: {e}")
                continue
            if cur["status"] == "killed":
                self._fail_dep(r.id, f"the hosted session was killed: {cur['kill_reason'] or 'kill switch'}")
                continue
            with self.sv.meta.session() as s:
                bad = s.scalar(select(func.count()).select_from(LabParityReport).where(
                    LabParityReport.session_id == cur["id"], LabParityReport.status == "divergence"))
            if bad:
                self._fail_dep(r.id, f"nightly parity of the hosted session diverged on {bad} day(s) - a logic "
                                     "divergence is a bug; paused until investigated")

    # ------------------------------------------------------------------ decisions -> signals (one delivery)
    def on_host_decision(self, sid: str, member_id: int, out: dict) -> list[int]:
        """Listener of the per-bar host (``PaperService.listeners``): the decisions of a hosted deployment become
        its persisted signals (exactly once per deployment, instrument, decision time and action) and are
        delivered like every other signal."""
        if not out or not out.get("intents"):
            return []
        with self.sv.meta.session() as s:
            x = s.get(LabPaperSession, sid)
            if x is None or not x.deployment_id:
                return []
            dep = s.get(Deployment, x.deployment_id)
            if dep is None or dep.stage not in STAGE_MODE or dep.status != "active":
                return []
            m = s.get(LabPaperMember, member_id)
            inst = s.get(Instrument, m.instrument_id)
            from tradealpha.platform.time.intervals import resolve
            bar_s = resolve(x.interval).seconds
            product = json.loads(x.exec_json)["spec"].get("product") or "MIS"
            t = int(out["ts"]) + bar_s                          # decided at the bar's close
            key = f"{inst.exchange}:{inst.symbol}"
            ids = []
            for rec in out["intents"]:
                action = ACTIONS.get(rec.get("kind"))
                if action is None or not rec.get("orders"):
                    continue                                    # no action (already in the position, cut-off ...)
                orders = [s.get(LabOrder, i) for i in rec["orders"]]
                main = next((o for o in orders if o is not None and o.reason == ("entry" if action != "EXIT"
                                                                                 else "exit")), None)
                if main is None:
                    continue
                if s.scalar(select(Signal.id).where(Signal.deployment_id == dep.id, Signal.key == key,
                                                    Signal.decision_t == t, Signal.action == action)):
                    continue
                sig = Signal(deployment_id=dep.id, run_id=None, instrument_id=inst.id, key=key, decision_t=t,
                             action=action, side=1 if main.side == "BUY" else -1, qty=int(main.qty),
                             ref_price=main.price, limit_price=main.price if main.order_type == "LIMIT" else None,
                             product=product if product in ("MIS", "CNC") else "MIS", fill_after=t,
                             valid_until=t + bar_s,
                             reason_json=json.dumps({"runtime": BAR_HOST, "session": sid, "member": member_id,
                                                     "intent": rec.get("kind"), "orders": rec["orders"],
                                                     "stage": dep.stage}),
                             fingerprint=out.get("bars_sha256"), status="new", created_at=self._utc())
                s.add(sig)
                s.flush()
                from tradealpha.platform.events import outbox as ev
                ev.try_emit(s, "signal", deployment_id=dep.id, ref=sig.id,
                            payload={"key": key, "action": action, "qty": int(main.qty), "price": main.price,
                                     "decision_t": t, "stage": dep.stage, "fingerprint": sig.fingerprint})
                ids.append(sig.id)
            s.commit()
            view = self._view(dep) if ids else None
        if ids:
            self._dispatch(view, ids)
        return ids

    # ------------------------------------------------------------------ parity (one view with the P4 classes)
    def on_host_parity(self, sid: str, rep: dict) -> dict | None:
        """Listener of the host's nightly parity: the report of a hosted session becomes the deployment's
        ``ParityCheck`` of that day, in the P4 divergence classes -
            logic          the as-seen replay differs from the journal, or a final-data difference that no revised /
                           missing bar explains (a bug: the deployment pauses - P4 rule)
            data_revision  explained by a revised / vanished bar of the final data
            late_data      bars the session never saw (missing in the feed at decision time, present later)
            data_source    a WebSocket-built bar (decision_source ws) differs from the downloaded record (Phase 4 L2)
            host_error     the strategy run failed on a bar (journaled, never traded)
        (the P4 class ``timing`` cannot occur: the host decides on every bar)."""
        from datetime import date as _date

        from tradealpha.marketdata.integrity.issues import raise_issue
        from tradealpha.platform.db.models import ParityCheck
        with self.sv.meta.session() as s:
            x = s.get(LabPaperSession, sid)
            if x is None or not x.deployment_id:
                return None
            dep_id, name = x.deployment_id, x.name
        rows = [r for m in rep.get("members", []) for r in m.get("differences", [])]
        classes = {"logic": sum(1 for r in rows if r.get("status") == "divergence"),
                   "data_revision": sum(1 for r in rows if r.get("status") == "revision"),
                   "data_source": sum(1 for r in rows if r.get("status") == "data_source"),
                   "host_error": sum(1 for r in rows if r.get("status") == "host_error"),
                   "late_data": sum(len(m.get("missing_bars") or []) for m in rep.get("members", []))}
        classes = {k: v for k, v in classes.items() if v}
        day = _date.fromisoformat(rep["day"])
        bad = bool(classes.get("logic") or classes.get("host_error"))
        with self.sv.meta.session() as s:
            row = s.scalar(select(ParityCheck).where(ParityCheck.deployment_id == dep_id, ParityCheck.session == day))
            if row is None:
                row = ParityCheck(deployment_id=dep_id, session=day)
                s.add(row)
            row.checked_at, row.compared, row.matched = self._utc(), int(rep.get("decisions") or 0), \
                int(rep.get("matched") or 0)
            row.classes_json = json.dumps(classes)
            row.divergences_json = json.dumps([{**r, "session": sid} for r in rows[:50]], default=str)
            row.status = "diverged" if classes else "ok"
            from tradealpha.platform.events import outbox as ev
            ev.try_emit(s, "parity", deployment_id=dep_id, ref=day.isoformat(),
                        payload={"classes": classes, "decisions": int(rep.get("decisions") or 0),
                                 "matched": int(rep.get("matched") or 0), "status": row.status})
            if bad:
                raise_issue(s, "signal_parity", f"signal_parity:{dep_id}:{day}",
                            f"{name} {day}: hosted parity {classes}", "error", trade_date=day)
            s.commit()
        return {"deployment_id": dep_id, "session": day.isoformat(), "classes": classes}

    def host_execution_quality(self, dep_id: str) -> dict:
        """Execution quality of a hosted deployment's broker fills (OpenAlgo Analyzer sessions): adverse slippage of
        each fill vs the quote mid at send, in bps (positive = worse), mean / P95 / share worse, and rejections -
        the same report the P4 books give for replayed deployments."""
        import numpy as np
        sids = [x.id for x in self.host_sessions(dep_id) if x.mode in ("analyzer", "live")]
        if not sids:
            return {"fills_compared": 0, "rejections": 0, "model": "quote_mid"}
        with self.sv.meta.session() as s:
            rows = s.execute(select(LabOrder.side, LabFill.price, LabFill.bid, LabFill.ask)
                             .join(LabFill, LabFill.order_id == LabOrder.id)
                             .where(LabOrder.session_id.in_(sids), LabFill.bid.is_not(None),
                                    LabFill.ask.is_not(None))).all()
            rejected = s.scalar(select(func.count()).select_from(LabOrder).where(
                LabOrder.session_id.in_(sids), LabOrder.status.in_(("rejected", "blocked")))) or 0
        bps = [((px - (b + a) / 2) if side == "BUY" else ((b + a) / 2 - px)) / ((b + a) / 2) * 1e4
               for side, px, b, a in rows if b and a]
        if not bps:
            return {"fills_compared": 0, "rejections": rejected, "model": "quote_mid"}
        arr = np.asarray(bps, dtype=float)
        return {"fills_compared": len(arr), "mean_bps": round(float(arr.mean()), 2),
                "p95_bps": round(float(np.percentile(arr, 95)), 2),
                "worse_than_model_pct": round(float((arr > 0).mean() * 100), 1), "rejections": rejected,
                "model": "quote_mid"}

    # ------------------------------------------------------------------ pilot report (Phase 5)
    def pilot_report(self, dep_id: str) -> dict:
        """The live pilot of a hosted deployment on one page: stage history, the dry-run and live sessions (days,
        orders, fills, rejects, realised P&L net of the contract-note charges), execution quality vs the model,
        paper-stage vs live daily returns, broker reconciliation and parity record, guardrail events, stepped caps,
        demotions - the evidence to scale up, hold, or stop."""
        import numpy as np

        from tradealpha.platform.db.models import BrokerReconciliation
        with self.sv.meta.session() as s:
            r = s.get(Deployment, dep_id)
            if r is None:
                raise KeyError(dep_id)
            s.expunge(r)
            events = [{"at": e.at.isoformat() + "Z", "kind": e.kind, "actor": e.actor,
                       "detail": {k: v for k, v in json.loads(e.detail_json or "{}").items()
                                  if k in ("from", "to", "reason", "passed", "forced", "stage", "origin")}}
                      for e in s.scalars(select(DeploymentEvent).where(DeploymentEvent.deployment_id == dep_id)
                                         .order_by(DeploymentEvent.id))]
            recs = [{"day": x.day.isoformat(), "session": x.session_id, "status": x.status,
                     "differences": x.differences,
                     "charges": json.loads(x.report_json or "{}").get("charges"),
                     "warnings": len(json.loads(x.report_json or "{}").get("warnings") or [])}
                    for x in s.scalars(select(BrokerReconciliation).where(
                        BrokerReconciliation.deployment_id == dep_id).order_by(BrokerReconciliation.day))]
        if not is_hosted(r):
            raise SignalError("the pilot report covers hosted (IR) deployments", 409)
        by_mode: dict[str, dict] = {}
        for x in self.host_sessions(dep_id):
            if x.deployment_stage not in ("paper", "eligible_live", "live"):
                continue
            key = "paper" if x.mode == "analyzer" else x.mode
            agg = by_mode.setdefault(key, {"sessions": [], "days": set(), "orders": 0, "filled": 0, "rejected": 0,
                                           "blocked": 0, "fills": 0, "fees": 0.0, "realised_net": 0.0,
                                           "daily": {}, "guardrail_events": {}})
            agg["sessions"].append({"id": x.id, "status": x.status, "stage": x.deployment_stage,
                                    "kill_reason": x.kill_reason})
            with self.sv.meta.session() as s:
                for st_, n in s.execute(select(LabOrder.status, func.count()).where(LabOrder.session_id == x.id)
                                        .group_by(LabOrder.status)):
                    agg["orders"] += n
                    if st_ in ("filled", "rejected", "blocked"):
                        agg[st_] += n
                fl = s.execute(select(LabFill.bar_ts, LabFill.fees).join(LabOrder, LabOrder.id == LabFill.order_id)
                               .where(LabOrder.session_id == x.id)).all()
                agg["fills"] += len(fl)
                agg["fees"] += sum(f or 0.0 for _t, f in fl)
                mids = list(s.scalars(select(LabPaperMember.id).where(LabPaperMember.session_id == x.id)))
                agg["days"] |= {day_of(t) for (t,) in s.execute(select(LabDecision.bar_ts).where(
                    LabDecision.member_id.in_(mids), LabDecision.bars_sha256 != "-"))} if mids else set()
                days = sorted({day_of(t) for t, _f in fl})
                for g_, n in s.execute(select(LabGuardrailEvent.guardrail, func.count()).where(
                        LabGuardrailEvent.session_id == x.id).group_by(LabGuardrailEvent.guardrail)):
                    agg["guardrail_events"][g_] = agg["guardrail_events"].get(g_, 0) + n
            for d in days:
                pnl = self.sv.lab_paper._realised_on(x.id, d)
                agg["daily"][d.isoformat()] = round(agg["daily"].get(d.isoformat(), 0.0) + pnl, 2)
            agg["realised_net"] = round(sum(agg["daily"].values()), 2)
        out_modes = {}
        for k, agg in by_mode.items():
            vals = list(agg["daily"].values())
            out_modes[k] = {**{kk: v for kk, v in agg.items() if kk not in ("days",)}, "days": len(agg["days"]),
                            "fees": round(agg["fees"], 2),
                            "mean_daily_pnl": round(float(np.mean(vals)), 2) if vals else None,
                            "worst_day": min(vals) if vals else None,
                            "reject_rate_pct": round(100.0 * agg["rejected"] / agg["orders"], 2)
                            if agg["orders"] else None}
        live = out_modes.get("live") or {}
        paper = out_modes.get("paper") or {}
        info = self._live_info(r)
        bad_recs = sum(1 for x in recs if x["status"] == "diverged")
        verdict = "no live sessions yet"
        if live.get("days"):
            if r.stage != "live":
                verdict = "demoted / stopped - see the events"
            elif bad_recs or live.get("guardrail_events", {}).get("G-IN-3"):
                verdict = "hold: investigate the reconciliation / drawdown events"
            elif live["days"] >= int(self.live_config()["step_min_sessions"]):
                verdict = "eligible for the next cap step"
            else:
                verdict = f"collecting evidence ({live['days']} of {self.live_config()['step_min_sessions']} " \
                          "sessions at this step)"
        return {"deployment": {"id": r.id, "name": r.name, "strategy": r.strategy, "stage": r.stage,
                               "status": r.status, "paused_reason": r.paused_reason},
                "live": {"mode_now": self.live_mode_for(r) if r.stage == "live" else None,
                         "step": info.get("step"), "caps": self.step_caps(int(info.get("step") or 1)),
                         "steps": info.get("steps", []), "dry_run_sessions": info.get("dry_run_sessions")},
                "routes": out_modes,
                "comparison": {"paper_mean_daily_pnl": paper.get("mean_daily_pnl"),
                               "live_mean_daily_pnl": live.get("mean_daily_pnl"),
                               "live_minus_paper": round(live["mean_daily_pnl"] - paper["mean_daily_pnl"], 2)
                               if live.get("mean_daily_pnl") is not None and paper.get("mean_daily_pnl") is not None
                               else None},
                "execution_quality": self.host_execution_quality(dep_id),
                "reconciliation": {"days": recs, "diverged": bad_recs,
                                   "ok": sum(1 for x in recs if x["status"] == "ok")},
                "events": events, "verdict": verdict}

    # ------------------------------------------------------------------ promotion evidence
    def _host_members(self, dep_id: str, stage: str) -> tuple[list[str], list[int]]:
        sids = [x.id for x in self.host_sessions(dep_id, stage)]
        with self.sv.meta.session() as s:
            mids = list(s.scalars(select(LabPaperMember.id).where(LabPaperMember.session_id.in_(sids)))) if sids else []
        return sids, mids

    def _member_bar_seconds(self, mids) -> dict[int, int]:
        from tradealpha.platform.time.intervals import resolve
        with self.sv.meta.session() as s:
            return {m: resolve(iv).seconds for m, iv in s.execute(
                select(LabPaperMember.id, LabPaperSession.interval).join(
                    LabPaperSession, LabPaperSession.id == LabPaperMember.session_id)
                .where(LabPaperMember.id.in_(list(mids))))} if mids else {}

    def host_evidence(self, r, since: datetime) -> dict:
        sids, mids = self._host_members(r.id, r.stage)
        lo = int(since.replace(tzinfo=UTC).timestamp()) if since.tzinfo is None else int(since.timestamp())
        ev = {"sessions": 0, "logic": 0, "revisions": 0, "gate_pass_pct": None, "daily_returns": [],
              "slippage_ratio": None, "loss_stops": 0, "host_sessions": sids}
        if not mids:
            return ev
        bar_s = self._member_bar_seconds(mids)
        with self.sv.meta.session() as s:
            # a decision belongs to the stage when it was MADE in it (bar end), not when its bar started - a daily
            # bar starts at midnight, before a promotion later that day
            decs = [(t, sha) for m, t, sha in s.execute(select(LabDecision.member_id, LabDecision.bar_ts,
                                                               LabDecision.bars_sha256).where(
                LabDecision.member_id.in_(mids), LabDecision.bar_ts >= lo - 86_400)) if t + bar_s.get(m, 0) >= lo]
            days = sorted({datetime.fromtimestamp(t, IST).date() for t, sha in decs if sha != "-"})
            ev["sessions"] = len(days)
            reps = list(s.scalars(select(LabParityReport).where(LabParityReport.session_id.in_(sids),
                                                                LabParityReport.day >= since.date())))
            ev["logic"] = sum(int(p.divergences or 0) for p in reps)
            ev["revisions"] = sum(int(p.revisions or 0) for p in reps)
            stale = s.scalar(select(func.count()).select_from(LabGuardrailEvent).where(
                LabGuardrailEvent.session_id.in_(sids), LabGuardrailEvent.guardrail == "G-IN-5",
                LabGuardrailEvent.created_at >= since)) or 0
            ok = sum(1 for _t, sha in decs if sha != "-")
            tot = len(decs) + stale
            ev["gate_pass_pct"] = round(ok / tot * 100, 2) if tot else None
            ev["loss_stops"] = s.scalar(select(func.count()).select_from(LabGuardrailEvent).where(
                LabGuardrailEvent.session_id.in_(sids), LabGuardrailEvent.guardrail == "G-IN-2",
                LabGuardrailEvent.created_at >= since)) or 0
            # Analyzer slippage vs the model (ADR-001 slippage fraction of the session's ExecSpec)
            adverse, model = [], []
            for x in s.scalars(select(LabPaperSession).where(LabPaperSession.id.in_(sids),
                                                             LabPaperSession.mode == "analyzer")):
                frac = float(json.loads(x.exec_json)["spec"].get("slippage_value") or 0.0)
                for side, px, bid, ask in s.execute(
                        select(LabOrder.side, LabFill.price, LabFill.bid, LabFill.ask)
                        .join(LabFill, LabFill.order_id == LabOrder.id)
                        .where(LabOrder.session_id == x.id, LabFill.bar_ts >= lo, LabFill.bid.is_not(None),
                               LabFill.ask.is_not(None))):
                    mid = (bid + ask) / 2
                    if mid > 0:
                        adverse.append(((px - mid) if side == "BUY" else (mid - px)) / mid * 1e4)
                        model.append(frac * 1e4)
            caps = {x.id: x.cash for x in s.scalars(select(LabPaperSession).where(LabPaperSession.id.in_(sids)))}
        if adverse and model and sum(model) > 0:
            ev["slippage_ratio"] = round(max(sum(adverse) / len(adverse), 0.0) / (sum(model) / len(model)), 3)
        eq = sum(caps.values()) / max(1, len(caps))
        for d in days:
            pnl = sum(self.sv.lab_paper._realised_on(sid, d) for sid in sids)
            ev["daily_returns"].append(pnl / eq if eq else 0.0)
            eq += pnl
        return ev

    def ops_readiness(self) -> list[dict]:
        """Gate inputs from the hbt Lab's operations (flagfix1): drills and the T0 readiness sign-off."""
        from tradealpha.lifecycle.gates import promotion as pm
        since = (self.now() - timedelta(days=30)).astimezone(UTC).replace(tzinfo=None)
        with self.sv.meta.session() as s:
            latest = {}
            for d in s.scalars(select(LabDrill).where(LabDrill.created_at >= since).order_by(LabDrill.id)):
                latest[d.name] = bool(d.passed)
            signed = s.scalar(select(LabSignoff).order_by(LabSignoff.id.desc()).limit(1))
        missing = [n for n in DRILLS if latest.get(n) is not True]
        return [pm.crit("operational drills passed (30 days)", sorted(k for k, v in latest.items() if v),
                        list(DRILLS), not missing, f"missing / failed: {missing}" if missing else ""),
                pm.crit("readiness sign-off on record", signed.signed_by if signed else None, "required",
                        signed is not None, "" if signed else "Lab -> Readiness: evaluate and sign off")]

    # ------------------------------------------------------------------ cohorts -> deployments
    def deploy_cohort(self, cohort_id: int, body: dict, actor: str = "user") -> list[dict]:
        """A frozen research cohort (flagfix1, D-18) becomes draft deployments: one per member, pinned to the member's
        IR version (frozen with the cohort), with the universe / book of ``body``."""
        from tradealpha.platform.db.lab_models import LabCohort
        with self.sv.meta.session() as s:
            c = s.get(LabCohort, int(cohort_id))
            if c is None:
                raise KeyError(cohort_id)
            members = json.loads(c.members_json)
            refs = []
            for m in members:
                v = s.get(LabStrategyVersion, int(m["strategy_version_id"]))
                st_ = s.get(LabStrategy, v.strategy_id) if v else None
                if v is None or st_ is None:
                    raise SignalError(f"cohort member version {m['strategy_version_id']} not found", 409)
                refs.append((f"ir:{st_.name}@{v.version}", v.ir_sha256, m.get("params") or {}))
            cname = c.name
        out = []
        for ref, sha, params in refs:
            self.sv.backtests.freeze(ref, note=f"cohort {cname}")
            b = {**body, "strategy": ref, "params": {**params, **(body.get("params") or {})},
                 "name": f"{cname}: {ref[3:]}"[:100]}
            out.append(self.create(b, actor))
            with self.sv.meta.session() as s:
                s.add(DeploymentEvent(at=self._utc(), deployment_id=out[-1]["id"], kind="sourced", actor=actor,
                                      detail_json=json.dumps({"cohort": int(cohort_id), "name": cname,
                                                              "code_sha": sha})))
                s.commit()
        return out


def _host_version_of(sv, r) -> tuple[str, str, str]:
    """(strategy name, canonical IR text, IR sha) of a hosted deployment's pinned version."""
    name = base_name(r.strategy)[3:]
    with sv.meta.session() as s:
        v = s.scalar(select(LabStrategyVersion).join(LabStrategy, LabStrategy.id == LabStrategyVersion.strategy_id)
                     .where(LabStrategy.name == name, LabStrategyVersion.ir_sha256 == r.code_sha)
                     .order_by(LabStrategyVersion.version.desc()).limit(1))
        if v is None:
            raise SignalError(f"the IR version {r.code_sha[:12]} of {r.strategy} no longer exists", 409)
        return name, v.ir_json, v.ir_sha256


def day_of(t: int) -> date:
    return datetime.fromtimestamp(int(t), IST).date()
