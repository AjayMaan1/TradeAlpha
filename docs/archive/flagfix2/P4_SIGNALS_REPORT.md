# P4 · Track T to Paper Signals: Implementation Report

| | |
|---|---|
| **Phase** | P4 of `PROJECT_ROADMAP_6_PHASES.md`; design `P4_SIGNALS_HLD.md` v1.1 (decisions R1–R12) |
| **Status** | **Built, self-reviewed, tested.** Paper operation (20–40 sessions in OpenAlgo Analyzer mode) runs in calendar time on your machine. |
| **Code** | `historify/app/lab/live/` (15 modules, ≈ 3,400 lines), engine live mode (`app/lab/sim/engine.py`), Alembic `0008_signals` (10 tables), API `app/web/signals_api.py`, page `/signals`, CLI `flask historify signals …` |
| **Tests** | 37 new tests (29 for P4 + 8 code-review regressions) and 2 UI tests, listed in §6. Full suite: **669 passed, 1 skipped, 9 xfailed** (non-UI; 632 before P4) · **43 UI passed** (41 before). |
| **User guide** | `docs/signals.md` |
| **Also in this report** | §9: the post-implementation code review. The full list is in `CODE_REVIEW_FINDINGS.md`. |

---

## 1. Summary

- **A live signal is a replay of the backtest (R1).** At every decision point, the deployment's P3 backtest is
  replayed up to *now* on a **LivePortal**, using the same strategy code, signal pass and engine. The replay's
  pending state becomes the signal:
  - an entry whose fill bar has not printed yet becomes ENTER;
  - a time exit, square-off, max-hold or signal exit due on the next bar becomes EXIT.
- **Parity is proven, not assumed.** The tests walk a frozen clock through two months of EOD decisions and through
  a full 15m trading day. In both, the persisted signals and the shadow-book fills **equal a P3 backtest of the same
  bars, fill for fill**. The nightly parity check also comes back clean: every decision point is replayed on the
  final data, and all signals match.
- **A deployment moves through stages.** `draft → signals → shadow → paper → eligible_live`, with a promotion gate
  built on P3 evidence (walk-forward, DSR, PBO, PF at 2× slippage, trade count, live-parity latency) and on the
  live history (sessions, parity, gate pass rate, the shadow bootstrap band, Analyzer slippage, approval).
- **Two paper books (R2).**
  - The *shadow* book holds the model's fills on live bars.
  - The *Analyzer* book holds real OpenAlgo sandbox orders: SL-M stop and LIMIT target after the entry fills, with
    one-cancels-other and reconciliation. Each Analyzer fill is compared with its shadow fill, which gives the
    execution-quality report (mean / P95 slippage in bps).
- **Safety.**
  - An Analyzer-mode check runs before every order.
  - Kill switch, limits and a daily loss stop.
  - A market-hours guard and idempotent client ids.
  - An API token on order-capable routes (R5) and a DNS-rebinding host guard.
  - An append-only audit log.
  - No live orders in P4.
- **Delivery (R4).** Signals reach the page through Server-Sent Events (5 s polling as fallback) and optional
  browser notifications. They also go to an HMAC-signed webhook (retries after 2 s / 10 s / 60 s / 5 min, then a
  dead letter) and, optionally, to Telegram.
- **Performance.** All three scenarios are within budget (§5):
  - 200-symbol EOD replay over a 3-year book: 6.5 s;
  - 100-symbol 15m in-process replay: 8.2 s;
  - 200-symbol 15m replay on the worker pool (R10): 6.1 s.

## 2. What was built (by HLD work package)

![P4 as built](diagrams/p4_as_built.png)

