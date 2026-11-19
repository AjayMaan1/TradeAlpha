# Provider profile: Angel One (SmartAPI) via OpenAlgo

Status: **defaults implemented, live verification pending (plan task 1.1 / 1.2, gate G1).**

Historify talks to OpenAlgo, which talks to Angel One. The limits below are Angel One's published SmartAPI limits
at the time of writing. They are encoded in `src/tradealpha/platform/ratelimit/profiles.py` (`ANGELONE`) and can be overridden.

## Limits used

| Limit | Value | Where enforced |
|---|---|---|
| History (`getCandleData`) rate | 3 req/s, 180 req/min, 5,000 req/h | `MultiWindowRateLimiter` (all three windows at once) |
| Budget share | 80% by default (2.4 req/s, 144/min, 4,000/h) | `HISTORIFY_RATE_BUDGET` (0-1] |
| Max calendar days per request | 1m 30 · 3m 60 · 5m 100 · 10m 100 · 15m 200 · 30m 200 · 1h 400 · D 2000 | `OpenAlgoProvider.history()` chunking |
| Quotes rate | 10 req/s (x budget) | quotes limiter (verify) |
| Weekly / monthly | not provided by OpenAlgo | rejected; derived from D by the BarStore |

## Behaviour

| Situation | Classification | What Historify does |
|---|---|---|
| Timeout, connection error, HTTP 5xx | TRANSIENT | retry (3x, exponential backoff + jitter) |
| HTTP 429 / "exceeding access rate" | RATE_LIMITED | pause all callers (`Retry-After` or 1 s), retry |
| "Invalid Token / please login", invalid API key, 401/403 | AUTH | **circuit breaker**: no further broker calls for 60 s (`HISTORIFY_AUTH_COOLDOWN`) or until *Settings -> Test connection* succeeds |
| Empty range (weekend, holiday, before listing) | NO_DATA | success with 0 bars |
| Invalid symbol / interval / range | PERMANENT | fail immediately, no retries |

Angel One sessions expire daily and OpenAlgo then needs a TOTP re-login. After re-login, click **Test connection**
in Settings to close the breaker at once.

## Daily timestamp convention

Brokers stamp daily bars differently (00:00 IST, 00:00 UTC, 09:15 IST). Historify v2 stores the **IST calendar date
of the epoch**, which gives the same trading date for all three conventions, so the answer does not affect
correctness. It still needs to be confirmed (gate G1):

```bash
export OPENALGO_HOST=http://127.0.0.1:5000 OPENALGO_API_KEY=...        # your instance, read-only calls
python review_harness/capture_golden.py --out historify/tests/support/fixtures/golden
# or, from inside the app:
flask --app run.py historify selftest
```

## Capacity planning (100% / 80% budget)

| Job | Requests | Time |
|---|---|---|
| 500 symbols, D + 5m + 1m incremental (daily maintenance) | 1,500 | ~8 / 10 min |
| 500 symbols, D, 10 years | 1,000 | ~6 / 7 min |
| 500 symbols, 1m, 1 year | 6,500 | ~1.1 / 1.3 h (hourly cap) |
| 500 symbols, 1m, 5 years | 30,500 | ~6.0 / 7.3 h (hourly cap) |

## Open verification items (need a live OpenAlgo + Angel One session)

- [ ] Rate limits and the HTTP status/message OpenAlgo returns when they are exceeded
- [ ] Max-days behaviour through OpenAlgo (does OpenAlgo's Angel adapter chunk internally?)
- [ ] Error shape for an expired session
- [ ] Daily timestamp convention (`capture_golden.py` -> `summary.json: daily_convention`)
- [ ] How far back intraday history is available
- [ ] Quotes limit; WebSocket quote mode for Phase 4
