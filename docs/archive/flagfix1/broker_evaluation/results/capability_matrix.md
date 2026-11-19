# API capabilities relevant to Historify Lab

Cell = value [confidence]. V = verified in the official SDK source; H/M/L = assistant knowledge; ? = unknown.

| Capability | Zerodha | Angel One | Upstox | Dhan | Fyers | ICICI Direct | Groww | Kotak Securities | Flattrade | Shoonya by Finvasia | 5paisa | Alice Blue |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Native intervals | 1m, 3m, 5m, 10m, 15m, 30m, 1h, D [H] | 1m, 3m, 5m, 10m, 15m, 30m, 1h, D [H] | 1m..300m (any), 1h..5h, D, W, M [V] | 1m, 5m, 15m, 25m, 1h, D [V] | 1m, 2m, 3m, 5m, 10m, 15m, 20m, 30m, 1h, 2h, 4h, D [V] | 1s, 1m, 5m, 30m, D [V] | 1m, 2m, 3m, 5m, 10m, 15m, 30m, 1h, 4h, D, W, M [V] | ? [?] | 1m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, 4h, D [V] | 1m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, 4h, D [V] | 1m, 3m, 5m, 10m, 15m, 30m, 1h, D [V] | 1m, D [V] |
| Open interest | yes [V] | no [?] | yes [M] | yes [V] | yes [M] | yes [?] | ? [?] | ? [?] | ? [?] | ? [?] | ? [?] | ? [?] |
| Continuous futures | daily candles (continuous=1) [V] | no [?] | no [?] | daily (expiry_code 0/1/2/3) [V] | yes (cont_flag=1) [V] | no [?] | no [?] | ? [?] | ? [?] | ? [?] | ? [?] | ? [?] |
| Expired F&O history | no [M] | no [?] | API: expired futures + options contracts and their candles (ExpiredInstrumentApi) [V] | API: expired options on a rolling basis (ATM-relative strikes) [V] | no [?] | yes: history by expiry/strike/right parameters (verify for expired contracts) [M] | API: historical expiries + contracts (backtesting API) [V] | no [?] | no [?] | no [?] | no [?] | no [?] |
| Corporate actions API | no [?] | no [?] | API by ISIN (fundamentals/{isin}/corporate-actions) [V] | no [?] | no [?] | no [?] | no [?] | ? [?] | ? [?] | ? [?] | ? [?] | ? [?] |
| Charges / contract-note API | virtual contract note (get_virtual_contract_note) [V] | estimateCharges [V] | get_brokerage (pre-trade), P&L charges breakdown [V] | no (verify) [?] | charges_history (post-trade) [V] | no (verify) [?] | no (verify) [?] | ? [?] | ? [?] | ? [?] | ? [?] | ? [?] |
| Holidays / timings API | no [?] | no [?] | market holidays + timings API [V] | no [?] | no [?] | no [?] | no [?] | ? [?] | ? [?] | ? [?] | ? [?] | ? [?] |
| Margin API | order_margins / basket_order_margins [V] | yes (verify endpoint) [?] | yes (verify endpoint) [?] | margin_calculator / margin_calculator_multi [V] | yes (verify endpoint) [?] | margin_calculator [V] | yes (verify) [?] | ? [?] | ? [?] | ? [?] | ? [?] | ? [?] |
| Broker sandbox | no [?] | no [?] | yes (verify scope) [M] | yes (DhanHQ sandbox; OpenAlgo lists 'Dhan (Sandbox)') - verify [M] | no [?] | no [?] | no [?] | ? [?] | ? [?] | ? [?] | ? [?] | ? [?] |
| Session / auth | daily login; access token valid until ~06:00 next day [M] | TOTP login (automatable); session expires daily [H] | OAuth; token expires daily (~03:30) [M] | access token with multi-day validity - verify [L] | daily token; TOTP login automatable [M] | daily session via browser login [M] | API key + TOTP (verify) [?] | TOTP + MPIN (verify) [?] | daily login + TOTP [?] | daily login + TOTP [?] | TOTP (verify) [?] | daily session (verify) [?] |