| WP | Built | Where |
|---|---|---|
| W1 Triggers | Decision points per signal timeframe: bar close + latency (15m), close + 15 min (EOD), `schedule:HH:MM`, and exit clocks (MIS square-off, strategy time exits). Each has a validity window. Runs are unique per (deployment, trigger, time). After downtime, the last 24 h are caught up; anything outside its window is recorded as `missed` (R8). **UniverseFollow:** one incremental download per (decision time, interval) for the union of universes, with no duplicates while one is pending. | `triggers.py`, `service.py` (`_plan`, `_refresh_data`, `_data_ready`) |
| W2 LivePortal, gate, replay, differ | The P3 `DataPortal` interface on the live store:<br>• bars in snapshot dtypes;<br>• the reference tables from the same function the snapshot bundle uses;<br>• every frame cut at `available_at ≤ now`;<br>• W / M never show a running period;<br>• a day-anchored lookback;<br>• a warm-up cache (days before today are converted once per session).<br>**Engine live mode:** pending entries sized at now, open positions with their pending exit, no forced end-of-data exits.<br>**Completeness gate:** policies `block` / `skip_instrument` / `warn`, one retry after 30 s, repair download, Data Health issue; declared reference series always block.<br>**Differ:** ENTER / EXIT with raw stop / target, validity, feature values (`reason`) and a data fingerprint. | `portal.py`, `gate.py`, `replay.py`, `sim/engine.py`, `sim/types.py` |
| W3 Signals store and delivery | Signals are persisted exactly once (unique key). Delivery goes to the SSE hub, the HMAC webhook (stable delivery id across retries) and Telegram. Daily digest (JSON + Markdown) and dashboard summary. | `service.py`, `delivery.py`, `reads.py` |
| W4 Shadow book, OrderBridge | **Shadow book:** the replay's model fills since the deployment entered shadow.<br>**OrderBridge:** OpenAlgo `analyzer` / `placeorder` / `cancelorder` / `cancelallorder` / `closeposition` / `orderbook` / `tradebook`. Order limiter: 2/s · 30/min · 200/h, never above the broker profile (R12). Book limiter from the profile.<br>**Paper orders:** placed at the intended fill bar (EOD entries at the next 09:15). MARKET / LIMIT entries; SL-M + LIMIT protectives with one-cancels-other; EXIT cancels protectives and working entries; working limit entries are cancelled when their validity ends; a double-fill alert when both protectives fill.<br>**Execution-quality report.** | `bridge.py`, `paper.py`, `reads.py` |
| W5 Risk, kill switch, token, audit | **Limits:** max positions, notional per position (qty reduced to fit), total notional (deployment and global), entries per day (deployment and global), daily loss stop (no new entries for the day), event-day blocks.<br>**Other controls:** paused deployments still exit. The kill switch cancels at the broker (optional flatten).<br>**Token:** `instance/signals_token` (0600), embedded only in the Signals page, and only on a loopback host.<br>**Audit log** of orders, cancels, promotions, kill switch, risk blocks and rejected tokens / hosts. | `risk.py`, `service.py`, `signals_api.py`, `pages.py` |
| W6 Parity, promotion, versions | **Parity:** nightly (22:45 IST, plus catch-up of the last 4 days). Every decision point of the session is replayed on the final data and compared 1:1 (qty ± 1, stop / target ± 1 tick). Classes: `late_data`, `data_revision` (fingerprint), `timing`, `logic`, with cascades inside one instrument. A `logic` divergence pauses the deployment and raises an error issue.<br>**Promotion gate:** with forced overrides (reason required, audited).<br>**Versions:** edits that change decisions create a new version and retire the old one. If the code changes after deployment, the deployment pauses. | `parity.py`, `promotion.py`, `promote.py`, `service.py` |
| W7 UI | `/signals`:<br>• **Today:** signals with reasons, ack / dismiss, decision-point timeline, kill switch, live indicator, notifications;<br>• **Deployments:** list, detail, positions, promotion panel with the gate table, history, new deployment form;<br>• **Journal:** both books, notes, slippage;<br>• **Parity:** checks, divergences, check now;<br>• **Paper orders;**<br>• **Settings:** webhook, secrets, Telegram, global limits, parity time.<br>Also: dashboard attention items, command palette entries, nav entry. Alpine CSP, no `innerHTML`. | `templates_v2/signals.html`, `static/js/v2/pages/signals.js`, `dashboard.js`, `core.js` |
| W8 Strategy work | The platform work is done. Running E9–E16 / E18–E21 in live-parity mode and deploying E9 (`mtf_pullback`) and E11 (`rsi2`) (R11) needs your data: see §8. | — |

