"""
Incremental twins of the indicators live signals (P4) update bar by bar. Each must equal the batch function to 1e-9
on any stream (parity property test) - the backtest and the live signal compute the same number.
"""
from __future__ import annotations

import math
from collections import deque


class EMA:
    def __init__(self, n: int, alpha: float | None = None):
        self.n, self.a = n, (2.0 / (n + 1) if alpha is None else alpha)
        self.buf: list[float] = []
        self.value: float | None = None

    def update(self, x: float) -> float:
        if self.value is None:
            self.buf.append(float(x))
            if len(self.buf) == self.n:
                self.value = sum(self.buf) / self.n
            return self.value if self.value is not None else math.nan
        self.value = self.a * float(x) + (1 - self.a) * self.value
        return self.value


class RMA(EMA):
    def __init__(self, n: int):
        super().__init__(n, 1.0 / n)


class WMA:
    def __init__(self, n: int):
        self.n = n
        self.buf: deque[float] = deque(maxlen=n)
        self.den = n * (n + 1) / 2

    def update(self, x: float) -> float:
        self.buf.append(float(x))
        if len(self.buf) < self.n:
            return math.nan
        return sum((i + 1) * v for i, v in enumerate(self.buf)) / self.den


class ATR:
    def __init__(self, n: int = 14):
        self.rma, self.prev = RMA(n), None

    def update(self, high: float, low: float, close: float) -> float:
        tr = high - low if self.prev is None else max(high - low, abs(high - self.prev), abs(low - self.prev))
        self.prev = close
        return self.rma.update(tr)


class RSI:
    def __init__(self, n: int = 14):
        self.g, self.l, self.prev = RMA(n), RMA(n), None

    def update(self, close: float) -> float:
        if self.prev is None:
            self.prev = close
            return math.nan
        d = close - self.prev
        self.prev = close
        ag, al = self.g.update(max(d, 0.0)), self.l.update(max(-d, 0.0))
        if math.isnan(ag):
            return math.nan
        if al == 0:
            return 50.0 if ag == 0 else 100.0
        return 100 - 100 / (1 + ag / al)


class VWAP:
    def __init__(self):
        self.day, self.pv, self.v = None, 0.0, 0.0

    def update(self, day: int, high: float, low: float, close: float, volume: float) -> float:
        if day != self.day:
            self.day, self.pv, self.v = day, 0.0, 0.0
        tp = (high + low + close) / 3
        self.pv += tp * volume
        self.v += volume
        return self.pv / self.v if self.v > 0 else tp


class Supertrend:
    def __init__(self, n: int = 10, mult: float = 3.0):
        self.atr, self.mult = ATR(n), mult
        self.fub = self.flb = self.dir = self.prev_close = None

    def update(self, high: float, low: float, close: float) -> tuple[float, float]:
        a = self.atr.update(high, low, close)
        if math.isnan(a):
            self.prev_close = close
            return math.nan, math.nan
        hl2 = (high + low) / 2
        ub, lb = hl2 + self.mult * a, hl2 - self.mult * a
        if self.fub is None:
            self.fub, self.flb, self.dir = ub, lb, 1.0
        else:
            pc = self.prev_close
            self.fub = ub if (ub < self.fub or pc > self.fub) else self.fub
            self.flb = lb if (lb > self.flb or pc < self.flb) else self.flb
            if self.dir == 1:
                self.dir = -1.0 if close < self.flb else 1.0
            else:
                self.dir = 1.0 if close > self.fub else -1.0
        self.prev_close = close
        return (self.flb if self.dir == 1 else self.fub), self.dir
