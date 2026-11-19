"""
An in-process OpenAlgo account for drills (trading-engine Phase 4, L4) - the ``provider`` of an ``OpenAlgoRoute`` /
``LiveRoute`` (``.post(endpoint, payload)``), with the real endpoint names and response shapes, so the drills run
the production routes, not a stand-in:

    analyzer, placeorder, orderbook, tradebook, positionbook, cancelorder, modifyorder, quotes, margin, funds

Faults: ``down`` = "refused" (nothing reaches the account: every call fails as a transient error) | "ack_lost" (an
order reaches the account, the acknowledgement does not) | None. Market orders fill at ``price_fn(symbol)``.
"""
from __future__ import annotations

import itertools
import threading

from tradealpha.marketdata.api import ErrorKind, ProviderError


class FakeOpenAlgo:
    def __init__(self, price_fn, analyze: bool = True, cash: float = 10_000_000.0):
        self.price_fn = price_fn
        self.analyze = analyze
        self.cash = cash
        self.down: str | None = None
        self.orders: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.calls: dict[str, int] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ book
    def _fill(self, o: dict, px: float) -> None:
        o["order_status"], o["average_price"], o["filled"] = "complete", px, o["quantity"]
        self.trades.append({"orderid": o["orderid"], "symbol": o["symbol"], "exchange": o["exchange"],
                            "action": o["action"], "quantity": o["quantity"], "average_price": px,
                            "product": o["product"], "strategy": o["strategy"]})

    def positions(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.trades:
            out[t["symbol"]] = out.get(t["symbol"], 0) + (t["quantity"] if t["action"] == "BUY" else -t["quantity"])
        return out

    def match(self) -> None:
        """Resting stop orders trigger on the current price."""
        for o in self.orders.values():
            if o["order_status"] != "open" or o["pricetype"] not in ("SL", "SL-M"):
                continue
            px = float(self.price_fn(o["symbol"]))
            trig = o["trigger_price"]
            if (o["action"] == "BUY" and px >= trig) or (o["action"] == "SELL" and px <= trig):
                self._fill(o, px if o["pricetype"] == "SL-M" else o["price"])

    # ------------------------------------------------------------------ the API
    def post(self, endpoint: str, payload: dict) -> dict:
        with self._lock:
            self.calls[endpoint] = self.calls.get(endpoint, 0) + 1
            if self.down == "refused":
                raise ProviderError(ErrorKind.TRANSIENT, "connection refused")
            if endpoint == "analyzer":
                return {"status": "success", "data": {"analyze_mode": self.analyze}}
            if endpoint == "placeorder":
                oid = f"{'SB' if self.analyze else 'LV'}{next(self._ids):08d}"
                o = {"orderid": oid, "symbol": payload["symbol"], "exchange": payload["exchange"],
                     "action": payload["action"], "quantity": int(payload["quantity"]),
                     "pricetype": payload.get("pricetype", "MARKET"), "product": payload.get("product", "MIS"),
                     "price": float(payload.get("price") or 0), "trigger_price": float(payload.get("trigger_price")
                                                                                     or 0),
                     "strategy": payload.get("strategy"), "order_status": "open", "average_price": 0.0,
                     "filled": 0}
                self.orders[oid] = o
                if o["pricetype"] == "MARKET":
                    self._fill(o, float(self.price_fn(o["symbol"])))
                if self.down == "ack_lost":
                    raise ProviderError(ErrorKind.TRANSIENT, "read timeout (the order may have been placed)")
                return {"status": "success", "orderid": oid}
            if self.down == "ack_lost":
                raise ProviderError(ErrorKind.TRANSIENT, "read timeout")
            self.match()
            if endpoint == "orderbook":
                return {"status": "success", "data": {"orders": [dict(o) for o in self.orders.values()]}}
            if endpoint == "tradebook":
                return {"status": "success", "data": [dict(t) for t in self.trades]}
            if endpoint == "positionbook":
                return {"status": "success", "data": [{"symbol": k, "quantity": str(v)}
                                                      for k, v in self.positions().items()]}
            if endpoint == "cancelorder":
                o = self.orders.get(str(payload.get("orderid")))
                if o is None or o["order_status"] != "open":
                    raise ProviderError(ErrorKind.PERMANENT, "order not open")
                o["order_status"] = "cancelled"
                return {"status": "success"}
            if endpoint == "modifyorder":
                o = self.orders.get(str(payload.get("orderid")))
                if o is None or o["order_status"] != "open":
                    raise ProviderError(ErrorKind.PERMANENT, "order not open")
                for k in ("price", "trigger_price"):
                    if payload.get(k) is not None:
                        o[k] = float(payload[k])
                return {"status": "success"}
            if endpoint == "quotes":
                px = float(self.price_fn(payload["symbol"]))
                return {"status": "success", "data": {"bid": round(px - 0.05, 2), "ask": round(px + 0.05, 2),
                                                      "ltp": px}}
            if endpoint == "margin":
                tot = sum(float(self.price_fn(p["symbol"])) * int(float(p["quantity"])) *
                          (0.2 if p.get("product", "MIS") == "MIS" else 1.0) for p in payload.get("positions", []))
                return {"status": "success", "data": {"total_margin_required": round(tot, 2)}}
            if endpoint == "funds":
                return {"status": "success", "data": {"availablecash": f"{self.cash:.2f}"}}
            raise ProviderError(ErrorKind.PERMANENT, f"unknown endpoint {endpoint}")
