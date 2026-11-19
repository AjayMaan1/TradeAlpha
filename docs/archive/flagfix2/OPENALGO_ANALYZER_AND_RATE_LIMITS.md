# OpenAlgo Analyzer endpoints and order rate limits (P4 design basis)

| | |
|---|---|
| **For** | P4 signal service and paper trading (`P4_SIGNALS_HLD.md` §9, §13) |
| **Design basis** | **Angel One SmartAPI standard limits.** SmartAPI has no paid tier: these are the limits every free SmartAPI app gets. Historify uses an 80 % share of them. OpenAlgo's own server limits and the SEBI retail-algo threshold also apply. |
| **Confidence** | Taken from OpenAlgo's public docs, its sample `.env`, and Angel One's published rate-limit page as of my knowledge cut-off. Both projects change: **run `flask historify openalgo-probe` on your machine** (§6) to confirm. The 🟡 items are what the probe and you must verify. |

---

## 1. How OpenAlgo's API works (all endpoints)

- **Transport:** `POST http://<openalgo-host>/api/v1/<endpoint>` with a JSON body that always contains `"apikey"`. This
  is the same convention Historify already uses for history and quotes (`app/core/provider/openalgo.py`).
- **Success:** `{"status": "success", ...}`.
- **Error:** `{"status": "error", "message": "..."}` with an HTTP 4xx/5xx code.
- **Rate-limit hits:** HTTP **429**, sometimes with `Retry-After`. Historify's provider already backs off on 429.

## 2. Analyzer (sandbox) mode

