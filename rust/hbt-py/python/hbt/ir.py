"""
Strategy IR v1 - Python builder (design §11.1). Builds the JSON document that the Rust compiler checks; nothing here
evaluates market data.

    from hbt.ir import col, params, Int, Float, ema, atr, sma, cross_above, cross_below, rule, strategy
    from hbt.ir import enter_long, exit_long, bracket, pct, price, risk_pct

    P = params(fast=Int(9, 3, 50), slow=Int(21, 10, 200), k=Float(2.0, 0.5, 5.0, step=0.25))
    f, s = ema(col.close, P.fast), ema(col.close, P.slow)
    st = strategy("EMA cross", interval="5m", product="MIS", params=P, constraints=[P.fast < P.slow], rules=[
        rule(cross_below(f, s), exit_long()),
        rule(cross_above(f, s) & (col.close.tf("D") > sma(col.close.tf("D"), 50)),
             enter_long(size=risk_pct(0.005), bracket=bracket(stop=price(col.close - P.k * atr(14)), target=pct(0.02)))),
    ])
    prog = st.compile()          # raises IRError(code, pointer, hint)

Operators overload + - * / < <= > >= & | ~ (and unary -). ``==`` / ``!=`` are NOT overloaded (Python truthiness
would make mistakes silent): use ``eq(a, b)`` / ``ne(a, b)``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

# ------------------------------------------------------------------------------------------------ canonical form
MAX_ABS_NUMBER = 1e15


def _num(x) -> str:
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    x = float(x)
    if x.is_integer() and abs(x) <= MAX_ABS_NUMBER:
        return str(int(x))
    return format(Decimal(repr(x)), "f")


def _canon(v, top: bool) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return _num(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list | tuple):
        return "[" + ",".join(_canon(x, False) for x in v) + "]"
    if isinstance(v, dict):
        keys = sorted(k for k in v if not (top and k in ("meta", "description")))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _canon(v[k], False) for k in keys) + "}"
    raise TypeError(f"not JSON: {type(v).__name__}")


def canonical(doc) -> str:
    """Canonical JSON of an IR document (identical to the Rust compiler's: hbt-ir/src/canon.rs)."""
    if isinstance(doc, str):
        doc = json.loads(doc)
    if isinstance(doc, Strategy):
        doc = doc.to_dict()
    return _canon(doc, True)


def ir_sha256(doc) -> str:
    return hashlib.sha256(canonical(doc).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ errors
class IRError(ValueError):
    """A compile error: ``code`` (E-...), ``pointer`` (JSON Pointer into the document), ``hint``."""

    def __init__(self, message: str, code: str = "E-SCHEMA", pointer: str = "", hint: str | None = None):
        super().__init__(message)
        self.code, self.pointer, self.hint = code, pointer, hint


def _lift(e) -> IRError:
    msg, code, pointer, hint = (list(e.args) + [None] * 4)[:4]
    return IRError(str(msg), code or "E-SCHEMA", pointer or "", hint)


# ------------------------------------------------------------------------------------------------ expressions
def _j(x):
    if isinstance(x, Expr):
        return x.to_json()
    if isinstance(x, bool | int | float):
        return x
    if isinstance(x, str):
        return x                                   # "HH:MM"
    raise TypeError(f"cannot use {type(x).__name__} in an IR expression")


class Expr:
    def to_json(self):
        raise NotImplementedError

    # arithmetic
    def __add__(self, o): return fn("add", self, o)                 # noqa: E704
    def __radd__(self, o): return fn("add", o, self)                # noqa: E704
    def __sub__(self, o): return fn("sub", self, o)                 # noqa: E704
    def __rsub__(self, o): return fn("sub", o, self)                # noqa: E704
    def __mul__(self, o): return fn("mul", self, o)                 # noqa: E704
    def __rmul__(self, o): return fn("mul", o, self)                # noqa: E704
    def __truediv__(self, o): return fn("div", self, o)             # noqa: E704
    def __rtruediv__(self, o): return fn("div", o, self)            # noqa: E704
    def __neg__(self): return fn("neg", self)                       # noqa: E704
    def __abs__(self): return fn("abs", self)                       # noqa: E704
    # comparison
    def __gt__(self, o): return fn("gt", self, o)                   # noqa: E704
    def __ge__(self, o): return fn("ge", self, o)                   # noqa: E704
    def __lt__(self, o): return fn("lt", self, o)                   # noqa: E704
    def __le__(self, o): return fn("le", self, o)                   # noqa: E704
    # logic
    def __and__(self, o): return fn("and", self, o)                 # noqa: E704
    def __rand__(self, o): return fn("and", o, self)                # noqa: E704
    def __or__(self, o): return fn("or", self, o)                   # noqa: E704
    def __ror__(self, o): return fn("or", o, self)                  # noqa: E704
    def __invert__(self): return fn("not", self)                    # noqa: E704

    def __bool__(self):
        raise TypeError("an IR expression has no truth value while building; use & | ~ and where()")

    __hash__ = object.__hash__

    def __repr__(self):
        return f"<ir {json.dumps(self.to_json())}>"


@dataclass(eq=False, repr=False)
class Col(Expr):
    name: str
    timeframe: str | None = None

    def tf(self, code: str) -> Col:
        return Col(self.name, code)

    def to_json(self):
        return {"col": self.name} if self.timeframe is None else {"col": self.name, "tf": self.timeframe}


class _Cols:
    open = Col("open")
    high = Col("high")
    low = Col("low")
    close = Col("close")
    volume = Col("volume")
    typical = Col("typical")
    hl2 = Col("hl2")

    def __call__(self, name: str, tf: str | None = None) -> Col:
        return Col(name, tf)


col = _Cols()


@dataclass(eq=False, repr=False)
class Sig(Expr):
    name: str

    def to_json(self):
        return {"sig": self.name}


def sig(name: str) -> Sig:
    return Sig(name)


@dataclass(eq=False, repr=False)
class ParamRef(Expr):
    name: str

    def to_json(self):
        return {"param": self.name}


OUTPUTS = {"bbands": ("upper", "mid", "lower"), "macd": ("macd", "signal", "hist"), "stoch": ("k", "d"),
           "adx": ("adx", "plus_di", "minus_di"), "keltner": ("upper", "mid", "lower"), "donchian": ("upper", "lower")}


@dataclass(eq=False, repr=False)
class Fn(Expr):
    name: str
    args: list
    out: str | None = None
    timeframe: str | None = None

    def __getattr__(self, item):
        outs = OUTPUTS.get(object.__getattribute__(self, "name"), ())
        if item in outs:
            return Fn(self.name, self.args, item, self.timeframe)
        raise AttributeError(item)

    def to_json(self):
        d = {"fn": self.name}
        if self.args:
            d["args"] = [_j(a) for a in self.args]
        if self.out:
            d["out"] = self.out
        if self.timeframe:
            d["tf"] = self.timeframe
        return d


def fn(name: str, *args, out: str | None = None, tf: str | None = None) -> Fn:
    return Fn(name, list(args), out, tf)


def time_(hhmm: str) -> str:
    """A time of day literal ("14:55") - compare it with minute_of_day()."""
    return hhmm


# Operator catalogue v1 (must equal hbt_core.operators(); tests/lab/test_ir_compiler.py checks it)
OPERATORS = (
    "add sub mul div min max pow neg abs log exp sqrt clip where gt ge lt le eq ne between and or not "
    "shift diff pct_change rolling_mean rolling_sum rolling_std rolling_min rolling_max rolling_quantile expanding_max "
    "expanding_min cumsum bars_since rising falling cross_above cross_below sma ema wma dema tema hma rsi roc mom stddev "
    "zscore linreg_slope bbands macd atr natr cci willr mfi stoch adx keltner donchian obv vwap minute_of_day "
    "bar_in_session is_first_bar is_last_bar session_open_price session_high session_low opening_range_high "
    "opening_range_low prior_day_open prior_day_high prior_day_low prior_day_close day_of_week position_qty "
    "entry_price bars_in_trade unrealised_pct highest_since_entry lowest_since_entry").split()
OHLC_IMPLICIT = {"atr", "natr", "cci", "willr", "mfi", "stoch", "adx", "keltner", "donchian", "obv"}


def _make(name):
    if name in OHLC_IMPLICIT:
        def f(*args, tf: str | None = None, out: str | None = None):
            return Fn(name, list(args), out, tf)
    else:
        def f(*args, out: str | None = None):
            return Fn(name, list(args), out)
    f.__name__ = name
    f.__doc__ = f"IR operator '{name}' (design §10 / Appendix C)."
    return f


_g = globals()
for _name in OPERATORS:
    _py = {"and": "and_", "or": "or_", "not": "not_", "min": "min_", "max": "max_", "abs": "abs_",
           "pow": "pow_"}.get(_name, _name)
    _g[_py] = _make(_name)


# ------------------------------------------------------------------------------------------------ params
@dataclass
class Int:
    default: int
    min: int | None = None
    max: int | None = None
    step: int | None = None

    def spec(self):
        return {"type": "int", "default": self.default, **self._range()}

    def _range(self):
        return {k: v for k, v in (("min", self.min), ("max", self.max), ("step", self.step)) if v is not None}


@dataclass
class Float(Int):
    default: float = 0.0

    def spec(self):
        return {"type": "float", "default": self.default, **self._range()}


@dataclass
class Bool:
    default: bool

    def spec(self):
        return {"type": "bool", "default": bool(self.default)}


@dataclass
class Choice:
    default: float
    choices: list = field(default_factory=list)

    def spec(self):
        return {"type": "choice", "default": self.default, "choices": list(self.choices)}


class Params:
    def __init__(self, **specs):
        self._specs = specs
        for k in specs:
            setattr(self, k, ParamRef(k))

    def to_dict(self) -> dict:
        return {k: v.spec() for k, v in self._specs.items()}

    def __iter__(self):
        return iter(self._specs)


def params(**specs) -> Params:
    return Params(**specs)


# ------------------------------------------------------------------------------------------------ intents
def _level(x):
    if x is None:
        return None
    if isinstance(x, dict):
        return {k: _j(v) for k, v in x.items()}
    raise TypeError("a bracket level is pct(x) or price(expression) (or None for none)")


def pct(x) -> dict:
    return {"pct": x}


def price(expr) -> dict:
    return {"price": expr}


def risk_pct(r) -> dict:
    return {"risk_pct": r}


def pct_equity(x) -> dict:
    return {"pct_equity": x}


def fixed_qty(q) -> dict:
    return {"fixed_qty": q}


def fixed_value(rupees) -> dict:
    return {"fixed_value": rupees}


def all_in() -> dict:
    return {"all_in": True}


_UNSET = object()


def bracket(stop=_UNSET, target=_UNSET, trail_pct=_UNSET, time_stop_bars=_UNSET) -> dict:
    """Unset = the OrderPlan default; None = explicitly none."""
    b = {}
    if stop is not _UNSET:
        b["stop"] = _level(stop)
    if target is not _UNSET:
        b["target"] = _level(target)
    if trail_pct is not _UNSET:
        b["trail_pct"] = None if trail_pct is None else _j(trail_pct)
    if time_stop_bars is not _UNSET:
        b["time_stop_bars"] = None if time_stop_bars is None else _j(time_stop_bars)
    return b


def market() -> dict:
    return {"type": "market"}


def limit(price=None, offset_pct=None) -> dict:
    return _order("limit", price, offset_pct)


def stop(price=None, offset_pct=None) -> dict:
    return _order("stop", price, offset_pct)


def _order(kind, price_, offset_pct):
    o = {"type": kind}
    if price_ is not None:
        o["price"] = _j(price_)
    if offset_pct is not None:
        o["offset_pct"] = _j(offset_pct)
    return o


def _entry(kind, order, size, bracket_, expiry_bars):
    body = {}
    if order is not None:
        body["order"] = order
    if size is not None:
        body["size"] = {k: _j(v) for k, v in size.items()}
    if bracket_:
        body["bracket"] = bracket_
    if expiry_bars is not None:
        body["expiry_bars"] = _j(expiry_bars)
    return {kind: body}


def enter_long(order=None, size=None, bracket=None, expiry_bars=None) -> dict:          # noqa: A002
    return _entry("enter_long", order, size, bracket, expiry_bars)


def enter_short(order=None, size=None, bracket=None, expiry_bars=None) -> dict:         # noqa: A002
    return _entry("enter_short", order, size, bracket, expiry_bars)


def exit_long() -> dict:
    return {"exit_long": {}}


def exit_short() -> dict:
    return {"exit_short": {}}


def exit_all() -> dict:
    return {"exit_all": {}}


def rule(when, do: dict, id: str | None = None) -> dict:                                  # noqa: A002
    r = {"when": _j(when), "do": do}
    if id:
        r["id"] = id
    return r


# ------------------------------------------------------------------------------------------------ strategy
@dataclass
class Strategy:
    name: str
    rules: list
    params: Params | dict | None = None
    signals: dict | None = None
    constraints: list | None = None
    interval: str | None = None
    product: str | None = None
    adjustment: str | None = None
    description: str | None = None
    meta: dict | None = None

    def to_dict(self) -> dict:
        d = {"ir_version": 1, "name": self.name}
        if self.description:
            d["description"] = self.description
        if self.params:
            d["params"] = self.params.to_dict() if isinstance(self.params, Params) else dict(self.params)
        if self.constraints:
            d["constraints"] = [_j(c) for c in self.constraints]
        if self.interval:
            d["universe"] = {"interval": self.interval}
        if self.adjustment:
            d["data"] = {"adjustment": self.adjustment}
        if self.signals:
            d["signals"] = {k: _j(v) for k, v in self.signals.items()}
        d["rules"] = list(self.rules)
        if self.product:
            d["product"] = self.product
        if self.meta:
            d["meta"] = self.meta
        return d

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @property
    def sha256(self) -> str:
        return ir_sha256(self.to_dict())

    def compile(self):
        return compile(self)


def strategy(name: str, rules: list, **kw) -> Strategy:
    return Strategy(name, rules, **kw)


def compile(doc):                                                                          # noqa: A001
    """Check an IR document (Strategy, dict or JSON text) -> hbt_core.Program. Raises IRError."""
    import hbt_core

    if isinstance(doc, Strategy):
        text = doc.to_json(None)
    elif isinstance(doc, dict):
        text = json.dumps(doc)
    else:
        text = str(doc)
    try:
        return hbt_core.compile(text)
    except hbt_core.IRError as e:
        raise _lift(e) from None


def load(path: str):
    """Compile an IR JSON file."""
    with open(path, encoding="utf-8") as f:
        return compile(f.read())
