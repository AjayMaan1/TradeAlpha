# Monthly cost by trading scenario (broker-dependent part)

Statutory schedule in force on 2025-01-15 (same for every broker). All amounts include GST. Confidence of each broker's tariff in brackets (V verified in SDK · H/M/L assistant knowledge · ? unknown). **Verify before deciding.** Regenerate after editing `broker_tariffs.toml`.

## S1 - Equity intraday (MIS): 10 round trips/day, Rs 2 lakh per trade

210 round trips/month (420 executed orders), turnover ₹8.40 cr/month. **Statutory + exchange charges (all brokers): ₹14,803/month** (₹70.49 per round trip, schedule 2024-10-01).

| Broker | Brokerage/order | Brokerage + GST /month | DP /month | Subscriptions /month | **Broker-dependent total** | Total incl. statutory | Per round trip | Tariff conf. |
|---|---|---|---|---|---|---|---|---|
| Flattrade (Noren API) | ₹0.00 | ₹0 | ₹0 | ₹0 | **₹0** | ₹14,803 | ₹0.00 | M |
| Kotak Securities (Neo API) | ₹0.00 | ₹0 | ₹0 | ₹0 | **₹0** | ₹14,803 | ₹0.00 | L |
| Shoonya by Finvasia (Noren API) | ₹0.00 | ₹0 | ₹0 | ₹0 | **₹0** | ₹14,803 | ₹0.00 | L |
| 5paisa (Xtream / OpenAPI) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹24,715 | ₹47.20 | M |
| Alice Blue (ANT API) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹24,715 | ₹47.20 | L |
| Angel One (SmartAPI) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹24,715 | ₹47.20 | M |
| Fyers (API v3) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹24,715 | ₹47.20 | L |
| ICICI Direct (Breeze) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹24,715 | ₹47.20 | L |
| Upstox (Upstox API v2/v3) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹24,715 | ₹47.20 | L |
| Dhan (DhanHQ) | ₹20.00 | ₹9,912 | ₹0 | ₹589 | **₹10,501** | ₹25,304 | ₹50.00 | H |
| Groww (Trade API) | ₹20.00 | ₹9,912 | ₹0 | ₹589 | **₹10,501** | ₹25,304 | ₹50.00 | M |
| Zerodha (Kite Connect) | ₹20.00 | ₹9,912 | ₹0 | ₹590 | **₹10,502** | ₹25,305 | ₹50.01 | H |

## S2 - Index options buying: 10 round trips/day, 2 lots NIFTY (150 units) at Rs 150 premium

210 round trips/month (420 executed orders), turnover ₹0.94 cr/month. **Statutory + exchange charges (all brokers): ₹8,784/month** (₹41.83 per round trip, schedule 2024-10-01).

| Broker | Brokerage/order | Brokerage + GST /month | DP /month | Subscriptions /month | **Broker-dependent total** | Total incl. statutory | Per round trip | Tariff conf. |
|---|---|---|---|---|---|---|---|---|
| Flattrade (Noren API) | ₹0.00 | ₹0 | ₹0 | ₹0 | **₹0** | ₹8,784 | ₹0.00 | M |
| Shoonya by Finvasia (Noren API) | ₹0.00 | ₹0 | ₹0 | ₹0 | **₹0** | ₹8,784 | ₹0.00 | L |
| Kotak Securities (Neo API) | ₹10.00 | ₹4,956 | ₹0 | ₹0 | **₹4,956** | ₹13,740 | ₹23.60 | L |
| 5paisa (Xtream / OpenAPI) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹18,696 | ₹47.20 | M |
| Alice Blue (ANT API) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹18,696 | ₹47.20 | L |
| Angel One (SmartAPI) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹18,696 | ₹47.20 | M |
| Fyers (API v3) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹18,696 | ₹47.20 | L |
| ICICI Direct (Breeze) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹18,696 | ₹47.20 | L |
| Upstox (Upstox API v2/v3) | ₹20.00 | ₹9,912 | ₹0 | ₹0 | **₹9,912** | ₹18,696 | ₹47.20 | L |
| Dhan (DhanHQ) | ₹20.00 | ₹9,912 | ₹0 | ₹589 | **₹10,501** | ₹19,285 | ₹50.00 | H |
| Groww (Trade API) | ₹20.00 | ₹9,912 | ₹0 | ₹589 | **₹10,501** | ₹19,285 | ₹50.00 | M |
| Zerodha (Kite Connect) | ₹20.00 | ₹9,912 | ₹0 | ₹590 | **₹10,502** | ₹19,286 | ₹50.01 | H |

## S3 - Index futures: 4 round trips/day, 1 lot NIFTY (75 units) at 24,000

84 round trips/month (168 executed orders), turnover ₹30.24 cr/month. **Statutory + exchange charges (all brokers): ₹39,794/month** (₹473.74 per round trip, schedule 2024-10-01).

