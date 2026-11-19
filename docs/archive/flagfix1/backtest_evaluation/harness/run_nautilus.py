import time, sys, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
from decimal import Decimal
import numpy as np, pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import Currency
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig
from parity_common import *
data = load_daily()
INR = Currency.from_str("INR")
class Cfg(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
class SmaCross(Strategy):
    def __init__(self, config, ent, ex, closes):
        super().__init__(config); self.ent, self.ex, self.i = ent, ex, -1
        self.fills = []
    def on_start(self):
        self.iid = InstrumentId.from_str(self.config.instrument_id)
        self.subscribe_bars(BarType.from_str(self.config.bar_type))
    def on_bar(self, bar):
        self.i += 1; i = self.i
        pos = self.portfolio.net_position(self.iid)
        if self.ent[i] and pos == 0:
            cash = float(self.portfolio.account(Venue("NSE")).balance_free(INR).as_double())
            q = int(cash // (float(bar.close) * (1 + FEE)))
            if q > 0: self.submit_order(self.order_factory.market(self.iid, OrderSide.BUY, Quantity.from_int(q)))
        elif self.ex[i] and pos > 0:
            self.close_all_positions(self.iid)
    def on_order_filled(self, e): self.fills.append(float(e.last_px))
res, fill_check = {}, {}
t0 = time.perf_counter()
for s, d in data.items():
    eng = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    eng.add_venue(Venue("NSE"), oms_type=OmsType.NETTING, account_type=AccountType.CASH, base_currency=INR,
                  starting_balances=[Money(CASH, INR)])
    iid = InstrumentId.from_str(f"{s}.NSE")
    inst = Equity(iid, Symbol(s), INR, 2, Price.from_str("0.01"), Quantity.from_int(1), 0, 0,
                  maker_fee=Decimal(str(FEE)), taker_fee=Decimal(str(FEE)))
    eng.add_instrument(inst)
    bt_ = f"{s}.NSE-1-DAY-LAST-EXTERNAL"
    df = d[["open", "high", "low", "close", "volume"]].copy()
    df.index = df.index.tz_convert("UTC") + pd.Timedelta(hours=15, minutes=30)   # bar CLOSE time (nautilus convention)
    bars = BarDataWrangler(BarType.from_str(bt_), inst).process(df)
    eng.add_data(bars)
    ent, ex = signals(d["close"].to_numpy())
    strat = SmaCross(Cfg(instrument_id=str(iid), bar_type=bt_), ent, ex, d["close"].to_numpy())
    eng.add_strategy(strat); eng.run()
    acct = eng.portfolio.account(Venue("NSE"))
    cash = float(acct.balance_total(INR).as_double())
    pos = float(eng.portfolio.net_position(iid))
    res[s] = (cash + pos * d["close"].iloc[-1], int(sum(1 for o in eng.cache.orders() if o.side == OrderSide.BUY and o.is_closed and o.filled_qty > 0)))
    if s == "EQ00":
        closes, opens = set(np.round(d["close"], 2)), set(np.round(d["open"], 2))
        fill_check = {"fills": len(strat.fills), "at_signal_close": sum(f in closes for f in strat.fills),
                      "at_some_open": sum(f in opens for f in strat.fills)}
    eng.dispose()
report("nautilus", res, time.perf_counter() - t0, "close", {"fill_check_EQ00": fill_check})
print("fill prices check EQ00:", fill_check)