| Aspect | Behaviour |
|---|---|
| What it is | A switch in OpenAlgo (dashboard toggle or API) that **stops orders from reaching the broker**. Recent versions add a **sandbox engine**: virtual capital (default ₹1 crore), simulated fills, positions, P&L, and MIS square-off at configured times (NSE/BSE default 15:15). |
| Fills in the sandbox | MARKET at the current LTP (fetched from the broker's quote API); LIMIT when the LTP crosses the limit; SL / SL-M when the trigger is hit. |
| Books in Analyzer mode | `orderbook`, `tradebook`, `positionbook` and `funds` return **sandbox** state while the mode is on, not your real account. |
| Older OpenAlgo versions 🟡 | Analyzer only **validated and logged** requests and returned an order id, with no simulated fills or books. Historify then still runs: the **shadow book** (P3 model fills) is the paper record and the Analyzer is only an order-path rehearsal. `openalgo-probe --fill-test` tells which version you have. |
| Broker budget used | Order calls: **none** (nothing reaches Angel One). Quote calls: **yes**, the sandbox polls LTP for pending orders. This is the main interaction with the free-tier limits (§4.3). |

### 2.1 Endpoints the P4 OrderBridge uses

| Endpoint | Purpose in P4 | Request body (besides `apikey`) | Typical response |
|---|---|---|---|
| `analyzer` | **Mode check before every order** | — | `{"status":"success","data":{"analyze_mode":true,"mode":"analyze","total_logs":12}}` |
| `analyzer/toggle` | Not used by Historify (you switch modes in OpenAlgo). Listed for completeness. | `{"mode": true}` | as above |
| `placeorder` | Entries, exits, stop orders | `strategy, symbol, exchange, action (BUY/SELL), pricetype (MARKET/LIMIT/SL/SL-M), product (MIS/CNC/NRML), quantity, price, trigger_price, disclosed_quantity` (numbers as strings) | `{"status":"success","orderid":"…","mode":"analyze"}` (`mode` appears in Analyzer mode) |
| `placesmartorder` | Not in P4 (position-size orders: the differ already computes the quantity) | as placeorder + `position_size` | — |
| `modifyorder` | Move a stop / target (for example after an ex-date re-base) | `strategy, symbol, action, exchange, orderid, product, pricetype, price, quantity, disclosed_quantity, trigger_price` | `{"status":"success","orderid":"…"}` |
| `cancelorder` | Cancel the other leg of a bracket; kill switch | `strategy, orderid` | `{"status":"success","orderid":"…"}` |
| `cancelallorder` | Kill switch (all open orders of the strategy tag) | `strategy` | list of cancelled / failed ids |
| `closeposition` | Kill switch / daily loss stop (flatten the strategy's positions) | `strategy` | `{"status":"success","message":"…"}` |
| `orderstatus` | Reconcile one order | `strategy, orderid` | `{"status":"success","data":{"orderid","symbol","action","quantity","price","average_price","order_status (open/complete/cancelled/rejected)","timestamp",…}}` |
| `openposition` | Position of one symbol for a strategy | `strategy, symbol, exchange, product` | `{"status":"success","quantity":"…"}` |
| `orderbook` | Reconciliation sweep (every 15 s in market hours) | — | `{"data":{"orders":[…],"statistics":{…}}}` |
| `tradebook` | Fill prices for execution-quality reports | — | `{"data":[{"orderid","average_price","quantity","timestamp",…}]}` |
| `positionbook` | End-of-day check | — | `{"data":[…]}` |
| `funds` | Paper capital check | — | `{"data":{"availablecash","collateral","m2mrealized","m2munrealized","utiliseddebits"}}` |
| `quotes` | LTP for the probe's far-from-market limit price; already used by the QuoteService | `symbol, exchange` | `{"data":{"ltp","bid","ask","open","high","low","prev_close","volume"}}` |
| `ping` | Connectivity and broker name | — | `{"data":{"broker":"…","message":"pong"}}` (🟡 may not exist in older versions) |

**Conventions Historify will use:**
- `strategy` = `hfy-<deployment id>`, so orders are grouped per deployment in OpenAlgo's UI and in `cancelallorder` /
  `closeposition`.
- The client-side idempotency key is `hfy-<signal_id>-<n>`. OpenAlgo has no client order id field (🟡), so Historify
  stores its key next to the returned `orderid` and checks the order book before retrying a place call. This avoids
  a double order after a timeout.

---

## 3. Rate limits

### 3.1 Angel One SmartAPI: standard limits (free, every SmartAPI app) 🟡 verify
| API (Angel One name) | Per second | Per minute | Per hour | Historify use |
|---|---|---|---|---|
| getCandleData (history) | **3** | **180** | **5,000** | downloads, follows, UniverseFollow (already in the `angelone` profile) |
| getLtpData / getMarketData (quotes) | **10** | 500 | 5,000 | QuoteService; OpenAlgo sandbox LTP polling |
| placeOrder / modifyOrder / cancelOrder | 20 (published) | 500 | 1,000 | live orders only (P6b); never used in Analyzer mode |
| getOrderBook / getTradeBook / getPosition / getHolding | **1** | — | — | live reconciliation only (P6b); in Analyzer mode these are served by OpenAlgo |
| getRMS (funds) | 2 | — | — | as above |
| login / generateTokens | 1 | — | — | OpenAlgo's session handling, not Historify |

### 3.2 OpenAlgo server-side limits (defaults in its sample `.env`; yours may differ) 🟡
| Setting | Default | Applies to |
|---|---|---|
| `API_RATE_LIMIT` | 10 per second | general API endpoints (history, quotes, books, analyzer …) |
| `ORDER_RATE_LIMIT` | 10 per second | placeorder, modifyorder, cancelorder … |
| `SMART_ORDER_RATE_LIMIT` | 2 per second | placesmartorder |
| `WEBHOOK_RATE_LIMIT` / `STRATEGY_RATE_LIMIT` | 100 / 200 per minute | OpenAlgo's own webhook and strategy endpoints (not used by Historify) |
| `SMART_ORDER_DELAY` | 0.5 s | delay between legs of multi-leg smart orders |

### 3.3 Regulatory ceiling (SEBI retail algo framework) 🟡
- Retail API orders **above 10 orders per second per exchange** count as a registered "algo" (broker approval, algo
  id). Below that threshold the order flow is treated like normal API trading.
- Brokers are also rolling out **static-IP whitelisting for API order placement**. Check Angel One's current
  requirement and date before P6b. It does **not** affect Analyzer mode, because no order reaches Angel One.

### 3.4 Effective limits and Historify's share
Effective limit = min(Angel One, OpenAlgo, SEBI). Historify takes 80 % (`budget_share`) of it, leaving headroom for
OpenAlgo's UI, the sandbox engine and your manual use.

| Class | Effective limit | Historify 80 % | Where it is enforced |
|---|---|---|---|
| History | 3/s · 180/min · 5,000/h | **2.4/s · 144/min · 4,000/h** | `ProviderProfile.history_windows` (existing) |
| Quotes | 10/s | 8/s → split: QuoteService ≤ 5/s, **≥ 3/s reserved for the OpenAlgo sandbox** while paper mode is on | `quote_windows` + the P4 reservation |
| Orders | min(20 Angel, 10 OpenAlgo, 10 SEBI) = **10/s** · 500/min · 1,000/h | **P4 default: 2/s · 30/min · 200/h**, plus a per-deployment max orders per day | new `order_windows` (added to the profile now) + OrderBridge limiter |
| Books | 1/s | **1 call per 15 s** in market hours (sweep), one at 15:35 | new `book_windows` (added now) + reconciliation schedule |

P4's order defaults are far below every ceiling on purpose. A 200-symbol 15m intraday deployment with a limit of 5
positions sends at most about 20–40 orders per day, including stops and cancels.

---

## 4. P4 load against the free-tier limits

### 4.1 History (the binding constraint)
| Load (market hours) | Calls per hour | Share of 4,000/h |
|---|---|---|
| UniverseFollow, 200 symbols × 15m (1m or 15m fetch) | 800 | 20 % |
| UniverseFollow, 50 symbols × 15m | 200 | 5 % |
| Scanner 09:31 refresh (100 symbols × 1m), once | 100 in that hour | 2.5 % |
| Chart follows (≤ 5 series, 1m) | ≤ 300 | ≤ 7.5 % |
| **Headroom for scheduled downloads / backfills** | ≥ 2,000 | ≥ 50 % (backfills are deprioritised automatically in market hours) |

Per bar close the limit is per **minute**: 200 calls at 144/min take **≈ 83 s**. The 15m signal for the last symbol
is therefore ready about 2.4 minutes after the bar close. This is why P4 v1 uses 15m (not 5m) for 200 symbols.

### 4.2 Orders and books (paper mode)
None of these calls reach Angel One. They count only against OpenAlgo's 10/s limits and Historify's own 2/s · 30/min
defaults.

### 4.3 Quotes (the hidden shared budget)
- **The shared budget:** the OpenAlgo sandbox prices MARKET fills and checks pending LIMIT/SL orders with LTP calls to
  Angel One's quote API. Those share the 10/s quote limit with Historify's QuoteService (dashboard / watchlist
  quotes).
- **What P4 does:**
  - caps Historify's quote use at 5/s while any deployment is in `paper` mode;
  - keeps pending paper orders few (brackets only for open positions).
- **Symptom if it is too tight:** 429s on quotes in the sandbox logs. The fix is to lower the QuoteService share
  further.

---

## 5. What changed in the code now
- `app/core/provider/profiles.py`: the Angel One profile now documents and carries `order_windows`
  (10/s · 500/min · 1,000/h, i.e. Angel's published 20/s capped at the SEBI 10/s threshold) and `book_windows`
  (1/s). The P4 OrderBridge will apply them with its lower defaults.
- `app/core/provider/openalgo_probe.py` + CLI `flask historify openalgo-probe`: the verification tool (§6).
- `tests/mock_provider/server.py`: the mock OpenAlgo now has Analyzer / sandbox semantics:
  - analyzer status and toggle;
  - place / status / cancel;
  - order, trade and position books and funds;
  - 10/s order and 1/s book limits;
  - a `live_orders` counter that tests assert stays 0.

  These tests (`tests/integration/test_openalgo_probe.py`, 6 tests) are the base of the P4 contract tests.

---

## 6. Verify on your machine (10 minutes)
1. Check the connection in read-only mode (about 10 s, one request per second):
   `flask --app run.py historify openalgo-probe`
   This checks ping, analyzer mode, funds, the three books and quotes, and writes a **redacted** report to
   `instance/probe/openalgo_<time>.json`. The API key and account values are not included; only field names and
   types are.
2. Switch **Analyzer mode ON** in OpenAlgo, then run:
   `flask --app run.py historify openalgo-probe --place-test-order NSE:SBIN --fill-test`
   - **Test order:** one 1-share LIMIT BUY at half the LTP (it cannot fill), its status, then a cancel.
   - **Fill test:** one 1-share MARKET buy and sell in the sandbox. It shows whether your OpenAlgo version simulates
     fills.
   - Both steps are **refused automatically** if Analyzer mode is off.
3. Send me the report file. From it I will:
   - pin the response fields in the P4 contract tests;
   - confirm that `orderstatus` / `tradebook` work in Analyzer mode;
   - confirm or adjust the order defaults.
4. Also tell me:
   - your OpenAlgo version;
   - any changes in its `.env` rate limits;
   - whether Angel One has asked you for a static IP for API orders;
   - Angel One's MIS auto square-off time on your account.
