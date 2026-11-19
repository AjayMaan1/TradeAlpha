# Data: backfill and daily-sync cost for the research universe

Target: 200 symbols, D × 10 y + 1m × 10 y (hybrid roll-up: 3m…1h are rolled up from 1m, so only 1m + D are downloaded). Historify runs at 80% of each broker's limits. Times are lower bounds from the published windows; real runs add latency and retries.

| Broker | 1m depth (y) | 1m years obtainable | Requests D | Requests 1m | Backfill time | Daily sync (1m + D) | Data conf. |
|---|---|---|---|---|---|---|---|
| Dhan (DhanHQ) | 5 | 5 | 200 | 4,200 | **18 min** | 2 min | L/M |
| Fyers (API v3) | 8 | 8 | 2,000 | 6,000 | **49 min** | 2 min | L/M |
| Zerodha (Kite Connect) | 10 | 10 | 400 | 12,200 | **1.5 h** | 3 min | H/L/M |
| Upstox (Upstox API v2/v3) | 4.8 | 4.8 | 200 | 11,400 | **3.0 h** | 0 min | M |
| Angel One (SmartAPI) | 8 | 8 | 400 | 19,600 | **4.0 h** | 3 min | L/M |
| ICICI Direct (Breeze) | 10 | 10 | 800 | 365,000 | **91.0 d** | 4 min | L/M |
| 5paisa (Xtream / OpenAPI) | ? | ? | ? | ? | ? | ? | not published / unknown |
| Alice Blue (ANT API) | ? | ? | ? | ? | ? | ? | not published / unknown |
| Flattrade (Noren API) | ? | ? | ? | ? | ? | ? | not published / unknown |
| Groww (Trade API) | ? | ? | ? | ? | ? | ? | not published / unknown |
| Kotak Securities (Neo API) | ? | ? | ? | ? | ? | ? | not published / unknown |
| Shoonya by Finvasia (Noren API) | ? | ? | ? | ? | ? | ? | not published / unknown |

Depth matters more than speed: a broker with 4–5 years of 1m cannot serve a 10-year intraday backtest at any speed. Older history then has to come from a purchased/vendor dataset or another broker.