| Broker | Brokerage/order | Brokerage + GST /month | DP /month | Subscriptions /month | **Broker-dependent total** | Total incl. statutory | Per round trip | Tariff conf. |
|---|---|---|---|---|---|---|---|---|
| Flattrade (Noren API) | ₹0.00 | ₹0 | ₹0 | ₹0 | **₹0** | ₹39,794 | ₹0.00 | M |
| Shoonya by Finvasia (Noren API) | ₹0.00 | ₹0 | ₹0 | ₹0 | **₹0** | ₹39,794 | ₹0.00 | L |
| Kotak Securities (Neo API) | ₹10.00 | ₹1,982 | ₹0 | ₹0 | **₹1,982** | ₹41,776 | ₹23.60 | L |
| 5paisa (Xtream / OpenAPI) | ₹20.00 | ₹3,965 | ₹0 | ₹0 | **₹3,965** | ₹43,759 | ₹47.20 | M |
| Alice Blue (ANT API) | ₹20.00 | ₹3,965 | ₹0 | ₹0 | **₹3,965** | ₹43,759 | ₹47.20 | L |
| Angel One (SmartAPI) | ₹20.00 | ₹3,965 | ₹0 | ₹0 | **₹3,965** | ₹43,759 | ₹47.20 | M |
| Fyers (API v3) | ₹20.00 | ₹3,965 | ₹0 | ₹0 | **₹3,965** | ₹43,759 | ₹47.20 | L |
| ICICI Direct (Breeze) | ₹20.00 | ₹3,965 | ₹0 | ₹0 | **₹3,965** | ₹43,759 | ₹47.20 | L |
| Upstox (Upstox API v2/v3) | ₹20.00 | ₹3,965 | ₹0 | ₹0 | **₹3,965** | ₹43,759 | ₹47.20 | L |
| Dhan (DhanHQ) | ₹20.00 | ₹3,965 | ₹0 | ₹589 | **₹4,554** | ₹44,348 | ₹54.21 | H |
| Groww (Trade API) | ₹20.00 | ₹3,965 | ₹0 | ₹589 | **₹4,554** | ₹44,348 | ₹54.21 | M |
| Zerodha (Kite Connect) | ₹20.00 | ₹3,965 | ₹0 | ₹590 | **₹4,555** | ₹44,349 | ₹54.22 | H |

## S4 - Swing / positional delivery (CNC): 30 round trips/month, Rs 1 lakh each

30 round trips/month (60 executed orders), turnover ₹0.60 cr/month. **Statutory + exchange charges (all brokers): ₹6,667/month** (₹222.25 per round trip, schedule 2024-10-01).

| Broker | Brokerage/order | Brokerage + GST /month | DP /month | Subscriptions /month | **Broker-dependent total** | Total incl. statutory | Per round trip | Tariff conf. |
|---|---|---|---|---|---|---|---|---|
| Flattrade (Noren API) | ₹0.00 | ₹0 | ₹0 | ₹0 | **₹0** | ₹6,667 | ₹0.00 | M |
| Shoonya by Finvasia (Noren API) | ₹0.00 | ₹0 | ₹0 | ₹0 | **₹0** | ₹6,667 | ₹0.00 | L |
| Alice Blue (ANT API) | ₹0.00 | ₹0 | ₹531 | ₹0 | **₹531** | ₹7,198 | ₹17.70 | L |
| Kotak Securities (Neo API) | ₹0.00 | ₹0 | ₹708 | ₹0 | **₹708** | ₹7,375 | ₹23.60 | L |
| Dhan (DhanHQ) | ₹0.00 | ₹0 | ₹442 | ₹589 | **₹1,031** | ₹7,699 | ₹34.38 | H |
| Zerodha (Kite Connect) | ₹0.00 | ₹0 | ₹478 | ₹590 | **₹1,068** | ₹7,735 | ₹35.60 | H |
| Fyers (API v3) | ₹20.00 | ₹1,416 | ₹425 | ₹0 | **₹1,841** | ₹8,508 | ₹61.36 | L |
| 5paisa (Xtream / OpenAPI) | ₹20.00 | ₹1,416 | ₹442 | ₹0 | **₹1,858** | ₹8,526 | ₹61.95 | M |
| Upstox (Upstox API v2/v3) | ₹20.00 | ₹1,416 | ₹655 | ₹0 | **₹2,071** | ₹8,738 | ₹69.03 | L |
| Angel One (SmartAPI) | ₹20.00 | ₹1,416 | ₹708 | ₹0 | **₹2,124** | ₹8,791 | ₹70.80 | M |
| Groww (Trade API) | ₹20.00 | ₹1,416 | ₹478 | ₹589 | **₹2,483** | ₹9,150 | ₹82.76 | M |
| ICICI Direct (Breeze) | ₹275.00 | ₹19,470 | ₹708 | ₹0 | **₹20,178** | ₹26,845 | ₹672.60 | L |