### Schema (Alembic `0008_signals`)
`deployments`, `deployment_events`, `deployment_runs` (unique deployment+trigger+time), `signals` (unique
deployment+instrument+decision+action), `book_fills` (unique per book and dedupe key), `paper_orders` (unique client id),
`signal_deliveries` (unique signal+channel), `parity_checks` (unique deployment+session), `journal_notes`, `audit_log`.
The models equal the migration (`test_migrations_match_the_models`).

## 3. Decisions as implemented

| # | Decision | Implementation |
|---|---|---|
| R1 | Replay at each trigger + differ | `replay.replay()` → `replay.diff()`; the engine gained a live mode (the backtest path is unchanged; the differential test stays green) |
| R2 | Shadow + Analyzer books | `book_fills` with `book = shadow | analyzer`; Analyzer fills carry the model price |
| R3 | 15m intraday for ≤ 200 symbols | Validation: intraday universes ≤ 200 symbols, signal bar ≥ 15m (≥ 5m for ≤ 50 symbols) |
| R4 | UI + HMAC webhook (+ Telegram) | `delivery.py`; signals blocked by risk are shown on the page but never pushed out |
| R5 | API token | Header `X-Historify-Token` on promotion to paper / eligible_live, resume of paper deployments, kill switch and order cancel |
| R6 | Promotion thresholds §11.2 | `promotion.DEFAULTS`, overridable in `signals.promotion` |
| R7 | Separate books + global limits | Per-deployment books; `global_max_notional`, `global_max_orders_per_day` |
| R8 | Mark missed | Runs and signals outside their validity window become `missed`, never traded late |
| R9 | Independent books | Each deployment replays and books alone |
| R10 | In-process EOD, pool for intraday > 100 | `pool_threshold` (100): the per-instrument signal pass runs in 4 spawn workers; the result is identical (tested) |
| R11 | First deployments E9 / E11 | Both strategies run in the service (E11 is covered by the EOD parity test); deployment on your data: §8 |
| R12 | Angel One free limits, 80 % share | Downloads go through the shared engine (history limiter). Orders: P4 windows ∩ profile. Books: profile windows. Reconcile every 15 s only while orders are open. |

## 4. How the design changed during implementation (and why)

