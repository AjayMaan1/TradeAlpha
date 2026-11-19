import sys, warnings; sys.path.insert(0, "/tmp/eval"); warnings.filterwarnings("ignore")
exec(open("/tmp/eval/run_nautilus.py").read().split("res, fill_check = {}, {}")[0])
import json
n = json.load(open("/tmp/eval/parity_nautilus_close.json"))["results"]; ref = json.load(open("/tmp/eval/parity_reference_close.json"))["results"]
s = max(n, key=lambda k: abs(n[k][0] - ref[k][0])); d = data[s]
eng = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
eng.add_venue(Venue("NSE"), oms_type=OmsType.NETTING, account_type=AccountType.CASH, base_currency=INR, starting_balances=[Money(CASH, INR)])
iid = InstrumentId.from_str(f"{s}.NSE")
inst = Equity(iid, Symbol(s), INR, 2, Price.from_str("0.01"), Quantity.from_int(1), 0, 0, maker_fee=Decimal(str(FEE)), taker_fee=Decimal(str(FEE)))
eng.add_instrument(inst); bt_ = f"{s}.NSE-1-DAY-LAST-EXTERNAL"
df = d[["open","high","low","close","volume"]].copy(); df.index = df.index.tz_convert("UTC") + pd.Timedelta(hours=15, minutes=30)
eng.add_data(BarDataWrangler(BarType.from_str(bt_), inst).process(df))
ent, ex = signals(d["close"].to_numpy())
class S2(SmaCross):
    def on_order_filled(self, e):
        self.fills.append((str(e.order_side), float(e.last_qty), float(e.last_px), float(e.commission.as_double())))
st = S2(Cfg(instrument_id=str(iid), bar_type=bt_), ent, ex, None); eng.add_strategy(st); eng.run()
# reference trade list
c = d.close.to_numpy(); cash, sh, reff = CASH, 0, []
for t in range(len(c)):
    if ent[t] and sh == 0:
        q = int(cash // (c[t]*(1+FEE))); cash -= q*c[t]*(1+FEE); sh = q; reff.append(("BUY", q, c[t], q*c[t]*FEE))
    elif ex[t] and sh > 0:
        cash += sh*c[t]*(1-FEE); reff.append(("SELL", sh, c[t], sh*c[t]*FEE)); sh = 0
print(s, "fills nautilus", len(st.fills), "reference", len(reff)); print("fills 25-26:", st.fills[25:27]); print("bar volume on that day (shares):", int(d.volume.to_numpy()[np.flatnonzero(ex)][np.searchsorted(np.flatnonzero(ex), 0)]) if False else "")
for k, (a, b) in enumerate(zip(st.fills, reff)):
    if abs(a[1]-b[1]) > 0 or abs(a[2]-b[2]) > 1e-9 or abs(a[3]-b[3]) > 0.006:
        print(k, "nautilus", a, "\n   reference", (b[0], b[1], round(b[2],2), round(b[3],4))); 
        if k > 6: break