| Change | Reason |
|---|---|
| Decision readiness is **polled** (no pending tasks that cover today for the run's instruments × interval), not a job-finished event bus | Simpler and restart-safe. It also covers jobs that other origins enqueued. |
| Runs replay at the **decision time**, not at the wall-clock time they execute | Deterministic: a late or retried run produces exactly what an on-time run would have, so re-runs are no-ops |
| EXIT signals only for open-based exits; stops / targets travel on the ENTER signal | The broker's SL-M / LIMIT orders (or the trader) act on them. Emitting EXIT for a stop fill the model already took would double-exit. |
| An EXIT whose ENTER was never acted on (blocked / missed) is recorded as `missed` | The trader never held that position |
| The ENTER quantity applies the participation cap to the median volume of the last 5 execution bars | The fill bar's volume is unknown at decision time. Without the estimate, the signal asked the broker for up to 3× what the model fills (found while reviewing the journal). |
| Gate on the strategy's **declared** reference series only; reference daily bar = the previous session's at intraday points | The default references (NIFTY, VIX) are not always used; intraday strategies read the last daily close |
| Exit clocks and manual runs never block | Closing risk must always stay possible |
| The W / M rule "a running period is not a bar" moved into the base DataPortal | The last decision of a backtest that ended mid-week saw a half week as a weekly bar. Live and backtest now agree (CR-06). |
| Weekly CNC checkpoints were not built | A 3-year CNC replay of 200 symbols takes 6.5 s, so the checkpoint is not needed yet (§7) |

### Screens (synthetic data, mock OpenAlgo in Analyzer mode)

| | |
|---|---|
| ![Today](diagrams/signals_today.png) | ![Deployment](diagrams/signals_deployment.png) |
| Today: signals with their reasons, decision-point timeline, kill switch | Deployment: positions, promotion gate (paper → eligible_live), history |
| ![Journal](diagrams/signals_journal.png) | ![Paper orders](diagrams/signals_orders.png) |
| Journal: shadow and Analyzer books, slippage in bps | Paper orders: entries, SL-M stops, LIMIT targets (OCO) |

## 5. Performance (`benchmarks/p4_signals.py`, 4 vCPU)

| Scenario | Measured | Budget (HLD §13.2) |
|---|---|---|
| A · EOD replay, 200 symbols, 3-year CNC book, RSI(2) | **6.5 s** | ≤ 15 s ✅ |
| B · 15m replay in-process, 100 symbols, 30 sessions of 1m warm-up, ORB/CPR (second trigger of the day; first: 7.9 s) | **8.2 s** | ≤ 10 s ✅ |
| C · 15m replay, 200 symbols, worker pool (4), identical to in-process | **6.1 s** (in-process cold: 12.2 s) | ≤ 10 s ✅ |

End-to-end latency adds the data refresh: ≈ 60 s finality plus about 0.4 s per symbol at the free-tier history rate.
For 200 symbols that is roughly 2.4 min, as in HLD §13.1. The runs record this as `latency_s`; the status page
shows p50 / p95.

## 6. Tests

| Test | What it proves |
|---|---|
| `unit/test_signals_engine.py` (6) | **Engine live mode agrees with the full backtest up to now on 480 random cuts:**<br>• random markets with gaps, circuit locks, zero volume and splits;<br>• every sizing mode, stops / targets / limits, time exits, max-hold, signal exits;<br>• latency, participation, CNC / MIS.<br>Every fill up to now is identical. Every decided intent is accounted for. Entries the backtest fills later are pending now. Open positions (in adjusted quantity) are identical. `live=False` is the backtest. |
| `integration/test_signals.py::test_eod_time_travel_equals_the_p3_backtest` | 40 EOD decision points (Jan–Feb 2025, RSI(2)):<br>• live ENTER signals = P3 filled entries + the last pending ones;<br>• live EXITs match P3 signal / max-hold exits;<br>• **shadow fills = P3 fills, fill for fill**;<br>• signals carry feature values and a fingerprint. |
| `…::test_intraday_15m_day_equals_the_p3_backtest_and_parity_is_clean` | A full 15m day (25 bar points + exit clocks, MIS):<br>• live entries = P3 entries;<br>• time-exit EXITs = P3 time exits;<br>• shadow fills = P3 fills, flat at the end;<br>• nightly parity `ok`, matched = compared;<br>• digest and summary. |
| `…::test_time_travel_with_the_mock_broker_download_engine` | The real path: the run waits for the signals job (origin `signals`, priority interactive) from the mock broker, passes the gate on final bars, the first decision also refreshes D, and end-of-day parity is clean |
| `…reruns_and_restarts…`, `…catch_up…` | Duplicate triggers, re-executing every run and a restart (new service, empty caches) add nothing. A catch-up after downtime marks late points `missed`. |
| `…completeness_gate_policies[block/skip/warn]`, `…reference_series_always_block` | Gate outcomes, retry, Data Health issue, skipped instruments |
| `…risk_limits_kill_switch_and_daily_loss_stop`, `…paused_deployments…` | Every limit and the loss stop. The kill switch blocks all ordering. Paused deployments still exit. |
| `…webhook_hmac_retries_and_dead_letter`, `…telegram_adapter` | Signature verifies, the delivery id is stable across retries, the backoff schedule holds, and the dead letter raises an issue |
| `…deployment_validation_versions_and_code_changes`, `…promotion_gate_uses_p3_evidence…` | Frozen-only deployment, validation, versioning. A code change pauses the deployment. Gate criteria are computed from P3 groups / runs and the live history, and forced promotions are audited. |
| `…parity_classifies_data_revisions_and_logic_divergences` | A bar revision is classified `data_revision` (with cascades) and does not pause. A same-data difference is classified `logic`, pauses the deployment and raises an error issue. |
| `…paper_orders_analyzer_only_oco_and_kill_switch`, `…wait_for_the_session_and_handle_rejections`, `…rate_limits…` | Mock OpenAlgo in Analyzer mode:<br>• MARKET entry → SL-M + LIMIT;<br>• target fill cancels the stop (OCO);<br>• Analyzer OFF → refused, paused, **zero live orders**;<br>• kill switch → `cancelallorder`;<br>• EOD orders wait for 09:15;<br>• RMS rejection;<br>• execution-quality report. |
| `…api_deployments_token_protection_and_page`, `…sse…`, `…cli…` | 403 without or with a wrong token. A form post is never accepted. The token is only on the Signals page and only on loopback hosts. Rejected tokens are audited. SSE pushes events. All CLI commands work. |
| `…worker_pool_signal_pass_equals_in_process` | R10: the pooled signal pass gives the same fills, pending entries, positions and captured features |
| `ui/test_signals_page.py` + `/signals` in `test_pages.py` | Create, promote (forced with a reason), a live signal arrives over SSE, acknowledge, kill switch (token header), parity, journal, settings. No console errors or CSP violations. |

## 7. Known limitations (by design or deferred)

| Item | Status |
|---|---|
| Weekly checkpoints for CNC replays | Deferred (6.5 s for 200 × 3 y). Revisit when a book is older than about 5 years. |
| `MODIFY_STOP` / `MODIFY_TARGET` signals | Not needed: P3 stops and targets are fixed at entry. The action names are reserved. |
| Re-basing broker SL orders on corporate-action ex-dates | Not automatic. The adjusted view keeps the model right. Cancel and re-place manually on an ex-date (rare in a 15m / swing horizon). |
| Clock-skew check against the OpenAlgo server | OpenAlgo's API does not expose server time. The host clock drives everything (use NTP). |
| Warm-up cache in pool workers | Workers rebuild frames on each trigger. Still within budget at 200 symbols. |
| Live-vs-backtest difference inherent to replay | If a position's intrabar stop and a new entry fall on the same next bar, the backtest allows the entry (exits before entries) but live cannot know. The parity check classifies it. This is rare and only happens while a position is open. |
| Bars revised after a decision inside the same session | The day's warm-up cache does not see intraday repairs of earlier days. Parity classifies them as `data_revision`. |

## 8. Operating it (your machine)

1. Verify OpenAlgo: run `flask historify openalgo-probe`, then
   `--place-test-order NSE:SBIN --fill-test` (Analyzer ON only).
2. For E11 (`rsi2`) and E9 (`mtf_pullback`):
   - run walk-forward + sweep + a backtest with `latency_s=60` for intraday;
   - `bt freeze`;
   - `signals deploy`;
   - `signals promote --walk-forward … --run … --sweep …`.
3. Run 5 sessions in `signals`, then promote to `shadow` (10 sessions), then to `paper` (20–40 sessions). Watch
   these on the page:
   - Parity;
   - Journal → execution quality;
   - the digest (`signals digest`).
4. In Settings → Signals: set the webhook URL and secret, Telegram, and the global limits.

## 9. Post-implementation code review (summary)

The review covered the whole codebase with P4 in place: download engine / planner / jobs, scheduler, store, integrity,
quotes, refdata, lab / backtests, web / API security. There are **11 findings**: 1 high, 4 medium, 6 low.
**10 are fixed**: 7 with regression tests (`tests/integration/test_review_fixes.py`) and 3 small Low fixes by
inspection. 1 (CR-11) is documented. The full list,
with evidence and impact, is in `CODE_REVIEW_FINDINGS.md`. Headlines:

- **High, fixed.** DNS rebinding let any web page drive the local API. In particular, it could repoint the OpenAlgo
  host so that Historify sent the stored API key (which can place orders) to the attacker. Now:
  - unknown Host names are refused app-wide;
  - moving to a new public host requires the key again.
- **Medium, fixed.**
  - Cross-site form posts (CSRF) to multipart and no-body endpoints.
  - `enqueue_download(dedupe=True)` dropped a whole request when one series was busy.
  - A job paused while its last task ran hung in `running` forever after resume, which blocked its schedule.
  - A restarted sweep / walk-forward left stale "queued" members.
